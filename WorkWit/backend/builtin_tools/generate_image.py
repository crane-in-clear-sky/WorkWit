"""文生图（供应商取决于系统配置的 vision 模型：OpenAI / 国产兼容 / 腾讯云 / 本地）。"""
import os
import re

META = {
    "name": "generate_image",
    "display_name": "文生图",
    "category": "multimodal",
    "description": "根据文字描述生成图片（供应商取决于系统配置的 vision 模型：OpenAI / 国产OpenAI兼容 / 腾讯云TCProxy / 本地SD）。当用户要求'画一张图/生成图片/文生图/画个XXX/AI作画'时调用。需系统已配置 role=vision 的模型（含供应商与凭证）。",
    "params": {
        "type": "object",
        "properties": {
            "prompt": {"type": "string", "description": "图片描述（中文）"},
            "resolution": {"type": "string", "description": "分辨率 '宽:高'，如 1024:1024（可选）"},
            "revise": {"type": "integer", "description": "是否智能改写提示词 1/0（可选，默认1）"}
        },
        "required": ["prompt"]
    },
    "backend_type": "builtin", "handler": "generate_image",
    "trigger_words": "生图,文生图,画图,画一张,生成图片,绘制,图片生成,AI作画",
    "skip_skill": 1,
}


def run(ctx, prompt, resolution=None, revise=None, seed=None):
    from db import get_active
    from multimodal_adapters import dispatch
    from builtin_tools._shared import _consume_multimodal
    prompt = (prompt or "").strip()
    if not prompt:
        return "生图失败：请提供图片描述（prompt）。"
    cfg = get_active("vision") or {}
    if not cfg.get("api_key"):
        return ("生图失败：系统尚未配置多模态生成凭证。请系统管理员在「模型管理」中新增一条 role=vision 的模型，"
                "选择供应商（OpenAI / 国产OpenAI兼容 / 腾讯云 / 本地）并填写 base_url 与 api_key。")
    provider = cfg.get("provider") or "openai_compatible"
    try:
        res = dispatch(provider, "image", cfg, resolution=resolution, revise=revise, seed=seed)
    except Exception as e:
        return f"生图失败：{e}"
    return _consume_multimodal(res, "image", prompt,
                               user_id=ctx.user.get("id") if getattr(ctx, "user", None) else None,
                               session_id=getattr(ctx, "session_id", None))
