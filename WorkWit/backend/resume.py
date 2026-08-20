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

from core import extract_text, parse_json, _model_params, _sse, SYSTEM_PROMPT, RESUME_PROMPT
from auth import require_login, require_perm, client_ip
from db import get_active, add_log

from fastapi import APIRouter
router = APIRouter()

@router.post("/api/review")
async def review(
    request: Request,
    file: UploadFile = File(...),
    party: str = Form("甲方"),
    base_url: str = Form(""),
    api_key: str = Form(""),
    model_name: str = Form(""),
    requirements: str = Form(""),
):
    u = require_login(request)  # 登录门禁：未登录不能提交审核
    # 预读文件内容到内存：UploadFile 底层临时文件在请求体解析完成后即关闭，
    # 而 SSE 生成器 gen() 是延迟执行的，若在生成器内再 read() 会触发
    # "I/O operation on closed file"。故必须在生成器执行前完成读取。
    file_raw = await file.read()
    filename = file.filename

    async def gen():
        try:
            active = get_active("chat") or {}
            base = (base_url or "").strip() or active.get("base_url", "")
            key = (api_key or "").strip() or active.get("api_key", "")
            name = (model_name or "").strip() or active.get("model_name", "")
            model_params = _model_params(active)
            # max_tokens 完全以「模型配置」界面填写为准：
            #   - 配置 > 0  → 使用配置值
            #   - 配置 0（不限制）→ 不传 max_tokens，交由模型/接口决定上限
            # 不再在代码里写死默认上限（如 4096），避免覆盖管理员的显式配置。
            model_params.setdefault("temperature", 0.1)   # 未配置时沿用原默认 0.1
            if not base:
                yield _sse({"type": "error", "message": "未配置主推理模型",
                            "detail": "请到「系统管理 → 模型配置」启用一个 chat 模型，或在 .env 配置 MODEL_30B_BASE"})
                return
            client = OpenAI(base_url=base, api_key=key or "not-needed", timeout=120)
            yield _sse({"type": "start"})
            yield _sse({"type": "step", "text": "正在解析合同文本…"})
            raw = file_raw
            text = await asyncio.to_thread(extract_text, raw, filename)
            if len(text) > 20000:
                text = text[:20000]
            system_prompt = SYSTEM_PROMPT.replace("{party}", party)
            req = (requirements or "").strip()
            if req:
                system_prompt += "\n\n【额外审核背景与要求】\n" + req
            yield _sse({"type": "step", "text": "已解析，正在调用 AI 模型逐条分析风险点（流式生成中）…"})

            def _stream():
                # 流式调用：边生成边产出增量文本片段，避免一次性长生成阻塞。
                # model_params 完全来自该模型在「模型配置」里显式设置的
                # temperature/max_tokens(top_p/thinking；未配置则不传。
                stream = client.chat.completions.create(
                    model=name,
                    messages=[
                        {"role": "system", "content": system_prompt},
                        {"role": "user", "content": "合同文本如下：\n\n" + text},
                    ],
                    stream=True,
                    **model_params,
                )
                for chunk in stream:
                    if not chunk.choices:
                        continue
                    delta = chunk.choices[0].delta
                    if delta and delta.content:
                        yield delta.content

            content = []
            it = _stream()
            while True:
                try:
                    piece = await asyncio.to_thread(next, it, None)
                except Exception as e:
                    yield _sse({"type": "error", "message": f"{type(e).__name__}: {e}",
                                "detail": traceback.format_exc()})
                    return
                if piece is None:
                    break
                content.append(piece)
                yield _sse({"type": "token", "text": piece})
            full = "".join(content)
            yield _sse({"type": "step", "text": "模型已返回，正在整理结构化结果…"})
            parsed = parse_json(full)
            add_log("contract_review", detail=filename, user=u, ip=client_ip(request))
            yield _sse({"type": "done", "data": parsed})
        except Exception as e:
            yield _sse({"type": "error", "message": f"{type(e).__name__}: {e}",
                        "detail": traceback.format_exc()})

    return StreamingResponse(gen(), media_type="text/event-stream")


logger = logging.getLogger("resume")


