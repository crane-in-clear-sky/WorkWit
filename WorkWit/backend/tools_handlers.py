import io, json, re, traceback, asyncio, logging, time, os, subprocess, sys, zipfile, shutil, tempfile, uuid, base64
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
    save_user_memory, get_user_memory, delete_user_memory,
    create_task, get_task, update_task, list_tasks,
    search_conversations, search_conversations_grouped,
)
from agent import run_agent, resolve_session_tools
import sandbox

from core import ToolContext, _llm_call, SYSTEM_PROMPT, RESUME_PROMPT, parse_json
from db import (save_skill, save_tool, list_skills, list_tools, _SKILL_NAME_RE)
import sandbox

_TRIG_STOP_CN = set(
    "的 了 和 与 及 或 在 是 为 对 把 被 给 让 使 由 从 到 向 以 于 这 那 个 些 "
    "我 你 他 她 它 我们 你们 他们 一种 一个 一项 进行 实现 功能 可以 能够 "
    "用于 用来 输入 输出 返回 结果 一段 处理 分析 生成 创建 基于 根据 通过 "
    "自动 智能 助手 工具 技能 用户 系统 程序".split()
)


_TRIG_LEAD_CN = set("把被从到向以于在由给让使将对为与及或和这那该此")


_TRIG_TAIL_CN = set("了的了个些是我你他她它们吗呢吧啊哟")


def _derive_trigger_words(desc):
    """从技能描述派生触发词（纯标准库，无 jieba 依赖）。

    抽取 ASCII 词 + 中文 2/3-gram，过滤纯停用词与首尾无义词碎片，最多 5 个，逗号分隔。
    仅在 create_skill 未显式提供 trigger_words 时作为保底使用。
    """
    if not desc:
        return ""
    out, seen = [], set()
    def _keep(t):
        if len(t) < 2 or t in seen or t in _TRIG_STOP_CN:
            return False
        if len(t) <= 3 and (t[0] in _TRIG_LEAD_CN or t[-1] in _TRIG_TAIL_CN):
            return False
        return True
    def _add(t):
        t = (t or "").strip().lower()
        if _keep(t):
            seen.add(t); out.append(t)
    for m in re.findall(r"[A-Za-z0-9_]+", desc):
        _add(m)
    for seg in re.findall(r"[\u4e00-\u9fff]+", desc):
        if len(seg) <= 4:
            _add(seg)
        for n in (2, 3):
            for i in range(len(seg) - n + 1):
                _add(seg[i:i + n])
    return ",".join(out[:5])


def _derive_instructions(name, display_name, description):
    """从名称/展示名/描述派生方法论技能的提示词/流程兜底文本。

    仅在对话自动创建（create_skill）时、模型未显式写出 instructions 才启用，
    保证一次创建成功，避免 ReAct 因缺参反复重试同一失败调用。
    模型主动提供 instructions 时不会被覆盖（优先级更高）。
    """
    title = (display_name or name or "该任务").strip()
    base = (description or title).strip()
    return (
        f"当你需要完成「{title}」类任务时，请按以下方式思考与执行：\n"
        f"1. 先明确用户的需求与最终交付标准（输出格式、范围、深度）。\n"
        f"2. 基于目标拆解为清晰步骤，分步推进，必要时调用可用工具。\n"
        f"3. 复用既有规范与最佳实践，保证结果与「{base}」的目标一致。\n"
        f"4. 完成后核对质量（准确性、完整性、格式），再向用户呈现结论。"
    )


def _derive_code_template(name, display_name, description):
    """从名称/描述派生代码工具技能的 Python 代码模板兜底。

    仅在对话自动创建（create_skill）时、模型未显式写出 code 才启用，
    保证一次创建成功，避免 ReAct 因缺参反复重试同一失败调用。
    生成的模板包含 def run(a) 骨架与文档注释，模型后续可在技能广场编辑完善。
    """
    title = (display_name or name or "该任务").strip()
    base = (description or title).strip()
    # 用 raw string 避免转义问题；模板保证沙箱 scan_code 能通过（纯数据处理）
    return (
        f'def run(a):\n'
        f'    """{title}——{base}"""\n'
        f'    # 待完善：根据「{base}」的具体需求实现处理逻辑\n'
        f'    input_text = a.get("input", a.get("text", ""))\n'
        f'    result = input_text  # TODO: 替换为实际处理逻辑\n'
        f'    return {{"ok": True, "output": result}}\n'
    )


# ─────────────────────────────────────────────────────────────────────────────
# 技能去重辅助（状态级）：基于「已有技能库」判定重复，而非对话关键词匹配。
# 这样无论路由层是否命中技能、无论多轮对话如何变化，create_skill 都不会产生
# competitive_analysis_report_v2 这类冗余技能；同时不再需要在路由层禁用 create_skill，
# 多技能/多轮任务可正常新建真正需要的技能。
# ─────────────────────────────────────────────────────────────────────────────
_SKILL_SUFFIX_RE = re.compile(
    r'[\s_\-]*(v\d+(\.\d+)*|\d+|fixed|fix|improved|improve|upgrade|upgraded|'
    r'enhanced|new|copy|final|plus|pro|高级|优化|升级|改进|重写|替换|新版|增强|定制|副本)$',
    re.IGNORECASE)


def _norm_skill_key(name):
    """去掉版本/变体后缀，归一化技能名用于重复比对。
    例：competitive_analysis_report_v2 / _fixed / _2 / _优化 → competitive_analysis_report"""
    n = (name or "").strip().lower()
    while True:
        m = _SKILL_SUFFIX_RE.search(n)
        if not m:
            break
        n = n[:m.start()].strip('_-')
        if not n:
            break
    return n or (name or "").lower()


def _tw_overlap_ratio(a, b):
    """触发词集合 Jaccard 重叠度（用于相似技能判定）。"""
    if not a or not b:
        return 0.0
    sa = {w.strip().lower() for w in re.split(r'[，,、\s]+', a) if w.strip()}
    sb = {w.strip().lower() for w in re.split(r'[，,、\s]+', b) if w.strip()}
    if not sa or not sb:
        return 0.0
    inter = sa & sb
    return len(inter) / min(len(sa), len(sb))


