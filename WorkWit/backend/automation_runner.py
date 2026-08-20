"""
P2⑩ 定时自动化执行器 + 调度器。

- run_automation(aid)：按 owner 身份构造 client/tools/ctx，调用 run_agent_with_retry 执行 prompt，
  结果写入 automation_runs，并回写 last_run/next_run；once 任务执行后置 DONE。
- scheduler_loop()：后台周期扫描到期任务并触发（由 app.py 的 startup 事件拉起）。

依赖：复用既有 run_agent 闭环、ToolContext、build_session_tools 等，不另造运行时。
"""
import asyncio
import traceback
from datetime import datetime

import db
from llm_adapter import create_client, ModelCaps
from core import ToolContext, _model_params
import tools_build
import agent

_RUNNING = set()          # 正在执行的自动化 id，防止并发重复触发
_POLL_SECONDS = 30        # 调度扫描间隔


def _build_run_context(u, notify_email=""):
    """按用户身份构造 (client, name, params, ctx, tools)。复用 chat 端点的工具构建逻辑。

    notify_email：若该自动化任务配置了「完成后邮件通知」收件邮箱，则注入 ctx：
    - send_email 工具的 to 缺省时自动用它兜底（避免 LLM 漏传 to 而失败）
    - 供 system prompt 显式告诉 LLM 收件人是谁，鼓励其主动传 to
    """
    active = db.get_active("chat")
    if not active or not active.get("base_url"):
        raise RuntimeError("未配置主推理模型（自动化无法执行）")
    base = (active.get("base_url") or "").strip()
    key = (active.get("api_key") or "").strip()
    name = (active.get("model_name") or "").strip()
    caps = ModelCaps(active)
    client = create_client(base, key or "not-needed", caps.client_timeout())
    params = _model_params(active)
    params.setdefault("temperature", 0.3)

    ctx = ToolContext(client, name, params, user=u)
    ctx.session_id = "auto:runner"   # 自动化运行上下文隔离标识
    ctx.is_automation = True         # 标记自动化上下文（供 ask_user/反思/handler 守卫使用）
    ctx.max_create_skills = 0        # 自动化运行禁止自行创建技能（防失控）
    ctx.default_notify_email = (notify_email or "").strip()

    tools_lib = db.list_tools(for_user_id=u["id"])
    skills_lib = db.list_skills(for_user_id=u["id"], usable_only=True, with_code=True)
    meta = tools_build.build_meta_tools(ctx)
    # 自动化任务无人在线：从元工具中移除 ask_user，避免 agent 误以为有用户等待回复
    # 而陷入挂起（agent 主循环会 yield final "已向你提出问题..." 让任务结束，
    # 邮件只能收到这条占位提示，没有任何真实结果）。
    meta = [t for t in meta if t.get("name") != "ask_user"]
    tools = (tools_build.build_session_tools(ctx, tools_lib)
             + tools_build.build_session_skill_tools(ctx, skills_lib)
             + meta)
    return client, name, params, ctx, tools


def _notify_by_email(auto, result_text):
    """自动化成功后的邮件通知（best-effort：失败仅记日志，不影响自动化状态）。"""
    recipients = (auto.get("notify_email") or "").strip()
    if not recipients:
        return
    try:
        from mailer import send_email
        subject = "自动化任务完成通知：" + (auto.get("name") or "未命名任务")
        body = (f"自动化任务「{auto.get('name') or '未命名任务'}」已于执行完成。\n\n"
                f"=== 执行结果 ===\n{(result_text or '')[:6000]}\n\n"
                f"（本邮件由企业 AI 办公助手自动发送）")
        res = send_email(recipients, subject, body)
        if res.get("ok"):
            print(f"[自动化邮件通知] 已发送至 {recipients}（任务 {auto.get('id')}）")
        else:
            print(f"[自动化邮件通知] 发送失败：{res.get('detail')}（任务 {auto.get('id')}）")
    except Exception as e:
        print(f"[自动化邮件通知] 异常：{e}（任务 {auto.get('id')}）")


async def run_automation(aid):
    """执行单个自动化任务（幂等：已在运行则跳过）。"""
    if aid in _RUNNING:
        return
    _RUNNING.add(aid)
    try:
        auto = db.get_automation(aid)
        if not auto or auto["status"] != "ACTIVE":
            return
        u = db.get_user_by_id(auto["owner_id"])
        if not u:
            db.record_automation_run(aid, "error", error="owner 用户不存在")
            return

        notify_email = (auto.get("notify_email") or "").strip()
        client, name, params, ctx, tools = _build_run_context(u, notify_email=notify_email)
        # 若配置了收件邮箱，system prompt 显式告知 LLM，鼓励其调 send_email 时把 to 填上
        notify_hint = ""
        if notify_email:
            notify_hint = ("\n\n【本任务收件邮箱】%s。如需在任务执行过程中发邮件推送结果，"
                           "请调用 send_email 工具并将 to 参数填为该邮箱；"
                           "若 to 留空，工具会自动用此邮箱兜底发送。" % notify_email)
        messages = [
            {"role": "system", "content":
                "你是一个自主执行的智能助理，正在按计划执行一个自动化任务。"
                "请独立完成任务，必要时调用可用工具；完成后给出清晰结果。\n\n"
                "【自动化场景须知】这是无人值守的定时任务，没有用户在线等待。"
                "ask_user 等需要用户实时确认的工具已被禁用（无人在线会直接导致任务挂起失败）。"
                "遇到信息不完整时，请：(a) 基于合理默认继续完成任务；"
                "(b) 换用其他可用工具（如 web_search 检索公开信息、send_email 发邮件等）；"
                "(c) 在最终回复中清晰说明无法完成的具体原因及已尝试的步骤。" + notify_hint},
            {"role": "user", "content": auto["prompt"]},
        ]

        final_text = ""
        async for ev in agent.run_agent_with_retry(
                client, name, messages, tools, params,
                max_steps=8, ctx=ctx, caps=None,
                original_question=auto["prompt"], max_attempts=2):
            if ev.get("type") == "final":
                final_text = ev.get("text", "")

        db.record_automation_run(aid, "success", result_text=final_text[:8000])
        # 完成后邮件通知（若配置了收件人）
        _notify_by_email(auto, final_text)
        # once 任务执行一次即结束
        if auto["schedule_type"] == "once":
            db.update_automation(aid, status="DONE")
    except Exception as e:
        tb = traceback.format_exc()
        db.record_automation_run(aid, "error", error=(str(e) + "\n" + tb)[:3000])
    finally:
        _RUNNING.discard(aid)


async def scheduler_loop():
    """后台调度主循环：周期扫描到期且有效的 ACTIVE 任务并触发执行。"""
    while True:
        try:
            now_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            rows = db.list_automations(include_all=True)
            for a in rows:
                if a["status"] != "ACTIVE":
                    continue
                # 有效期校验
                if a.get("valid_from") and a["valid_from"] > now_str:
                    continue
                if a.get("valid_until") and a["valid_until"] < now_str:
                    db.update_automation(a["id"], status="DONE")
                    continue
                # 到点触发（next_run 为空说明未启用调度，跳过）
                if a.get("next_run") and a["next_run"] <= now_str:
                    asyncio.create_task(run_automation(a["id"]))
        except Exception:
            pass
        await asyncio.sleep(_POLL_SECONDS)
