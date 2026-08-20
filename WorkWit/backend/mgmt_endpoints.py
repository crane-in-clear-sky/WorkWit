import io, json, re, traceback, asyncio, logging, time, os, subprocess, sys, zipfile, shutil, tempfile, uuid
from typing import List
import urllib.request, urllib.parse, html
import html as html_lib
logger = logging.getLogger("mgmt")
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
    get_available_features,
    get_nav_settings, save_nav_settings,
    get_system_settings, save_system_settings,
    list_tools, save_tool, inc_tool_calls, toggle_tool,
    save_skill, get_skill, get_skill_by_name, list_skills,
    delete_skill, toggle_skill, review_skill, inc_skill_calls, set_skill_visibility,
    update_skill, list_skill_versions, rollback_skill, clone_skill,
    install_skill, uninstall_skill,
    _SKILL_NAME_RE,
    MASK,
    PERMS, PERM_LABELS,
    get_smtp_config, save_smtp_config, SMTP_KEY,
)
from agent import run_agent, resolve_session_tools
import sandbox

from auth import require_login, require_perm, client_ip, user_public, bad
from db import (get_active, get_conn, list_tools, toggle_tool, save_tool, get_skill,
               list_skills, update_skill, list_skill_versions, rollback_skill, clone_skill,
               install_skill, uninstall_skill, delete_skill, toggle_skill, set_skill_visibility,
               review_skill, save_skill, add_log, MASK, PERMS, PERM_LABELS,
               verify_password, create_session, delete_session, row_to_dict,
               get_user_by_token, has_permission, _SKILL_NAME_RE)
import sandbox
from core import _llm_call, _model_params
from tools_handlers import _derive_trigger_words
from tools_build import _run_skill_sandboxed
import mcp_client

from fastapi import APIRouter
router = APIRouter()

@router.get("/api/agent/tools")
async def agent_tools(request: Request):
    """全局工具库：列出已注册工具（含禁用），供前端展示与检索。
    - 管理员：返回全部（含他人私有工具，便于管理）
    - 普通用户：返回 公共库 + 自己创建的私有工具（scope=private AND owner_id=自己）
    """
    u = require_perm("agent", request)
    is_admin = (u.get("role") == "admin")
    tools = (list_tools(include_disabled=True) if is_admin
             else list_tools(include_disabled=True, for_user_id=u["id"]))
    return JSONResponse(content={"tools": tools, "is_admin": is_admin})


@router.post("/api/agent/tools/{tool_id}/toggle")
async def agent_tool_toggle(tool_id: int, request: Request, payload: dict):
    """管理员启停全局工具。仅系统管理员可操作。"""
    u = require_perm("agent", request)
    if u.get("role") != "admin":
        return JSONResponse(status_code=403, content={"error": "仅系统管理员可启停工具"})
    toggle_tool(tool_id, bool(payload.get("enabled")))
    add_log("tool_toggle", target=str(tool_id), detail=str(payload.get("enabled")), user=u, ip=client_ip(request))
    return JSONResponse(content={"ok": True})


@router.delete("/api/agent/tools/{tool_id}")
async def agent_tool_delete(tool_id: int, request: Request):
    """删除工具：仅系统管理员可删除。内置工具不可删（仅可停用）。"""
    u = require_perm("agent", request)
    conn = get_conn()
    try:
        row = conn.execute(
            "SELECT id, owner_id, builtin FROM tools WHERE id=?", (tool_id,)
        ).fetchone()
        if not row:
            return JSONResponse(status_code=404, content={"error": "工具不存在"})
        is_admin = (u.get("role") == "admin")
        if row["builtin"]:
            return JSONResponse(status_code=403, content={"error": "内置工具不可删除，仅可停用"})
        if not is_admin:
            return JSONResponse(status_code=403, content={"error": "仅系统管理员可删除工具"})
        conn.execute("DELETE FROM tools WHERE id=?", (tool_id,))
        conn.commit()
    finally:
        conn.close()
    add_log("tool_delete", target=str(tool_id), user=u, ip=client_ip(request))
    return JSONResponse(content={"ok": True})


@router.put("/api/agent/tools/{tool_id}")
async def agent_tool_update(tool_id: int, request: Request, payload: dict):
    """编辑工具元数据：仅系统管理员可编辑。可改 中英文名称、公开/私有、描述、触发词。"""
    u = require_perm("agent", request)
    if u.get("role") != "admin":
        return JSONResponse(status_code=403, content={"error": "仅系统管理员可编辑工具"})
    name = (payload.get("name") or "").strip()
    display_name = (payload.get("display_name") or name).strip()
    description = (payload.get("description") or "").strip()
    trigger_words = (payload.get("trigger_words") or "").strip()
    scope = (payload.get("scope") or "global").strip()
    if scope not in ("global", "private"):
        return JSONResponse(status_code=400, content={"error": "scope 必须为 global 或 private"})
    if not name:
        return JSONResponse(status_code=400, content={"error": "英文标识符(name)必填"})
    if not description:
        return JSONResponse(status_code=400, content={"error": "描述必填"})
    conn = get_conn()
    try:
        row = conn.execute("SELECT id, owner_id FROM tools WHERE id=?", (tool_id,)).fetchone()
        if not row:
            return JSONResponse(status_code=404, content={"error": "工具不存在"})
        current_owner = row["owner_id"]
        dup = conn.execute(
            "SELECT id FROM tools WHERE name=? AND id!=?", (name, tool_id)
        ).fetchone()
        if dup:
            return JSONResponse(status_code=400, content={"error": "英文标识符已存在，请更换"})
        # 设为「私有」时若原无归属（owner_id 为空，例如管理员把自建的全局工具私有化），
        # 绑定到当前操作管理员，杜绝产生「scope='private' 且 owner_id=NULL」的永久不可用孤儿工具
        sets = "name=?, display_name=?, description=?, trigger_words=?, scope=?"
        vals = [name, display_name, description, trigger_words, scope]
        if scope == "private" and current_owner is None:
            sets += ", owner_id=?"
            vals.append(u["id"])
        vals.append(tool_id)
        conn.execute(f"UPDATE tools SET {sets} WHERE id=?", vals)
        conn.commit()
    finally:
        conn.close()
    add_log("tool_update", target=str(tool_id), detail=name, user=u, ip=client_ip(request))
    return JSONResponse(content={"ok": True})


def _find_tool_md(root):
    """在解压/展开的目录树中定位 TOOL.md（工具包）；其次兼容 SKILL.md。"""
    cands = []
    for dirpath, _, files in os.walk(root):
        for f in files:
            fl = f.lower()
            if fl == "tool.md":
                cands.append((0, os.path.join(dirpath, f)))
            elif fl == "skill.md":
                cands.append((1, os.path.join(dirpath, f)))
            elif fl.endswith(".md") and fl != "readme.md":
                cands.append((2, os.path.join(dirpath, f)))
    if not cands:
        return None
    cands.sort(key=lambda x: x[0])
    return cands[0][1]


