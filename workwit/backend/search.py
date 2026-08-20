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


_WEB_UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
           "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36")


_WEB_HEADERS = {"User-Agent": _WEB_UA, "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8"}


_QUERY_NOISE = re.compile(r"(帮我|请帮|请问|麻烦|我想知道|你能不能|能不能|可以吗|一下|谢谢|thanks?)", re.I)


_QUERY_LEAD_VERB = re.compile(
    r"^(?:帮我|请|麻烦)?\s*(?:查询一下|查一下|查查|查询|查|搜索一下|搜一下|搜搜|搜索|搜"
    r"|看一下|看看|告诉我|说说|讲讲|介绍一下|介绍)\s*")


_QUERY_TIME = re.compile(r"(今日|今天|本日|眼下|现在|目前|当前|最新|近期|最近|实时)")


_QUERY_ASK = re.compile(r"(怎么样|怎么着|怎样|如何|是什么|什么情况|有哪些|多少钱|吗|呢|啊|吧)")


_QUERY_PUNCT = re.compile(r"[?？。！!，,、；;：:\"'“”‘’()（）的]+")


def _clean_query(q, limit=80):
    """把口语化提问转成更适合搜索引擎的查询串。

    分两级清洗：先去口水词/标点，再去时效词与疑问句式；若二级清洗把内容
    削得过短（如「今天几号」被删空），自动回退到一级结果，避免查询失真。
    """
    raw = re.sub(r"\s+", " ", str(q or "")).strip()
    lvl1 = re.sub(r"\s+", " ", _QUERY_PUNCT.sub(" ", _QUERY_NOISE.sub(" ", raw))).strip()
    lvl1 = _QUERY_LEAD_VERB.sub("", lvl1).strip() or lvl1
    lvl2 = re.sub(r"\s+", " ", _QUERY_ASK.sub(" ", _QUERY_TIME.sub(" ", lvl1))).strip()
    out = lvl2 if len(lvl2) >= 2 else lvl1
    # 丢弃清洗残留的孤立单字（"查"/"看"等），它们会被引擎当作独立检索词
    toks = out.split()
    if len(toks) > 1:
        kept = [t for t in toks if len(t) > 1]
        if kept:
            out = " ".join(kept)
    # 天气类查询若只剩"天气"或"X天气"，补充"预报"以提高命中天气网
    if "天气" in out and "预报" not in out:
        if out == "天气" or out.endswith("天气"):
            out = out + "预报"
    return (out or raw)[:limit]


def _http_text(url, timeout=8, data=None, headers=None):
    """抓取 URL 并按响应/页面声明的字符集解码为文本。"""
    req = urllib.request.Request(url, data=data, headers=headers or _WEB_HEADERS)
    with urllib.request.urlopen(req, timeout=timeout) as r:
        raw = r.read()
        ctype = r.headers.get("Content-Type", "") or ""
    enc = None
    m = re.search(r"charset=([\w-]+)", ctype, re.I)
    if m:
        enc = m.group(1)
    else:
        m2 = re.search(rb"charset=[\"']?([\w-]+)", raw[:3000], re.I)
        if m2:
            enc = m2.group(1).decode("ascii", "ignore")
    return raw.decode(enc or "utf-8", "replace")


def _strip_tags(s):
    return html_lib.unescape(re.sub(r"<[^>]+>", "", s or "")).strip()


def _search_bing(query, k):
    """必应 HTML 结果页解析（中国大陆可直连）。"""
    url = "https://cn.bing.com/search?ensearch=0&q=" + urllib.parse.quote(query)
    text = _http_text(url)
    out = []
    for block in re.findall(r'<li class="b_algo".*?</li>', text, re.S):
        m = re.search(r'<h2[^>]*>\s*<a[^>]*href="([^"]+)"', block, re.S)
        h = re.search(r"<h2[^>]*>(.*?)</h2>", block, re.S)
        p = re.search(r"<p[^>]*>(.*?)</p>", block, re.S)
        if not (m and h):
            continue
        title = _strip_tags(h.group(1))
        if not title:
            continue
        out.append({"title": title, "url": m.group(1),
                    "snippet": _strip_tags(p.group(1)) if p else ""})
        if len(out) >= k:
            break
    return out


def _search_sogou(query, k):
    """搜狗 HTML 结果页解析（免 key 二级降级）。"""
    text = _http_text("https://www.sogou.com/web?query=" + urllib.parse.quote(query))
    out = []
    for block in re.findall(r'<div class="vrwrap".*?(?=<div class="vrwrap"|$)', text, re.S):
        m = re.search(r'<h3[^>]*class="vr-title"[^>]*>\s*<a[^>]*href="([^"]+)"[^>]*>(.*?)</a>',
                      block, re.S)
        if not m:
            m = re.search(r'<a[^>]*href="([^"]+)"[^>]*>(.*?)</a>', block, re.S)
        if not m:
            continue
        title = _strip_tags(m.group(2))
        if not title or len(title) < 2:
            continue
        href = m.group(1)
        if href.startswith("/link?"):
            href = "https://www.sogou.com" + href
        sn = re.search(r'<div[^>]*class="[^"]*(?:text-layout|str-text-info|fz-mid)[^"]*"[^>]*>(.*?)</div>',
                       block, re.S)
        out.append({"title": title, "url": href,
                    "snippet": _strip_tags(sn.group(1))[:300] if sn else ""})
        if len(out) >= k:
            break
    return out


