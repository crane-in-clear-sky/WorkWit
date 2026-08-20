import io, json, re, traceback, asyncio, logging, time, os, subprocess, sys, zipfile, shutil, tempfile, uuid
from typing import List
import urllib.request, urllib.parse, html
import html as html_lib
from fastapi import FastAPI, UploadFile, File, Form, Request, Response, HTTPException
from fastapi.responses import FileResponse, JSONResponse, StreamingResponse
from openai import OpenAI
import docx
from pypdf import PdfReader
from db import (
    init_db, get_user_by_token, create_session, delete_session, add_log,
    get_active, list_models, save_model, delete_model, activate, toggle_model_enabled,
    list_orgs, create_org, update_org, delete_org,
    list_departments, create_department, update_department, delete_department,
    list_users, create_user, update_user, delete_user, admin_count,
    get_user_permissions, set_user_permissions, has_permission, list_logs, list_logs_for_user,
    delete_logs_range,
    get_conn,
    list_tools, save_tool, inc_tool_calls, toggle_tool,
    save_skill, get_skill, get_skill_by_name, list_skills,
    delete_skill, toggle_skill, review_skill, inc_skill_calls, set_skill_visibility,
    update_skill, list_skill_versions, rollback_skill, clone_skill,
    install_skill, uninstall_skill,
    _SKILL_NAME_RE,
    MASK,
    PERMS, PERM_LABELS,
)
from agent import run_agent, resolve_session_tools
import sandbox

from auth import require_perm, bad, client_ip
from db import (list_models, save_model, delete_model, activate, toggle_model_enabled,
               list_orgs, create_org, update_org, delete_org,
               list_departments, create_department, update_department, delete_department,
               list_users, create_user, update_user, delete_user, admin_count,
               get_user_permissions, set_user_permissions, list_logs, list_logs_for_user,
               delete_logs_range, get_conn, verify_password, row_to_dict, MASK,
               PERMS, PERM_LABELS, has_permission, get_active)

from fastapi import APIRouter
router = APIRouter()

_DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")


def _valid_date(s: str):
    """校验 YYYY-MM-DD 格式；空串返回 None，非法返回哨兵 'ERR'。"""
    if not s:
        return None
    return s if _DATE_RE.match(s) else "ERR"


@router.get("/api/admin/models")
async def am_list(request: Request):
    require_perm("m_models", request)
    return list_models()


@router.post("/api/admin/models")
async def am_save(payload: dict, request: Request):
    u = require_perm("m_models", request)
    try:
        mid = save_model(payload)
    except ValueError as e:
        return bad(e)
    add_log("model_" + ("update" if payload.get("id") else "create"),
             target=str(mid), user=u, ip=client_ip(request))
    return {"ok": True, "id": mid}


@router.delete("/api/admin/models/{model_id}")
async def am_delete(model_id: int, request: Request):
    u = require_perm("m_models", request)
    delete_model(model_id)
    add_log("model_delete", target=str(model_id), user=u, ip=client_ip(request))
    return {"ok": True}


@router.post("/api/admin/models/{model_id}/activate")
async def am_activate(model_id: int, request: Request):
    from db import get_conn
    u = require_perm("m_models", request)
    c = get_conn(); r = c.execute("SELECT role FROM models WHERE id=?", (model_id,)).fetchone(); c.close()
    if not r:
        raise HTTPException(status_code=404, detail="模型不存在")
    activate(r["role"], model_id)
    add_log("model_activate", target=str(model_id), user=u, ip=client_ip(request))
    return {"ok": True}


@router.post("/api/admin/models/{model_id}/toggle")
async def am_toggle(model_id: int, request: Request, payload: dict):
    """启用/禁用模型（同一 role 下可同时启用多个）。被禁用会自动取消默认。"""
    require_perm("m_models", request)
    enabled = bool(payload.get("enabled", True))
    toggle_model_enabled(model_id, enabled)
    return {"ok": True, "enabled": enabled}