def _find_similar_skill(name, trigger_words, for_user_id, when_to_use=""):
    """在用户可见的全部技能（广场公开 + 本人私有）中查找与待创建技能相似的已有技能。
    命中条件（满足其一即视为重复）：① 归一化名称相同；② 触发词重叠度 ≥ 0.6；
    ③ 适用情形(when_to_use)语义重叠度 ≥ 0.6。
    返回匹配到的技能 dict，无则 None。这是「状态级」去重，不依赖对话关键词匹配。"""
    cand_key = _norm_skill_key(name)
    try:
        all_sk = list_skills(for_user_id=for_user_id) or []
    except Exception:
        all_sk = []
    hit = None
    for s in all_sk:
        sname = s.get("name") or ""
        if cand_key and _norm_skill_key(sname) == cand_key:
            hit = s
            break
        stw = s.get("trigger_words") or ""
        if _tw_overlap_ratio(trigger_words, stw) >= 0.6:
            if hit is None:
                hit = s
        swu = s.get("when_to_use") or ""
        if when_to_use and _tw_overlap_ratio(when_to_use, swu) >= 0.6:
            if hit is None:
                hit = s
    return hit


async def _h_create_skill(ctx, name, description, code="", trigger_words="", category="general",
                          rules="", allowed_tools=None, skill_type="method", instructions="",
                          display_name="", when_to_use=""):
    """智能体在会话中动态创建技能，两层技能：

    - skill_type='method'（默认）：方法论技能，仅提示词/流程，注入 system prompt 约束思考，不执行代码；
    - skill_type='code'：代码工具技能，Python 代码经沙箱安全校验后入库，运行时在沙箱执行。

    场景四支持：rules（业务规则文本）+ allowed_tools（工具清单），由模型在创建时一并产出，
    Skill 仅加载这些约束，不执行调用；执行权仍在 LLM/ReAct。
    """
    if not ctx.user:
        return "创建技能失败：缺少用户上下文"
    name = (name or "").strip()
    description = (description or "").strip()
    skill_type = (skill_type or "method").strip().lower()
    if skill_type not in ("method", "code"):
        skill_type = "method"
    code = (code or "").strip()
    instructions = (instructions or "").strip()
    _instr_auto = False
    display_name = (display_name or "").strip()
    # 触发词兜底：模型未提供时，从描述自动派生，保证技能可被关键词路由命中
    trigger_words = (trigger_words or "").strip() or _derive_trigger_words(description)
    if not name or not description:
        return "创建技能失败：name、description 均必填"
    if not _SKILL_NAME_RE.match(name):
        return "创建技能失败：name 须为合法标识符（字母/数字/下划线，如 my_skill）"
    # 单次会话创建数量上限（语义创建弹窗会传 1，避免一次请求生成多张卡片）
    if getattr(ctx, "max_create_skills", None) and ctx._skill_create_count >= ctx.max_create_skills:
        last = ctx.created_skills[-1] if ctx.created_skills else None
        if last:
            return (f"已为你创建技能（id={last['id']}，名称 {last['name']}），本次只需创建一个，"
                    f"请直接总结，无需再创建新技能。")
        return "本次已无需再创建技能，请直接总结结果。"
    if skill_type == "method":
        # 方法论技能：不执行代码，无需安全扫描
        if not instructions:
            # 兜底：对话自动创建时若模型漏填 instructions（如 GLM 系列偶发），
            # 用描述自动派生提示词/流程，保证一次创建成功、避免 ReAct 反复重试失败调用。
            instructions = _derive_instructions(name, display_name or name, description)
            _instr_auto = True
    else:
        # 代码工具技能：要求代码并通过沙箱安全扫描
        if not code:
            # 兜底：对话自动创建时若模型漏填 code（如 GLM 系列偶发），
            # 用描述自动派生 Python 代码模板，保证一次创建成功、避免 ReAct 反复重试失败调用。
            code = _derive_code_template(name, display_name or name, description)
            _instr_auto = True  # 复用此标记在返回消息中提示
        ok, reason = sandbox.scan_code(code)
        if not ok:
            return ("创建技能失败：代码未通过安全扫描（" + reason +
                    "）。请改用纯 Python 数据处理逻辑，不要使用 os/sys/subprocess/socket/requests/shutil "
                    "等模块，也不要做文件/网络/系统调用，禁止使用 eval/exec。")
    # 同一次会话内同名技能幂等：已创建过则直接复用，避免重复卡片
    for s in getattr(ctx, "created_skills", []):
        if s.get("name") == name:
            return (f"技能 {name} 已创建成功（id={s['id']}，私有、立即可用），"
                    f"后续相关任务会自动检索并注入该技能执行，无需重复创建。")
    # 工具清单归一化为列表
    at = allowed_tools or []
    if isinstance(at, str):
        at = [x.strip() for x in at.split(",") if x.strip()]
    if not isinstance(at, list):
        at = []
    # ── 状态级去重：检测待创建技能是否已存在（精确名 / 归一化名 / 触发词重叠）──
    # 这是「基于已有技能库」的稳健去重，不依赖对话关键词匹配，
    # 因此即使路由层未能命中技能，也不会重复创建 competitive_analysis_report_v2 这类冗余技能。
    existing_skill = _find_similar_skill(name, trigger_words, ctx.user["id"], when_to_use)
    if existing_skill:
        sid = existing_skill["id"]
        s_name = existing_skill.get("name") or name
        s_display = existing_skill.get("display_name") or s_name
        # 检查当前用户是否已安装该技能
        conn = get_conn()
        installed = conn.execute(
            "SELECT 1 FROM skill_installs WHERE user_id=? AND skill_id=?",
            (ctx.user["id"], sid)
        ).fetchone()
        conn.close()
        if not installed:
            # 技能存在但未安装 → 自动安装 + 返回提示
            try:
                install_skill(ctx.user["id"], sid)
                return (f"✅ 已为你自动安装技能广场中的「{s_display}」技能（id={sid}），"
                        f"现已立即可用。后续相关任务会自动检索并注入该技能执行，请直接使用，无需新建。")
            except Exception as inst_err:
                # 安装失败（如非公开技能不允许安装）→ 给明确提示
                return (f"⚠️ 技能「{s_display}」（name={s_name}）已存在于系统中但无法自动安装"
                        f"（{inst_err}）。请直接使用该已有技能，或换一个明显不同的名称创建新技能。")
        else:
            # 已安装 → 直接复用
            return (f"技能「{s_display}」（name={s_name}）已存在且已安装（id={sid}），"
                    f"无需重复创建。后续相关任务会自动检索并注入该技能执行，请直接使用。")

    try:
        sid = save_skill({
            "name": name, "display_name": display_name or name, "description": description,
            "category": category or "general", "code_text": code,
            "trigger_words": trigger_words, "scope": "private", "status": "private",
            "rules": (rules or "").strip(), "allowed_tools": at,
            "skill_type": skill_type, "instructions": instructions,
            "when_to_use": (when_to_use or "").strip(),
            "create_source": "agent_auto",
        }, ctx.user["id"])
    except Exception as e:
        return "创建技能失败：" + str(e)
    # 沙箱试跑：仅代码类技能需要验证可运行（生成代码应保证空参也能返回合理结果）
    if skill_type == "code":
        tests = [{}, {"input": "测试文本", "text": "测试文本", "a": 1, "b": 2}]
        ran = [sandbox.run_code(code, t) for t in tests]
        ok_run = any(r.get("ok") for r in ran)
        note = "" if ok_run else "（提示：试跑未通过，调用时可能需要传入正确的参数；建议 run(a) 用 a.get('key', 默认值) 取参）"
    else:
        note = ""
    if not hasattr(ctx, "created_skills"):
        ctx.created_skills = []
    ctx.created_skills.append({"name": name, "id": sid})
    ctx._skill_create_count += 1
    if _instr_auto:
        if skill_type == "code":
            note_instr = "（代码模板由系统按描述自动生成，建议后续在技能广场补充完善实现逻辑）"
        else:
            note_instr = "（提示词/流程由系统按描述自动生成，建议后续在技能广场补充细化）"
    else:
        note_instr = ""
    return (f"✅ 技能已创建成功（id={sid}，名称 {display_name or name}（{name}），类型 {'方法论' if skill_type=='method' else '代码工具'}，已设为私有、仅你可见）。"
            f"后续相关任务会自动检索并注入该技能执行。{note}{note_instr}")


