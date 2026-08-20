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

from core import ToolContext
from mcp_client import _call_mcp_tool_sync
from tools_handlers import (HANDLERS, _h_create_skill, _h_create_tool,
                           _h_list_skills, _h_list_tools,
                           _h_save_memory, _h_forget_memory,
                           _h_run_temp_code, _h_ask_user,
                           _h_task_create, _h_task_get, _h_task_update, _h_task_list,
                           _h_spawn_subagent, _h_conversation_search)
import sandbox

def build_session_tools(ctx, defs):
    """把 DB 读出的工具定义（含 handler 名）绑定到 ctx，构造 run_agent 需要的工具列表。

    只有 handler 可解析（在 HANDLERS 中）的工具才会进入会话；无 handler 的
    （如未来 api/codegen 类工具）暂不参与执行，交由对应后端类型处理。
    """
    tools = []
    for d in defs:
        # P1④ MCP 工具：经 mcp_client 转发到外部 MCP 服务（复用 tools 表，backend_type='mcp'）
        if d.get("backend_type") == "mcp":
            _server = d.get("target")
            _mcp_tool = d["name"]
            try:
                _ct = json.loads(d.get("code_text") or "{}")
                _mcp_tool = _ct.get("mcp_tool") or d["name"]
            except Exception:
                pass
            if not _server:
                continue
            _meta = {"server": _server, "mcp_tool": _mcp_tool}

            def _make_mcp_wrap(meta):
                async def _wrap(**a):
                    return await asyncio.to_thread(_call_mcp_tool_sync, meta, a)
                return _wrap

            tools.append({
                "name": d["name"],
                "description": d["description"],
                "parameters": d.get("params") or {"type": "object", "properties": {}},
                "handler": _make_mcp_wrap(_meta),
                "kind": "tool",
            })
            continue
        # 用户私有工具（is_user_created=1）：走沙箱执行，与 build_session_skill_tools 同逻辑
        if d.get("is_user_created"):
            code = d.get("code_text") or ""
            if not code:
                continue
            uname = d["name"]
            def _make_user_wrap(code, uname):
                async def _wrap(**a):
                    return await asyncio.to_thread(_run_skill_sandboxed, code, a, uname)
                return _wrap
            tools.append({
                "name": uname,
                "description": d["description"],
                "parameters": d.get("params") or {"type": "object", "properties": {}},
                "handler": _make_user_wrap(code, uname),
                "kind": "tool",
            })
            continue
        # 内置/admin 全局工具：绑定 HANDLERS handler
        h = HANDLERS.get(d.get("handler")) if d.get("handler") else None
        if not h:
            continue

        def _make_wrap(h, ctx):
            async def _wrap(**a):
                r = h(ctx, **a)
                if asyncio.iscoroutine(r):
                    r = await r
                return r
            return _wrap

        tools.append({
            "name": d["name"],
            "description": d["description"],
            "parameters": d.get("params") or {"type": "object", "properties": {}},
            "handler": _make_wrap(h, ctx),
            "kind": "tool",  # 全局工具（区别于技能/元工具），用于前端按类型显示
        })
    return tools


def _run_skill_sandboxed(code_text, args, skill_name=""):
    """在沙箱中执行用户上传技能代码，返回给智能体工具用的字符串结果。"""
    res = sandbox.run_code(code_text, args)
    if res.get("ok"):
        result = res.get("result") or ""
        arts = res.get("artifacts") or []
        if arts:
            # 让 _detect_generated_file 能识别产物路径，前端据此推下载卡片
            result = (result + "\n" + "\n".join("已生成：" + p for p in arts)).strip()
        return result
    msg = res.get("error") or "技能执行失败"
    if res.get("timed_out"):
        msg = "技能执行超时（已在沙箱终止）"
    extra = res.get("stdout")
    if extra:
        msg += "\n--- 技能输出 ---\n" + extra[:1500]
    # 回传 stderr 和 traceback——这是诊断根因的关键信息
    err = res.get("stderr")
    if err:
        msg += "\n--- 错误详情 ---\n" + err[:1500]
    tb = res.get("traceback")
    if tb:
        msg += "\n--- 调用栈 ---\n" + tb[:1500]
    return "技能执行失败：" + msg