@router.post("/api/admin/models/test")
async def am_test(payload: dict, request: Request):
    """连接测试：用给定（或已存）配置做一次轻量调用，判断模型是否可用。"""
    u = require_perm("m_models", request)
    base = (payload.get("base_url") or "").strip()
    role = (payload.get("role") or "").strip()
    name = (payload.get("model_name") or "").strip()
    mid = payload.get("model_id")
    key = (payload.get("api_key") or "").strip()
    # 列表行「连接测试」只传 model_id：从已存配置回退 base_url/role/model_name/api_key/thinking，
    # 否则即便模型已配置好也会误报“请先填写 Base URL”。
    thinking = bool(payload.get("thinking"))
    # 编辑态重新测试时：表单里的 api_key 显示的是掩码 ********（或留空），
    # 必须回退到已存密钥；否则会把字面量 ******** 当 key 发给模型 → 401 Token invalid。
    # 只要传了 model_id 就以已存配置为准，表单值仅在「确实填了非掩码内容」时覆盖。
    if mid:
        c = get_conn()
        row = c.execute("SELECT base_url, api_key, model_name, role, thinking FROM models WHERE id=?", (mid,)).fetchone()
        c.close()
        if row:
            base = base or (row["base_url"] or "").strip()
            name = name or (row["model_name"] or "").strip()
            role = role or (row["role"] or "chat")
            thinking = thinking or bool(row["thinking"])
            if not key or key == MASK:
                key = (row["api_key"] or "").strip()
    if not base:
        return {"ok": False, "error": "请先填写 Base URL 再测试"}
    if not name:
        return {"ok": False, "error": "请先填写模型名再测试"}
    client = OpenAI(base_url=base, api_key=key or "not-needed", timeout=20)

    # 思考模型需要更多 token；普通 ping 用 5，思考模型放大到 300 以真实校验。
    test_max = 300 if thinking else 5
    chat_kwargs = {"model": name or "test", "messages": [{"role": "user", "content": "ping"}],
                   "max_tokens": test_max, "timeout": 20}
    if thinking:
        chat_kwargs["extra_body"] = {"enable_thinking": True}

    def _chat():
        client.chat.completions.create(**chat_kwargs)

    def _embed():
        client.embeddings.create(model=name or "test", input="ping", timeout=20)

    def _list():
        client.models.list()

    t0 = time.time()
    try:
        if role in ("chat", "vision"):
            await asyncio.to_thread(_chat)
        elif role == "embed":
            await asyncio.to_thread(_embed)
        else:  # rerank 无标准端点，仅验证连通性
            await asyncio.to_thread(_list)
        return {"ok": True, "message": "连接成功，模型可用", "latency_ms": int((time.time() - t0) * 1000)}
    except Exception as e:
        err = f"{type(e).__name__}: {e}"
        detail = ""
        try:
            await asyncio.to_thread(_list)
            detail = "（Base URL 与密钥有效，但模型名可能不正确）"
        except Exception as e2:
            detail = f"（可能是地址不可达或密钥错误：{type(e2).__name__}: {e2}）"
        return {"ok": False, "error": "连接测试未通过", "detail": err + " " + detail,
                "latency_ms": int((time.time() - t0) * 1000)}


@router.get("/api/admin/orgs")
async def ao_list(request: Request, q: str = ""):
    require_perm("m_orgs", request); return list_orgs(q or None)


@router.post("/api/admin/orgs")
async def ao_create(payload: dict, request: Request):
    u = require_perm("m_orgs", request)
    try:
        oid = create_org(payload)
    except ValueError as e:
        return bad(e)
    add_log("org_create", target=str(oid), detail=payload.get("name", ""), user=u, ip=client_ip(request))
    return {"ok": True, "id": oid}


@router.put("/api/admin/orgs/{oid}")
async def ao_update(oid: int, payload: dict, request: Request):
    u = require_perm("m_orgs", request)
    update_org(oid, payload)
    add_log("org_update", target=str(oid), user=u, ip=client_ip(request))
    return {"ok": True}


@router.delete("/api/admin/orgs/{oid}")
async def ao_delete(oid: int, request: Request):
    u = require_perm("m_orgs", request)
    delete_org(oid)
    add_log("org_delete", target=str(oid), user=u, ip=client_ip(request))
    return {"ok": True}


@router.get("/api/admin/departments")
async def ad_list(request: Request, q: str = ""):
    require_perm("m_depts", request); return list_departments(q or None)


@router.post("/api/admin/departments")
async def ad_create(payload: dict, request: Request):
    u = require_perm("m_depts", request)
    try:
        did = create_department(payload)
    except ValueError as e:
        return bad(e)
    add_log("dept_create", target=str(did), detail=payload.get("name", ""), user=u, ip=client_ip(request))
    return {"ok": True, "id": did}


@router.put("/api/admin/departments/{did}")
async def ad_update(did: int, payload: dict, request: Request):
    u = require_perm("m_depts", request)
    update_department(did, payload)
    add_log("dept_update", target=str(did), user=u, ip=client_ip(request))
    return {"ok": True}