async def _h_list_skills(ctx, keyword=""):
    """返回当前用户已安装/可用的技能列表，供模型发现已有能力。"""
    u = ctx.user
    kw = (keyword or "").strip()
    skills = list_skills(for_user_id=u["id"], usable_only=True, with_code=False,
                        keyword=kw if kw else None)
    if not skills:
        return ("当前你还没有安装任何技能。"
                "系统中有一些公开技能可供安装使用。"
                "如果需要创建新技能，请使用 create_skill 工具。")
    lines = []
    for s in skills:
        dn = s.get("display_name") or s.get("name") or "?"
        nm = s.get("name") or "?"
        desc = s.get("description") or ""
        st = s.get("skill_type", "code")
        st_label = "方法论" if st == "method" else "代码工具"
        tw = s.get("trigger_words") or ""
        line = f"- {dn}（{nm}）| 类型：{st_label}"
        if desc:
            line += f" | 描述：{desc}"
        if tw:
            line += f" | 触发词：{tw}"
        lines.append(line)
    return ("以下是你当前可用（已安装）的技能列表：\n\n"
            + "\n".join(lines)
            + "\n\n如需使用其中某个技能，直接按其描述说明执行即可（方法论类技能的提示词已自动注入你的思考流程）。"
              "如需新能力，请用 create_skill 创建。")


async def _h_list_tools(ctx, keyword=""):
    """返回当前已启用的「全局工具库」工具清单，供模型发现系统已注册的能力。

    区别于 list_available_skills：技能（list_available_skills）= 用户态、按需安装使用；
    全局工具（list_available_tools）= 管理员在「全局工具库」中注册的内置能力，
    由路由 LLM 按任务关键词命中后自动注入，无需用户安装。
    """
    kw = (keyword or "").strip()
    tools = list_tools(include_disabled=False, for_user_id=ctx.user["id"])
    if kw:
        klow = kw.lower()
        tools = [t for t in tools
                 if klow in (t.get("name") or "").lower()
                 or klow in (t.get("display_name") or "")
                 or klow in (t.get("description") or "")
                 or klow in (t.get("trigger_words") or "")]
    if not tools:
        return ("当前「全局工具库」中没有匹配的工具。"
                "（管理员可在「全局工具库」页面注册新工具或调整启停状态。）"
                if kw else
                "当前「全局工具库」为空。所有可用能力只能通过技能提供。")
    lines = []
    for t in tools:
        dn = t.get("display_name") or t.get("name") or "?"
        nm = t.get("name") or "?"
        desc = t.get("description") or ""
        cat = t.get("category") or ""
        tw = t.get("trigger_words") or ""
        line = f"- {dn}（{nm}）" + (f" | 分类：{cat}" if cat else "")
        if desc:
            line += f" | 描述：{desc}"
        if tw:
            line += f" | 触发词：{tw}"
        lines.append(line)
    return (f"当前系统「全局工具库」中已注册的工具（共 {len(tools)} 项）：\n\n"
            + "\n".join(lines)
            + "\n\n这些工具由系统自动按需注入，无需用户主动安装。"
              "如某个工具未出现在当前任务的可调用列表中，说明本次任务未触发其触发词——"
              "可换更明确的表述，或在「全局工具库」中调整该工具的触发词。")


