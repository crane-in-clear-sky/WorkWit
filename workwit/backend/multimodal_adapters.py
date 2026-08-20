# -*- coding: utf-8 -*-
"""
multimodal_adapters - 多供应商可插拔的多模态生成适配器。

设计目标：生图/生视频不再写死腾讯云，而是按 vision 模型配置的 `provider`
分发到对应适配器。系统内置 4 种 adapter：
  - openai            : OpenAI 官方（DALL·E / gpt-image-1 文生图；Sora 文生视频）
  - openai_compatible : 国产 OpenAI 兼容网关（通义万相/智谱CogView/火山方舟/阶跃等），仅文生图
  - tencent           : 腾讯云 TCProxy（复用 WorkBuddy 多模态底层，文生图+文生视频）
  - local             : 本地/自建（SD WebUI txt2img 文生图；文生视频暂不支持）

新增供应商只需在 REGISTRY 注册一个 `(mode, prompt, cfg, **opts) -> {url|b64|ext}` 的函数，
handler 层无需改动。

cfg 结构（来自 vision 模型记录）：
  { "provider": str, "base_url": str, "api_key": str, "model_name": str, "extra": dict|str }
adapter 返回归一化 dict：
  { "url": <远程地址> } 或 { "b64": <base64字符串>, "ext": ".png" }
handler 负责把 url 下载 / 把 b64 解码写入产物目录并呈现下载卡片。
"""
import base64
import json
import time
import urllib.error
import urllib.request

_BUILTIN_PROVIDERS = ("openai", "openai_compatible", "tencent", "local")


def _norm_extra(cfg):
    extra = cfg.get("extra")
    if isinstance(extra, dict):
        return extra
    if isinstance(extra, str) and extra.strip():
        try:
            return json.loads(extra)
        except Exception:
            return {}
    return {}


def _http_json(method, url, body, api_key):
    data = json.dumps(body, ensure_ascii=False).encode("utf-8") if body is not None else None
    headers = {"Content-Type": "application/json"}
    if api_key:
        headers["Authorization"] = "Bearer " + api_key
    req = urllib.request.Request(url, data=data, headers=headers, method=method)
    try:
        with urllib.request.urlopen(req, timeout=180) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        raise RuntimeError(f"HTTP {e.code}: {e.read().decode('utf-8', 'replace')[:300]}")
    except Exception as e:
        raise RuntimeError(f"请求多模态服务失败：{e}")


# ----------------------------------------------------------------------------
# OpenAI 风格（官方 + 国产兼容共用实现，allow_video 区分）
# ----------------------------------------------------------------------------
def _openai_style(mode, prompt, cfg, allow_video=True, **opts):
    base = (cfg.get("base_url") or "").strip().rstrip("/")
    api_key = cfg.get("api_key") or ""
    extra = _norm_extra(cfg)
    if mode == "image":
        endpoint = (base + "/images/generations") if base else "https://api.openai.com/v1/images/generations"
        model = cfg.get("model_name") or "gpt-image-1"
        body = {
            "model": model,
            "prompt": prompt,
            "n": 1,
            "size": extra.get("size", "1024x1024"),
            "response_format": "b64_json",
        }
        r = _http_json("POST", endpoint, body, api_key)
        data = (r.get("data") or [])
        if not data:
            raise RuntimeError("OpenAI 未返回图像数据：" + str(r)[:200])
        item = data[0]
        if item.get("b64_json"):
            return {"b64": item["b64_json"], "ext": ".png"}
        if item.get("url"):
            return {"url": item["url"]}
        raise RuntimeError("OpenAI 返回结构异常：" + str(r)[:200])

    # ---- video ----
    if not allow_video:
        raise RuntimeError(
            "当前供应商(openai_compatible)不支持文生视频；如需视频请配置腾讯云 TCProxy 或 OpenAI 官方(Sora)。")
    endpoint = (base + "/videos/generations") if base else "https://api.openai.com/v1/videos/generations"
    model = cfg.get("model_name") or "sora"
    body = {"model": model, "prompt": prompt, "n": 1}
    if opts.get("duration"):
        body["duration"] = int(opts["duration"])
    r = _http_json("POST", endpoint, body, api_key)
    vid_id = r.get("id")
    if not vid_id:
        if r.get("url"):
            return {"url": r["url"]}
        if r.get("b64_json"):
            return {"b64": r["b64_json"], "ext": ".mp4"}
        raise RuntimeError("OpenAI 视频未返回任务 id：" + str(r)[:200])
    status_url = (base + f"/videos/{vid_id}") if base else f"https://api.openai.com/v1/videos/{vid_id}"
    deadline = time.time() + 600
    while time.time() < deadline:
        s = _http_json("GET", status_url, None, api_key)
        if s.get("status") == "completed" or s.get("url") or s.get("b64_json"):
            if s.get("url"):
                return {"url": s["url"]}
            if s.get("b64_json"):
                return {"b64": s["b64_json"], "ext": ".mp4"}
            nested = (s.get("data") or [{}])[0]
            if nested.get("url"):
                return {"url": nested["url"]}
            if nested.get("b64_json"):
                return {"b64": nested["b64_json"], "ext": ".mp4"}
        if s.get("status") == "failed":
            raise RuntimeError("OpenAI 视频生成失败：" + str(s)[:200])
        time.sleep(5)
    raise RuntimeError("OpenAI 视频生成超时（轮询超过 600s）")