def build_session_skill_tools(ctx, defs):
    """把可用技能定义绑定为沙箱工具，注入智能体会话（代码在沙箱中执行）。"""
    tools = []
    for d in defs:
        code = d.get("code_text") or ""
        name = d["name"]
        def _make_wrap(code, name):
            async def _wrap(**a):
                return await asyncio.to_thread(_run_skill_sandboxed, code, a, name)
            return _wrap
        tools.append({
            "name": name,
            "description": d["description"],
            "parameters": d.get("params") or {"type": "object", "properties": {}},
            "handler": _make_wrap(code, name),
            "kind": "skill",  # 代码类技能（区别于全局工具/元工具）
        })
    return tools


def build_subagent_tools(ctx, allowed_names=None):
    """构造子 Agent 可用的工具集：仅业务工具/技能（按 allowed_names 过滤），不含任何元工具。

    子 Agent 禁止创建技能/工具、禁止向用户提问、禁止再派生子 Agent——因此不注入任何元工具，
    只给它业务执行能力（内置工具 / 用户私有工具 / 代码类技能）。allowed_names 为 None 时放开全部。
    """
    uid = ctx.user["id"] if ctx.user else None
    tools_lib = list_tools(for_user_id=uid) if uid else []
    skills_lib = list_skills(for_user_id=uid, usable_only=True, with_code=True) if uid else []
    names = set(allowed_names) if allowed_names else None
    def _keep(d):
        return names is None or d["name"] in names
    tool_defs = [d for d in tools_lib if _keep(d)]
    skill_defs = [d for d in skills_lib if _keep(d)]
    code_defs = [d for d in skill_defs if d.get("skill_type") != "method"]
    return build_session_tools(ctx, tool_defs) + build_session_skill_tools(ctx, code_defs)