async def _h_run_temp_code(ctx, code, input_text="", artifacts=None):
    """在沙箱中执行一段临时代码完成任务，但不保存为工具（用户拒绝持久化时的兜底）。

    与 create_tool 的区别：本工具执行后不写库、不生成工具卡片，仅返回本次执行结果/产物路径。
    代码需定义 def run(args): ...（args 收到 {'text':..., 'content':..., 'input':..., 'artifacts':[...]}）。
    artifacts 为本轮对话已生成的文件绝对路径列表（如 generate_image 生成的图片），可在代码里直接 open 读取做后处理。

    2026-08-19 修订：本工具的产出**会被自动收集并展示给用户**；同一任务反复调用会产生多份文件
    （hash 不同），污染用户结果。务必遵守：
    1. 如果 `args.get('artifacts')` 非空 且 当前任务与其中任一文件相关（修改/补充/覆盖/发送），
       **必须**用 `open(p).read()` 读取已有内容 → 修改/复用 → 写回**同一路径**（覆盖）。
       不要在新沙箱会话里重新生成同名/同主题文件。
    2. 只有完全独立的新任务（如本次输入与已有文件毫无关系）才创建新文件。
    3. 写文件请用 `open(path, 'w')` 或 `shutil.copy()` 覆盖；最终通过 return 字符串中的
       `已生成：/abs/path` 让系统识别产物（同 generate_pptx 约定）。
    """
    code = (code or "").strip()
    if not code:
        return "错误：未提供 code（要执行的 Python 代码，需定义 def run(args): ...）。"
    _arts = list(artifacts or [])
    args = {"text": input_text or "", "content": input_text or "", "input": input_text or "",
            "artifacts": _arts}
    try:
        # 用户级隔离：沙箱产物按 <user_id>/<session_id>/ 落盘（惰性 import 防循环依赖）
        from builtin_tools._shared import _user_dir
        _art_root = _user_dir(ctx.user.get("id") if getattr(ctx, "user", None) else None,
                              getattr(ctx, "session_id", None), "artifacts")
        res = sandbox.run_code(code, args, artifact_root=_art_root)
    except Exception as e:
        return f"临时代码执行失败：{type(e).__name__}: {e}"
    if res.get("ok"):
        result = res.get("result") or ""
        arts = res.get("artifacts") or []
        if arts:
            result = (result + "\n" + "\n".join("已生成：" + p for p in arts)).strip()
        return result or "（代码执行成功，但未返回任何内容）"
    msg = res.get("error") or "临时代码执行失败"
    if res.get("timed_out"):
        msg = "临时代码执行超时（已在沙箱终止）"
    extra = res.get("stdout")
    if extra:
        msg += "\n--- 输出 ---\n" + extra[:1500]
    err = res.get("stderr")
    if err:
        msg += "\n--- 错误详情 ---\n" + err[:1500]
    tb = res.get("traceback")
    if tb:
        msg += "\n--- 调用栈 ---\n" + tb[:1500]
    return "临时代码执行失败：" + msg