def _slugify(s):
    """把中文/空格/特殊字符转成合法英文标识符（字母数字下划线）。"""
    s = (s or "").strip().lower()
    out = []
    for ch in s:
        if ch.isalnum() and (ch.isascii()):
            out.append(ch)
        elif ch in " _-":
            if out and out[-1] != "_":
                out.append("_")
    name = "".join(out).strip("_")
    # 去掉连续下划线、首字符数字
    name = re.sub(r"_+", "_", name)
    if name and name[0].isdigit():
        name = "t_" + name
    return name or ""


def _register_global_tool(name, display_name, description, code, trigger_words="", category="general", u=None, create_source="manual"):
    """管理员注册「全局工具」：沙箱执行的 Python 代码工具，scope=global、owner=None、is_user_created=1。

    返回 dict：{"ok": bool, "message": str, "tid": int|None}
    create_source 标记来源：manual=语义生成/手动注册 / upload=上传工具包。"""
    name = (name or "").strip()
    display_name = (display_name or name).strip()
    description = (description or "").strip()
    code = (code or "").strip()
    if not name:
        return {"ok": False, "message": "英文标识符(name)必填"}
    if not description:
        return {"ok": False, "message": "描述必填"}
    if not _SKILL_NAME_RE.match(name):
        return {"ok": False, "message": "name 须为合法标识符（字母/数字/下划线，如 my_tool）"}
    if not code:
        return {"ok": False, "message": "代码(code_text)必填，需定义 def run(a): ..."}
    ok, reason = sandbox.scan_code(code)
    if not ok:
        return {"ok": False, "message":
                "代码未通过安全扫描（" + reason + "）。请改用纯 Python 数据处理逻辑，"
                "不要使用 os/sys/subprocess/socket/requests/shutil 等模块，也不要做文件/网络/系统调用，禁止使用 eval/exec。"}
    try:
        _creator_name = (u.get("display_name") or u.get("username") or "") if u else ""
        tid = save_tool({
            "name": name, "display_name": display_name or name, "description": description,
            "category": category or "general", "trigger_words": trigger_words or "",
            "scope": "global", "owner_id": None, "is_user_created": 1,
            "code_text": code, "backend_type": "user_code", "handler": None,
            "creator_name": _creator_name, "create_source": create_source,
        })
    except Exception as e:
        return {"ok": False, "message": "保存失败：" + str(e)}
    # 沙箱试跑：保证空参 / 常见参数也能返回结果
    tests = [{}, {"input": "测试", "text": "测试", "a": 1, "b": 2}]
    ran = []
    try:
        ran = [sandbox.run_code(code, t) for t in tests]
    except Exception:
        ran = []
    ok_run = any(r.get("ok") for r in ran)
    note = "" if ok_run else "（提示：试跑未通过，调用时可能需要传入正确的参数；建议 run(a) 用 a.get('key', 默认值) 取参）"
    return {"ok": True, "tid": tid,
            "message": f"工具「{display_name or name}（{name}）」已注册为全局工具{('，' + note) if note else ''}。"}


@router.post("/api/agent/tools/upload")
async def agent_tool_upload_package(request: Request,
                                     files: List[UploadFile] = File(...)):
    """管理员上传工具包：文件夹 / .zip（需含 TOOL.md）或单个 .md。
    TOOL.md 须含 YAML frontmatter（name、description），Python 代码写在 ```python 代码块中（定义 def run(a)）。"""
    u = require_perm("agent", request)
    if u.get("role") != "admin":
        return JSONResponse(status_code=403, content={"error": "仅系统管理员可注册全局工具"})
    if not files:
        return JSONResponse(status_code=400, content={"error": "未收到文件"})
    tmp = tempfile.mkdtemp()
    try:
        single_zip = (len(files) == 1 and (files[0].filename or "").lower().endswith(".zip"))
        if single_zip:
            data = await files[0].read()
            try:
                with zipfile.ZipFile(io.BytesIO(data)) as z:
                    z.extractall(tmp)
            except Exception as e:
                return JSONResponse(status_code=400, content={"error": "压缩包解压失败：" + str(e)})
        else:
            for f in files:
                data = await f.read()
                rel = (f.filename or "upload").replace("\\", "/")
                dst = os.path.join(tmp, rel)
                os.makedirs(os.path.dirname(dst) or tmp, exist_ok=True)
                with open(dst, "wb") as fh:
                    fh.write(data)
        md_path = _find_tool_md(tmp)
        if not md_path:
            return JSONResponse(status_code=400, content={
                "error": "未找到 TOOL.md（文件夹/.zip 需包含 TOOL.md；或直接上传单个 .md 文件）。"})
        try:
            text = open(md_path, encoding="utf-8").read()
        except Exception as e:
            return JSONResponse(status_code=400, content={"error": "读取 TOOL.md 失败：" + str(e)})
        info = parse_skill_md(text)  # 复用解析：name/description/trigger_words/category + python 代码块
        name, desc = info["name"], info["description"]
        code = info["code"]
        if not name:
            return JSONResponse(status_code=400,
                                content={"error": "TOOL.md 缺少 YAML 字段 name（英文标识符）"})
        if not desc:
            return JSONResponse(status_code=400,
                                content={"error": "TOOL.md 缺少 YAML 字段 description（工具描述）"})
        if not _SKILL_NAME_RE.match(name):
            return JSONResponse(status_code=400,
                                content={"error": "name 须为合法标识符（字母/数字/下划线）"})
        if not code:
            return JSONResponse(status_code=400, content={
                "error": "TOOL.md 未包含 Python 代码块（需 ```python ... ``` 并定义 def run(a): ...）"})
        res = _register_global_tool(name, info.get("display_name") or name, desc, code,
                                    info.get("trigger_words") or "", info.get("category") or "general", u,
                                    create_source="upload")
        if not res["ok"]:
            return JSONResponse(status_code=400, content={"error": res["message"]})
        add_log("tool_upload", target=str(res["tid"]), detail=name, user=u, ip=client_ip(request))
        return JSONResponse(content={"ok": True, "tid": res["tid"], "message": res["message"]})
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