def build_meta_tools(ctx):
    """始终注入的智能体「元工具」（如：动态创建技能/工具、查询可用能力）。不参与检索，保证随时可用。"""
    async def _wrap_create(**a):
        return await _h_create_skill(ctx, **a)
    async def _wrap_create_tool(**a):
        return await _h_create_tool(ctx, **a)
    async def _wrap_list_skills(**a):
        return await _h_list_skills(ctx, **a)
    async def _wrap_list_tools(**a):
        return await _h_list_tools(ctx, **a)
    async def _wrap_save_memory(**a):
        return await _h_save_memory(ctx, **a)
    async def _wrap_forget_memory(**a):
        return await _h_forget_memory(ctx, **a)
    async def _wrap_run_temp_code(**a):
        return await _h_run_temp_code(ctx, **a)
    async def _wrap_ask_user(**a):
        return await _h_ask_user(ctx, **a)
    async def _wrap_task_create(**a):
        return await _h_task_create(ctx, **a)
    async def _wrap_task_get(**a):
        return await _h_task_get(ctx, **a)
    async def _wrap_task_update(**a):
        return await _h_task_update(ctx, **a)
    async def _wrap_task_list(**a):
        return await _h_task_list(ctx, **a)
    async def _wrap_spawn_subagent(**a):
        return await _h_spawn_subagent(ctx, **a)
    async def _wrap_conversation_search(**a):
        return await _h_conversation_search(ctx, **a)
    return [
        {
            "name": "create_skill",
        "description": (
            "当用户要求「帮我创建一个技能（用来指导某种做法/思考方式）」时调用本工具。"
            "本工具只创建「方法论技能」（skill_type=method）：直接写出中文【提示词/流程 instructions】"
            "（写清思考方式、做法、体例，例如「先核对主体资格与授权，再逐条比对付款/交付/违约条款」），不编写任何 Python 代码、不定义 def run。"
            "若用户要求「写一段可运行的 Python 代码实现某功能」，请改用 create_tool 工具（创建私有工具，沙箱执行）。"
            "调用本工具需给出：name（英文标识符）、display_name（中文展示名）、description（中文描述）、instructions（中文提示词/流程）。"
            "若该技能有清晰的适用情形，请务必用一句话填写 when_to_use（如「用户要求审查合同、给出风险清单时」），"
            "它描述「用户说什么/处于什么场景时该用此技能」，供路由 LLM 做语义匹配（与触发词互补，提升命中准确率）；无明确场景可留空。"
            "display_name 不要留空、不要用英文；若该技能绑定特定业务场景，请一并填写 rules（业务规则/约束，中文一句话）与 allowed_tools（"
            "本场景允许使用的工具名清单，如 [\"calculator\",\"summarize_text\"]）；若无需绑定则留空。"
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "name": {"type": "string", "description": "技能英文标识符，如 contract_review_guide"},
                "display_name": {"type": "string", "description": "技能的中文展示名（必填）：将作为卡片上的中文名，配合英文 name 显示为「中文（英文）」，如「文本字数统计」「合同审查指引」；不要用英文，不要留空"},
                "description": {"type": "string", "description": "中文描述，说明技能做什么、适用场景，用于智能体检索匹配"},
                "skill_type": {"type": "string", "enum": ["method"],
                               "description": "技能类型：method=方法论技能（仅提示词/流程，注入思考约束，不执行代码，默认且唯一支持类型）。若需创建可执行 Python 代码工具，请改用 create_tool 元工具。",
                               "default": "method"},
                "instructions": {"type": "string",
                                 "description": "方法论技能（skill_type=method）的提示词/流程正文：请直接写出中文思考方式、做法、体例（如「先核对主体资格与授权，再逐条比对付款/交付/违约条款」）。这是技能的核心价值，强烈建议亲笔撰写；若未提供，系统会按描述自动生成兜底版本（质量较低，建议后续在技能广场细化）"},
                "code": {"type": "string", "description": "Python 代码，仅 skill_type=code 时必填：必须定义 def run(a): ... 并返回结果", "default": ""},
                "trigger_words": {"type": "string", "description": "可选，逗号分隔的触发词，如 合同审查,审查要点", "default": ""},
                "when_to_use": {"type": "string", "description": "可选，一句话描述「用户说什么/处于什么场景时该用此技能」，如「用户要求审查合同、给出风险清单时」；供路由 LLM 语义匹配，与触发词互补", "default": ""},
                "category": {"type": "string", "description": "可选分类", "default": "general"},
                "rules": {"type": "string", "description": "可选，业务规则/场景约束（中文），如「仅统计中文，忽略标点与英文」", "default": ""},
                "allowed_tools": {"type": "array", "items": {"type": "string"},
                                 "description": "可选，本技能绑定场景下允许使用的工具名清单，如 [\"calculator\"]", "default": []},
            },
            "required": ["name", "description", "display_name", "skill_type"],
        },
        "handler": _wrap_create,
            "kind": "meta",
        },
        {
            "name": "list_available_skills",
            "description": (
                "查看当前用户已安装并可用的技能列表。"
                "当用户询问「有哪些技能」「系统中有什么能力」「能帮我做什么」"
                "或你需要了解当前可用技能时调用此工具。"
                "返回每个技能的中英文名称、类型（方法论/代码工具）、描述和触发词。"
                "建议在创建新技能前先调用此工具，避免重复创建已有技能。"
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "keyword": {"type": "string", "description": "可选关键词，用于过滤技能（如填'合同'则只返回含'合同'的技能）", "default": ""},
                },
                "required": [],
            },
            "handler": _wrap_list_skills,
            "kind": "meta",
        },
        {
            "name": "list_available_tools",
            "description": (
                "查看系统「全局工具库」中已注册的所有工具。"
                "当用户询问「系统支持哪些工具」「全局工具库里有什么」「我能用什么工具」"
                "或你需要了解系统已注册的能力时调用此工具。"
                "返回每个工具的中英文名称、分类、描述和触发词。"
                "区别于 list_available_skills（用户态技能）：本工具列出的是管理员在"
                "「全局工具库」注册的内置能力（按需自动注入），无需用户安装。"
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "keyword": {"type": "string", "description": "可选关键词，用于过滤工具（如填'计算'则只返回含'计算'的工具）", "default": ""},
                },
                "required": [],
            },
            "handler": _wrap_list_tools,
            "kind": "meta",
        },
        {
            "name": "create_tool",
            "description": (
                "当用户要求「创建一个工具（写一段可运行的 Python 代码实现某功能）」时调用本工具。"
                "本工具创建的是「私有工具」：Python 代码在沙箱中执行（定义 def run(a): ... 并返回结果），"
                "保存为私有工具（仅创建者本人可见可用），存于「全局工具库」中作为工具卡片。"
                "与 create_skill 区分：create_skill 创建方法论技能（提示词/流程，注入思考约束，不执行代码）；"
                "create_tool 创建可执行工具（Python 代码，沙箱执行）。"
                "调用时需提供：name（英文标识符）、display_name（中文展示名）、description（用途说明）、"
                "code（Python 代码，必须定义 def run(a): ... 且仅做纯数据处理，禁止 os/sys/subprocess/socket/requests/eval/exec）。"
                "可选：trigger_words（触发词，逗号分隔，未提供时系统按描述自动派生）、category（分类，默认 general）。"
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "name": {"type": "string", "description": "工具英文标识符，如 word_counter"},
                    "display_name": {"type": "string", "description": "工具的中文展示名（必填），如「字数统计」"},
                    "description": {"type": "string", "description": "中文描述，说明工具做什么、何时用，用于智能体检索匹配"},
                    "code": {"type": "string", "description": "Python 代码，必须定义 def run(a): ... 并返回结果。仅做纯数据处理，禁止文件/网络/系统调用"},
                    "trigger_words": {"type": "string", "description": "可选，逗号分隔的触发词，如 字数,统计,计数", "default": ""},
                    "category": {"type": "string", "description": "可选分类", "default": "general"},
                },
                "required": ["name", "display_name", "description", "code"],
            },
            "handler": _wrap_create_tool,
            "kind": "meta",
        },
        {
            "name": "save_memory",
            "description": (
                "把「值得长期记住的用户信息」写入该用户的跨会话长期记忆库；"
                "存储的充要条件是「本工具被调用」（用户显式要求，或你推断该记），下次新对话每轮会自动注入 prompt（对标 WorkBuddy 云记忆）。"
                "注意：不要因为「某次对话里提到了」就自动调用——只有符合下方【该存】才调用，符合【不该存】坚决不调用。\n\n"
                "【该存】满足以下任一即可：\n"
                "1. 用户显式要求长期保留（「记住这个」「以后都这样」「记一下」）；\n"
                "2. 稳定偏好 / 习惯（「我习惯用 Excel」「输出用中文」「红色表示上涨」）；\n"
                "3. 长期事实（职业、公司、项目背景、技术栈、常用工具）；\n"
                "4. 用户设定的约定 / 规则（「以后代码都用 TypeScript」「报告统一用 Markdown」）。\n\n"
                "【不该存】以下一律不要调用本工具：\n"
                "1. 一次性对话细节、当前任务的临时上下文（如「本次要处理的是 test.xlsx」）；\n"
                "2. 敏感凭证 / 密码 / 密钥 / Token（绝不记录）；\n"
                "3. 琐碎、会过期的临时信息（临时文件名、某次任务的中间产物、一次性计算结果）。\n\n"
                "参数：content（记忆正文，中文，必填）；mem_type（preference 偏好 / project 项目 / convention 约定 / note 其他，默认 note）；"
                "mem_key（可选去重键，同名键会覆盖更新，便于修正既有记忆）。"
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "content": {"type": "string", "description": "记忆正文（中文，必填），如「用户偏好用红色表示股票上涨、绿色表示下跌」"},
                    "mem_type": {"type": "string", "enum": ["preference", "project", "convention", "note"],
                                 "description": "记忆类型：preference=用户偏好 / project=项目背景 / convention=双方约定 / note=其他", "default": "note"},
                    "mem_key": {"type": "string", "description": "可选去重键；填了同名键再次保存会覆盖更新，用于「修正/细化既有记忆」", "default": ""},
                },
                "required": ["content"],
            },
            "handler": _wrap_save_memory,
            "kind": "meta",
        },
        {
            "name": "forget_memory",
            "description": (
                "删除该用户 long-term 记忆库中的某条记录（对标「忘了XX」「不要记了」）。"
                "删除后该条既不再注入 prompt、也不在「我的记忆」面板显示——读写删全链路归一，都来自同一张 user_memory 表。"
                "调用时机：用户明确要求删除，或你判断某记忆已过时 / 有误 / 当初就不该存（如误存了临时信息）。"
                "可通过 mem_key（去重键）或 mem_id（记忆 id）定位；二选一即可。"
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "mem_key": {"type": "string", "description": "可选，按去重键删除（与保存时填的 mem_key 对应）", "default": ""},
                    "mem_id": {"type": "integer", "description": "可选，按记忆 id 删除（可在「我的记忆」面板查看 id）", "default": None},
                },
                "required": [],
            },
            "handler": _wrap_forget_memory,
            "kind": "meta",
        },
        {
            "name": "run_temp_code",
            "description": (
                "在沙箱中执行一段**临时代码**来完成任务，但**不保存为工具**（用于用户拒绝把代码持久化为工具时的兜底）。"
                "\n\n【能力范围】"
                "\n✅ 纯本地数据处理：文本/数值/表格转换、计算、格式化、正则匹配、JSON/CSV 处理等。"
                "\n✅ 对已生成图片做后处理：可直接读取 generate_image / generate_video 产出的文件"
                "（路径会通过 args['artifacts'] 传入，也会出现在工具返回文本「已生成：/app/data/artifacts/xxx.png」中），"
                "用 PIL 进行裁剪/缩放/旋转/加文字水印/拼图/格式转换等；处理后把结果写到沙箱目录（如 out.png），"
                "系统会自动收集并返回「已生成：<路径>」，生成可下载文件。"
                "\n\n【能力边界（做不到的事）】"
                "\n1. 文生图/文生视频（从文本凭空生成全新图像/视频）：沙箱无 GPU、无外部 API，无法凭空生成；这类任务请用 generate_image / generate_video。"
                "\n2. 网络请求/下载/调用外部 API：沙箱无网络访问。"
                "\n\n调用方式：代码需定义 def run(args) 并返回结果；args 含 text/content/input（任务文本），"
                "以及 artifacts（本轮已生成文件的绝对路径列表，可能为空）。"
                "\n示例（给刚生成的图片加红色文字水印）："
                "\n```python\nfrom PIL import Image, ImageDraw\nimg = Image.open(args['artifacts'][0])\n"
                "ImageDraw.Draw(img).text((10, 10), '样图', fill='red')\nimg.save('out.png')\nreturn '已加水印'\n```"
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "code": {"type": "string", "description": "Python 代码，必须定义 def run(args): ... 并返回结果。可做本地数据处理，或读取 args['artifacts'] 里的已生成图片用 PIL 做后处理（裁剪/加字/拼图/格式转换），把结果写回沙箱目录即可被自动收集下载。禁止网络/外部API。"},
                    "input_text": {"type": "string", "description": "可选，要交给代码处理的内容/文本（会传入 run(args) 的 args['text']/args['content']/args['input']）", "default": ""},
                },
                "required": ["code"],
            },
            "handler": _wrap_run_temp_code,
            "kind": "meta",
        },
        {
            "name": "ask_user",
            "description": (
                "向用户提出一个澄清问题（AskUserQuestion 确认原语）。"
                "当你在执行任务过程中需要用户确认关键参数/选项、而不应凭空假设时调用本工具："
                "传入 question（问题）与可选 options（候选选项列表）。"
                "系统会弹出对话框等待用户选择/输入，用户回复后任务自动继续。"
                "仅用于「必须和用户确认才能继续」的场景（如缺失的业务参数、互斥的方案选择）；"
                "普通闲聊或无需确认的信息不要用本工具。"
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "question": {"type": "string", "description": "要问用户的问题（中文，必填）"},
                    "options": {"type": "array", "items": {"type": "string"},
                                "description": "可选，候选选项列表（用户可从中选择，亦可自由输入）", "default": []},
                },
                "required": ["question"],
            },
            "handler": _wrap_ask_user,
            "kind": "meta",
        },
        {
            "name": "task_create",
            "description": (
                "创建一条任务（用于把长程、多步骤的复杂任务拆分成可追踪的子任务）。"
                "返回任务 id 与状态。建议：接到大任务时先 task_create 拆出若干子任务，"
                "执行过程中用 task_update 把状态从 pending 改为 in_progress / completed，"
                "用 task_list 随时查看整体进度。任务会关联到当前对话，前端可展示进度面板。"
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "title": {"type": "string", "description": "任务标题（必填，简洁动宾结构，如「抓取竞品官网数据」）"},
                    "description": {"type": "string", "description": "可选，任务详细描述/交付标准", "default": ""},
                    "active_form": {"type": "string", "description": "可选，进行中时前端展示的现在分词短语，如「正在抓取竞品数据」", "default": ""},
                    "parent_id": {"type": "integer", "description": "可选，父任务 id（用于子任务归属）", "default": None},
                    "add_blocks": {"type": "array", "items": {"type": "integer"}, "description": "可选，本任务完成后才解锁的任务 id 列表", "default": []},
                    "add_blocked_by": {"type": "array", "items": {"type": "integer"}, "description": "可选，阻塞本任务的前置任务 id 列表", "default": []},
                },
                "required": ["title"],
            },
            "handler": _wrap_task_create,
            "kind": "meta",
        },
        {
            "name": "task_get",
            "description": "读取单条任务的详情（标题/描述/状态/依赖等）。传入 task_id。",
            "parameters": {
                "type": "object",
                "properties": {
                    "task_id": {"type": "integer", "description": "任务 id"},
                },
                "required": ["task_id"],
            },
            "handler": _wrap_task_get,
            "kind": "meta",
        },
        {
            "name": "task_update",
            "description": (
                "更新任务的状态/标题/描述/依赖。常用：把 status 改为 in_progress（开始做）、"
                "completed（完成）。传入 task_id 与要改的字段。"
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "task_id": {"type": "integer", "description": "任务 id"},
                    "status": {"type": "string", "enum": ["pending", "in_progress", "completed", "deleted"],
                               "description": "任务状态：pending=待办 / in_progress=进行中 / completed=已完成 / deleted=已删除", "default": None},
                    "title": {"type": "string", "description": "可选，新标题", "default": None},
                    "description": {"type": "string", "description": "可选，新描述", "default": None},
                    "active_form": {"type": "string", "description": "可选，进行中展示短语", "default": None},
                    "add_blocks": {"type": "array", "items": {"type": "integer"}, "description": "可选，更新阻塞下游任务列表", "default": None},
                    "add_blocked_by": {"type": "array", "items": {"type": "integer"}, "description": "可选，更新前置依赖列表", "default": None},
                },
                "required": ["task_id"],
            },
            "handler": _wrap_task_update,
            "kind": "meta",
        },
        {
            "name": "task_list",
            "description": "列出当前用户（可指定 session_id 仅看本次对话）的全部任务与状态，用于追踪长程任务进度。",
            "parameters": {
                "type": "object",
                "properties": {
                    "session_id": {"type": "string", "description": "可选，仅返回该连续对话的任务；不传则返回该用户全部任务", "default": None},
                },
                "required": [],
            },
            "handler": _wrap_task_list,
            "kind": "meta",
        },
        {
            "name": "spawn_subagent",
            "description": (
                "委派一个子 Agent 去独立完成某个子任务（常用于把大任务拆给专人执行、"
                "或需要隔离上下文的子任务）。传入 task（子任务描述）与可选 context（背景信息）、"
                "allowed_tools（限定子 Agent 可使用的业务工具名列表，不传则放开全部业务工具）。"
                "子 Agent 会在独立会话中自行调用工具完成任务，把最终结论返回给你，由你整合进总任务。"
                "约束：子 Agent 不能创建技能/工具、不能向用户提问、不能再派生子 Agent；"
                "它只使用你指定的业务工具。适合『明确、可独立交付』的子任务。"
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "task": {"type": "string", "description": "子任务描述（必填，清晰、可独立交付），如「抓取 A 公司官网的产品列表并整理成表格」"},
                    "context": {"type": "string", "description": "可选，传给子 Agent 的背景/共享信息（如前面已收集的数据、约束说明）", "default": ""},
                    "allowed_tools": {"type": "array", "items": {"type": "string"}, "description": "可选，限定子 Agent 可使用的业务工具名列表，如 [\"web_search\",\"summarize_text\"]；不传则放开全部业务工具", "default": []},
                },
                "required": ["task"],
            },
            "handler": _wrap_spawn_subagent,
            "kind": "meta",
        },
        {
            "name": "conversation_search",
            "description": (
                "检索当前用户自己的历史对话（跨会话、按对话窗口聚合），用于「用户提到以前聊过的事」「引用之前的结论/约定」"
                "「回顾某次任务的结果」等场景。按关键词（可多词空格分隔，AND 匹配）检索，返回命中的对话窗口列表"
                "（含窗口标题、命中条数、最新命中预览、时间），以「会话级」方式定位过往对话。仅检索本人历史，不跨用户。"
                "当你需要回忆过去对话属于哪个窗口、或引用之前结论时调用。"
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {"type": "string", "description": "检索关键词，可多词空格分隔，如「销售 图表 上周」"},
                    "limit": {"type": "integer", "description": "最多返回条数，默认 10", "default": 10},
                },
                "required": ["query"],
            },
            "handler": _wrap_conversation_search,
            "kind": "meta",
        },
    ]


