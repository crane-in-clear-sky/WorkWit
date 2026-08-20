"""文生视频（供应商取决于系统配置的 vision 模型：OpenAI Sora / 腾讯云 TCProxy）。"""
import re

META = {
    "name": "generate_video",
    "display_name": "文生视频",
    "category": "multimodal",
    "description": "根据文字描述生成短视频（供应商取决于系统配置的 vision 模型：OpenAI Sora / 腾讯云 TCProxy）。当用户要求'生成视频/文生视频/做个视频/动画/短片'时调用。需系统已配置 role=vision 的模型（含供应商与凭证）。",
    "params": {
        "type": "object",
        "properties": {
            "prompt": {"type": "string", "description": "视频描述（中文）"},
            "duration": {"type": "integer", "description": "时长秒数（可选，默认5）"}
        },
        "required": ["prompt"]
    },
    "backend_type": "builtin", "handler": "generate_video",
    "trigger_words": "生视频,文生视频,生成视频,做视频,视频生成,动画,短片",
    "skip_skill": 1,
}


def run(ctx, prompt, duration=None):
    from db import get_active
    from multimodal_adapters import dispatch
    from builtin_tools._shared import _consume_multimodal
    prompt = (prompt or "").strip()
    if not prompt:
        return "生视频失败：请提供视频描述（prompt）。"
    cfg = get_active("vision") or {}
    if not cfg.get("api_key"):
        return ("生视频失败：系统尚未配置多模态生成凭证。请系统管理员在「模型管理」中新增一条 role=vision 的模型，"
                "选择供应商（OpenAI Sora / 腾讯云）并填写 base_url 与 api_key。")
    provider = cfg.get("provider") or "openai_compatible"
    try:
        res = dispatch(provider, "video", cfg, duration=duration)
    except Exception as e:
        return f"生视频失败：{e}"
    return _consume_multimodal(res, "video", prompt,
                               user_id=ctx.user.get("id") if getattr(ctx, "user", None) else None,
                               session_id=getattr(ctx, "session_id", None))
