"""P4 · 多模型兼容抽象层（薄适配层）。

设计目标（对照重构方案 §4.5「多模型兼容层」）：
- 任意 OpenAI 兼容大模型均可接入：以 OpenAI SDK 作为统一客户端（业界事实标准，
  绝大多数「任意模型」部署都暴露 OpenAI 兼容 /v1 端点），供应商切换仅改配置，不碰业务代码。
- 能力声明：每个模型在 DB 中声明 supports_tools / timeout / thinking，运行时据此分支，
  而非写死假设「模型一定支持 function calling」。
- 文本模式降级：supports_tools=0 的模型（不支持原生工具调用）走「文本工具调用」模式，
  由系统注入工具清单 + 解析模型输出的结构化工具调用块，保证「完美兼容任意模型」。

本模块刻意保持零业务依赖：只依赖 openai SDK 与标准库，避免循环导入。
"""
import json
import re
from openai import OpenAI


def _to_int(v, default=0):
    try:
        return int(v)
    except Exception:
        return default


class ModelCaps:
    """从模型配置 dict 读取「模型能力声明」，驱动 run_agent 的分支与超时。

    配置字段（来自 models 表 / get_active 返回的 dict）：
      - supports_tools: 1/0，模型是否支持原生 function calling（tool_calls）。默认 1。
      - timeout:        int 秒，单次请求超时；0/未配置 → 默认 180。
      - thinking:       1/0，是否启用思考模式（enable_thinking）。
    """

    def __init__(self, cfg):
        self.cfg = cfg or {}

    def supports_tools(self):
        """模型是否支持原生 function calling（tool_calls）。默认 True。"""
        return bool(_to_int(self.cfg.get("supports_tools"), 1))

    def client_timeout(self):
        """单次请求客户端超时（秒）。0/未配置 → 默认 180。"""
        t = _to_int(self.cfg.get("timeout"), 0)
        return t if t > 0 else 180

    def call_timeout(self):
        """异步 wait_for 包裹的单次调用超时（秒）；与客户端超时一致，作为唯一守卫。"""
        return self.client_timeout()

    def enable_thinking(self):
        return bool(_to_int(self.cfg.get("thinking"), 0))

    def label(self):
        return self.cfg.get("name") or self.cfg.get("model_name") or "?"

    def describe(self):
        return "支持工具调用" if self.supports_tools() else "文本模式(无原生工具调用)"


def create_client(base_url, api_key, timeout=180):
    """统一创建 OpenAI 兼容客户端。

    base_url 指向任意 OpenAI 兼容端点（vLLM / Ollama / 中转 / 云厂商）即可直接接入，
    无需修改业务代码。供应商切换只改模型配置里的 base_url / api_key / model_name。
    """
    return OpenAI(base_url=(base_url or "").rstrip("/"),
                  api_key=api_key or "not-needed",
                  timeout=timeout)


# ---------------------------------------------------------------------------
# 文本工具调用模式（supports_tools=0 降级）
# ---------------------------------------------------------------------------
_TEXT_TOOL_CALL_RE = re.compile(r"```tool_call\s*\n(.*?)\n```", re.S | re.I)
_TEXT_TOOL_CALL_RE2 = re.compile(r"<<TOOL_CALL>>\s*(.*?)\s*<<END_TOOL_CALL>>", re.S)


def _extract_first_json(s):
    """从字符串中抽取第一个合法的 JSON 对象（支持嵌套括号）。失败返回 None。"""
    start = s.find("{")
    if start == -1:
        return None
    depth = 0
    instr = False
    esc = False
    for i in range(start, len(s)):
        ch = s[i]
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
        if ch == "{":
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0:
                frag = s[start:i + 1]
                try:
                    return json.loads(frag)
                except Exception:
                    return None
    return None