def _build_skill_constraints(skill_defs):
    """汇总被命中技能的业务规则与工具白名单（场景四）。

    返回 (约束文本, 允许工具名集合)。
    - 约束文本：拼进 system 提示，约束 LLM 的行为与参数合规。
    - 允许工具名集合：非空时用于收窄 function-calling 的工具面（仅清单内工具可见）。
    Skill 本身不执行调用，仅加载规则 + 绑定工具清单，执行权仍在 LLM/ReAct。
    """
    parts = []
    allowed = set()
    for s in skill_defs or []:
        rules = (s.get("rules") or "").strip()
        at = s.get("allowed_tools") or []
        if isinstance(at, str):
            try:
                at = json.loads(at)
            except Exception:
                at = []
        if not isinstance(at, list):
            at = []
        name = s.get("display_name") or s.get("name") or "技能"
        if rules:
            parts.append(f"- 场景「{name}」业务规则：{rules}")
        if at:
            allowed.update(at)
            parts.append(f"  该场景允许使用的工具清单：{', '.join(at)}；"
                         f"请仅从清单内选择工具，并依据上述规则填充合规参数。")
    text = ""
    if parts:
        text = ("【业务场景约束】\n" + "\n".join(parts) +
                "\n请严格遵循以上业务规则与工具清单，不要调用清单之外的工具。")
    return text, allowed


