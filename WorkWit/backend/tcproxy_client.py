# -*- coding: utf-8 -*-
"""
tcproxy_client - 腾讯云 TCProxy 多模态生成客户端（复用 WorkBuddy 底层协议）。

底层与 WorkBuddy 的 buddy-cloud.py 完全一致（TC3-HMAC-SHA256 签名 + 腾讯云多模态网关），
仅凭证来源不同：WorkBuddy 从桌面会话取 token，本客户端从系统管理端配置的
vision 模型 api_key 读取 token（由 ai-office-mvp 管理员填入）。

功能：文生图（aiart / HunyuanImage 3.0）、文生视频（vclm / AIGC Video）。
零依赖（仅标准库 urllib + hmac + hashlib）。

endpoint 解析优先级：
  1. 显式传入 endpoint
  2. 环境变量 BUDDY_CLOUD_ENDPOINT
  3. vision 模型配置的 base_url（若为 TCProxy 网关则直接用，否则拼 /agenttool/v1/tcproxy）
  4. 默认 https://copilot.tencent.com/agenttool/v1/tcproxy
"""
import base64
import datetime
import hashlib
import hmac
import json
import os
import time
import urllib.error
import urllib.request
from urllib.parse import urlparse

# ---- provider / service 映射（与 buddy-cloud.py 的 _PROVIDER_MAP 解码值一致）----
_PROVIDER = {
    "image": {
        "provider": "hy-aiart", "service": "aiart", "version": "2022-12-29",
        "submit": "SubmitTextToImageJob", "query": "QueryTextToImageJob",
    },
    "video": {
        "provider": "video-effect", "service": "vclm", "version": "2024-05-23",
        "submit": "SubmitAigcVideoJob", "query": "DescribeAigcVideoJob",
    },
}

_REGION = "ap-guangzhou"
_TCPROXY_PATH = "/agenttool/v1/tcproxy"
_FALLBACK = "https://copilot.tencent.com" + _TCPROXY_PATH
_SIGNING_KEY = "codebuddy"  # 与 buddy-cloud.py 内部签名密钥一致


def _resolve_endpoint(vision_base_url=None):
    env = os.environ.get("BUDDY_CLOUD_ENDPOINT")
    if env:
        return env.rstrip("/")
    if vision_base_url and vision_base_url.strip():
        b = vision_base_url.strip().rstrip("/")
        if "tcproxy" in b or "/agenttool" in b:
            return b
        return b + _TCPROXY_PATH
    return _FALLBACK


def _sha256_hex(data):
    return hashlib.sha256(data).hexdigest()


def _hmac(key, msg):
    return hmac.new(key, msg, hashlib.sha256).digest()


def _sign(secret_id, secret_key, service, action, version, region, host, payload, timestamp):
    date = datetime.datetime.fromtimestamp(
        timestamp, tz=datetime.timezone.utc).strftime("%Y-%m-%d")
    ctype = "application/json; charset=utf-8"
    signed = "content-type;host;x-tc-action"
    cheaders = (
        f"content-type:{ctype}\n"
        f"host:{host}\n"
        f"x-tc-action:{action.lower()}\n"
    )
    hashed_payload = _sha256_hex(payload.encode("utf-8"))
    canonical = (
        f"POST\n/\n\n{cheaders}\n{signed}\n{hashed_payload}"
    )
    alg = "TC3-HMAC-SHA256"
    scope = f"{date}/{service}/tc3_request"
    hashed_canonical = _sha256_hex(canonical.encode("utf-8"))
    string_to_sign = f"{alg}\n{timestamp}\n{scope}\n{hashed_canonical}"
    secret_date = _hmac(("TC3" + secret_key).encode("utf-8"), date.encode("utf-8"))
    secret_service = _hmac(secret_date, service.encode("utf-8"))
    secret_signing = _hmac(secret_service, b"tc3_request")
    signature = hmac.new(
        secret_signing, string_to_sign.encode("utf-8"), hashlib.sha256).hexdigest()
    authorization = (
        f"{alg} Credential={secret_id}/{scope}, "
        f"SignedHeaders={signed}, Signature={signature}"
    )
    return {
        "Authorization": authorization,
        "Content-Type": ctype,
        "Host": host,
        "X-TC-Action": action,
        "X-TC-Version": version,
        "X-TC-Region": region,
        "X-TC-Timestamp": str(timestamp),
    }


