"""网页抓取：抓取指定 URL 的正文纯文本（去脚本/样式/导航噪音），可选让 LLM 总结。

与 web_search 互补：search 给『有哪些结果』，fetch 给『某篇结果的完整内容』。
索引到需要先联网拿真实数据、再交给生成类工具时，本工具是『数据准备』的一环——
抓回来的原文要作为 generate_ppt / generate_word / make_chart 的 content，避免凭空编造。
"""
import asyncio

META = {
    "name": "web_fetch", "display_name": "网页抓取", "category": "web",
    "description": (
        "抓取指定网页 URL 的正文内容，去除脚本/样式/导航等噪音，返回干净文本；"
        "可选 summarize=true 让模型对正文做摘要。\n\n"
        "[何时用] ① 已有具体链接（如搜索结果里的某条 url、用户给的网页）需要深读其正文；"
        "② 搜索摘要太短、缺具体数值（温度 / 价格 / 日期），需要读原文补全；"
        "③ 用户要求『读一下这个网页 / 打开链接』。\n"
        "[何时不用] 只想泛泛搜索——请用 web_search；普通聊天不需要。\n\n"
        "[参数] url 必填（须 http/https）；summarize 默认 false（返回原文，便于引用具体段落），"
        "true 时返回模型摘要（适合长文速览）。\n"
        "[示例] web_fetch(url=\"https://example.com/report\", summarize=true)"
    ),
    "params": {"type": "object",
               "properties": {
                   "url": {"type": "string", "description": "要抓取的网页地址，必须以 http:// 或 https:// 开头"},
                   "summarize": {"type": "boolean", "description": "是否对正文做摘要，默认 false（返回原文）", "default": False},
                   "lang": {"type": "string", "description": "摘要输出语言，默认 中文", "default": "中文"}},
               "required": ["url"]},
    "backend_type": "builtin", "handler": "web_fetch",
    "trigger_words": "抓取,打开链接,读网页,抓取网页,提取网页,网页内容,fetch",
}


async def run(ctx, url, summarize=False, lang="中文"):
    url = (url or "").strip()
    if not url:
        return "网页抓取失败：url 不能为空。"
    if not (url.startswith("http://") or url.startswith("https://")):
        return "网页抓取失败：url 须以 http:// 或 https:// 开头（收到：%s）。" % url
    from search import fetch_page_text
    try:
        text = await asyncio.to_thread(fetch_page_text, url, limit=4000, timeout=10)
    except Exception as e:
        return "网页抓取失败：%s: %s" % (type(e).__name__, e)
    if not text or not text.strip():
        return ("网页抓取失败：未能从该页面提取到正文（可能页面需要登录、是纯 JS 渲染、"
                "或已被反爬拦截）。可尝试换一个来源链接，或用 web_search 找其它结果。")
    text = text.strip()
    if summarize:
        try:
            from core import _llm_call
            summary = await asyncio.to_thread(
                _llm_call, ctx.client, ctx.model_name,
                "你是一名专业助理，请用%s对下面的网页正文做简明摘要，突出关键事实与数据，"
                "保留具体数字与来源要点。" % lang,
                text, ctx.params)
            return "网页《%s》摘要：\n%s\n\n（原文长度 %d 字符，已抓取自 %s）" % (
                url, (summary or "").strip(), len(text), url)
        except Exception as e:
            # 摘要失败不静默降级为『成功但空』，而是回退原文并提示
            return ("网页正文已抓取（摘要生成失败：%s），返回原文：\n\n%s\n\n"
                    "（来源：%s，长度 %d 字符）" % (type(e).__name__, text, url, len(text)))
    return "网页正文（来自 %s，长度 %d 字符）：\n\n%s" % (url, len(text), text)