def _build_method_prompt(skill_defs):
    """汇总被命中的方法论技能（skill_type=method）的提示词/流程，注入 system prompt。

    返回约束文本（可能为空）。方法论技能只约束 LLM 的「思考/做法」，不绑定工具、不执行代码。
    """
    parts = []
    for s in skill_defs or []:
        if (s.get("skill_type") or "code") != "method":
            continue
        ins = (s.get("instructions") or "").strip()
        if not ins:
            continue
        name = s.get("display_name") or s.get("name") or "技能"
        parts.append(f"- 方法论技能「{name}」专家流程：\n{ins}")
    if not parts:
        return ""
    return ("【方法论技能 / 专家流程】（以下为应采纳的思考方式与做法，用于约束本次回答，不触发额外工具调用）\n"
            + "\n".join(parts))


_METHOD_PROPOSE_RE = re.compile(r"\[PROPOSE_METHOD\]\s*(.*?)\s*\[/PROPOSE_METHOD\]", re.S)


_METHOD_SUGGEST_RE = re.compile(r"\[SUGGEST_SAVE_SKILL\]\s*(.*?)\s*\[/SUGGEST_SAVE_SKILL\]", re.S)


_TOOL_PROPOSE_RE = re.compile(r"\[PROPOSE_TOOL\]\s*(.*?)\s*\[/PROPOSE_TOOL\]", re.S)