@router.post("/api/agent/tools/semantic")
async def agent_tool_semantic(request: Request, payload: dict):
    """管理员语义生成全局工具：自然语言描述 → LLM 生成 Python 代码(def run(a)) → 沙箱校验注册。"""
    u = require_perm("agent", request)
    if u.get("role") != "admin":
        return JSONResponse(status_code=403, content={"error": "仅系统管理员可注册全局工具"})
    display_name = (payload.get("display_name") or "").strip()
    name = (payload.get("name") or "").strip()
    description = (payload.get("description") or "").strip()
    trigger_words = (payload.get("trigger_words") or "").strip()
    category = (payload.get("category") or "general").strip() or "general"
    if not description:
        return JSONResponse(status_code=400, content={"error": "功能描述必填（自然语言说明工具要做什么）"})
    if not name:
        name = _slugify(display_name or description)
        if not name:
            return JSONResponse(status_code=400, content={"error": "无法从描述生成英文标识符，请手动填写 name"})
    if not display_name:
        display_name = name
    if not trigger_words:
        trigger_words = _derive_trigger_words(description)
    # 调用主推理模型生成工具代码
    active = get_active("chat") or {}
    base = (active.get("base_url") or "").strip()
    key = (active.get("api_key") or "").strip()
    model_name = (active.get("model_name") or "").strip()
    if not base or not model_name:
        return JSONResponse(status_code=400, content={"error": "未配置主推理模型，无法语义生成代码"})
    client = OpenAI(base_url=base, api_key=key or "not-needed", timeout=120)
    params = _model_params(active)
    params.setdefault("temperature", 0.3)
    sys_p = ("你是一个 Python 工具代码生成器。用户会描述一个工具的功能，请生成一段纯 Python 代码，"
             "定义一个函数：\n"
             "def run(a):\n"
             "    # a 是 dict，包含调用参数；用 a.get('key', 默认值) 取参\n"
             "    ...\n"
             "    return <结果>\n"
             "硬性要求：\n"
             "1. 只允许纯数据处理逻辑（字符串、列表、字典、正则 re、数学 math、json 等）；\n"
             "2. 严禁使用 os/sys/subprocess/socket/shutil/requests/urllib 等模块，严禁文件/网络/系统调用，严禁 eval/exec；\n"
             "3. 返回值必须是可 JSON 序列化的对象（dict / str / list / number）；\n"
             "4. 只输出一个 python 代码块（```python ... ```），不要任何解释文字。")
    try:
        raw = await asyncio.to_thread(_llm_call, client, model_name, sys_p, description, params)
    except Exception as e:
        return JSONResponse(status_code=502, content={"error": "调用模型生成代码失败：" + str(e)})
    code = _extract_python(raw)
    if not code:
        return JSONResponse(status_code=400, content={
            "error": "模型未返回可解析的 Python 代码块。模型原文：\n" + (raw or "")[:2000]})
    res = _register_global_tool(name, display_name, description, code, trigger_words, category, u,
                                create_source="manual")
    if not res["ok"]:
        return JSONResponse(status_code=400, content={"error": res["message"]})
    add_log("tool_semantic", target=str(res["tid"]), detail=name, user=u, ip=client_ip(request))
    return JSONResponse(content={"ok": True, "tid": res["tid"], "message": res["message"]})


def _find_skill_md(root):
    """在解压/展开的目录树中定位 SKILL.md：优先精确名为 SKILL.md，其次任意 .md（深度浅优先）。"""
    cands = []
    for dirpath, _, files in os.walk(root):
        for f in files:
            fl = f.lower()
            depth = os.path.relpath(dirpath, root).count(os.sep)
            if fl == "skill.md":
                cands.append((0, os.path.join(dirpath, f)))
            elif fl.endswith(".md"):
                cands.append((depth + 1, os.path.join(dirpath, f)))
    if not cands:
        return None
    cands.sort(key=lambda x: x[0])
    return cands[0][1]


def _parse_frontmatter(fm):
    """极简 YAML frontmatter 解析：支持 `key: value`、引号、行内列表 [a, b]。"""
    meta = {}
    for raw in fm.split("\n"):
        line = raw.rstrip()
        if not line.strip() or line.lstrip().startswith("#"):
            continue
        if ":" not in line:
            continue
        key, _, val = line.partition(":")
        key, val = key.strip(), val.strip()
        if len(val) >= 2 and val[0] == val[-1] and val[0] in ('"', "'"):
            val = val[1:-1]
        if val.startswith("[") and val.endswith("]"):
            inner = val[1:-1].strip()
            items = [x.strip().strip('"').strip("'") for x in inner.split(",") if x.strip()]
            val = items
        if key:
            meta[key] = val
    return meta


def _extract_python(body):
    """从正文提取首个 ```python 代码块（无语言标记亦可）。"""
    m = re.search(r"```(?:python)?\s*\n(.*?)```", body, re.DOTALL)
    if m:
        return m.group(1).strip()
    return ""


def parse_skill_md(text):
    """解析 SKILL.md：YAML frontmatter(name/description/trigger_words/when_to_use/category/rules/allowed_tools/kind)
    + 正文。kind=method 为方法论技能（仅提示词/流程，注入 system prompt，不执行代码）；
    kind=code（默认）为代码工具技能（Python 代码块在沙箱执行）。"""
    lines = text.split("\n")
    meta, body = {}, text
    if lines and lines[0].strip() == "---":
        end = None
        for i in range(1, len(lines)):
            if lines[i].strip() == "---":
                end = i
                break
        if end is not None:
            meta = _parse_frontmatter("\n".join(lines[1:end]))
            body = "\n".join(lines[end + 1:])
    code = _extract_python(body)
    # 方法论指令：去除 ``` 代码块后的正文（frontmatter 的 instructions 优先覆盖）
    _bl, _in = [], False
    for ln in body.split("\n"):
        if ln.strip().startswith("```"):
            _in = not _in
            continue
        if not _in:
            _bl.append(ln)
    instructions = (meta.get("instructions") or "").strip() or "\n".join(_bl).strip()
    tw = meta.get("trigger_words", "")
    if isinstance(tw, list):
        tw = ",".join(tw)
    wu = meta.get("when_to_use", "")
    if isinstance(wu, list):
        wu = "，".join(wu)
    # 场景四：业务规则(rules) + 工具清单(allowed_tools)
    rules = (meta.get("rules") or "").strip()
    at = meta.get("allowed_tools") or []
    if isinstance(at, str):
        at = [x.strip() for x in at.split(",") if x.strip()]
    if not isinstance(at, list):
        at = []
    skill_type = (meta.get("kind") or meta.get("skill_type") or "code").strip().lower()
    if skill_type not in ("method", "code"):
        skill_type = "code"
    return {
        "name": (meta.get("name") or "").strip(),
        "display_name": (meta.get("display_name") or "").strip(),
        "description": (meta.get("description") or "").strip(),
        "trigger_words": (tw or "").strip(),
        "when_to_use": (wu or "").strip(),
        "category": (meta.get("category") or "general").strip() or "general",
        "rules": rules,
        "allowed_tools": at,
        "code": code,
        "skill_type": skill_type,
        "instructions": (instructions or "").strip(),
    }