def _find_similar_tool(name, description, trigger_words, existing_tools):
    """检查新工具是否与已有工具功能相似，返回相似的工具字典或 None。

    匹配策略（由强到弱）：
    0. 文件格式互斥：若新工具和已有工具涉及不同文件格式（docx/ppt/pdf/xlsx 等），
       直接判定为不相似，跳过。这是最重要的前置过滤。
    1. 名称完全相同或仅差版本后缀（v2/v3/_v2 等）
    2. 触发词重叠 ≥ 2 个「有意义的」关键词（排除通用词）
    3. 描述核心关键词重叠 ≥ 3 个（使用 bigram + 停用词过滤）
    """
    import re as _re

    # ── 格式关键词映射：同一组的格式视为相同，不同组视为互斥 ──
    _FORMAT_GROUPS = {
        "docx": {"word", "docx", "doc", ".docx", ".doc", "word文档", "word文件"},
        "ppt":  {"ppt", "pptx", "powerpoint", "幻灯片", ".ppt", ".pptx", "演示文稿"},
        "pdf":  {"pdf", ".pdf", "pdf文档", "pdf文件"},
        "xlsx": {"excel", "xlsx", "xls", "表格", ".xlsx", ".xls", "电子表格"},
        "csv":  {"csv", ".csv"},
        "img":  {"图片", "图像", "png", "jpg", "jpeg", "gif", ".png", ".jpg"},
        "txt":  {"文本", "txt", ".txt", "纯文本"},
        "html": {"html", "网页", ".html", "htm"},
        "md":   {"markdown", "md", ".md", "markdown文件"},
    }
    # 构建反向映射：keyword -> group_name
    _KW_TO_GROUP = {}
    for _gname, _kwds in _FORMAT_GROUPS.items():
        for _k in _kwds:
            _KW_TO_GROUP[_k] = _gname

    def _detect_format_group(text):
        """从文本中检测文件格式组名，返回集合（可能为空）。"""
        low = (text or "").lower()
        found = set()
        for _kw, _gn in _KW_TO_GROUP.items():
            if _kw in low:
                found.add(_gn)
        return found

    new_formats = _detect_format_group(description + " " + (trigger_words or ""))

    name_low = (name or "").strip().lower().rstrip("_0123456789")
    desc_low = (description or "").lower()

    # 中文通用停用字/词——几乎任何工具描述都会包含的无区分度字符
    _STOP_CHARS = set("的 是 在 有 和 与 或 能 可 将 对 中 并 其 这 那 它 为 以 及 也 而 但 如 从 到 用 所 下 上 被 让 给 把 被".split())
    _STOP_CHARS |= set("生成 创建 制作 输出 生产 构建 编写 形成 完成 执行 处理 工具 文件 内容 数据 信息 结果 任务 用户 系统 自动 智能 帮助 进行 实现 提供 支持 需要 通过 根据 基于 采用 包括 包含 使用 调用 操作 管理 功能 模块 方法 过程 步骤 方式 类型 格式 样式 效果 条件 参数 返回 获取 设置 添加 删除 修改 更新 查询 搜索 分析 转换 导入 导出 读取 写入 保存 存储 发送 接收 上传 下载 访问 连接 配置 运行 测试 验证 检查 计算 比较 合并 分割 提取 过滤 排序 统计 匹配 替换 格式化 序列化 反序列化 编码 解码 压缩 解压 加密 解密 签名 验证 授权 登录 注册 注销 会话 请求 响应 错误 异常 日志 记录 历史 版本 更新 发布 部署 安装 卸载 配置 监控 报警 通知 消息 邮件 短信 电话 语音 视频 音频 图片 图像 文档 表格 报告 清单 列表 详情 概览 总结 摘要 说明 简介 注释 备注 示例 样本 模板 范例 规范 标准 规则 策略 方案 计划 目标 要求 约束 条件 限制 权限 角色 账号 密码 密钥 令牌 凭证 证书 签名 哈希 摘要 校验 完整性 一致性 可靠性 可用性 性能 效率 质量 安全 隐私 合规 审计 追踪 监管 风控 容灾 备份 恢复 迁移 同步 异步 并发 分布式 缓存 队列 消息 事件 流 管道 任务 调度 定时 批量 实时 离线 在线 动态 静态 公开 私有 内部 外部 本地 远程 云端 服务 客户端 服务端 前端 后端 数据库 缓存 文件系统 网络 协议 接口 API SDK 库 框架 引擎 平台 系统 应用 程序 进程 线程 协程 事务 会话 上下文 环境 配置 参数 选项 属性 字段 列 行 记录 集合 映射 列表 数组 元组 字符串 数字 布尔 空 无 真 假 是 否 有 没 不 未 已 将 正 再 更 最 很 太 较 稍 略 均 仅 全 各 每 某 凡 任 何 谁 哪 么 些 这 那 哪 几 多 少 大 小 长 短 高 低 快 慢 新 老 好 坏 强 弱 难 易 复 简 粗 细 宽 窄 厚 薄 深 浅 轻 重 冷 热 干 湿 软 硬 明 暗 虚 实 真 假 正 反 内 外 左 右 上 下 前 后 东 西 南 北 中 心 边 角 顶 底 首 尾 始 末 开 关 出 入 来 去 回 进 退 起 落 升 降 加 减 乘 除 余 与 或 非 且 因 若 则 虽然 但是 然而 因此 所以 因为 由于 如果 即使 尽管 无论 不管 只要 只有 除非 否则 另外 此外 而且 同时 之后 之前 期间 其中 之中 之内 之外 以上 以下 之中 之间".split())

    def _tokens(text):
        """提取有意义的中文 bigram + 英文单词，过滤停用词。"""
        t = text.lower()
        # 中文 bigram（相邻两字组合），比单字更有语义
        chars = [c for c in t if '\u4e00' <= c <= '\u9fff']
        bigrams = set()
        for i in range(len(chars) - 1):
            bg = chars[i] + chars[i + 1]
            if bg not in _STOP_CHARS:
                bigrams.add(bg)
        # 英文按空格拆
        words = [w for w in _re.split(r'[^a-z0-9]+', t) if len(w) >= 2 and w not in _STOP_CHARS]
        return set(bigrams + words)

    new_tokens = _tokens(desc_low + " " + (trigger_words or ""))
    new_trigs = set((trigger_words or "").replace(",", " ").lower().split())
    # 过掉触发词中的通用词
    new_trigs = {t for t in new_trigs if len(t) >= 2 and t not in _STOP_CHARS}

    for t in existing_tools:
        t_name = (t.get("name") or "").lower().rstrip("_0123456789.v")
        t_desc = (t.get("description") or "").lower()
        t_trigs = set((t.get("trigger_words") or "").replace(",", " ").lower().split())
        t_trigs = {tt for tt in t_trigs if len(tt) >= 2 and tt not in _STOP_CHARS}
        t_tokens = _tokens(t_desc)

        # ── 规则0：文件格式互斥检查 ──
        t_formats = _detect_format_group(t_desc + " " + (t.get("trigger_words") or ""))
        if new_formats and t_formats:
            # 双方都检测到格式关键词，但无交集 → 不同格式，不算相似
            if not (new_formats & t_formats):
                continue  # 跳过这个已有工具，不是重复

        # 规则1：名称高度相似（去掉版本号后缀后相同）
        if t_name and name_low and (t_name == name_low or t_name in name_low or name_low in t_name):
            if len(t_name) >= 6:  # 避免短名误判
                return t

        # 规则2：触发词重叠 ≥ 2（有意义的）
        trig_overlap = new_trigs & t_trigs
        if len(trig_overlap) >= 2:
            return t

        # 规则3：描述核心词（bigram）重叠 ≥ 3
        tok_overlap = new_tokens & t_tokens
        if len(tok_overlap) >= 3:
            return t

    return None


