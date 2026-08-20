"""联网搜索：调用多引擎检索（bing/sogou/bocha/tavily/serper，受环境变量配置驱动），
返回结构化结果 [{title,url,snippet,engine}]，供智能体引用真实来源、避免凭空编造。

[重要] 本工具只负责「检索」，不负责「总结」。检索结果自带 title/url/snippet，
调用方（智能体）应把结果中的具体信息（数字/日期/结论）直接引用进最终回答或传给
generate_ppt / generate_word 等生成类工具，不要仅凭主题自行发挥。
"""
import asyncio

META = {
    "name": "web_search", "display_name": "联网搜索", "category": "web",
    "description": (
        "联网检索指定关键词，返回来自搜索引擎的 {标题, 网址, 摘要, 搜索引擎} 列表。"
        "适用于：查实时资讯 / 天气 / 股价 / 价格 / 新闻 / 政策 / 技术资料等"
        "「模型训练数据之外」或「需要最新来源」的内容。\n\n"
        "[何时用] 用户问题涉及最新事实、具体数据、外部事件，或明确要求『上网查 / 搜一下』时调用。"
        "检索到的 url 与 snippet 是真实来源，请直接引用其中的具体信息，严禁凭主题编造。\n"
        "[何时不用] 纯常识 / 闲聊 / 写代码 / 处理已上传的文件内容——这些不需要联网，误调会拖慢响应。\n\n"
        "[配置] 搜索引擎顺序与密钥由环境变量控制：WEB_SEARCH_ENGINES（如 bing,sogou）、"
        "BOCHA_API_KEY / TAVILY_API_KEY / SERPER_API_KEY。未配置密钥时自动降级到免密引擎（bing/sogou），"
        "若全部不可用则返回『未检索到结果』的明确提示（不会假装搜到了）。\n\n"
        "[示例] web_search(query=\"2026 苏州 8 月天气 预报\", top_k=5)"
    ),
    "params": {"type": "object",
               "properties": {
                   "query": {"type": "string",
                             "description": "搜索关键词/问题。可含口语，工具会自动清洗为检索词（去口水词/标点/疑问词）。"},
                   "top_k": {"type": "integer", "description": "返回结果条数，默认 5，范围 1-10",
                             "default": 5}},
               "required": ["query"]},
    "backend_type": "builtin", "handler": "web_search",
    "trigger_words": "搜索,搜一下,查一下,上网查,联网,谷歌,百度,必应,查资料,最新,实时",
}


async def run(ctx, query, top_k=5):
    if not (query or "").strip():
        return "联网搜索失败：query 不能为空。"
    try:
        top_k = max(1, min(int(top_k or 5), 10))
    except (TypeError, ValueError):
        top_k = 5
    # 惰性导入：search.py 顶层依赖较重（agent/db/openai），避免在工具扫描阶段触发循环导入
    from search import search_web
    try:
        results = await asyncio.to_thread(search_web, query, top_k)
    except Exception as e:
        return "联网搜索失败：%s: %s（请检查网络或 WEB_SEARCH_ENGINES 配置）" % (type(e).__name__, e)
    if not results:
        return ("未检索到相关结果。可能原因：① 未配置联网搜索 API 且免密引擎（bing/sogou）"
                "在当前网络不可用；② 关键词过偏。可尝试：配置 BOCHA_API_KEY/TAVILY_API_KEY "
                "并设 WEB_SEARCH_ENGINES，或更换更通用的关键词。")
    lines = []
    for i, r in enumerate(results, 1):
        lines.append("%d. %s\n   来源：%s\n   链接：%s" % (
            i, (r.get("title") or "").strip(), (r.get("engine") or "unknown"),
            (r.get("url") or "").strip()))
        sn = (r.get("snippet") or "").strip()
        if sn:
            lines.append("   摘要：%s" % sn)
    engines = sorted({r.get("engine") for r in results if r.get("engine")})
    return ("联网搜索结果（共 %d 条，引擎：%s）：\n\n%s\n\n"
            "【引用提示】以上为真实检索来源，请在回答中直接使用其中的具体信息"
            "（数字 / 日期 / 结论），并保留链接以便溯源；切勿脱离来源自行编造。" % (
                len(results), "、".join(engines), "\n".join(lines)))