@router.post("/api/agent/skills/upload")
async def agent_skill_upload_package(request: Request,
                                     files: List[UploadFile] = File(...),
                                     scope: str = Form("private")):
    """上传技能包：文件夹 / .zip（需含 SKILL.md）或单个 .md 文件。
    SKILL.md 须含 YAML frontmatter（name、description），代码写在 Python 代码块中。"""
    u = require_perm("agent", request)
    is_admin = (u.get("role") == "admin")
    if not files:
        return JSONResponse(status_code=400, content={"error": "未收到文件"})
    tmp = tempfile.mkdtemp()
    try:
        single_zip = (len(files) == 1 and (files[0].filename or "").lower().endswith(".zip"))
        if single_zip:
            data = await files[0].read()
            try:
                with zipfile.ZipFile(io.BytesIO(data)) as z:
                    z.extractall(tmp)
            except Exception as e:
                return JSONResponse(status_code=400, content={"error": "压缩包解压失败：" + str(e)})
        else:
            for f in files:
                data = await f.read()
                rel = (f.filename or "upload").replace("\\", "/")
                dst = os.path.join(tmp, rel)
                os.makedirs(os.path.dirname(dst) or tmp, exist_ok=True)
                with open(dst, "wb") as fh:
                    fh.write(data)
        md_path = _find_skill_md(tmp)
        if not md_path:
            return JSONResponse(status_code=400, content={
                "error": "未找到 SKILL.md（文件夹/.zip 需包含 SKILL.md；或直接上传单个 .md 文件）"})
        try:
            text = open(md_path, encoding="utf-8").read()
        except Exception as e:
            return JSONResponse(status_code=400, content={"error": "读取 SKILL.md 失败：" + str(e)})
        info = parse_skill_md(text)
        name, desc = info["name"], info["description"]
        st = info.get("skill_type") or "code"
        if not name:
            return JSONResponse(status_code=400,
                                content={"error": "SKILL.md 缺少 YAML 字段 name（技能名称，英文标识符）"})
        if not desc:
            return JSONResponse(status_code=400,
                                content={"error": "SKILL.md 缺少 YAML 字段 description（技能描述）"})
        if not _SKILL_NAME_RE.match(name):
            return JSONResponse(status_code=400,
                                content={"error": "name 须为合法标识符（字母/数字/下划线）"})
        if st == "method":
            code = ""
            instructions = (info.get("instructions") or "").strip()
            if not instructions:
                return JSONResponse(status_code=400, content={
                    "error": "method 类技能需在 SKILL.md 正文提供提示词/流程（或 frontmatter 的 instructions 字段），不执行代码"})
        else:
            code = info["code"]
            if not code:
                return JSONResponse(status_code=400, content={
                    "error": "SKILL.md 未包含 Python 代码块（需 ```python ... ``` 并定义 def run(a): ...）"})
            ok, reason = sandbox.scan_code(code)
            if not ok:
                return JSONResponse(status_code=400, content={"error": "代码未通过安全扫描：" + reason})
            instructions = ""
        scope = (scope or "private").strip()
        if scope not in ("private", "public"):
            scope = "private"
        try:
            sid = save_skill({
                "name": name, "display_name": info.get("display_name") or name, "description": desc,
                "category": info["category"] or "general",
                "code_text": code, "skill_type": st, "instructions": instructions,
                "trigger_words": info["trigger_words"] or "",
                "when_to_use": (info.get("when_to_use") or "").strip(),
                "rules": (info.get("rules") or "").strip(),
                "allowed_tools": info.get("allowed_tools") or [],
                "scope": scope,
                "create_source": "upload",
            }, u["id"])
        except Exception as e:
            return JSONResponse(status_code=400, content={"error": "保存失败：" + str(e)})
        if is_admin and scope == "public":
            review_skill(sid, u["id"], "approve")
        add_log("skill_upload_pkg", target=name, user=u, ip=client_ip(request))
        return JSONResponse(content={
            "ok": True, "id": sid, "name": name,
            "status": "approved" if (is_admin and scope == "public") else
                      ("private" if scope == "private" else "pending")})
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


@router.post("/api/agent/skills")
async def agent_skill_upload(request: Request, payload: dict):
    """上传技能：先按 skill_type 做校验（method 仅提示词、code 需沙箱扫描），再入库。
    私有技能立即可用；申请公开进入待审核。"""
    u = require_perm("agent", request)
    is_admin = (u.get("role") == "admin")
    skill_type = (payload.get("skill_type") or "code").strip().lower()
    if skill_type not in ("method", "code"):
        skill_type = "code"
    code = (payload.get("code_text") or "").strip()
    instructions = (payload.get("instructions") or "").strip()
    if skill_type == "method":
        # 方法论技能：不执行代码，无需安全扫描；提示词/流程必填
        if not instructions:
            return JSONResponse(status_code=400,
                                content={"error": "method 类技能需填写「提示词/流程 instructions」"})
        code = ""  # 确保不存代码
    else:
        ok, reason = sandbox.scan_code(code)
        if not ok:
            return JSONResponse(status_code=400,
                                content={"error": "代码未通过安全扫描：" + reason})
        if not code:
            return JSONResponse(status_code=400,
                                content={"error": "code 类技能需填写代码（须定义 def run(args) 函数）"})
    scope = (payload.get("scope") or "private").strip()
    # 用规范化后的值回填，确保 save_skill 拿到一致数据
    payload = dict(payload)
    payload["skill_type"] = skill_type
    payload["code_text"] = code
    payload["instructions"] = instructions
    payload["create_source"] = "manual"  # 用户在技能广场手写创建
    try:
        sid = save_skill(payload, u["id"])
    except ValueError as e:
        return JSONResponse(status_code=400, content={"error": str(e)})
    # 管理员发布的公共技能直接审核通过
    if is_admin and scope == "public":
        review_skill(sid, u["id"], "approve")
    add_log("skill_upload", target=payload.get("name"), detail="上传技能", user=u, ip=client_ip(request))
    return JSONResponse(content={
        "ok": True, "id": sid,
        "status": "approved" if (is_admin and scope == "public") else
                  ("private" if scope == "private" else "pending")})


@router.get("/api/agent/skills")
async def agent_skill_list(request: Request):
    """技能列表：管理员看全部；普通用户看「已发布 + 我的」；非所有者/非管理员不返回代码。
    P3 发现筛选：?category=分类 &keyword=关键词 &sort=popular（按调用次数热门排序）。"""
    u = require_perm("agent", request)
    is_admin = (u.get("role") == "admin")
    qp = request.query_params
    kw = (qp.get("keyword") or "").strip()
    cat = (qp.get("category") or "").strip()
    sort = (qp.get("sort") or "").strip()
    if is_admin:
        skills = list_skills(include_all=True, for_user_id=u["id"], with_code=True, category=cat or None,
                             keyword=kw or None, sort=(sort or None))
    else:
        skills = list_skills(for_user_id=u["id"], with_code=True, category=cat or None,
                             keyword=kw or None, sort=(sort or None))
    for s in skills:
        if s.get("owner_id") != u["id"] and not is_admin:
            s.pop("code_text", None)
    return JSONResponse(content={"skills": skills, "is_admin": is_admin})


@router.get("/api/agent/skills/{skill_id}")
async def agent_skill_detail(skill_id: int, request: Request):
    u = require_perm("agent", request)
    is_admin = (u.get("role") == "admin")
    s = get_skill(skill_id, with_code=True)
    if not s:
        return JSONResponse(status_code=404, content={"error": "技能不存在"})
    if s.get("owner_id") != u["id"] and not is_admin:
        s.pop("code_text", None)
    # 补 installed 字段：与 list 端点完全一致——本人私有技能天然 True，公开技能看 skill_installs
    if s.get("status") == "private" and s.get("owner_id") == u["id"]:
        s["installed"] = True
    else:
        _ic = get_conn()
        _hit = _ic.execute("SELECT 1 FROM skill_installs WHERE user_id=? AND skill_id=?",
                           (u["id"], skill_id)).fetchone()
        _ic.close()
        s["installed"] = bool(_hit)
    versions = list_skill_versions(skill_id)
    return JSONResponse(content={"skill": s, "versions": versions})


