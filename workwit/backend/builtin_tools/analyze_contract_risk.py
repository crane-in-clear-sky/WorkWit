"""合同风险分析：分析合同文本中的风险点，返回结构化风险列表。"""
import asyncio

META = {
    "name": "analyze_contract_risk", "display_name": "合同风险分析", "category": "legal",
    "description": "分析合同文本中的风险点，返回结构化风险列表（条款、等级、问题、建议）。",
    "params": {"type": "object",
               "properties": {"text": {"type": "string", "description": "合同全文或片段"}},
               "required": ["text"]},
    "backend_type": "builtin", "handler": "analyze_contract_risk",
    "trigger_words": "合同,风险,条款,审核,合规",
}


async def run(ctx, text):
    from core import _llm_call, parse_json, SYSTEM_PROMPT
    sp = SYSTEM_PROMPT.replace("{party}", "甲方")
    raw = await asyncio.to_thread(_llm_call, ctx.client, ctx.model_name, sp,
                                  "合同文本如下：\n\n" + text, ctx.params)
    return parse_json(raw)
