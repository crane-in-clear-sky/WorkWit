"""读取文档：读取服务器上已存在的 txt / docx / pdf 文件，抽取纯文本返回。

本工具是「数据准备」的关键一环（呼应 PPT 教训）：当用户提供了文档资料、或此前步骤
已经生成了文档时，智能体应先用本工具把文档正文读出来，再把正文作为 generate_ppt /
generate_word / make_chart 的 content 传进去——而不是只凭主题自行编造内容。

[安全边界] 仅允许读取 .txt/.docx/.pdf 三类扩展名、且位于「产物目录 / data 目录 / 工作目录」
内的普通文件；系统目录与 .db/.key/.env 等敏感文件天然被扩展名白名单排除。
"""
import os

from builtin_tools._shared import _artifact_root, _file_belongs_to

META = {
    "name": "read_document", "display_name": "读取文档", "category": "io",
    "description": (
        "读取服务器上已存在的文档（txt / docx / pdf），抽取纯文本返回，供智能体把「真实输入资料」"
        "喂给生成类工具。\n\n"
        "[何时用] ① 用户提供了文档路径、或此前某步骤已生成文档，需要读取其正文；"
        "② 生成 PPT / Word / 图表之前，先把用户给的真实资料读出来作为 content（避免凭空编造）。\n"
        "[何时不用] 需要联网查资料请用 web_search / web_fetch；普通聊天不需要。\n\n"
        "[参数] path 必填：文档的绝对路径，或相对产物目录的路径（仅支持 .txt/.docx/.pdf）；"
        "max_chars 可选，返回文本的最大字符数（默认 8000，超出截断并提示）。\n"
        "[示例] read_document(path=\"/app/data/artifacts/项目资料.docx\")"
    ),
    "params": {"type": "object",
               "properties": {
                   "path": {"type": "string",
                            "description": "要读取的文档路径（绝对路径或相对产物目录路径），仅支持 .txt/.docx/.pdf"},
                   "max_chars": {"type": "integer",
                                 "description": "返回文本的最大字符数，默认 8000，超出截断并提示",
                                 "default": 8000}},
               "required": ["path"]},
    "backend_type": "builtin", "handler": "read_document",
    "trigger_words": "读文件,读取文档,读一下,打开文件,读取,文档内容,提取文档,read,extract",
}


_ALLOWED_EXTS = (".txt", ".docx", ".pdf")
_MAX_BYTES = 20 * 1024 * 1024  # 20MB


def _resolve_path(path):
    """把用户给的 path 解析为真实绝对路径并做安全校验。返回 (real_path, err_msg)。"""
    if not path or not str(path).strip():
        return None, "path 不能为空。"
    path = str(path).strip()
    # 相对路径 → 相对产物目录解析（与生成类工具落盘目录一致）
    if not os.path.isabs(path):
        path = os.path.join(_artifact_root(), path)
    try:
        real = os.path.realpath(os.path.abspath(path))
    except Exception as e:
        return None, "路径解析失败：%s" % e
    # ① 扩展名白名单（同时天然排除 .db/.key/.env/.py 等敏感文件）
    ext = os.path.splitext(real)[1].lower()
    if ext not in _ALLOWED_EXTS:
        return None, "仅支持读取 txt / docx / pdf 文件（收到扩展名：%s）。" % (ext or "无")
    # ② 允许目录校验：仅产物目录 / data 目录 / 工作目录
    roots = [_artifact_root(), "/app/data", os.getcwd()]
    allowed = {os.path.normcase(os.path.realpath(r)) for r in roots if os.path.isdir(r)}
    rc = os.path.normcase(real)
    if allowed and not any(rc == a or rc.startswith(a + os.sep) for a in allowed):
        return None, ("出于安全，仅允许读取产物目录 / data 目录 / 工作目录下的文档（收到：%s）。"
                      "如需读取其它位置的文档，请先将其放入允许目录。" % real)
    return real, None


async def run(ctx, path, max_chars=8000):
    real, err = _resolve_path(path)
    if err:
        return "读取文档失败：%s" % err
    # 用户归属校验：普通用户只能读自己 <user_id>/ 子目录下的文件（管理员放行；旧平铺无主文件拒绝）
    if not _file_belongs_to(getattr(ctx, "user", None), real):
        return "读取文档失败：无权访问该文件（仅能读取本人上传/生成的文件）。"
    if not os.path.isfile(real):
        return "读取文档失败：文件不存在：%s" % real
    try:
        size = os.path.getsize(real)
    except OSError as e:
        return "读取文档失败：无法获取文件信息：%s" % e
    if size > _MAX_BYTES:
        return "读取文档失败：文件过大（%.1f MB，上限 20MB）。" % (size / 1048576.0)
    try:
        with open(real, "rb") as f:
            raw = f.read()
    except Exception as e:
        return "读取文档失败：%s: %s" % (type(e).__name__, e)
    # 惰性导入：core.py 顶层依赖较重（docx/pypdf/openai），避免扫描阶段循环导入
    from core import extract_text
    try:
        text = extract_text(raw, os.path.basename(real))
    except Exception as e:
        return "读取文档失败：%s: %s" % (type(e).__name__, e)
    if not text or not text.strip():
        return "读取文档失败：文档内容为空或无法解析出文本：%s" % real
    text = text.strip()
    total = len(text)
    try:
        max_chars = max(100, int(max_chars or 8000))
    except (TypeError, ValueError):
        max_chars = 8000
    truncated = total > max_chars
    if truncated:
        text = text[:max_chars]
    return ("文档内容（%s，共 %d 字符%s）：\n\n%s" % (
        os.path.basename(real), total,
        ("，已截断至 %d 字符" % max_chars) if truncated else "",
        text))