@router.put("/api/agent/skills/{skill_id}")
async def agent_skill_update(skill_id: int, request: Request, payload: dict):
    """更新技能（升版本）：先静态扫描代码，再覆盖字段并快照历史。"""
    u = require_perm("agent", request)
    is_admin = (u.get("role") == "admin")
    s = get_skill(skill_id, with_code=False)
    if not s:
        return JSONResponse(status_code=404, content={"error": "技能不存在"})
    if not is_admin:
        return JSONResponse(status_code=403, content={"error": "仅系统管理员可编辑技能（owner 不可编辑）"})
    code = payload.get("code_text")
    if code is not None:
        ok, reason = sandbox.scan_code(code)
        if not ok:
            return JSONResponse(status_code=400,
                                content={"error": "代码未通过安全扫描：" + reason})
    try:
        nv = update_skill(skill_id, payload, u["id"], is_admin=is_admin)
    except ValueError as e:
        return JSONResponse(status_code=400, content={"error": str(e)})
    add_log("skill_update", target=str(skill_id), detail="v%d" % nv, user=u, ip=client_ip(request))
    return JSONResponse(content={"ok": True, "version": nv,
                                 "status": "pending" if (s.get("status") == "approved") else s.get("status")})


@router.get("/api/agent/skills/{skill_id}/versions")
async def agent_skill_versions(skill_id: int, request: Request):
    """版本历史列表。"""
    u = require_perm("agent", request)
    is_admin = (u.get("role") == "admin")
    s = get_skill(skill_id, with_code=False)
    if not s:
        return JSONResponse(status_code=404, content={"error": "技能不存在"})
    if s.get("owner_id") != u["id"] and not is_admin:
        return JSONResponse(status_code=403, content={"error": "无权查看该技能版本"})
    return JSONResponse(content={"versions": list_skill_versions(skill_id)})


@router.post("/api/agent/skills/{skill_id}/rollback")
async def agent_skill_rollback(skill_id: int, request: Request, payload: dict):
    """回滚到指定历史版本（owner 或 admin）。"""
    u = require_perm("agent", request)
    is_admin = (u.get("role") == "admin")
    s = get_skill(skill_id, with_code=False)
    if not s:
        return JSONResponse(status_code=404, content={"error": "技能不存在"})
    if s.get("owner_id") != u["id"] and not is_admin:
        return JSONResponse(status_code=403, content={"error": "无权回滚该技能"})
    try:
        version = int(payload.get("version"))
    except (TypeError, ValueError):
        return JSONResponse(status_code=400, content={"error": "version 须为整数"})
    try:
        nv = rollback_skill(skill_id, version, u["id"], is_admin=is_admin)
    except ValueError as e:
        return JSONResponse(status_code=400, content={"error": str(e)})
    add_log("skill_rollback", target=str(skill_id), detail="to v%d" % version, user=u, ip=client_ip(request))
    return JSONResponse(content={"ok": True, "version": nv})


@router.post("/api/agent/skills/{skill_id}/clone")
async def agent_skill_clone(skill_id: int, request: Request):
    """把已发布的公开技能收藏/复制为本人私有技能（立即可用，免审核）。"""
    u = require_perm("agent", request)
    try:
        new_id = clone_skill(skill_id, u["id"])
    except ValueError as e:
        return JSONResponse(status_code=400, content={"error": str(e)})
    add_log("skill_clone", target=str(skill_id), detail="->%d" % new_id, user=u, ip=client_ip(request))
    return JSONResponse(content={"ok": True, "id": new_id})


@router.post("/api/agent/skills/{skill_id}/install")
async def agent_skill_install(skill_id: int, request: Request):
    """普通用户安装公开技能（轻量开关，装后可被本人智能体调用）。"""
    u = require_perm("agent", request)
    try:
        install_skill(u["id"], skill_id)
    except ValueError as e:
        return JSONResponse(status_code=400, content={"error": str(e)})
    add_log("skill_install", target=str(skill_id), user=u, ip=client_ip(request))
    return JSONResponse(content={"ok": True, "installed": True})


@router.delete("/api/agent/skills/{skill_id}/install")
async def agent_skill_uninstall(skill_id: int, request: Request):
    """卸载公开技能（取消安装，之后本人智能体不再可调用）。"""
    u = require_perm("agent", request)
    uninstall_skill(u["id"], skill_id)
    add_log("skill_uninstall", target=str(skill_id), user=u, ip=client_ip(request))
    return JSONResponse(content={"ok": True, "installed": False})


@router.delete("/api/agent/skills/{skill_id}")
async def agent_skill_delete(skill_id: int, request: Request):
    u = require_perm("agent", request)
    is_admin = (u.get("role") == "admin")
    ok = delete_skill(skill_id, for_user_id=u["id"], is_admin=is_admin)
    if not ok:
        return JSONResponse(status_code=403, content={"error": "无权删除该技能（仅本人或管理员可删）"})
    add_log("skill_delete", target=str(skill_id), user=u, ip=client_ip(request))
    return JSONResponse(content={"ok": True})


@router.post("/api/agent/skills/{skill_id}/review")
async def agent_skill_review(skill_id: int, request: Request, payload: dict):
    """管理员审核：approve 通过并启用；reject 驳回并停用。"""
    u = require_perm("agent", request)
    if u.get("role") != "admin":
        return JSONResponse(status_code=403, content={"error": "仅管理员可审核"})
    try:
        review_skill(skill_id, u["id"], payload.get("action"), payload.get("note", ""))
    except ValueError as e:
        return JSONResponse(status_code=400, content={"error": str(e)})
    add_log("skill_review", target=str(skill_id), detail=payload.get("action"), user=u, ip=client_ip(request))
    return JSONResponse(content={"ok": True})


@router.post("/api/agent/skills/{skill_id}/toggle")
async def agent_skill_toggle(skill_id: int, request: Request, payload: dict):
    u = require_perm("agent", request)
    is_admin = (u.get("role") == "admin")
    s = get_skill(skill_id, with_code=False)
    if not s:
        return JSONResponse(status_code=404, content={"error": "技能不存在"})
    if s.get("owner_id") != u["id"] and not is_admin:
        return JSONResponse(status_code=403, content={"error": "无权操作该技能"})
    toggle_skill(skill_id, bool(payload.get("enabled")))
    return JSONResponse(content={"ok": True})


@router.post("/api/agent/skills/{skill_id}/visibility")
async def agent_skill_visibility(skill_id: int, request: Request, payload: dict):
    """设置技能可见性：公开（所有人可用）/ 私有（仅我可用）。仅本人或管理员可操作。"""
    u = require_perm("agent", request)
    is_admin = (u.get("role") == "admin")
    s = get_skill(skill_id, with_code=False)
    if not s:
        return JSONResponse(status_code=404, content={"error": "技能不存在"})
    if s.get("owner_id") != u["id"] and not is_admin:
        return JSONResponse(status_code=403, content={"error": "无权修改该技能可见性"})
    vis = (payload.get("visibility") or "").strip()
    try:
        set_skill_visibility(skill_id, vis, user_id=u["id"], is_admin=is_admin)
    except ValueError as e:
        return JSONResponse(status_code=400, content={"error": str(e)})
    add_log("skill_visibility", target=str(skill_id), detail=vis, user=u, ip=client_ip(request))
    return JSONResponse(content={"ok": True, "visibility": vis,
                                 "scope": "public" if vis == "public" else "private",
                                 "status": "approved" if vis == "public" else "private"})


