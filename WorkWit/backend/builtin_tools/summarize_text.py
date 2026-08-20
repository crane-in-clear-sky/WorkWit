"""文本摘要：对较长文本做简明摘要，提取关键要点。"""
import asyncio

META = {
    "name": "summarize_text", "display_name": "文本摘要", "category": "text",
    "description": "对较长文本做简明摘要，提取关键要点。",
    "params": {"type": "object",
               "properties": {"text": {"type": "string", "description": "要摘要的文本"},
                              "lang": {"type": "string", "description": "输出语言，默认中文"}},
               "required": ["text"]},
    "backend_type": "builtin", "handler": "summarize_text",
    "trigger_words": "总结,摘要,提炼,归纳,要点",
}


async def run(ctx, text, lang="中文"):
    from core import _llm_call
    return await asyncio.to_thread(
        _llm_call, ctx.client, ctx.model_name,
        f"你是一名专业助理，请用{lang}对下面的内容做简明摘要，突出关键要点。",
        text, ctx.params)