def _call(endpoint, provider, service, version, action, body, token):
    secret_id = f"{provider}.{token}"
    secret_key = _SIGNING_KEY
    host = urlparse(endpoint).hostname
    payload = json.dumps(body, ensure_ascii=False)
    headers = _sign(secret_id, secret_key, service, action, version,
                    _REGION, host, payload, int(time.time()))
    req = urllib.request.Request(
        endpoint, data=payload.encode("utf-8"), headers=headers, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=120) as resp:
            result = json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        raise RuntimeError(
            f"HTTP {e.code}: {e.read().decode('utf-8', 'replace')[:300]}")
    except Exception as e:
        raise RuntimeError(f"请求多模态服务失败：{e}")
    if "Response" in result:
        inner = result["Response"]
        if "Error" in inner:
            raise RuntimeError(inner["Error"].get("Message", "请求失败"))
        return inner
    if "error" in result:
        raise RuntimeError(result.get("message", "请求失败"))
    return result


def _result_url(res):
    for k in ("ResultImageUrl", "ResultImage", "ResultVideoUrl",
              "ResultUrl", "ModelUrl", "ResultModelUrl"):
        if res.get(k):
            v = res[k]
            return v[0] if isinstance(v, list) else v
    return None


def generate(kind, prompt, token, endpoint=None, **opts):
    """生成图片/视频。

    kind: 'image' | 'video'
    prompt: 描述文本
    token: 腾讯云多模态 token（来自 vision 模型 api_key）
    endpoint: 可选覆盖网关地址
    返回最终结果 dict（含 ResultImageUrl / ResultVideoUrl 等）。
    """
    if kind not in _PROVIDER:
        raise ValueError("kind 必须是 image 或 video")
    if not token:
        raise RuntimeError("缺少多模态 token（请在 vision 模型配置中填写 api_key）")
    cfg = _PROVIDER[kind]
    ep = endpoint or _resolve_endpoint(opts.get("vision_base_url"))

    if kind == "image":
        body = {"Prompt": prompt}
        if opts.get("resolution"):
            body["Resolution"] = opts["resolution"]
        if opts.get("revise") is not None:
            body["Revise"] = int(opts["revise"])
        if opts.get("seed") is not None:
            body["Seed"] = int(opts["seed"])
    else:
        body = {
            "Vendor": "Kling",
            "Model": "v2.6",
            "Prompt": prompt,
            "ModelParam": json.dumps({"Duration": int(opts.get("duration", 5))}),
            "LogoAdd": 1,
        }

    submit = _call(ep, cfg["provider"], cfg["service"], cfg["version"],
                   cfg["submit"], body, token)
    job_id = submit.get("JobId")
    if not job_id:
        url = _result_url(submit)
        if url:
            return submit
        raise RuntimeError("服务未返回 JobId，请求可能被拒绝（检查 token 与 prompt）")

    # 异步轮询
    deadline = time.time() + int(opts.get("max_poll_time", 600))
    interval = int(opts.get("poll_interval", 5))
    while True:
        if time.time() > deadline:
            raise RuntimeError("生成超时（轮询超过上限，任务可能仍在后台运行）")
        res = _call(ep, cfg["provider"], cfg["service"], cfg["version"],
                    cfg["query"], {"JobId": job_id}, token)
        status = res.get("Status", "")
        code = res.get("JobStatusCode")
        try:
            code = int(code) if code is not None else None
        except Exception:
            code = None
        if status == "DONE" or code == 5:
            return res
        if status == "FAIL" or code == 4:
            raise RuntimeError(res.get("ErrorMessage", res.get("JobErrorMsg", "生成失败")))
        time.sleep(interval)