def _build_job_block(job_title, age_req, major_req, education_req, exp_years, work_exp_req, project_exp_req, custom_req):
    def fl(label, val):
        val = (val or "").strip()
        return f"- {label}：{val}\n" if val else ""
    job_block = "【招聘岗位画像】\n"
    job_block += fl("岗位名称", job_title)
    job_block += fl("年龄要求", age_req)
    job_block += fl("专业要求", major_req)
    job_block += fl("学历要求", education_req)
    job_block += fl("工作经验", exp_years)
    job_block += fl("工作经历要求", work_exp_req)
    job_block += fl("项目经历要求", project_exp_req)
    job_block += fl("个性化要求", custom_req)
    if job_block.strip() == "【招聘岗位画像】":
        job_block += "- （未提供具体要求，请根据通用标准评估候选人综合素质）\n"
    return job_block


def _build_summary(results):
    counts = {"强烈推荐": 0, "推荐": 0, "待定": 0, "不推荐": 0}
    for r in results:
        if not r.get("error"):
            counts[r.get("recommend", "待定")] = counts.get(r.get("recommend", "待定"), 0) + 1
    valid = sum(counts.values())
    return (
        f"本次共筛选 {valid} 份有效简历：强烈推荐 {counts['强烈推荐']} 份、"
        f"推荐 {counts['推荐']} 份、待定 {counts['待定']} 份、不推荐 {counts['不推荐']} 份。"
        f"建议优先面试前 {counts['强烈推荐'] + counts['推荐']} 份候选人。"
    )


async def _screen_file(client, model_name, job_block, filename, raw, params=None):
    """解析单份简历并调用模型，返回结构化 dict（含 filename）。
    注意：接收 raw bytes 而非 UploadFile 对象，避免 SSE 生成器执行时底层临时文件已关闭。
    params: 来自模型配置的推理参数 dict（temperature/max_tokens/top_p/extra_body）。"""
    params = dict(params or {})
    params.setdefault("temperature", 0.2)  # 未配置时沿用原默认 0.2
    try:
        try:
            text = await asyncio.to_thread(extract_text, raw, filename)
        except Exception as e:
            logger.warning("简历解析失败 [%s]：%s", filename, e)
            return {"filename": filename, "error": f"文件解析失败：{e}"}
        if len(text) > 12000:
            text = text[:12000]
        try:
            resp = await asyncio.to_thread(
                client.chat.completions.create,
                model=model_name,
                messages=[
                    {"role": "system", "content": RESUME_PROMPT},
                    {"role": "user", "content": job_block + f"\n\n【候选人简历：{filename}】\n" + text},
                ],
                **params,
            )
            content = resp.choices[0].message.content
            parsed = parse_json(content)
            parsed["filename"] = filename
            if not isinstance(parsed.get("score"), int) or "name" not in parsed:
                logger.warning("模型返回解析失败 [%s]", filename)
                return {"filename": filename, "error": "模型返回解析失败", "raw": content}
            logger.info("简历评估完成 [%s] -> %s（%s 分，%s）", filename, parsed.get("name"), parsed.get("score"), parsed.get("recommend"))
            return parsed
        except Exception as e:
            logger.error("模型调用失败 [%s]：%s: %s", filename, type(e).__name__, e)
            return {"filename": filename, "error": f"{type(e).__name__}: {e}"}
    except Exception as e:
        return {"filename": filename, "error": f"{type(e).__name__}: {e}"}