async def _h_create_tool(ctx, name, display_name, description, code, trigger_words="", category="general"):
    """智能体在会话中动态创建「私有工具」：用户用 Python 代码定义工具，沙箱执行。

    与 create_skill 区分：
    - create_skill 只创建方法论技能（method 类型，注入 system prompt 约束思考方式）；
    - create_tool 创建可执行的 Python 工具（在沙箱中跑 def run(a)），保存为私有工具，
      仅创建者本人可见可用，存于「全局工具库」（与 admin 注册的全局工具并列展示）。
    """
    if not ctx.user:
        return "创建工具失败：缺少用户上下文"
    name = (name or "").strip()
    display_name = (display_name or "").strip()
    description = (description or "").strip()
    code = (code or "").strip()
    if not name or not description or not code:
        return "创建工具失败：name、description、code 均必填"
    if not _SKILL_NAME_RE.match(name):
        return "创建工具失败：name 须为合法标识符（字母/数字/下划线，如 my_tool）"
    # 服务端去重：检查是否已有功能相似的工具，防止 LLM 反复创建重复工具
    # 但「修复/升级/替换」意图除外——用户通过 [PROPOSE_TOOL] 确认后，明确要替代旧工具
    _upgrade_keywords = {"修复", "fix", "升级", "v2", "v3", "改进", "替换", "重写", "新版",
                          "修正", "解决.*错误", "解决.*bug", "解决.*问题", "完善版", "增强版"}
    _is_upgrade = any(k in (description + " " + name).lower() for k in _upgrade_keywords)
    if not _is_upgrade:
        existing = list_tools(for_user_id=ctx.user["id"]) if ctx.user else []
        dup = _find_similar_tool(name, description, trigger_words or "", existing)
        if dup:
            _dup_name = dup['name']
            _dup_disp = dup['display_name']
            # 根据已有工具类型给出具体调用指引
            if _dup_name in ('generate_word', 'generate_ppt'):
                _example = f"请立即调用 {_dup_name}(content='你的文档内容（表格用 | 列1 | 列2 | 语法）', title='文档标题') 执行任务"
            else:
                _example = f"请立即调用 {_dup_name} 工具执行任务（将内容传给 text 或 content 参数）"
            return (f"⚠️ 功能相似的工具已存在：「{_dup_disp}」（{_dup_name}），"
                    f"描述：{dup['description'][:80]}。"
                    f"{_example}，不要创建重复工具。"
                    f"如果该工具确实有缺陷需要修复，请在描述中明确写明「修复版」「解决XXX错误」等意图词，"
                    f"系统会允许你创建替换版本。")
    # 升级模式：自动确保名字不与已有工具冲突（追加 _v2 / _fixed 后缀）
    if _is_upgrade:
        existing = list_tools(for_user_id=ctx.user["id"]) if ctx.user else []
        _taken = {t["name"] for t in existing}
        if name in _taken:
            import re as _re
            # 去掉已有的 _v2/_fixed 等后缀再重新加
            _base = _re.sub(r'(_v\d+|_fixed|_upgrade)$', '', name)
            _suffix = "_fixed"
            _candidate = _base + _suffix
            _n = 2
            while _candidate in _taken:
                _candidate = f"{_base}_v{_n}"
                _n += 1
            name = _candidate
            if not display_name:
                display_name = name
    # 触发词兜底：从描述派生，保证工具能被关键词路由命中
    trigger_words = (trigger_words or "").strip() or _derive_trigger_words(description)
    # 沙箱安全扫描
    ok, reason = sandbox.scan_code(code)
    if not ok:
        return ("创建工具失败：代码未通过安全扫描（" + reason +
                "）。请改用纯 Python 数据处理逻辑，不要使用 os/sys/subprocess/socket/requests/shutil "
                "等模块，也不要做文件/网络/系统调用，禁止使用 eval/exec。")
    try:
        tid = save_tool({
            "name": name, "display_name": display_name or name, "description": description,
            "category": category or "general", "trigger_words": trigger_words,
            "scope": "private", "owner_id": ctx.user["id"], "is_user_created": 1,
            "code_text": code, "backend_type": "user_code", "handler": None,
            "create_source": "agent_auto",
        })
    except Exception as e:
        return "创建工具失败：" + str(e)
    # 沙箱试跑：保证空参也能返回合理结果（与 create_skill 的 code 类技能一致）
    tests = [{}, {"input": "测试文本", "text": "测试文本", "a": 1, "b": 2}]
    ran = [sandbox.run_code(code, t) for t in tests]
    ok_run = any(r.get("ok") for r in ran)
    note = "" if ok_run else "（提示：试跑未通过，调用时可能需要传入正确的参数；建议 run(a) 用 a.get('key', 默认值) 取参）"
    if not hasattr(ctx, "created_tools"):
        ctx.created_tools = []
    ctx.created_tools.append({"name": name, "id": tid, "code": code, "description": description, "display_name": display_name or name})
    return (f"✅ 工具「{display_name or name}」（{name}）已创建成功（id={tid}），"
            f"已设为私有工具并加入全局工具库。"
            f"**下一步（必须）**：请在接下来的步骤中立即调用 {name} 工具来执行任务"
            f"（将实际内容传给 text 或 content 参数）。不要再次调用 create_tool，工具已经创建好了。"
            f"{note}")


async def _h_save_memory(ctx, content, mem_type="note", mem_key=None):
    """智能体在会话中把「值得长期记住的用户信息」写入该用户的跨会话长期记忆库（user_memory 表）。
    存储充要条件是本函数被调用（用户显式要求，或模型推断该记）；写入后每轮对话会由 agent_endpoints 自动注入 prompt，
    但「不会自动显示」在面板——面板是用户按需 GET 才读。两者解耦，都源自同一张表。
    调用时机（【该存】）：① 用户显式要求长期保留（「记住这个」「以后都这样」）；② 稳定偏好/习惯
    （「我习惯用 Excel」「输出用中文」「红色表示上涨」）；③ 长期事实（职业、项目背景、技术栈）；
    ④ 用户设定的约定/规则（「以后代码都用 TypeScript」）。
    【不该存】一律不调用：① 一次性对话细节/临时上下文；② 敏感凭证/密码/密钥/Token；③ 琐碎、会过期的临时信息。
    参数：content（记忆正文，中文，必填）；mem_type（偏好 preference / 项目 project / 约定 convention / 其他 note，默认 note）；
         mem_key（可选去重键，同名键会覆盖更新，便于「修正既有记忆」）。
    """
    if not ctx.user:
        return "保存记忆失败：缺少用户上下文"
    content = (content or "").strip()
    if not content:
        return "保存记忆失败：content 不能为空"
    mem_type = (mem_type or "note").strip() or "note"
    mem_key = (mem_key or "").strip() or None
    try:
        save_user_memory(ctx.user["id"], mem_type, content, mem_key)
    except Exception as e:
        return "保存记忆失败：" + str(e)
    _k = f"（键：{mem_key}）" if mem_key else ""
    return (f"✅ 已为你保存一条长期记忆（类型：{mem_type}）{_k}，下次新对话会自动带上。"
            f"内容摘要：{content[:60]}")