def _parse_method_markers(text):
    """缺技能/缺工具兜底：从 LLM 的 final 回复里抽取标记。

    返回 (清理后文本, propose_framework or None, (display_name, name) or None, propose_tool or None)。
    标记从原文剔除，前端拿到的是干净的回复正文；专用 SSE 事件单独下发，触发弹窗。
    """
    text = text or ""
    propose = None
    suggest = None
    propose_tool = None
    m = _METHOD_PROPOSE_RE.search(text)
    if m:
        propose = m.group(1).strip()
        text = (text[:m.start()] + text[m.end():]).rstrip()
    m2 = _METHOD_SUGGEST_RE.search(text)
    if m2:
        body = m2.group(1).strip()
        # 约定格式「中文展示名 | 英文标识符」
        if "|" in body:
            dn, _, nm = body.partition("|")
            suggest = (dn.strip(), nm.strip())
        else:
            # 兜底：整段当 display_name，name 用拼音/英文名留空让 LLM 后续补
            suggest = (body, "")
        text = (text[:m2.start()] + text[m2.end():]).rstrip()
    m3 = _TOOL_PROPOSE_RE.search(text)
    if m3:
        propose_tool = m3.group(1).strip()
        text = (text[:m3.start()] + text[m3.end():]).rstrip()
    return text, propose, suggest, propose_tool