@router.delete("/api/admin/departments/{did}")
async def ad_delete(did: int, request: Request):
    u = require_perm("m_depts", request)
    delete_department(did)
    add_log("dept_delete", target=str(did), user=u, ip=client_ip(request))
    return {"ok": True}


@router.get("/api/admin/users")
async def au_list(request: Request, q: str = ""):
    require_perm("m_users", request); return list_users(q or None)


@router.post("/api/admin/users")
async def au_create(payload: dict, request: Request):
    u = require_perm("m_users", request)
    try:
        uid = create_user(payload)
    except ValueError as e:
        return bad(e)
    if "permissions" in payload:
        set_user_permissions(uid, payload["permissions"] or [])
    add_log("user_create", target=str(uid), detail=payload.get("username", ""), user=u, ip=client_ip(request))
    return {"ok": True, "id": uid}


@router.put("/api/admin/users/{uid}")
async def au_update(uid: int, payload: dict, request: Request):
    u = require_perm("m_users", request)
    # 防止把唯一的 admin 降级/禁用
    if (payload.get("role") == "user" or payload.get("status") == "disabled"):
        from db import get_conn
        c = get_conn(); r = c.execute("SELECT role,status FROM users WHERE id=?", (uid,)).fetchone(); c.close()
        if r and r["role"] == "admin" and admin_count() <= 1:
            return bad("不能修改唯一管理员的角色或禁用它")
    update_user(uid, payload)
    if "permissions" in payload:
        set_user_permissions(uid, payload["permissions"] or [])
    add_log("user_update", target=str(uid), user=u, ip=client_ip(request))
    return {"ok": True}


@router.delete("/api/admin/users/{uid}")
async def au_delete(uid: int, request: Request):
    u = require_perm("m_users", request)
    from db import get_conn
    c = get_conn(); r = c.execute("SELECT role FROM users WHERE id=?", (uid,)).fetchone(); c.close()
    if r and r["role"] == "admin" and admin_count() <= 1:
        return bad("不能删除唯一的管理员账号")
    delete_user(uid, by_admin_id=u["id"])
    add_log("user_delete", target=str(uid), user=u, ip=client_ip(request))
    return {"ok": True}


@router.get("/api/admin/users/{uid}/permissions")
async def au_perms_get(uid: int, request: Request):
    require_perm("m_users", request)
    return {"permissions": get_user_permissions(uid)}


@router.put("/api/admin/users/{uid}/permissions")
async def au_perms_put(uid: int, payload: dict, request: Request):
    u = require_perm("m_users", request)
    # 管理员始终拥有全部权限，不由本接口改动
    from db import get_conn
    c = get_conn(); r = c.execute("SELECT role FROM users WHERE id=?", (uid,)).fetchone(); c.close()
    if r and r["role"] == "admin":
        return {"ok": True, "permissions": PERMS}
    set_user_permissions(uid, payload.get("permissions") or [])
    add_log("user_perms_update", target=str(uid), detail=",".join(payload.get("permissions") or []),
            user=u, ip=client_ip(request))
    return {"ok": True, "permissions": get_user_permissions(uid)}


@router.get("/api/admin/logs")
async def al_list(request: Request, limit: int = 200, offset: int = 0,
                  start_date: str = "", end_date: str = ""):
    u = require_perm("m_logs", request)
    sd, ed = _valid_date(start_date), _valid_date(end_date)
    if sd == "ERR":
        return bad("start_date 格式应为 YYYY-MM-DD")
    if ed == "ERR":
        return bad("end_date 格式应为 YYYY-MM-DD")
    if u["role"] == "admin":
        return list_logs(limit=min(limit, 500), offset=offset, start_date=sd, end_date=ed)
    return list_logs_for_user(u["id"], limit=min(limit, 500), start_date=sd, end_date=ed)


@router.post("/api/admin/logs/delete")
async def al_delete_range(payload: dict, request: Request):
    u = require_perm("m_logs", request)
    sd, ed = _valid_date(payload.get("start_date") or ""), _valid_date(payload.get("end_date") or "")
    if sd is None or ed is None or sd == "ERR" or ed == "ERR":
        return bad("请同时提供 start_date 与 end_date（格式 YYYY-MM-DD）")
    uid = None if u["role"] == "admin" else u["id"]  # 会话隔离：普通用户只删自己的
    n = delete_logs_range(sd, ed, uid)
    add_log("log_delete_range", target=f"{sd}~{ed}", detail=f"删除{n}条日志",
            user=u, ip=client_ip(request))
    return {"ok": True, "deleted": n}