async def _h_forget_memory(ctx, mem_key=None, mem_id=None):
    """删除该用户 long-term 记忆库中的某条记录（调用 delete_user_memory）。删除后与 forget_memory 工具、面板 DELETE 路由
    走的是同一个删除函数——删除后该条既不再注入 prompt、也不在面板显示，读写删全链路归一。
    调用时机：用户说「忘了XX」「不要记那个了」，或你判断某记忆已过时/有误/当初就不该存。
    可通过 mem_key（去重键）或 mem_id（记忆 id）定位，二选一。
    """
    if not ctx.user:
        return "删除记忆失败：缺少用户上下文"
    if not mem_key and not mem_id:
        return "删除记忆失败：需提供 mem_key 或 mem_id 之一"
    try:
        delete_user_memory(ctx.user["id"], mem_id=mem_id, mem_key=mem_key)
    except Exception as e:
        return "删除记忆失败：" + str(e)
    return "✅ 已删除对应的长期记忆。"


async def _h_ask_user(ctx, question, options=None):
    """向用户提出澄清问题（AskUserQuestion 确认原语）。

    注意：run_agent 在分发前已按名称拦截本工具——它不会真正“执行”，
    而是向前端 yield ask_user 事件并暂停本轮，等用户回复后由新消息续跑。
    因此本 handler 仅在极端兜底（未被拦截）时被调用，返回一句提示即可。

    自动化场景（ctx.is_automation=True）：无人在线，ask_user 已在 _build_run_context
    从 meta 工具列表移除；这里是兜底——返回"已自动忽略"占位，避免 agent 因
    ask_user 触发最终回答 "⏸ 已向你提出问题..." 而挂起（自动化邮件收到空提示）。
    """
    if getattr(ctx, "is_automation", False):
        return ("[ask_user·自动化兜底] 本工具在自动化任务中无人在线，已自动忽略。"
                "请继续基于现有信息完成任务，不要再次调用 ask_user。")
    return ("[ask_user] 已向用户提出问题，等待用户在对话框中回复。"
            "请勿凭空假设答案，必须等用户回复后再继续。")


async def _h_conversation_search(ctx, query, limit=10):
    """P2⑪ 跨对话历史检索：在用户自己的历史对话中按关键词检索，按对话窗口聚合返回。

    用于「用户提到以前聊过的事」「引用之前的结论/约定」「回顾某次任务结果」等场景。
    仅检索当前用户（ctx.user.id）的历史，不跨用户；返回命中的对话窗口列表
    （含窗口标题、命中条数、最新命中预览、时间），以「会话级」方式呈现结果。
    参数：query（检索关键词，可多词空格分隔，按 AND 匹配）；limit（最多返回窗口数，默认 10）。
    """
    if not ctx.user:
        return "检索失败：缺少用户上下文"
    query = (query or "").strip()
    if not query:
        return "检索失败：query 不能为空"
    try:
        limit = int(limit) if limit else 10
    except Exception:
        limit = 10
    rows = search_conversations_grouped(ctx.user["id"], query, limit=max(1, min(limit, 30)))
    if not rows:
        return f"未检索到与「{query}」相关的历史对话。"
    lines = []
    for i, r in enumerate(rows, 1):
        title = r.get("title") or "(未命名对话)"
        hit = r.get("hit_count") or 0
        preview = (r.get("preview") or "").strip().replace("\n", " ")
        preview = preview[:160]
        last = r.get("last_hit_at") or ""
        lines.append(f"{i}. 对话窗口「{title}」— 命中 {hit} 条（最近命中 {last}）\n   预览：{preview}")
    return ("以下是检索到的历史对话窗口（已按会话分组）：\n" + "\n\n".join(lines) +
            "\n\n如需查看某窗口完整内容，可让用户在「历史对话」面板打开对应会话。")


async def _h_spawn_subagent(ctx, task, context="", allowed_tools=None, subagent_type="generic", depth=0):
    """委派子 Agent 独立完成子任务（P1⑤：复用 run_agent 作为子 Agent 运行时）。

    隔离防护：① 独立 session_id（不污染主历史）；② 工具集仅业务工具/技能（无元工具），
    禁止写库/提问/再派生；③ depth 锁 1 层（父 depth>=1 则拒绝，防递归失控）；
    ④ 独立 max_steps（6）避免子任务失控；⑤ 共享 client/model/params/user。
    返回子 Agent 的最终结论文本，由主 Agent 整合进总任务。
    """
    cur_depth = getattr(ctx, "depth", 0) or 0
    if cur_depth >= 1:
        return ("❌ 已达到子 Agent 委派深度上限（禁止递归派生孙 Agent），"
                "请直接完成任务，或换一种不依赖嵌套委派的方式。")
    task = (task or "").strip()
    if not task:
        return "❌ 委派失败：task（子任务描述）不能为空。"
    from tools_build import build_subagent_tools
    sub_session = f"{getattr(ctx, 'session_id', 'main')}:sub:{uuid.uuid4().hex[:8]}"
    sub_ctx = ToolContext(ctx.client, ctx.model_name, ctx.params, user=ctx.user)
    sub_ctx.session_id = sub_session
    sub_ctx.depth = 1                 # 子 Agent 不能再派生
    sub_ctx.created_tools = []
    sub_ctx.max_create_skills = 0     # 禁止写库
    sub_ctx.parent_session = getattr(ctx, "session_id", None)
    sub_tools = build_subagent_tools(sub_ctx, allowed_tools)
    messages = [
        {"role": "system", "content": (
            "你是主智能体委派的执行子 Agent。请严格基于下方「任务」独立完成，"
            "只能调用已提供给你的业务工具/技能（禁止创建技能或工具、禁止向用户提问、"
            "禁止再派生子 Agent）。所有中间过程自行处理，最后用中文给出精炼的最终结论。"
        )},
        {"role": "user", "content": f"任务：{task}\n\n背景信息：{context or '（无）'}"},
    ]
    sub_id = sub_session.split(":")[-1] if ":" in sub_session else uuid.uuid4().hex[:8]
    # 冒泡通道：子 Agent 的中间事件（plan/call/result）写入 ctx._subagent_events，
    # 由主 run_agent 工具执行循环在 result 之后逐条 yield 给前端。
    _sub_events = []
    setattr(ctx, "_subagent_events", _sub_events)
    setattr(ctx, "_subagent_current_id", sub_id)
    final_text = ""
    try:
        async for ev in run_agent(ctx.client, ctx.model_name, messages, sub_tools, ctx.params,
                                  max_steps=6, on_tool_call=None, cancel_check=None,
                                  ctx=sub_ctx, caps=None):
            if ev.get("type") == "final":
                final_text = ev.get("text", "")
                # final 也入列（前端用它标记子 Agent 完成）
                _sub_events.append({"type": "sub_final", "sub_id": sub_id, "text": final_text})
            elif ev.get("type") == "aborted":
                _sub_events.append({"type": "sub_aborted", "sub_id": sub_id})
                return "⏹ 子 Agent 任务已被用户中止。"
            elif ev.get("type") == "artifacts":
                # 内部事件，不冒泡
                continue
            else:
                # plan / call / result / error → 打上 sub_id 标签冒泡
                _ev = dict(ev)
                _ev["sub_id"] = sub_id
                _ev["type"] = "sub_step"
                _sub_events.append(_ev)
    except Exception as e:
        _sub_events.append({"type": "sub_error", "sub_id": sub_id, "text": str(e)})
        return f"❌ 子 Agent 执行出错：{str(e)}"
    if not final_text:
        _sub_events.append({"type": "sub_empty", "sub_id": sub_id})
        return "⚠️ 子 Agent 未能产生有效结论（可能受步数上限约束）。"
    return "🤖 子 Agent 执行结果：\n" + final_text