@router.post("/api/agent/skills/{skill_id}/run")
async def agent_skill_run(skill_id: int, request: Request, payload: dict):
    """手动在沙箱中测试运行某技能（仅本人或管理员，用于验证代码）。"""
    u = require_perm("agent", request)
    is_admin = (u.get("role") == "admin")
    s = get_skill(skill_id, with_code=True)
    if not s:
        return JSONResponse(status_code=404, content={"error": "技能不存在"})
    if s.get("owner_id") != u["id"] and not is_admin:
        return JSONResponse(status_code=403, content={"error": "仅本人或管理员可测试运行"})
    args = payload.get("args") or {}
    if not isinstance(args, dict):
        args = {}
    result = _run_skill_sandboxed(s["code_text"], args, s["name"])
    return JSONResponse(content={"ok": True, "result": result})


@router.get("/api/config/active")
async def config_active(request: Request):
    require_login(request)  # 仅在已登录时暴露当前模型名/地址（不含密钥）
    out = {}
    for role in ["chat", "vision", "embed", "rerank"]:
        a = get_active(role)
        out[role] = ({"name": a.get("name"), "model_name": a.get("model_name"),
                      "base_url": a.get("base_url"), "configured": True} if a
                     else {"configured": False})
    return out


# ────────────────────────────────────────────────────────────────────────────
# P2 · MCP 配置管理（仅系统管理员）
# 列表 / 新增或更新 / 删除 / 触发同步。配置落盘于 /app/data/mcp.json（持久化）。
# ────────────────────────────────────────────────────────────────────────────
def _require_admin(request: Request):
    """校验系统管理员，非管理员抛 403。返回当前用户字典。"""
    u = require_perm("agent", request)
    if u.get("role") != "admin":
        raise HTTPException(status_code=403, detail="仅系统管理员可访问该管理接口")
    return u


# ────────────────────────────────────────────────────────────────────────────
# 邮件服务器（SMTP）配置管理（仅系统管理员）
# 读取 / 保存 / 连接测试。密码对外返回掩码，保存时掩码回写不覆盖现有密码。
# ────────────────────────────────────────────────────────────────────────────
@router.get("/api/admin/permissions")
async def admin_permissions(request: Request):
    """返回当前系统所有可用的功能权限位（含 label/group/nav_id），按 group 排序。
    单一权威源：新增功能只要在 db.FEATURE_REGISTRY 加一行，这里会自动出现。"""
    try:
        _require_admin(request)
        from db import PERM_GROUP_LABELS
        return JSONResponse(content={
            "ok": True,
            "features": get_available_features(),
            "groups": [{"key": g, "label": PERM_GROUP_LABELS.get(g, g)}
                       for g in ("biz", "ai", "memory", "admin")],
        })
    except HTTPException:
        raise
    except Exception as e:
        return JSONResponse(status_code=500, content={"ok": False, "error": str(e)})


# ────────────────────────────────────────────────────────────────────────────
# 导航设置（系统管理员）：功能排列顺序 + 显示/隐藏。全局生效，对所有用户可见性生效。
# ────────────────────────────────────────────────────────────────────────────
@router.get("/api/admin/nav")
async def admin_nav_get(request: Request):
    """读取全部功能的导航配置（顺序 + 显隐）。仅拥有 m_nav 权限的管理员。"""
    try:
        require_perm("m_nav", request)
        from db import PERM_GROUP_LABELS
        return JSONResponse(content={
            "ok": True,
            "settings": get_nav_settings(),
            "groups": [{"key": g, "label": PERM_GROUP_LABELS.get(g, g)}
                       for g in ("biz", "ai", "memory", "admin")],
        })
    except HTTPException:
        raise
    except Exception as e:
        return JSONResponse(status_code=500, content={"ok": False, "error": str(e)})


@router.put("/api/admin/nav")
async def admin_nav_put(payload: dict, request: Request):
    """保存导航配置。payload = {settings: [{key, order, visible}, ...]}。仅管理员。"""
    try:
        u = require_perm("m_nav", request)
        items = (payload or {}).get("settings") or []
        save_nav_settings(items)
        add_log("nav_update", detail=f"{len(items)} 项", user=u, ip=client_ip(request))
        return JSONResponse(content={"ok": True, "settings": get_nav_settings()})
    except HTTPException:
        raise
    except Exception as e:
        return JSONResponse(status_code=500, content={"ok": False, "error": str(e)})


@router.get("/api/nav/config")
async def nav_config(request: Request):
    """导航渲染配置（任意登录用户）：返回全部功能的顺序 + 显隐，前端按此排序/隐藏。"""
    try:
        require_login(request)
        return JSONResponse(content={"ok": True, "features": get_nav_settings()})
    except HTTPException:
        raise
    except Exception as e:
        return JSONResponse(status_code=500, content={"ok": False, "error": str(e)})


# ────────────────────────────────────────────────────────────────────────────
# 系统基础设置（m_system）：系统名称/副标题/图标/背景/Favicon，全局生效。
# 资产文件存 /app/data/system_assets/，由 /api/system/asset/<filename> 提供给前端。
# ────────────────────────────────────────────────────────────────────────────
import os, uuid as _uuid
_SYSTEM_ASSETS_DIR = os.environ.get("SYSTEM_ASSETS_DIR") or (
    "/app/data/system_assets" if os.path.isdir("/app/data") else os.path.join(tempfile.gettempdir(), "system_assets"))
os.makedirs(_SYSTEM_ASSETS_DIR, exist_ok=True)

_ALLOWED_UPLOAD_TYPES = {
    "icon":    (".png", ".jpg", ".jpeg", ".svg", ".gif", ".webp"),    # 侧栏/登录页 logo
    "favicon": (".ico", ".png", ".svg"),                              # 浏览器标签图标
    "bg":      (".jpg", ".jpeg", ".png", ".webp"),                   # 登录页背景图
}
_ASSET_TO_KEY = {"icon": "icon_url", "favicon": "favicon_url", "bg": "login_bg_url"}


@router.get("/api/admin/system-settings")
async def admin_system_settings_get(request: Request):
    """读取系统基础设置。仅拥有 m_system 权限的管理员。"""
    try:
        require_perm("m_system", request)
        return JSONResponse(content={"ok": True, "settings": get_system_settings()})
    except HTTPException:
        raise
    except Exception as e:
        return JSONResponse(status_code=500, content={"ok": False, "error": str(e)})


