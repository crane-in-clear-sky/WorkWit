"""发送邮件：调用系统已配置的 SMTP 服务器发送一封邮件。

依赖系统管理中的「邮件服务器（SMTP）」配置；未配置或不可用时返回明确错误，不假装发送成功。
这是把「邮件能力」暴露给智能体/自动化任务的入口——自动化提示词里只要让智能体
「把结果发邮件给 xxx@yyy.com」即可调用本工具。
"""
import json

META = {
    "name": "send_email", "display_name": "发送邮件", "category": "notify",
    "description": (
        "通过系统已配置的 SMTP 邮件服务器发送一封邮件。\n\n"
        "[何时用] 用户或自动化任务要求『发邮件 / 通知某人 / 把结果发到邮箱 / 定时邮件推送』时调用；"
        "典型场景：自动化任务跑完把简报发到指定邮箱、把生成的文档链接/摘要邮件通知团队成员。\n"
        "[何时不用] 站内消息 / 聊天回复 / 不涉及真实邮箱收件人的场景——这些不需要发邮件，误调会骚扰收件人。\n\n"
        "[前置条件] 系统管理员需先在「系统管理 → 邮件服务器」配置并启用 SMTP，否则会返回明确的『SMTP 未配置』错误。\n\n"
        "[参数说明]\n"
        "  - to：必填，收件人邮箱，多个用逗号分隔（如 a@x.com,b@y.com）\n"
        "  - subject：必填，邮件主题\n"
        "  - body：必填，邮件正文（纯文本）\n"
        "  - cc：选填，抄送邮箱，逗号分隔\n"
        "  - html：选填，是否把 body 当作 HTML 渲染（默认 false 纯文本）\n\n"
        "[示例] send_email(to=\"boss@company.com\", subject=\"每日销售简报\", body=\"今日销售额 120 万，详见附件。\")"
    ),
    "params": {"type": "object",
               "properties": {
                   "to": {"type": "string",
                          "description": "收件人邮箱，多个用逗号分隔。必填。"},
                   "subject": {"type": "string", "description": "邮件主题。必填。"},
                   "body": {"type": "string", "description": "邮件正文（纯文本或 HTML，取决于 html 参数）。必填。"},
                   "cc": {"type": "string", "description": "抄送邮箱，多个用逗号分隔。选填。"},
                   "html": {"type": "boolean", "description": "是否将 body 作为 HTML 渲染。默认 false（纯文本）。",
                            "default": False}},
               "required": ["to", "subject", "body"]},
    "backend_type": "builtin", "handler": "send_email",
    "trigger_words": "发邮件,发信,邮件通知,通知邮箱,邮件推送,发送通知,Email,email,mail",
}


async def run(ctx, to, subject, body, cc="", html=False):
    to = (to or "").strip()
    subject = (subject or "").strip()
    body = body or ""
    # 自动化场景兜底：若 LLM 漏传 to，且 ctx 上挂了 default_notify_email（来自自动化任务配置），
    # 自动用其作为收件人。这让"天气预报推送到邮箱"这类指令无需 LLM 知晓具体收件地址。
    if not to and getattr(ctx, "is_automation", False):
        default_to = (getattr(ctx, "default_notify_email", "") or "").strip()
        if default_to:
            to = default_to
    if not to:
        return "发送邮件失败：收件人(to)不能为空。"
    if not subject:
        return "发送邮件失败：邮件主题(subject)不能为空。"
    # 惰性导入，避免在工具扫描阶段触发 db 的循环导入/初始化副作用
    from mailer import send_email
    try:
        res = send_email(to, subject, body, cc_addrs=cc, html=bool(html))
    except Exception as e:
        return "发送邮件失败：%s: %s" % (type(e).__name__, e)
    if res.get("ok"):
        # 含「已发送」便于闭环/产物识别；同时给出详情
        return "邮件已发送：%s\n%s" % (res.get("message", ""), res.get("detail", ""))
    return "发送邮件失败：%s（%s）" % (res.get("message", ""), res.get("detail", ""))