async def _h_task_create(ctx, title, description="", active_form="", parent_id=None,
                         add_blocks=None, add_blocked_by=None):
    """创建一个任务（长程任务的子任务拆分）。返回任务 id 与状态。"""
    if not ctx.user:
        return "创建任务失败：缺少用户上下文"
    title = (title or "").strip()
    if not title:
        return "创建任务失败：title 不能为空"
    sid = getattr(ctx, "session_id", None)
    try:
        tid = create_task(ctx.user["id"], sid, title, description=(description or ""),
                          status="pending", parent_id=parent_id,
                          active_form=(active_form or ""),
                          add_blocks=add_blocks or [], add_blocked_by=add_blocked_by or [])
    except Exception as e:
        return "创建任务失败：" + str(e)
    if not tid:
        return "创建任务失败：参数无效"
    return ("✅ 已创建任务 #%d（状态：pending）：%s。可用 task_update 更新状态/依赖，"
            "用 task_list 查看全部任务。" % (tid, title))


async def _h_task_get(ctx, task_id):
    """读取单条任务详情。"""
    if not ctx.user:
        return "读取任务失败：缺少用户上下文"
    try:
        t = get_task(task_id, ctx.user["id"])
    except Exception as e:
        return "读取任务失败：" + str(e)
    if not t:
        return "未找到任务 #%s（或不属于当前用户）。" % task_id
    return json.dumps({
        "id": t["id"], "title": t["title"], "description": t["description"],
        "status": t["status"], "parent_id": t["parent_id"],
        "active_form": t["active_form"],
        "add_blocks": json.loads(t["add_blocks"] or "[]"),
        "add_blocked_by": json.loads(t["add_blocked_by"] or "[]"),
        "created_at": t["created_at"], "updated_at": t["updated_at"],
    }, ensure_ascii=False)


async def _h_task_update(ctx, task_id, status=None, title=None, description=None,
                         active_form=None, add_blocks=None, add_blocked_by=None):
    """更新任务状态/标题/描述/依赖。返回更新后的任务。"""
    if not ctx.user:
        return "更新任务失败：缺少用户上下文"
    fields = {}
    if status is not None:
        fields["status"] = status
    if title is not None:
        fields["title"] = title
    if description is not None:
        fields["description"] = description
    if active_form is not None:
        fields["active_form"] = active_form
    if add_blocks is not None:
        fields["add_blocks"] = add_blocks
    if add_blocked_by is not None:
        fields["add_blocked_by"] = add_blocked_by
    try:
        t = update_task(task_id, ctx.user["id"], **fields)
    except Exception as e:
        return "更新任务失败：" + str(e)
    if not t:
        return "未找到任务 #%s（或不属于当前用户）。" % task_id
    return ("✅ 任务 #%d 已更新：状态=%s，标题=%s。" % (t["id"], t["status"], t["title"]))


async def _h_task_list(ctx, session_id=None):
    """列出当前用户的任务；可传入 session_id 仅看某次连续对话的任务。"""
    if not ctx.user:
        return "列出任务失败：缺少用户上下文"
    try:
        rows = list_tasks(ctx.user["id"], session_id=session_id)
    except Exception as e:
        return "列出任务失败：" + str(e)
    if not rows:
        return "当前没有任务。"
    out = []
    for t in rows:
        out.append({
            "id": t["id"], "title": t["title"], "status": t["status"],
            "description": t["description"], "parent_id": t["parent_id"],
            "active_form": t["active_form"],
        })
    return json.dumps({"count": len(out), "tasks": out}, ensure_ascii=False)


# 业务工具的真实执行函数统一从 builtin_tools 包加载（每个工具一个 .py，自动扫描注册）。
# 元工具（create_skill / create_tool / list_* / save_memory / run_temp_code / task_* / ...）
# 仍定义在本文件，并由 tools_build.build_meta_tools 直接包裹注入，不走 HANDLERS。
from builtin_tools import TOOL_HANDLERS

HANDLERS = dict(TOOL_HANDLERS)