@router.put("/api/admin/system-settings")
async def admin_system_settings_put(payload: dict, request: Request):
    """保存系统基础设置。payload = {settings: {key: value}}。仅允许白名单 key。"""
    try:
        u = require_perm("m_system", request)
        items = (payload or {}).get("settings") or {}
        save_system_settings(items)
        add_log("system_settings_update", detail=",".join(items.keys()), user=u, ip=client_ip(request))
        return JSONResponse(content={"ok": True, "settings": get_system_settings()})
    except HTTPException:
        raise
    except Exception as e:
        return JSONResponse(status_code=500, content={"ok": False, "error": str(e)})


@router.post("/api/admin/system-settings/upload")
async def admin_system_settings_upload(
    request: Request,
    type: str = Form(...),
    file: UploadFile = File(...),
):
    """上传系统资源（图标/Favicon/背景）。自动校验类型与大小，返回访问 URL 并写库。"""
    try:
        u = require_perm("m_system", request)
        _type = (type or "").strip().lower()
        if _type not in _ALLOWED_UPLOAD_TYPES:
            return JSONResponse(status_code=400, content={"ok": False, "error": f"不支持的资源类型：{_type}"})
        # 校验扩展名
        _fname = file.filename or "upload"
        _ext = os.path.splitext(_fname)[1].lower()
        if _ext not in _ALLOWED_UPLOAD_TYPES[_type]:
            return JSONResponse(status_code=400, content={"ok": False, "error":
                f"该类型仅支持 {', '.join(_ALLOWED_UPLOAD_TYPES[_type])}，收到 {_ext}"})
        # 读并限大小 5MB
        _data = await file.read()
        if len(_data) > 5 * 1024 * 1024:
            return JSONResponse(status_code=400, content={"ok": False, "error": "文件过大（>5MB）"})
        # 命名：<type>_<uuid>.<ext>，避免多版本冲突；旧版本会在后续清理（保留最近 5 个）
        _new = f"{_type}_{_uuid.uuid4().hex[:8]}{_ext}"
        _path = os.path.join(_SYSTEM_ASSETS_DIR, _new)
        with open(_path, "wb") as f:
            f.write(_data)
        _url = f"/api/system/asset/{_new}"
        # 同步写库
        save_system_settings({_ASSET_TO_KEY[_type]: _url})
        # 旧版本清理：保留最近 5 个同名 type 前缀
        try:
            _siblings = sorted(
                [fn for fn in os.listdir(_SYSTEM_ASSETS_DIR) if fn.startswith(_type + "_")],
                key=lambda fn: os.path.getmtime(os.path.join(_SYSTEM_ASSETS_DIR, fn)),
                reverse=True)
            for _old in _siblings[5:]:
                try: os.remove(os.path.join(_SYSTEM_ASSETS_DIR, _old))
                except Exception: pass
        except Exception: pass
        add_log("system_asset_upload", detail=f"{_type} -> {_new}", user=u, ip=client_ip(request))
        return JSONResponse(content={"ok": True, "url": _url, "key": _ASSET_TO_KEY[_type],
                                     "settings": get_system_settings()})
    except HTTPException:
        raise
    except Exception as e:
        return JSONResponse(status_code=500, content={"ok": False, "error": str(e)})


@router.delete("/api/admin/system-settings/asset")
async def admin_system_settings_asset_del(payload: dict, request: Request):
    """删除指定资源（恢复默认）。payload = {key: 'icon_url'|'favicon_url'|'login_bg_url'}"""
    try:
        u = require_perm("m_system", request)
        key = (payload or {}).get("key") or ""
        if key not in _ASSET_TO_KEY.values():
            return JSONResponse(status_code=400, content={"ok": False, "error": "非法的 key"})
        save_system_settings({key: ""})
        add_log("system_asset_clear", detail=key, user=u, ip=client_ip(request))
        return JSONResponse(content={"ok": True, "settings": get_system_settings()})
    except HTTPException:
        raise
    except Exception as e:
        return JSONResponse(status_code=500, content={"ok": False, "error": str(e)})


@router.get("/api/system/public-config")
async def system_public_config(request: Request):
    """公开系统配置（登录页用）：无需登录即可读，用于登录前渲染。"""
    try:
        return JSONResponse(content={"ok": True, "settings": get_system_settings()})
    except Exception as e:
        return JSONResponse(status_code=500, content={"ok": False, "error": str(e)})


@router.get("/api/system/asset/{filename}")
async def system_asset(filename: str):
    """提供系统资源文件（图标/Favicon/背景）。注意：路径仅取 basename 防越权。"""
    # 安全：禁止 / 和 ..，且强制只允许已知扩展名
    if "/" in filename or ".." in filename or "\\" in filename:
        raise HTTPException(status_code=400, detail="非法文件名")
    _path = os.path.join(_SYSTEM_ASSETS_DIR, filename)
    if not os.path.isfile(_path):
        raise HTTPException(status_code=404, detail="文件不存在")
    _ext = os.path.splitext(filename)[1].lower()
    _ct = {".png":"image/png", ".jpg":"image/jpeg", ".jpeg":"image/jpeg",
           ".gif":"image/gif", ".webp":"image/webp", ".svg":"image/svg+xml",
           ".ico":"image/x-icon"}.get(_ext, "application/octet-stream")
    return FileResponse(_path, media_type=_ct, headers={"Cache-Control": "public, max-age=3600"})


@router.get("/api/admin/smtp")
async def smtp_get(request: Request):
    """读取当前 SMTP 配置（密码已掩码）。仅管理员。"""
    try:
        _require_admin(request)
        cfg = get_smtp_config(mask_password=True)
        return JSONResponse(content={"ok": True, "config": cfg,
                                      "configured": bool(cfg.get("host"))})
    except HTTPException:
        raise
    except Exception as e:
        return JSONResponse(status_code=500, content={"ok": False, "error": str(e)})


@router.post("/api/admin/smtp")
async def smtp_save(request: Request, payload: dict = None):
    """保存 SMTP 配置。body: {host,port,username,password,sender,use_tls,use_ssl,timeout,enabled}。仅管理员。"""
    try:
        u = _require_admin(request)
        p = payload or {}
        # 密码为掩码时视为未改动；其余字段按传入保存
        merged = save_smtp_config(p, by=(u.get("username") or "admin"))
        # 返回掩码后的配置
        merged["password"] = MASK if merged.get("password") else ""
        return JSONResponse(content={"ok": True, "config": merged})
    except HTTPException:
        raise
    except Exception as e:
        return JSONResponse(status_code=400, content={"ok": False, "error": str(e)})


