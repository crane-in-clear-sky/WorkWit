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


SYSTEM_PROMPT = (
    "你是资深合同法务。请站在我方（{party}）的角度审查合同，"
    "只输出一个 JSON 对象，结构如下，不要输出多余文字：\n"
    '{"summary":"一句话总体评价","risks":['
    '{"clause":"条款名称或位置","level":"高/中/低",'
    '"issue":"风险描述","suggestion":"修改建议"}]}\n'
    "level 按对我方不利程度分为 高/中/低。"
)


RESUME_PROMPT = (
    "你是一名资深的招聘与人才评估专家（HR 视角）。请根据下方【招聘岗位画像】对【候选人简历】进行筛选评估。"
    "只输出一个 JSON 对象，不要输出多余文字（也不要使用 ```json 代码块标记）：\n"
    '{"name":"候选人姓名（尽量从简历推断，推断不出填\\"未知\\"）",'
    '"age":"简历中的年龄或出生年份（如 28岁 / 1995年生，看不出填\\"未知\\"）",'
    '"major":"专业（看不出填\\"未知\\"）",'
    '"education":"最高学历（本科/硕士/博士，看不出填\\"未知\\"）",'
    '"position":"当前或期望岗位（看不出填\\"未知\\"）",'
    '"experience_years":"工作年限（如 5年，看不出填\\"未知\\"）",'
    '"score":0,'
    '"recommend":"强烈推荐/推荐/待定/不推荐",'
    '"strengths":["优势点1","优势点2"],'
    '"weaknesses":["不足或风险点1","不足或风险点2"],'
    '"comment":"一句话综合评语"}\n'
    "score 为 0-100 的整数（与岗位的匹配度）；"
    "recommend 取值需与 score 一致：强烈推荐>=85，推荐 70-84，待定 55-69，不推荐<55。"
    "请务必从简历正文中提取 age/major/education/position/experience_years 关键字段，用于与岗位画像对照。"
)


def extract_text(raw: bytes, filename: str) -> str:
    name = filename.lower()
    if name.endswith(".txt"):
        return raw.decode("utf-8", errors="ignore")
    if name.endswith(".docx"):
        d = docx.Document(io.BytesIO(raw))
        return "\n".join(p.text for p in d.paragraphs)
    if name.endswith(".pdf"):
        reader = PdfReader(io.BytesIO(raw))
        return "\n".join((p.extract_text() or "") for p in reader.pages)
    raise ValueError("仅支持 txt / docx / pdf 格式")


def parse_json(content: str):
    content = (content or "").strip()
    try:
        return json.loads(content)
    except json.JSONDecodeError:
        pass
    m = re.search(r"\{.*\}", content, re.DOTALL)
    if m:
        try:
            return json.loads(m.group(0))
        except Exception:
            pass
    # 截断兜底：模型输出被 max_tokens 截断时 JSON 往往未闭合，
    # 从最后一个 { 起按括号平衡补齐缺失的 } / ] 再解析，尽量挽回结果。
    repaired = _repair_truncated_json(content)
    if repaired is not None:
        return repaired
    return {"summary": "模型返回解析失败", "risks": [], "raw": content}


def _repair_truncated_json(content: str):
    """尽力修复被截断的 JSON：从最外层 { 起，用栈记录未闭合括号，按逆序补齐闭合符号。"""
    start = content.find("{")
    if start == -1:
        return None
    sub = content[start:]
    stack = []          # 未闭合的开括号类型：'{' 或 '['
    instr = False
    esc = False
    for ch in sub:
        if esc:
            esc = False
            continue
        if ch == "\\":
            esc = True
            continue
        if ch == '"':
            instr = not instr
            continue
        if instr:
            continue
        if ch in "{[":
            stack.append(ch)
        elif ch in "}]":
            if stack:
                stack.pop()
    close = '"' if instr else ""          # 若字符串未闭合先补引号
    for open_ch in reversed(stack):       # 按后进先出逆序补闭括号
        close += "}" if open_ch == "{" else "]"
    try:
        return json.loads(sub + close)
    except Exception:
        return None


def _sse(obj):
    return f"data: {json.dumps(obj, ensure_ascii=False)}\n\n"


_FILE_EXTS = (".pptx", ".docx", ".pdf", ".xlsx", ".txt", ".csv", ".zip",
              ".json", ".md", ".html", ".png", ".jpg", ".jpeg", ".gif",
              ".mp4", ".webm", ".mov", ".avi", ".mp3", ".wav")