def _openai_adapter(mode, prompt, cfg, **opts):
    return _openai_style(mode, prompt, cfg, allow_video=True, **opts)


def _openai_compatible_adapter(mode, prompt, cfg, **opts):
    return _openai_style(mode, prompt, cfg, allow_video=False, **opts)


# ----------------------------------------------------------------------------
# 腾讯云 TCProxy（复用 WorkBuddy 多模态底层）
# ----------------------------------------------------------------------------
def _tencent_adapter(mode, prompt, cfg, **opts):
    from tcproxy_client import generate as _tc_gen, _resolve_endpoint, _result_url
    token = cfg.get("api_key") or ""
    if not token:
        raise RuntimeError("腾讯云供应商需要 api_key（多模态 token）。")
    ep = _resolve_endpoint(cfg.get("base_url")) if cfg.get("base_url") else None
    res = _tc_gen(
        mode, prompt, token, endpoint=ep,
        resolution=opts.get("resolution"),
        revise=opts.get("revise"),
        seed=opts.get("seed"),
        duration=opts.get("duration"),
        vision_base_url=cfg.get("base_url"),
    )
    url = _result_url(res)
    if url:
        return {"url": url}
    raise RuntimeError("腾讯云未返回结果地址：" + str(res)[:200])


# ----------------------------------------------------------------------------
# 本地 / 自建（SD WebUI txt2img 文生图；视频暂不支持）
# ----------------------------------------------------------------------------
def _local_adapter(mode, prompt, cfg, **opts):
    base = (cfg.get("base_url") or "").strip().rstrip("/")
    if not base:
        raise RuntimeError("本地多模态需配置 base_url（如 http://host:7860）。")
    extra = _norm_extra(cfg)
    if mode == "image":
        endpoint = base + "/sdapi/v1/txt2img"
        body = {
            "prompt": prompt,
            "steps": int(extra.get("steps", 20)),
            "cfg_scale": float(extra.get("cfg_scale", 7.0)),
            "width": int(extra.get("width", 512)),
            "height": int(extra.get("height", 512)),
            "sampler_name": extra.get("sampler", "DPM++ 2M Karras"),
            "n_iter": 1,
            "batch_size": 1,
        }
        r = _http_json("POST", endpoint, body, None)
        imgs = r.get("images") or []
        if not imgs:
            raise RuntimeError("SD WebUI 未返回图像：" + str(r)[:200])
        return {"b64": imgs[0], "ext": ".png"}
    raise RuntimeError(
        "本地适配器当前仅支持文生图（SD WebUI txt2img）。文生视频请配置腾讯云 TCProxy 或 OpenAI Sora。")


REGISTRY = {
    "openai": _openai_adapter,
    "openai_compatible": _openai_compatible_adapter,
    "tencent": _tencent_adapter,
    "local": _local_adapter,
}


def dispatch(provider, mode, prompt, cfg, **opts):
    """按 provider 分发到对应 adapter。

    provider: str（openai/openai_compatible/tencent/local）
    mode: 'image' | 'video'
    cfg: vision 模型记录字典
    返回归一化 dict {url:..} 或 {b64:.., ext:..}
    """
    if mode not in ("image", "video"):
        raise ValueError("mode 必须是 image 或 video")
    provider = (provider or "openai_compatible").lower().strip()
    if provider not in REGISTRY:
        raise RuntimeError(
            f"不支持的多模态供应商：{provider}。系统内置：{', '.join(_BUILTIN_PROVIDERS)}；"
            f"如需其他供应商，请由管理员在代码 REGISTRY 中注册对应适配器。")
    return REGISTRY[provider](mode, prompt, cfg, **opts)


def supported_providers():
    return list(_BUILTIN_PROVIDERS)
