"""邮件发送模块（SMTP）。

- send_email(...)：读取系统 SMTP 配置后发送邮件，支持 TLS/SSL、抄送、HTML 正文、附件。
- test_smtp(config)：在不真正群发的情况下连接 SMTP 并登录，验证配置是否可用。

配置来源优先级：
  1. 调用方显式传入 config（dict）；
  2. 否则从 db.get_smtp_config(mask_password=False) 读取系统级 SMTP 配置。

返回统一结构：{"ok": bool, "message": str, "detail": str}。
"""
import logging
import os
import smtplib
import ssl
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from email.mime.base import MIMEBase
from email import encoders
from email.header import Header
from email.utils import formataddr
from typing import List, Optional, Union

logger = logging.getLogger("mailer")


def _normalize_addrs(v) -> List[str]:
    """将 字符串/逗号分隔串/列表 统一为去空白的非空收件人列表。"""
    if not v:
        return []
    if isinstance(v, str):
        v = v.split(",")
    out = []
    for x in v:
        x = (x or "").strip()
        if x:
            out.append(x)
    return out


def _load_config(config=None):
    if config:
        cfg = dict(config)
    else:
        from db import get_smtp_config
        cfg = get_smtp_config(mask_password=False) or {}
    if not cfg.get("host"):
        raise RuntimeError("SMTP 服务器未配置（主机为空）。请先在「系统管理 → 邮件服务器」填写 SMTP 信息。")
    if not cfg.get("enabled"):
        raise RuntimeError("SMTP 服务器未启用（enabled=false）。请先在邮件服务器配置中启用。")
    return cfg


def _split_addr(raw):
    """从 '显示名 <addr>' 或 'addr' 提取纯邮箱地址（仅用于 SMTP 信封 MAIL FROM / RCPT TO）。

    smtplib 会对信封地址（from_addr / to_addrs）做 ASCII 编码，若其中残留中文显示名
    （如 '企业AI智能体 <noreply@x.com>'）会触发 UnicodeEncodeError。因此信封层必须只留纯邮箱。
    """
    raw = (raw or "").strip()
    if not raw:
        return raw
    if "<" in raw and raw.endswith(">"):
        return raw[raw.rfind("<") + 1:-1].strip()
    return raw


def _encode_header(raw):
    """把可能含中文的展示值编码为可安全放入邮件头（From/To/Subject 等）的字符串。

    - '显示名 <addr>' 形式用 formataddr 对显示名做 RFC 2047 编码，保留可读名；
    - 纯邮箱或纯名若含非 ASCII，用 email.header.Header 编码；
    - 已是 ASCII 则原样返回。
    """
    raw = (raw or "").strip()
    if not raw:
        return raw
    if "<" in raw and raw.endswith(">"):
        name, addr = raw.rsplit("<", 1)
        name = name.strip().strip('"')
        addr = addr[:-1].strip()
        if name:
            return formataddr((name, addr))
        return addr
    try:
        raw.encode("ascii")
        return raw
    except UnicodeEncodeError:
        return Header(raw, "utf-8").encode()


def _build_message(cfg, to_addrs, subject, body, cc_addrs, html, attachments):
    sender_display = (cfg.get("sender") or cfg.get("username") or "").strip()
    sender_addr = _split_addr(sender_display)
    if not sender_addr or "@" not in sender_addr:
        raise RuntimeError(
            "发件人(sender)未配置或格式错误，必须填写邮箱地址"
            "（可带显示名，如 '名称 <addr@x.com>'）。")
    msg = MIMEMultipart("alternative" if html else "mixed")
    msg["From"] = _encode_header(sender_display)
    msg["To"] = ", ".join(_encode_header(t) for t in to_addrs)
    if cc_addrs:
        msg["Cc"] = ", ".join(_encode_header(c) for c in cc_addrs)
    msg["Subject"] = _encode_header(subject or "(无主题)")

    # 正文
    if html:
        part = MIMEText(body or "", "html", "utf-8")
    else:
        part = MIMEText(body or "", "plain", "utf-8")
    msg.attach(part)

    # 附件
    for att in (attachments or []):
        path = None
        filename = None
        data = None
        if isinstance(att, str):
            path = att
            filename = os.path.basename(att)
        elif isinstance(att, dict):
            path = att.get("path")
            filename = att.get("filename") or (os.path.basename(path) if path else "attachment")
            data = att.get("data")
        if data is None and path and os.path.isfile(path):
            with open(path, "rb") as f:
                data = f.read()
        if data is None:
            logger.warning("邮件附件跳过（不存在或无数据）：%s", filename)
            continue
        mime = MIMEBase("application", "octet-stream")
        mime.set_payload(data)
        encoders.encode_base64(mime)
        # 附件名含中文时用 RFC 2231 编码（filename*=utf-8''...），避免头编码失败
        mime.add_header("Content-Disposition", "attachment", filename=filename or "attachment")
        msg.attach(mime)
    # 信封地址：纯邮箱，供 smtplib.sendmail 使用
    envelope_recipients = [_split_addr(t) for t in to_addrs] + [_split_addr(c) for c in cc_addrs]
    return msg, sender_addr, envelope_recipients