def _detect_generated_file(text):
    """从工具返回文本中提取一个「磁盘上真实存在」的文件绝对路径。

    兼容 Linux（/tmp/...）与 Windows（C:\\...）绝对路径，并忽略 http(s) 链接、
    不存在的路径，避免误把 URL 或临时文本当成产物。
    """
    if not text or not isinstance(text, str):
        return None
    # 1) 显式「已生成：<path>」格式优先
    m = re.search(r"已生成[:：]\s*(\S+)", text)
    if m:
        p = m.group(1).strip().rstrip(".,;:)")
        if os.path.isabs(p) and os.path.isfile(p):
            return p
    # 2) 通用扫描：按空白与常见分隔符切词，找「绝对路径 + 已知扩展名 + 磁盘存在」
    for tok in re.split(r"[\s，。；、,;：]+", text):
        tok = tok.strip().strip("()\"'<>|.")
        if not tok or tok.lower().startswith(("http://", "https://")):
            continue
        if not tok.lower().endswith(_FILE_EXTS):
            continue
        if os.path.isabs(tok) and os.path.isfile(tok):
            return tok
    return None


def _detect_generated_files(text):
    """从文本中提取所有「磁盘上真实存在」的文件绝对路径（去重，按出现顺序）。

    兼容 Linux（/tmp/...）与 Windows（C:\\...）绝对路径，忽略 http(s) 链接、
    不存在或带反引号/引号包裹的路径，避免误把 URL / 临时文本当成产物。
    供 SSE 流对每个真实产物推送一张下载卡片（result 与 final 事件均会扫描）。
    """
    if not text or not isinstance(text, str):
        return []
    found, seen = [], set()

    def _accept(p):
        p = p.strip().strip("`*\"'<>|()[]{}。，、,;：:.").rstrip(".,;:)")
        if (not p) or p.lower().startswith(("http://", "https://")):
            return None
        if not p.lower().endswith(_FILE_EXTS):
            return None
        if os.path.isabs(p) and os.path.isfile(p) and p not in seen:
            seen.add(p)
            return p
        return None

    # 1) 显式中文提示格式优先：已生成/已保存/保存至/输出到/路径：<path>
    for m in re.finditer(
            r"(?:已生成|已保存|保存至|保存到|存放于|输出到|输出至|路径)[:：]\s*(\S+)", text):
        p = _accept(m.group(1))
        if p:
            found.append(p)
    # 2) 通用扫描：按空白与常见分隔符切词，找「绝对路径 + 已知扩展名 + 磁盘存在」
    for tok in re.split(r"[\s，。；、,;：]+", text):
        p = _accept(tok)
        if p:
            found.append(p)
    return found


def _ext_of(path):
    return os.path.splitext(path)[1].lower().lstrip(".")


def _model_params(m, fallback_temp=0.2):
    """从模型配置 dict 提取推理参数，仅包含被显式配置的项；未配置项交给调用方用默认值兜底。

    - temperature: 仅当 >=0 时带上（DB 默认 -1 表示不设置）
    - max_tokens: 仅当 >0 时带上（DB 默认 0 表示不限制）
    - top_p: 仅当 >=0 时带上（DB 默认 -1 表示不设置）
    - thinking: 为真时附加 extra_body={"enable_thinking": True}（Qwen 等支持）
    """
    p = {}
    t = m.get("temperature")
    if isinstance(t, (int, float)) and t >= 0:
        p["temperature"] = float(t)
    mt = m.get("max_tokens")
    if isinstance(mt, (int, float)) and mt > 0:
        p["max_tokens"] = int(mt)
    tp = m.get("top_p")
    if isinstance(tp, (int, float)) and tp >= 0:
        p["top_p"] = float(tp)
    if m.get("thinking"):
        p["extra_body"] = {"enable_thinking": True}
    return p


def _llm_call(client, model_name, system, user, params):
    """单次非流式模型调用，返回文本内容。工具 handler 内部用它做 LLM 类任务。"""
    msgs = []
    if system:
        msgs.append({"role": "system", "content": system})
    msgs.append({"role": "user", "content": user})
    p = dict(params or {})
    p.setdefault("temperature", 0.3)
    resp = client.chat.completions.create(model=model_name, messages=msgs, **p)
    return resp.choices[0].message.content or ""


class ToolContext:
    """一次智能体会话的上下文，注入到各工具 handler。"""
    def __init__(self, client, model_name, params, user=None):
        self.client = client
        self.model_name = model_name
        self.params = params
        self.user = user
        self.created_skills = []
        self._skill_create_count = 0
        self.max_create_skills = None
        # 子 Agent 委派相关（P1⑤）：独立会话标识 / 已创建工具 / 递归深度 / 父会话
        self.session_id = None
        self.created_tools = []
        self.depth = 0
        self.parent_session = None
        # P2⑮: 本任务完整 messages 副本（system + 历史 + user），供工具入口
        # 在 LLM 未传参数时回灌聊天历史数据——例如 generate_ppt 在 content
        # 缺失时自动从最近一条 assistant 消息抽取资料作为 content。
        # 由 agent_endpoints.agent_chat 在调 run_agent 之前注入。
        self.messages = None


AGENT_TASKS = {}