@router.post("/api/admin/smtp/test")
async def smtp_test(request: Request, payload: dict = None):
    """连接测试（可选带测试收件人，发出一封测试邮件）。仅管理员。"""
    try:
        _require_admin(request)
        p = payload or {}
        from mailer import test_smtp, send_email
        # 若前端传了完整配置则先暂存测试（不落库），否则用已保存配置
        test_cfg = None
        if p.get("host"):
            # 用传入配置做测试，不覆盖已保存密码（掩码时取库内）
            existing = get_smtp_config(mask_password=False) or {}
            test_cfg = dict(existing)
            for k in ("host", "port", "username", "sender", "use_tls", "use_ssl", "timeout", "enabled"):
                if k in p and p[k] is not None:
                    test_cfg[k] = p[k]
            if p.get("password") and p.get("password") != MASK:
                test_cfg["password"] = p["password"]
        res = test_smtp(test_cfg)
        # 若指定了测试收件人，在连通后发一封测试邮件
        to = (p.get("test_to") or "").strip()
        if res.get("ok") and to:
            send_res = send_email(
                to, "SMTP 连接测试邮件",
                "这是一封来自企业 AI 办公助手的 SMTP 连接测试邮件，收到即代表配置可用。",
                config=test_cfg)
            if not send_res.get("ok"):
                res = {"ok": True,
                       "message": "SMTP 连接成功，但测试邮件发送失败",
                       "detail": send_res.get("detail", "")}
            else:
                res = {"ok": True,
                       "message": "SMTP 连接成功，测试邮件已发送至 " + to,
                       "detail": res.get("detail", "")}
        return JSONResponse(content=res)
    except HTTPException:
        raise
    except Exception as e:
        return JSONResponse(status_code=500, content={"ok": False, "error": str(e)})


@router.get("/api/agent/mcp")
async def mcp_list(request: Request):
    """列出所有 MCP 配置 server 及其已同步工具数。仅管理员。"""
    try:
        _require_admin(request)
        servers = mcp_client.get_mcp_servers_with_status()
        logger.info("[MCP LIST] 返回 %d 个服务: %s", len(servers), [s["name"] for s in servers])
        return JSONResponse(content={"ok": True, "servers": servers,
                                      "config_path": mcp_client._default_cfg_path()})
    except HTTPException:
        raise
    except Exception as e:
        return JSONResponse(status_code=500, content={"ok": False, "error": str(e)})


@router.post("/api/agent/mcp")
async def mcp_save(request: Request, payload: dict = None):
    """新增或更新一个 MCP server。payload: name, transport, url/command, args, env, disabled, sync。仅管理员。"""
    try:
        _require_admin(request)
        p = payload or {}
        name = (p.get("name") or "").strip()
        if not name:
            return JSONResponse(status_code=400, content={"ok": False, "error": "name 不能为空"})
        if not re.match(r"^[A-Za-z0-9_.\-]+$", name):
            return JSONResponse(status_code=400,
                                content={"ok": False, "error": "name 仅允许字母数字 _ . -"})
        # 读现有配置 → 合并（保留未显式传入的字段，如仅切换 disabled 时不丢 url/command）
        servers = {}
        p_read = mcp_client._read_cfg_path()
        if os.path.exists(p_read):
            try:
                with open(p_read, "r", encoding="utf-8") as f:
                    data = json.load(f)
                raw = data.get("mcpServers") if "mcpServers" in data else data
                if isinstance(raw, dict):
                    servers = dict(raw)
            except Exception:
                pass
        existing = servers.get(name)
        transport = (p.get("transport") or (existing or {}).get("transport") or "streamable-http").lower()
        # 归一化别名：旧 http/sse 与多种连字符写法统一为 streamable-http
        if transport in ("http", "sse", "streamablehttp", "streamable_http", "streamable-http"):
            transport = "streamable-http"
        if transport not in ("streamable-http", "stdio"):
            return JSONResponse(status_code=400,
                                content={"ok": False, "error": "transport 仅支持 streamable-http / stdio"})
        cfg = dict(existing) if existing else {"transport": transport}
        cfg["transport"] = transport
        if transport == "streamable-http":
            # URL 与 headers 为 HTTP 类型字段
            if p.get("url") is not None:
                cfg["url"] = (p.get("url") or "").strip()
            if p.get("headers") is not None:
                cfg["headers"] = p.get("headers") or {}
            elif "headers" not in cfg and existing:
                cfg["headers"] = (existing or {}).get("headers", {})
            # 清理 stdio 残留字段
            cfg.pop("command", None)
            cfg.pop("args", None)
            cfg.pop("env", None)
        else:  # stdio
            if p.get("command") is not None:
                cfg["command"] = (p.get("command") or "").strip()
                cfg["args"] = p.get("args") if p.get("args") is not None else (existing or {}).get("args", [])
                cfg["env"] = p.get("env") if p.get("env") is not None else (existing or {}).get("env", {})
            # 清理 HTTP 残留字段
            cfg.pop("url", None)
            cfg.pop("headers", None)
        if not existing:
            # 新建且未提供必要地址字段：按类型报错
            if transport == "streamable-http" and not cfg.get("url"):
                return JSONResponse(status_code=400, content={"ok": False, "error": "streamable-http 类型必须填写 url"})
            if transport == "stdio" and not cfg.get("command"):
                return JSONResponse(status_code=400, content={"ok": False, "error": "stdio 类型必须填写 command"})
        cfg["disabled"] = bool(p.get("disabled"))
        servers[name] = cfg
        logger.info("[MCP SAVE] 写入服务 name=%s transport=%s url=%s 已有=%s 总数=%d",
                    name, transport, cfg.get("url",""), existing is not None, len(servers))
        mcp_client.save_mcp_config(servers)

        synced = 0
        if p.get("sync") is not False:  # 默认同步
            try:
                synced = mcp_client.sync_mcp_tools()
            except Exception as e:
                logger.warning("MCP 保存后同步失败: %s", e)
        return JSONResponse(content={"ok": True, "synced": synced})
    except HTTPException:
        raise
    except Exception as e:
        return JSONResponse(status_code=500, content={"ok": False, "error": str(e)})


@router.delete("/api/agent/mcp/{name}")
async def mcp_delete(name: str, request: Request):
    """删除一个 MCP server 配置。仅管理员。"""
    try:
        _require_admin(request)
        p_read = mcp_client._read_cfg_path()
        servers = {}
        if os.path.exists(p_read):
            try:
                with open(p_read, "r", encoding="utf-8") as f:
                    data = json.load(f)
                raw = data.get("mcpServers") if "mcpServers" in data else data
                if isinstance(raw, dict):
                    servers = dict(raw)
            except Exception:
                pass
        if name not in servers:
            return JSONResponse(status_code=404, content={"ok": False, "error": "配置不存在"})
        del servers[name]
        mcp_client.save_mcp_config(servers)
        # 同步一次，清掉已失效工具
        try:
            mcp_client.sync_mcp_tools()
        except Exception as e:
            logger.warning("MCP 删除后同步失败: %s", e)
        return JSONResponse(content={"ok": True})
    except HTTPException:
        raise
    except Exception as e:
        return JSONResponse(status_code=500, content={"ok": False, "error": str(e)})


@router.post("/api/agent/mcp/sync")
async def mcp_sync_now(request: Request):
    """手动触发 MCP 工具同步（重新发现并 upsert 进 tools 表）。仅管理员。"""
    try:
        _require_admin(request)
        n = mcp_client.sync_mcp_tools()
        return JSONResponse(content={"ok": True, "synced": n})
    except HTTPException:
        raise
    except Exception as e:
        return JSONResponse(status_code=500, content={"ok": False, "error": str(e)})