def send_email(to_addrs, subject, body, *, cc_addrs=None, html=False,
               attachments=None, config=None) -> dict:
    """发送一封邮件。

    参数：
      to_addrs:   收件人，字符串(逗号分隔)或列表
      subject:    主题
      body:       正文（纯文本或 HTML，取决于 html）
      cc_addrs:   抄送，字符串(逗号分隔)或列表
      html:       是否 HTML 正文
      attachments: 附件列表，元素可为 文件路径字符串 或 {"filename":..., "path":...} 或 {"filename":..., "data": bytes}
      config:     可选，SMTP 配置 dict；省略则从系统配置读取
    返回：{"ok", "message", "detail"}
    """
    try:
        to_list = _normalize_addrs(to_addrs)
        cc_list = _normalize_addrs(cc_addrs)
        if not to_list:
            return {"ok": False, "message": "收件人不能为空", "detail": ""}
        cfg = _load_config(config)
        msg, sender_addr, recipients = _build_message(cfg, to_list, subject, body, cc_list, html, attachments)

        host = cfg["host"]
        port = int(cfg.get("port") or 0)
        use_ssl = bool(int(cfg.get("use_ssl") or 0))
        use_tls = bool(int(cfg.get("use_tls") or 0))
        timeout = int(cfg.get("timeout") or 30)
        user = (cfg.get("username") or "").strip()
        pwd = cfg.get("password") or ""

        if use_ssl:
            if not port:
                port = 465
            ctx = ssl.create_default_context()
            with smtplib.SMTP_SSL(host, port, timeout=timeout) as s:
                if user:
                    s.login(user, pwd)
                s.sendmail(sender_addr, recipients, msg.as_string())
        else:
            if not port:
                port = 25 if not use_tls else 587
            with smtplib.SMTP(host, port, timeout=timeout) as s:
                s.ehlo()
                if use_tls:
                    s.starttls(context=ssl.create_default_context())
                    s.ehlo()
                if user:
                    s.login(user, pwd)
                s.sendmail(sender_addr, recipients, msg.as_string())
        return {"ok": True,
                "message": f"邮件已发送至 {', '.join(to_list)}",
                "detail": f"主题：{subject or '(无主题)'}"}
    except Exception as e:
        logger.exception("邮件发送失败")
        return {"ok": False, "message": "邮件发送失败", "detail": f"{type(e).__name__}: {e}"}


def test_smtp(config=None) -> dict:
    """连接 SMTP 并（如有账号）登录，验证配置可用性。不发送业务邮件。

    参数：config 可选，省略则从系统配置读取。
    返回：{"ok", "message", "detail"}
    """
    try:
        cfg = _load_config(config)
        host = cfg["host"]
        port = int(cfg.get("port") or 0)
        use_ssl = bool(int(cfg.get("use_ssl") or 0))
        use_tls = bool(int(cfg.get("use_tls") or 0))
        timeout = int(cfg.get("timeout") or 30)
        user = (cfg.get("username") or "").strip()
        pwd = cfg.get("password") or ""
        if use_ssl:
            if not port:
                port = 465
            ctx = ssl.create_default_context()
            with smtplib.SMTP_SSL(host, port, timeout=timeout) as s:
                s.ehlo()
                if user:
                    s.login(user, pwd)
        else:
            if not port:
                port = 25 if not use_tls else 587
            with smtplib.SMTP(host, port, timeout=timeout) as s:
                s.ehlo()
                if use_tls:
                    s.starttls(context=ssl.create_default_context())
                    s.ehlo()
                    if user:
                        s.login(user, pwd)
        return {"ok": True,
                "message": "SMTP 连接测试成功",
                "detail": f"{host}:{port}（{'SSL' if use_ssl else ('STARTTLS' if use_tls else '明文')}）"}
    except Exception as e:
        logger.exception("SMTP 测试失败")
        return {"ok": False, "message": "SMTP 连接测试失败", "detail": f"{type(e).__name__}: {e}"}