def _narrow_tools_by_whitelist(tool_defs, tools_lib, allowed_union):
    """按技能声明的工具白名单收窄外部工具面（场景四）。

    仅保留白名单内的工具；白名单中未被检索命中的工具也补回，保证技能声明的工具面可用。
    Skill 工具与元工具不在收窄范围内（由调用方另行并入）。
    """
    have = {d["name"] for d in tool_defs}
    extra = [t for t in tools_lib if t["name"] in allowed_union and t["name"] not in have]
    return [d for d in tool_defs if d["name"] in allowed_union] + extra


def _has_lexical_hit(task, library):
    """场景二(B)：判断任务是否词面命中任一工具/技能。

    无任何命中（纯语言闲聊、文案润色等）时，调用方可直接短路，跳过
    resolve_session_tools 的路由 LLM 调用以省一次模型开销。

    匹配词库**仅含 trigger_words + name**（不含 description）：description 是一句
    自然语言描述，纳入关键词匹配会让闲聊里出现的描述性词汇（如"总结""分析"）误触发
    「强制注入 method 技能 / 提示安装技能」，扭曲模型行为（"理解错"的直接根因）。
    语义级匹配仍由路由 LLM 路径承担（catalog 已含 description + when_to_use 双路）。
    """
    task_low = (task or "").lower()
    for t in library:
        trigs = (t.get("trigger_words") or "").replace(",", " ").lower().split()
        hay = trigs + t["name"].lower().split()
        for tok in hay:
            tok = tok.strip()
            if len(tok) >= 2 and tok in task_low:
                return True
    return False


def _apply_skip_skill(selected, tool_names):
    """场景二(A)：命中带 skip_skill 标记的全局工具时，真正跳过 Skill 匹配。

    返回仅保留「工具 + 元工具」的列表（剔除所有技能）。skip_skill 工具即
    「全局免 Skill 快通道」：一旦被选中，就不再注入任何技能，直接走工具调用。
    """
    if not any(bool(t.get("skip_skill")) for t in selected if t["name"] in tool_names):
        return selected
    return [d for d in selected if d["name"] in tool_names or d.get("kind") == "meta"]

