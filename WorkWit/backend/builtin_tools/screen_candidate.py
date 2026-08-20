"""简历评估：根据简历文本与岗位要求评估候选人匹配度，返回结构化评估结果。"""
import asyncio

META = {
    "name": "screen_candidate", "display_name": "简历评估", "category": "hr",
    "description": "根据简历文本与岗位要求评估候选人匹配度，返回结构化评估结果。",
    "params": {"type": "object",
               "properties": {"resume_text": {"type": "string", "description": "候选人简历文本"},
                              "job_text": {"type": "string", "description": "岗位要求（可选）"}},
               "required": ["resume_text"]},
    "backend_type": "builtin", "handler": "screen_candidate",
    "trigger_words": "简历,候选人,招聘,评估,筛选,面试",
}


async def run(ctx, resume_text, job_text=""):
    from core import _llm_call, parse_json, RESUME_PROMPT
    jb = job_text.strip() or "【招聘岗位画像】\n- （未提供具体要求，请根据通用标准评估候选人综合素质）"
    raw = await asyncio.to_thread(
        _llm_call, ctx.client, ctx.model_name, "",
        RESUME_PROMPT + "\n\n" + jb + f"\n\n【候选人简历】\n" + resume_text, ctx.params)
    return parse_json(raw)