def _search_bocha(query, k, key):
    """博查 AI 搜索（国内，为大模型优化的结构化检索）。"""
    data = json.dumps({"query": query, "count": k, "summary": True}).encode("utf-8")
    txt = _http_text("https://api.bochaai.com/v1/web-search", data=data,
                     headers={"Authorization": "Bearer " + key,
                              "Content-Type": "application/json"})
    j = json.loads(txt)
    pages = (((j.get("data") or {}).get("webPages") or {}).get("value") or [])[:k]
    return [{"title": p.get("name", ""), "url": p.get("url", ""),
             "snippet": p.get("summary") or p.get("snippet", "")} for p in pages]


def _search_tavily(query, k, key):
    data = json.dumps({"api_key": key, "query": query, "max_results": k,
                       "search_depth": "basic"}).encode("utf-8")
    txt = _http_text("https://api.tavily.com/search", data=data,
                     headers={"Content-Type": "application/json"})
    j = json.loads(txt)
    return [{"title": i.get("title", ""), "url": i.get("url", ""),
             "snippet": i.get("content", "")} for i in (j.get("results") or [])[:k]]


def _search_serper(query, k, key):
    data = json.dumps({"q": query}).encode("utf-8")
    txt = _http_text("https://google.serper.dev/search", data=data,
                     headers={"X-API-KEY": key, "Content-Type": "application/json"})
    j = json.loads(txt)
    return [{"title": i.get("title", ""), "url": i.get("link", ""),
             "snippet": i.get("snippet", "")} for i in (j.get("organic") or [])[:k]]


def search_web(query, k=5):
    """多引擎联网检索，返回 [{title,url,snippet,engine}]；全部失败返回 []。

    引擎顺序可用环境变量 WEB_SEARCH_ENGINES 覆盖（逗号分隔），例如：
        WEB_SEARCH_ENGINES=bing,sogou
    该函数为同步阻塞 IO，调用方应放线程池并用 asyncio.wait_for 限制总时间。
    """
    import socket
    q = _clean_query(query)
    if not q:
        return []
    order = [s.strip().lower() for s in
             (os.environ.get("WEB_SEARCH_ENGINES") or
              "bocha,tavily,serper,bing,sogou").split(",") if s.strip()]
    keys = {"bocha": os.environ.get("BOCHA_API_KEY"),
            "tavily": os.environ.get("TAVILY_API_KEY"),
            "serper": os.environ.get("SERPER_API_KEY")}
    old_timeout = socket.getdefaulttimeout()
    socket.setdefaulttimeout(8)
    try:
        for eng in order:
            try:
                if eng in keys:
                    if not keys[eng]:
                        continue
                    fn = {"bocha": _search_bocha, "tavily": _search_tavily,
                          "serper": _search_serper}[eng]
                    hits = fn(q, k, keys[eng])
                elif eng == "bing":
                    hits = _search_bing(q, k)
                elif eng == "sogou":
                    hits = _search_sogou(q, k)
                else:
                    continue
                if hits:
                    for h in hits:
                        h["engine"] = eng
                    return hits
            except Exception as e:
                logging.warning("search_web[%s] 失败: %s: %s", eng, type(e).__name__, e)
        return []
    finally:
        socket.setdefaulttimeout(old_timeout)


def fetch_page_text(url, limit=2500, timeout=6):
    """抓取网页正文纯文本（去脚本/样式/标签 + 丢弃导航碎片行）。

    搜索摘要往往只有一两句，缺少具体数值（如天气温度、价格、日期）。
    深读 Top N 结果的正文可显著提升联网回答的准确度。
    """
    import socket
    old_timeout = socket.getdefaulttimeout()
    socket.setdefaulttimeout(timeout)
    try:
        t = _http_text(url, timeout=timeout)
        t = re.sub(r"(?is)<(script|style|noscript|svg|iframe).*?</\1>", " ", t)
        t = re.sub(r"(?s)<!--.*?-->", " ", t)
        t = re.sub(r"<[^>]+>", "\n", t)
        t = html_lib.unescape(t)
        # 逐行降噪：导航菜单多为 1-3 字碎片，正文行普遍 >=4 字
        lines = []
        seen = set()
        for ln in t.split("\n"):
            ln = re.sub(r"\s+", " ", ln).strip()
            if len(ln) < 4 or ln in seen:
                continue
            seen.add(ln)
            lines.append(ln)
        return " ".join(lines)[:limit]
    except Exception as e:
        logging.warning("fetch_page_text 失败 %s: %s", url[:60], e)
        return ""
    finally:
        socket.setdefaulttimeout(old_timeout)