def _parse_text_tool_call(text):
    """从模型文本回复中解析文本模式的工具调用。

    支持两种格式：
      1) 代码块：```tool_call\n{"name":"x","arguments":{...}}\n```
      2) 标记：   <<TOOL_CALL>> {...} <<END_TOOL_CALL>>
    返回 (tool_name, args_dict, cleaned_text)；解析不到则返回 (None, None, text)。
    """
    text = text or ""
    block = None
    m = _TEXT_TOOL_CALL_RE.search(text)
    if m:
        block = m.group(1)
    else:
        m2 = _TEXT_TOOL_CALL_RE2.search(text)
        if m2:
            block = m2.group(1)
    if not block:
        return None, None, text
    # 优先整块解析（块内容本就是完整 JSON）；失败则用括号配平扫描抽取最外层对象
    obj = None
    try:
        obj = json.loads(block.strip())
    except Exception:
        obj = _extract_first_json(block)
    if isinstance(obj, dict) and obj.get("name") and "arguments" in obj:
        args = obj.get("arguments") or {}
        if not isinstance(args, dict):
            args = {}
        start = text.find(block)
        cleaned = (text[:start] + text[start + len(block):]).strip()
        return obj["name"], args, cleaned
    return None, None, text


def build_text_tool_section(tools):
    """构造文本模式的「工具调用说明 + 工具清单」，注入 system prompt。

    2026-08-18 强化（方案B）：针对 qwen3-8b 等无原生工具能力模型——
    - 增加「硬性规则」：必须用 tool_call 代码块才能执行工具；
    - 增加 few-shot 正反示例；
    - 明确禁止「在文本中声称已生成文件」——只有真实调用工具后才算数。
    """
    lines = [
        "【工具调用（文本模式）】",
        "本模型通过文本格式调用工具，无需原生 function calling。",
        "",
        "═══ 硬性规则（必须遵守）═══",
        "1) 你需要调用工具完成任务时，必须单独输出一个 tool_call 代码块，格式如下（严格按此格式）：",
        "```tool_call",
        '{"name": "工具名", "arguments": {"参数名": "参数值"}}',
        "```",
        "2) 只输出代码块，代码块前后不要夹带其他文字；",
        "3) 输出代码块后，系统会执行该工具并把结果回传给你，你应基于结果继续；",
        "4) 需要调用多个工具时，一次只输出一个代码块，等结果回传后再输出下一个；",
        "5) 当任务已完成、无需再调用工具时，直接给出最终答复（不要再输出代码块）。",
        "",
        "═══ 反例（错误示范，禁止这样做）═══",
        "❌ 不要在最终答复里说「文档已生成，点击下载 xxx.docx」却不调用生成工具——",
        "   没有输出 tool_call 代码块，系统不会生成任何文件，你的话只是文字，用户拿不到文件。",
        "❌ 不要假装调用了工具，例如写「已调用 generate_word 生成文档」但没有代码块——无效。",
        "❌ 不要用其他格式代替代码块，例如 `调用工具：xxx`、`[工具]xxx`——无效，解析不到。",
        "",
        "═══ 正例（正确示范）═══",
        "✅ 用户要求生成 Word 报告，且你已准备好内容时，应输出：",
        "```tool_call",
        '{"name": "generate_word", "arguments": {"title": "苏州近期天气情况报告", "content": "今日天气：晴 28℃..."}}',
        "```",
        "   系统执行后会回传文件路径，你再基于结果给出最终答复（含真实文件路径）。",
        "✅ 需要查资料时：",
        "```tool_call",
        '{"name": "web_search", "arguments": {"query": "苏州 8月 天气"}}',
        "```",
        "",
        "═══ 可用工具（name · 描述 · 参数）═══",
    ]
    for t in tools:
        params = t.get("parameters") or {}
        props = params.get("properties") or {}
        ps = ", ".join(
            f"{k}({v.get('type', '')}: {v.get('description', '')})"
            for k, v in props.items()
        ) or "（无参数）"
        lines.append(f"- {t['name']} · {t.get('description', '')} · 参数：{ps}")
    return "\n".join(lines)
