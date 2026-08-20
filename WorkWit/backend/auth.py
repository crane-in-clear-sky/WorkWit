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

from db import (get_user_by_token, has_permission, PERMS, PERM_LABELS,
    verify_password, create_session, delete_session, row_to_dict, add_log)

from fastapi import APIRouter
router = APIRouter()

def client_ip(request):
    return request.client.host if request.client else ""


def require_login(request: Request):
    """登录门禁：任意需登录的功能都过这一关。"""
    u = get_user_by_token(request.cookies.get("session"))
    if not u:
        raise HTTPException(status_code=401, detail="未登录")
    return u


def require_perm(perm_key: str, request: Request):
    """功能级权限：管理员自动放行；普通用户必须有对应 perm_key。"""
    u = get_user_by_token(request.cookies.get("session"))
    if not u:
        raise HTTPException(status_code=401, detail="未登录")
    if not has_permission(u, perm_key):
        raise HTTPException(status_code=403, detail=f"无权限：{PERM_LABELS.get(perm_key, perm_key)}")
    return u


def require_perm_or(perm_keys, request: Request, fallback=("agent",)):
    """兼容多权限任一即可：用于迁移期，旧『agent』权限用户也能进新注册的功能位。

    - 管理员直接放行
    - 普通用户任一 perm_key 命中即放行
    - 若候选全部 miss，自动加入『fallback』再判一次（默认 agent）—— 老用户不感知升级
    """
    u = get_user_by_token(request.cookies.get("session"))
    if not u:
        raise HTTPException(status_code=401, detail="未登录")
    keys = list(perm_keys or [])
    for k in keys:
        if has_permission(u, k):
            return u
    # 全部 miss 后尝试 fallback；老用户只持有 agent 时，由 fallback 兜底
    for k in fallback:
        if has_permission(u, k):
            return u
    # 取最贴近的 label 当错误提示
    hint = PERM_LABELS.get(keys[0], keys[0]) if keys else "对应功能"
    raise HTTPException(status_code=403, detail=f"无权限：{hint}")


def user_public(u):
    """对外安全的用户信息（含权限）。"""
    return {
        "id": u["id"], "username": u["username"],
        "display_name": u["display_name"], "role": u["role"],
        "permissions": PERMS if u["role"] == "admin" else get_user_permissions(u["id"]),
    }


def bad(e):
    return JSONResponse(status_code=400, content={"error": str(e)})


@router.post("/api/auth/login")
async def login(payload: dict, request: Request, response: Response):
    from db import get_conn, verify_password, row_to_dict
    username = (payload.get("username") or "").strip()
    pw = payload.get("password") or ""
    c = get_conn()
    row = c.execute("SELECT * FROM users WHERE username=?", (username,)).fetchone()
    c.close()
    if not row or not verify_password(pw, row["password_hash"]):
        add_log("login_fail", target=username, ip=client_ip(request))
        raise HTTPException(status_code=401, detail="用户名或密码错误")
    token = create_session(row["id"])
    response.set_cookie("session", token, httponly=True, samesite="lax", path="/", max_age=7 * 24 * 3600)
    add_log("login", user=row_to_dict(row), ip=client_ip(request))
    return {"ok": True, "user": user_public(row_to_dict(row))}


@router.post("/api/auth/logout")
async def logout(request: Request, response: Response):
    token = request.cookies.get("session")
    if token:
        delete_session(token)
    response.delete_cookie("session")
    return {"ok": True}


@router.get("/api/auth/me")
async def me(request: Request):
    u = get_user_by_token(request.cookies.get("session"))
    if not u:
        return {"user": None, "permissions": []}
    return {"user": user_public(u), "permissions": user_public(u)["permissions"]}

