"""内置工具共享 helper（私有模块，以下划线开头，不单独注册为工具）。

仅依赖标准库；供 builtin_tools 内各工具文件 import 使用，避免与 tools_handlers 循环依赖。
"""
import base64
import os
import re
import tempfile
import uuid
import urllib.request


def _artifact_root():
    """返回产物落盘目录（优先 SKILL_ARTIFACT_ROOT 环境变量，其次 /app/data/artifacts，最后临时目录）。"""
    _art = os.environ.get("SKILL_ARTIFACT_ROOT")
    if _art and os.path.isdir(os.path.dirname(_art) or "."):
        return _art
    if os.path.isdir("/app/data"):
        return "/app/data/artifacts"
    return tempfile.gettempdir()


def _upload_root():
    """返回上传附件根目录（UPLOAD_ROOT 可配，默认 /app/data/uploads）。"""
    return os.environ.get("UPLOAD_ROOT") or "/app/data/uploads"


def _safe_id(x):
    """归一化 user_id / session_id 为安全目录名（防路径穿越）。"""
    if x is None or x == "":
        return ""
    return re.sub(r"[^\w\-]", "_", str(x))[:64]


def _user_dir(user_id, session_id=None, kind="artifacts"):
    """返回某用户（可选会话）的产物/上传目录：<root>/<user_id>/<session_id>/。

    user_id 为空时退回全局根（兼容旧行为 / 无用户场景）。kind ∈ {artifacts, uploads}。
    目录不在此创建，由调用方按需 makedirs。
    """
    base = _upload_root() if kind == "uploads" else _artifact_root()
    uid = _safe_id(user_id)
    if not uid:
        return base
    d = os.path.join(base, uid)
    sid = _safe_id(session_id)
    if sid:
        d = os.path.join(d, sid)
    return d


def _file_belongs_to(user, realpath):
    """判断文件是否归属当前用户。管理员放行全部；普通用户要求文件落在其 <user_id>/ 子目录下。

    旧平铺文件（直接位于 artifacts/uploads 根下、无 <user_id>/ 段）不归属任何普通用户 → 拒绝。
    """
    if user and user.get("role") == "admin":
        return True
    uid = _safe_id(user.get("id") if user else None)
    if not uid:
        return False
    real = os.path.normcase(os.path.realpath(realpath))
    for base in (_artifact_root(), _upload_root()):
        udir = os.path.normcase(os.path.realpath(os.path.join(base, uid)))
        if real == udir or real.startswith(udir + os.sep):
            return True
    return False


def _ppt_slides_from_content(topic, content, style="business_blue"):
    """将纯文本/Markdown 内容解析为 slides 结构（content 类型页：title + items 要点）。

    分页优先级：① 显式 '---' 分隔符；② Markdown 标题行（#/##/### 开头）；③ 空行分段。
    返回 None 表示无内容（调用方回退默认模板）。
    """
    raw = (content or "").strip()
    if not raw:
        return None
    slides = {"style": style, "page_numbers": True,
              "slides": [{"type": "cover", "title": topic, "subtitle": "AI 智能体自动生成"}]}
    # 1) 显式分页符
    if "---" in raw:
        blocks = [b.strip() for b in raw.split("---") if b.strip()]
    else:
        # 2) 按 Markdown 标题分页
        blocks = [p.strip() for p in re.split(r"\n(?=#{1,3}\s)", raw) if p.strip()]
    for blk in blocks:
        lines = [l.rstrip() for l in blk.split("\n") if l.strip()]
        if not lines:
            continue
        title = re.sub(r"^#{1,3}\s*", "", lines[0]).strip() or topic
        items = [re.sub(r"^[-*]\s*", "", l).strip() for l in lines[1:] if l.strip()]
        if not items:
            items = [title]
        slides["slides"].append({
            "type": "content",
            "title": (title[:40] or "内容"),
            "items": items[:10],
        })
    return slides


def _write_artifact_bytes(data, ext, kind, prompt, user_id=None, session_id=None):
    """把字节写入（用户隔离的）产物目录，返回本地路径。"""
    root = _user_dir(user_id, session_id, "artifacts")
    try:
        os.makedirs(root, exist_ok=True)
    except Exception:
        root = tempfile.gettempdir()
    _allowed = (".png", ".jpg", ".jpeg", ".gif", ".mp4", ".webm", ".mov", ".avi")
    if ext not in _allowed:
        ext = ".png" if kind == "image" else ".mp4"
    _safe = re.sub(r"\W+", "_", (prompt or (kind + "生成"))[:30]) or "gen"
    dst = os.path.join(root, f"{_safe}_{uuid.uuid4().hex[:8]}{ext}")
    with open(dst, "wb") as f:
        f.write(data)
    return dst


def _format_artifact(path, kind, prompt):
    _label = (prompt or "")[:24]
    return f"{'图片' if kind == 'image' else '视频'}已生成（{_label}）：\n已生成：{path}"


def _download_multimodal(url, kind, prompt, user_id=None, session_id=None):
    """下载远程文件到（用户隔离的）产物目录，返回含「已生成：<path>」的文本。"""
    try:
        data = urllib.request.urlopen(url, timeout=180).read()
    except Exception as e:
        return (f"{'图片' if kind == 'image' else '视频'}已生成，但下载到本地失败：{e}\n"
                f"在线预览：{url}")
    if not data:
        return (f"{'图片' if kind == 'image' else '视频'}已生成，但本地未取到文件。\n"
                f"在线预览：{url}")
    _base = url.split("?")[0].rsplit("/", 1)[-1]
    ext = os.path.splitext(_base)[1].lower() if "." in _base else ""
    path = _write_artifact_bytes(data, ext, kind, prompt, user_id=user_id, session_id=session_id)
    return _format_artifact(path, kind, prompt)


def _consume_multimodal(res, kind, prompt, user_id=None, session_id=None):
    """把 adapter 返回的 {url|b64|ext} 转成（用户隔离的）本地产物文本。"""
    if not isinstance(res, dict):
        return f"{'图片' if kind == 'image' else '视频'}生成失败：服务返回异常。"
    if res.get("b64"):
        try:
            data = base64.b64decode(res["b64"])
        except Exception as e:
            return f"{'图片' if kind == 'image' else '视频'}生成失败：结果解码失败：{e}"
        path = _write_artifact_bytes(data, res.get("ext", ".png"), kind, prompt,
                                     user_id=user_id, session_id=session_id)
        return _format_artifact(path, kind, prompt)
    if res.get("url"):
        return _download_multimodal(res["url"], kind, prompt, user_id=user_id, session_id=session_id)
    return f"{'图片' if kind == 'image' else '视频'}生成失败：服务未返回可用结果。"