@router.post("/api/resume/analyze")
async def resume_analyze(
    request: Request,
    files: List[UploadFile] = File(...),
    job_title: str = Form(""),
    age_req: str = Form(""),
    major_req: str = Form(""),
    education_req: str = Form(""),
    exp_years: str = Form(""),
    work_exp_req: str = Form(""),
    project_exp_req: str = Form(""),
    custom_req: str = Form(""),
    base_url: str = Form(""),
    api_key: str = Form(""),
    model_name: str = Form(""),
):
    """简历筛选助手：上传多份简历 + 结构化岗位画像，逐份调用 chat 模型评估匹配度（一次性返回 JSON）。"""
    u = require_perm("resume", request)  # 登录门禁 + 简历筛选权限
    if not files:
        return JSONResponse(status_code=400, content={"error": "请至少上传一份简历"})
    try:
        active = get_active("chat") or {}
        base = (base_url or "").strip() or active.get("base_url", "")
        key = (api_key or "").strip() or active.get("api_key", "")
        name = (model_name or "").strip() or active.get("model_name", "")
        if not base:
            return JSONResponse(status_code=200, content={
                "error": "未配置主推理模型",
                "message": "请到「系统管理 → 模型配置」启用一个主推理(chat)模型，或在 .env 配置 MODEL_30B_BASE",
            })
        client = OpenAI(base_url=base, api_key=key or "not-needed", timeout=120)
        params = _model_params(active)  # 应用该模型的 temperature/max_tokens/top_p/thinking 配置
        job_block = _build_job_block(job_title, age_req, major_req, education_req, exp_years, work_exp_req, project_exp_req, custom_req)
        # 预读所有文件内容到内存，避免后续异步处理时 UploadFile 底层临时文件已关闭
        file_data = []
        for f in files:
            file_data.append((f.filename, await f.read()))
        results = []
        for i, (fn, raw) in enumerate(file_data):
            logger.info("开始评估 第 %d/%d 份：%s", i + 1, len(file_data), fn)
            results.append(await _screen_file(client, name, job_block, fn, raw, params))
        results.sort(key=lambda x: (x.get("score") or 0), reverse=True)
        summary = _build_summary(results)
        add_log("resume_screen", detail=f"筛选{len(files)}份简历", user=u, ip=client_ip(request))
        return {"results": results, "summary": summary}
    except Exception as e:
        logger.exception("简历筛选整体失败")
        return JSONResponse(status_code=200, content={
            "error": "处理失败", "message": f"{type(e).__name__}: {e}",
        })


@router.post("/api/resume/stream")
async def resume_stream(
    request: Request,
    files: List[UploadFile] = File(...),
    job_title: str = Form(""),
    age_req: str = Form(""),
    major_req: str = Form(""),
    education_req: str = Form(""),
    exp_years: str = Form(""),
    work_exp_req: str = Form(""),
    project_exp_req: str = Form(""),
    custom_req: str = Form(""),
    base_url: str = Form(""),
    api_key: str = Form(""),
    model_name: str = Form(""),
):
    """简历筛选助手（流式）：通过 SSE 实时推送 开始/每文件进度/最终结果，便于前端展示进度条与工作状态。"""
    u = require_perm("resume", request)
    if not files:
        async def _e():
            yield _sse({"type": "error", "error": "请至少上传一份简历"})
        return StreamingResponse(_e(), media_type="text/event-stream")

    active = get_active("chat") or {}
    base = (base_url or "").strip() or active.get("base_url", "")
    key = (api_key or "").strip() or active.get("api_key", "")
    name = (model_name or "").strip() or active.get("model_name", "")
    if not base:
        async def _e():
            yield _sse({"type": "error", "error": "未配置主推理模型",
                        "message": "请到「系统管理 → 模型配置」启用一个 chat 模型"})
        return StreamingResponse(_e(), media_type="text/event-stream")

    client = OpenAI(base_url=base, api_key=key or "not-needed", timeout=120)
    params = _model_params(active)  # 应用该模型的 temperature/max_tokens/top_p/thinking 配置
    job_block = _build_job_block(job_title, age_req, major_req, education_req, exp_years, work_exp_req, project_exp_req, custom_req)
    total = len(files)
    # 预读所有文件内容到内存，避免异步生成器执行时 UploadFile 底层临时文件已关闭
    file_data = []
    for f in files:
        file_data.append((f.filename, await f.read()))

    async def gen():
        yield _sse({"type": "start", "total": total})
        results = []
        for i, (fn, raw) in enumerate(file_data):
            yield _sse({"type": "file", "index": i, "total": total, "filename": fn})
            logger.info("开始评估 第 %d/%d 份：%s", i + 1, total, fn)
            parsed = await _screen_file(client, name, job_block, fn, raw, params)
            results.append(parsed)
            yield _sse({"type": "result", "index": i, "total": total, "data": parsed})
        results.sort(key=lambda x: (x.get("score") or 0), reverse=True)
        summary = _build_summary(results)
        add_log("resume_screen", detail=f"筛选{total}份简历", user=u, ip=client_ip(request))
        yield _sse({"type": "finish", "results": results, "summary": summary})

    return StreamingResponse(gen(), media_type="text/event-stream")

