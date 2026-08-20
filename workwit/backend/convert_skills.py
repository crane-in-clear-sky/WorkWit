# -*- coding: utf-8 -*-
"""将 workbuddy_builtin_skills_full.md 解析出的 46 个技能 → builtin_skills/<name>/SKILL.md。

形态（与 memory「已确立形态」一致）：
- 每个技能一个目录 backend/builtin_skills/<internal_name>/SKILL.md
- SKILL.md = YAML frontmatter（name/display_name/description/category/trigger_words/
  skill_type/when_to_use/allowed_tools/source_name/create_source）+ 完整正文（方法论全文）
- skill_type 一律 method（方法论技能，不执行代码）；能引用系统工具的通过
  allowed_tools 引用（如 tencent-pptx→generate_ppt、写作专家→generate_word）
- DB seed 时 instructions 用「精简短版」（完整正文存在文件里，避免注入 system prompt 爆上下文）
"""
import json
import os
import re

BASE = os.path.dirname(os.path.abspath(__file__))
PARSED = os.path.join(BASE, "_skills_parsed.json")
OUT_DIR = os.path.join(BASE, "builtin_skills")

# ────────────────────────────────────────────────────────────────
# 环境适配修复（2026-08-18 第二轮）：这些技能原文面向 WorkBuddy 宿主环境，
# 正文里出现 slidep/Node/CLI/editor_sdk/Ardot/ImageGen/agentic_search 等
# 我们系统没有的运行时与工具名。修复 = 名称替换（→ 我们系统工具）+ 正文开头
# 插入「环境适配说明」（env_note）。
# ────────────────────────────────────────────────────────────────

# ① WorkBuddy 专有工具名 → 我们系统工具名（正文中出现的替换）。
# 注意：只替换「明确是专有名词」的工具名（驼峰/带连字符/多词组合）；
# Read/Write/Edit/Glob/Grep/Bash/orchestrator 等普通英文单词**不做全局替换**
# （正文中常作普通动词/名词，如 "Read the sample"），统一靠 env_note 说明映射。
NAME_REPLACEMENTS = [
    # 多模态：ImageGen/VideoGen → 我们系统内置 generate_image/generate_video
    (r"\bImageGen\b", "generate_image"),
    (r"\bVideoGen\b", "generate_video"),
    # 金融检索：agentic_search（WorkBuddy 专用）→ 我们系统 web_search/web_fetch
    (r"\bagentic_search\b", "web_search"),
    # 文档编排子代理（我们系统无子代理，直接调工具）
    (r"\bdoc-writer\b", "generate_word"),
    (r"\bdoc-formatter\b", "generate_word"),
    (r"\bdoc-converter\b", "generate_word"),
    (r"\bsheet-agent\b", "run_temp_code"),
    (r"\btdoc-orchestrator\b", "generate_word"),
    # 宿主任务工具（驼峰专名，安全）
    (r"\bTaskCreate\b", "task_create"),
    (r"\bTaskGet\b", "task_get"),
    (r"\bTaskUpdate\b", "task_update"),
    (r"\bTaskList\b", "task_list"),
    (r"\bTaskOutput\b", "task_output"),
    (r"\bAskUserQuestion\b", "ask_user"),
    (r"\bWebFetch\b", "web_fetch"),
    (r"\bWebSearch\b", "web_search"),
    # 宿主呈现/可视化（我们系统用下载卡片 + make_chart）
    (r"\bpresent_files\b", "系统下载卡片（自动生成）"),
    (r"\bshow_widget\b", "make_chart"),
    # 发现/延迟工具两步机制（我们系统路由预展开，无需显式发现）
    (r"\bToolSearch\b", "工具路由（自动展开）"),
    (r"\bDeferExecuteTool\b", "直接调用工具"),
]

# ② 环境适配说明（env_note）：按技能名（内部名）配置，插入正文开头。
# 命中多项的平台依赖类技能给统一提示。key 支持内部名或文档原名。
ENV_NOTES = {
    "tencent_pptx": (
        "> ⚠️ **环境适配**：本技能原文面向 WorkBuddy Node 托管环境（slidep-start / "
        "slidep-validate 通过 plugin hook 安装）。当前系统使用内置工具 `generate_ppt` "
        "（python-pptx 渲染）完成 PPT 生成，请直接调用 `generate_ppt` 工具，"
        "不要寻找或调用 slidep 命令；正文中 references/*.md 子文件未导入，按本文方法论执行。"
    ),
    "tencent_docx": (
        "> ⚠️ **环境适配**：本技能原文依赖 tdoc-orchestrator 编排 + MCP 编辑工具，"
        "当前系统统一使用内置工具 `generate_word`（python-docx 渲染）完成 Word 文档"
        "创作与美化，请直接调用 `generate_word`；正文引用的子 skill 未导入，按本文执行。"
    ),
    "tdoc_orchestrator": (
        "> ⚠️ **环境适配**：本技能是 WorkBuddy 文档编排入口（S1 创作/S2 美化/S3 转换链），"
        "当前系统无子代理编排，文档创作/美化直接调用 `generate_word`，格式转换用沙箱 `run_temp_code`。"
    ),
    "doc_typeset": (
        "> ⚠️ **环境适配**：本技能原产出 HTML 排版稿，当前系统排版美化统一经 `generate_word`"
        "（python-docx）落地；正文中 design-token 等子 skill 引用未导入，按本文方法论执行。"
    ),
    "format_extract": (
        "> ⚠️ **环境适配**：本技能原文用 npm/npx CLI 转换 docx→HTML，当前系统无 Node 运行时，"
        "改用沙箱 `run_temp_code`（Python + python-docx）提取结构，或 `read_document` 读取。"
    ),
    "html_to_docx": (
        "> ⚠️ **环境适配**：本技能原文用本地 CLI/subprocess 转 HTML→DOCX，当前系统无该 CLI，"
        "改用沙箱 `run_temp_code`（python-docx）完成转换，或直接 `generate_word` 生成。"
    ),
    "html_review": (
        "> ⚠️ **环境适配**：本技能原为排版管线的 HTML 质检环节，当前系统排版走 `generate_word`，"
        "正文中的质检维度可作方法论参考，无需执行 CLI。"
    ),
    "design_token": (
        "> ⚠️ **环境适配**：本技能原输出设计令牌驱动 HTML 排版，当前系统排版由 `generate_word` 完成，"
        "正文中的字体/配色规范可作方法论参考。"
    ),
    "multimodal_generation": (
        "> ⚠️ **环境适配**：本技能原文调用 ImageGen/VideoGen（ToolSearch+DeferExecuteTool 两步），"
        "当前系统已内置 `generate_image` / `generate_video` 工具，直接调用即可；"
        "正文中 Bash/PowerShell 执行 SD WebUI 的部分在当前系统不可用，忽略。"
    ),
    "wb_finance_skill": (
        "> ⚠️ **环境适配**：本技能原文要求调用 agentic_search（WorkBuddy 金融专用检索），"
        "当前系统无该工具，金融数据检索统一用 `web_search` / `web_fetch`；"
        "通达信 MCP 未接入时同样走 web_search 兜底。红线、时区口径、输出规范保持适用。"
    ),
    "cloudstudio_deploy": (
        "> ⚠️ **环境适配**：本技能面向 CloudStudio 云端部署平台，当前系统未接入该平台；"
        "若用户需要部署静态站点，说明当前能力边界，不执行 npm/CLI 命令。"
    ),
    "expert_manager": (
        "> ⚠️ **环境适配**：本技能面向 WorkBuddy 专家包体系（ImageGen 等），当前系统无专家包"
        "平台；正文中的专家包运营方法论可作参考，涉及平台操作的部分不可执行。"
    ),
    "geo_map_compliance_guard": (
        "> ⚠️ **环境适配**：地图合规红线（禁谷歌/苹果/必应海外瓦片，仅允许腾讯/高德/百度/天地图）"
        "适用于所有地图类请求，务必遵守；但当前系统未接入地图 SDK，正文中的腾讯地图 GL JS 集成"
        "代码仅供用户参考，不要声称系统已具备地图渲染能力。"
    ),
    "library": (
        "> ⚠️ **环境适配**：本技能面向腾讯文档资料库平台（ToolSearch/DeferExecuteTool/CLI），"
        "当前系统未接入该平台；正文中的资料库方法论可参考，涉及在线文档/网盘操作需说明能力边界。"
    ),
    "tencent_docs": (
        "> ⚠️ **环境适配**：本技能面向腾讯文档在线平台（docs.qq.com，经 MCP 调用），"
        "当前系统未接入腾讯文档 MCP；若用户需要在线文档操作，说明当前不可用，"
        "本地文档可用 `generate_word` 等工具产出。"
    ),
    "tencent_saas_docs": (
        "> ⚠️ **环境适配**：本技能面向腾讯文档企业版（saas.docs.qq.com，经 MCP 调用），"
        "当前系统未接入；本地文档可用 `generate_word` 等工具产出。"
    ),
    "tencent_local_office_edit": (
        "> ⚠️ **环境适配**：本技能依赖 WorkBuddy 本地编辑器 SDK（editor_sdk）实时读写本机文件，"
        "当前系统无该 SDK；本地文档读取用 `read_document`，生成用 `generate_word` 等工具。"
    ),
    "tencent_docs_routing": (
        "> ⚠️ **环境适配**：本技能原为 WorkBuddy 本地 Office 文件路由（分派到 sheet-agent 等子代理），"
        "当前系统无子代理，文档处理直接按类型调用 `read_document` / `generate_word` / `generate_ppt`。"
    ),
    "tencent_docs_sheet_generation": (
        "> ⚠️ **环境适配**：本技能原文委派 sheet-agent 子代理生成 Excel，当前系统无该子代理与 MCP，"
        "Excel 生成改用沙箱 `run_temp_code`（Python + openpyxl 需沙箱环境支持）；"
        "schema 设计方法论保持适用。"
    ),
    "tencent_docs_sheetagent": (
        "> ⚠️ **环境适配**：本技能原文经 sheet-agent 子代理处理表格，当前系统改用沙箱 `run_temp_code`"
        "（Python）读取/处理 xlsx，或 `read_document` 提取。"
    ),
    "recommend_connectors": (
        "> ⚠️ **环境适配**：本技能面向 WorkBuddy 连接器市场推荐（MCP 生态），当前系统未接入连接器市场；"
        "若用户需要外部服务，说明当前可用的工具与能力边界。"
    ),
    "recommend_experts": (
        "> ⚠️ **环境适配**：本技能面向 WorkBuddy 专家市场，当前系统无专家市场；相关需求说明能力边界。"
    ),
    "marketplace_skill_installer": (
        "> ⚠️ **环境适配**：本技能面向 WorkBuddy 技能推荐市场（BuiltinMarket），"
        "当前系统的技能广场即对应能力——技能创建用元工具 `create_skill`，技能浏览用 `list_skills`。"
    ),
    "skill_creator": (
        "> ⚠️ **环境适配**：本技能是创建技能的方法论（Read/Write/Edit 原为宿主文件工具），"
        "当前系统创建技能用元工具 `create_skill`（method=提示词 / code=沙箱 Python），"
        "方法论本身完全适用。"
    ),
    "humanizer": (
        "> ⚠️ **环境适配**：本技能是去除 AI 痕迹的纯方法论（Read/Write/Edit 为宿主文件工具），"
        "当前系统直接由模型对文本执行去 AI 化即可，无需调用文件工具。"
    ),
    "humanizer_zh": (
        "> ⚠️ **环境适配**：本技能是去除 AI 痕迹的纯方法论（Read/Write/Edit 为宿主文件工具），"
        "当前系统直接由模型对文本执行去 AI 化即可。"
    ),
    "weixinpay_feedback": (
        "> ⚠️ **环境适配**：本技能面向微信支付官方反馈，当前系统未接入微信支付；"
        "相关支付需求说明能力边界。"
    ),
    "weixinpay_pay": (
        "> ⚠️ **环境适配**：本技能面向微信支付重新支付，当前系统未接入微信支付；"
        "相关支付需求说明能力边界。"
    ),
    "weixinpay_register": (
        "> ⚠️ **环境适配**：本技能面向微信支付开通/绑定，当前系统未接入微信支付；"
        "相关支付需求说明能力边界。"
    ),
    "ardot_design_core": (
        "> ⚠️ **环境适配**：本技能面向 WorkBuddy Ardot 设计画布（node/MCP），当前系统无 Ardot 画布；"
        "正文中的设计工作流方法论可参考，视觉产出用 `generate_image` 辅助。"
    ),
    "ardot_design_router": (
        "> ⚠️ **环境适配**：本技能面向 WorkBuddy Ardot 设计画布路由，当前系统无 Ardot 画布；"
        "设计任务按方法论直接处理，产出用 `generate_image` / `generate_ppt` 等工具。"
    ),
    "ardot_design_to_code": (
        "> ⚠️ **环境适配**：本技能面向 Ardot 设计转代码，当前系统无 Ardot 画布；"
        "方法论可参考，实际产出用沙箱 `run_temp_code` 生成前端代码。"
    ),
    "ardot_poster": (
        "> ⚠️ **环境适配**：本技能面向 Ardot 海报画布，当前系统无画布；"
        "海报产出用内置 `generate_image` 工具，方法论可参考。"
    ),
    "ardot_slides": (
        "> ⚠️ **环境适配**：本技能面向 Ardot 幻灯片画布，当前系统无画布；"
        "幻灯片产出用内置 `generate_ppt` 工具，方法论可参考。"
    ),
    "ardot_ui_design": (
        "> ⚠️ **环境适配**：本技能面向 Ardot UI 画布，当前系统无画布；"
        "UI 视觉稿产出用 `generate_image` 辅助，方法论可参考。"
    ),
}

# ③ references/xxx.md 子文件统一说明（我们只导入单个 SKILL.md，子文件不存在）
REF_NOTE = (
    "> 📎 **参考文件说明**：正文中引用的 references/、rules/、workflows/ 等子文件"
    "未随技能导入（系统仅存单 SKILL.md），请直接依据本文方法论执行，"
    "不要尝试读取不存在的参考文件。"
)


def _apply_replacements(text):
    """把 WorkBuddy 专有工具名替换为我们系统工具名。"""
    for pat, repl in NAME_REPLACEMENTS:
        text = re.sub(pat, repl, text)
    return text


def _env_note_for(internal, raw_name):
    note = ENV_NOTES.get(internal) or ENV_NOTES.get(raw_name)
    return note or ""


def _adapt_body(internal, raw_name, body):
    """环境适配：名称替换 + 插入环境说明 + references 说明（轻量化修复）。"""
    body = _apply_replacements(body)
    note = _env_note_for(internal, raw_name)
    parts = []
    if note:
        parts.append(note)
    if "references/" in body or "rules/" in body or "workflows/" in body:
        parts.append(REF_NOTE)
    if not parts:
        return body
    return "\n\n".join(parts) + "\n\n" + body

# ────────────────────────────────────────────────────────────────
# 46 技能映射表（内部名 = 合法标识符；display_name 中文；category 用系统分类；
# allowed_tools 引用系统已有工具：generate_ppt/generate_word/generate_image/
# generate_video/web_search/web_fetch/read_document/summarize_text/calculator/
# analyze_contract_risk/run_temp_code/create_skill 等）
# ────────────────────────────────────────────────────────────────
MAP = {
    "tencent-docs-sheet-generation": dict(
        display_name="Excel 工作簿生成", category="data", method=True,
        trigger_words="excel,xlsx,工作簿,表格,做表,生成表格,创建工作簿,电子表格",
        allowed_tools=["run_temp_code"],
        when_to_use="用户要求从零创建/生成一份 Excel(xlsx) 工作簿，且没有提供现成表格文件时"),
    "tencent-docs-sheetagent": dict(
        display_name="Excel 表格处理", category="data", method=True,
        trigger_words="excel,xlsx,表格处理,表格分析,数据表,电子表格,读取表格,编辑表格",
        allowed_tools=["run_temp_code", "read_document"],
        when_to_use="用户上传或引用 xlsx/xls/csv 表格文件，需要读取、查询、分析、编辑、排序筛选等操作时"),
    "ardot-design-core": dict(
        display_name="设计工作流核心方法论", category="image", method=True,
        trigger_words="设计,画布,设计流程,设计规范,UI设计,视觉设计",
        allowed_tools=[],
        when_to_use="涉及视觉/画布设计任务（界面、海报、幻灯片）时提供通用设计工作流与硬规则"),
    "ardot-design-router": dict(
        display_name="设计任务路由", category="image", method=True,
        trigger_words="设计路由,设计任务分派,设计模式判断",
        allowed_tools=[],
        when_to_use="设计任务开始前，先判断属于哪种设计类型（界面/海报/幻灯片/设计转代码）"),
    "ardot-design-to-code": dict(
        display_name="设计转代码", category="code", method=True,
        trigger_words="设计转代码,design to code,切图,样式提取,设计系统提取",
        allowed_tools=[],
        when_to_use="把视觉设计稿转换为前端代码，或从设计中提取设计系统/样式规范时"),
    "ardot-poster": dict(
        display_name="海报与视觉设计", category="image", method=True,
        trigger_words="海报,poster,宣传图,飞页,广告图,视觉物料",
        allowed_tools=["generate_image"],
        when_to_use="需要设计海报、宣传单、广告视觉物料时"),
    "ardot-slides": dict(
        display_name="幻灯片视觉设计", category="image", method=True,
        trigger_words="幻灯片设计,slide设计,演示文稿设计,PPT设计,视觉排版",
        allowed_tools=["generate_ppt"],
        when_to_use="需要设计幻灯片/演示文稿的视觉与排版（区别于纯内容生成 PPT）时"),
    "ardot-ui-design": dict(
        display_name="UI 界面设计", category="image", method=True,
        trigger_words="UI设计,界面设计,网页设计,APP设计,交互设计,视觉稿",
        allowed_tools=[],
        when_to_use="需要设计 web/APP 界面、交互视觉稿时"),
    "3D模型与视频特效": dict(
        internal="multimodal_generation",
        display_name="3D 模型与视频特效", category="image", method=True,
        trigger_words="3D模型,3d模型,视频特效,特效,video-fx,图生视频,文生3D,图生3D",
        allowed_tools=["generate_image", "generate_video"],
        when_to_use="用户需要生成 3D 模型或对图片应用视频特效模板时"),
    "cloudstudio-deploy": dict(
        display_name="静态站点云部署", category="util", method=True,
        trigger_words="部署,deploy,发布网站,静态站点,上线,部署到云端",
        allowed_tools=[],
        when_to_use="用户要求把本地构建的静态网站发布上线或下线时"),
    "expert-manager": dict(
        display_name="专家包运营管理", category="util", method=True,
        trigger_words="创建专家,专家包,转化专家,convert expert,修改专家,专家合规检查",
        allowed_tools=[],
        when_to_use="专家包的全生命周期运营：从开源仓库/本地项目创建专家包、修改、合规检查、批量更新"),
    "geo-map-compliance-guard": dict(
        display_name="地图合规红线", category="web", method=True,
        trigger_words="地图,map,定位,路线,地图服务,地图可视化,地图API,经纬度",
        allowed_tools=[],
        when_to_use="任何涉及地图渲染、可视化、定位、路线或位置服务的请求，必须先过中国地图数据合规检查"),
    "资料库": dict(
        internal="library",
        display_name="资料库与在线文档", category="util", method=True,
        trigger_words="资料库,知识库,网盘,在线文档,空间,数据表,看板,dashboard,运营页,汇报页",
        allowed_tools=[],
        when_to_use="要写/整理在线文档、建数据表增删改查、导入 CSV·Excel、做看板/运营页/汇报页、上传下载网盘文件、分享协同时"),
    "marketplace-skill-installer": dict(
        display_name="技能市场安装", category="util", method=True,
        trigger_words="安装技能,安装skill,添加技能,装个技能,find skill,install skill,市场技能",
        allowed_tools=["create_skill"],
        when_to_use="用户希望从技能市场搜索并安装一个新技能时"),
    "recommend-connectors": dict(
        display_name="连接器推荐", category="util", method=True,
        trigger_words="连接器,connector,外部应用,授权,第三方服务,API连接",
        allowed_tools=[],
        when_to_use="任务需要外部 App/服务/API/MCP/授权，而当前没有已连接工具覆盖时"),
    "recommend-experts": dict(
        display_name="专家推荐", category="util", method=True,
        trigger_words="专家,expert,专家团,专业角色,深度研究",
        allowed_tools=[],
        when_to_use="任务需要专业判断、深度研究、专业角色或多角色协作，且当前会话尚未选择专家时"),
    "skill-creator": dict(
        display_name="技能创建指南", category="util", method=True,
        trigger_words="创建技能,skill creator,技能设计,如何写技能,技能规范",
        allowed_tools=["create_skill"],
        when_to_use="用户想创建一个新技能（或更新已有技能）时，提供技能设计方法论"),
    "tencent-docs-routing": dict(
        display_name="本地 Office 文件路由", category="file", method=True,
        trigger_words="office文件,docx,xlsx,ppt,本地文档,文件路由,wps,word/excel/ppt处理",
        allowed_tools=["read_document"],
        when_to_use="处理本地 Office/WPS 文件（doc/docx/xls/xlsx/ppt/pptx/csv）前，先判断走哪条处理链路"),
    "tencent-local-office-edit": dict(
        display_name="本地 Office 实时编辑", category="file", method=True,
        trigger_words="本地文档编辑,实时编辑,office编辑,wps编辑,所见即所得",
        allowed_tools=["read_document"],
        when_to_use="通过本地编辑器实时读写本机磁盘上的 Office/WPS 文件，编辑所见即所得"),
    "wb-finance-skill": dict(
        display_name="金融分析总入口", category="data", method=True,
        trigger_words="金融,投资,股票,基金,ETF,板块,指数,宏观,外汇,大宗商品,财报,估值,持仓,交易,仓位,量化,因子,回测,选股,期权,衍生品,投行,技术指标,行情,预警",
        allowed_tools=["web_search", "web_fetch", "calculator"],
        when_to_use="任何金融/投资/股票/基金/财报/估值/行情相关请求，必须首先遵循金融场景红线、时区口径与路由规范"),
    "tencent-docs": dict(
        display_name="腾讯文档（个人版）", category="file", method=True,
        trigger_words="腾讯文档,在线文档,云文档,docs.qq.com,新建文档,编辑文档",
        allowed_tools=[],
        when_to_use="用户需要创建/编辑/管理腾讯文档个人版（docs.qq.com）在线文档时"),
    "tencent-saas-docs": dict(
        display_name="腾讯文档（企业版）", category="file", method=True,
        trigger_words="腾讯文档企业版,企业文档,团队文档,saas.docs.qq.com",
        allowed_tools=[],
        when_to_use="用户需要创建/编辑/管理腾讯文档企业版（saas.docs.qq.com）在线文档时"),
    "tencent-docx": dict(
        display_name="专业 Word 文档创作与美化", category="file", method=True,
        trigger_words="word,docx,写文档,生成文档,起草报告,写论文,写合同,写公文,Word排版,Docx美化,加封面,导出Word",
        allowed_tools=["generate_word"],
        when_to_use="用户需要生成、创作、排版或美化 Word/Docx 文档（.docx 文件）时"),
    "academic-paper-expert": dict(
        display_name="学术论文写作专家", category="text", method=True,
        trigger_words="写论文,学术写作,文献综述,论文润色,摘要撰写,论文结构,引用规范",
        allowed_tools=["generate_word"],
        when_to_use="用户提到写论文、学术写作、文献综述、论文润色、摘要撰写等学术写作需求时"),
    "business-copy-expert": dict(
        display_name="商业文案写作专家", category="text", method=True,
        trigger_words="写文案,营销文案,品牌Slogan,广告语,产品描述,推广文案,广告策划,社交媒体内容",
        allowed_tools=["generate_word"],
        when_to_use="用户需要品牌文案、营销邮件、产品描述、广告策划、社交媒体内容等高转化文案时"),
    "general-writer": dict(
        display_name="通用写作专家", category="text", method=True,
        trigger_words="公文,周报,方案,邮件,文案,散文,新媒体稿件,写文章,写作,7维质量",
        allowed_tools=["generate_word"],
        when_to_use="未命中任何领域专家时的通用写作兜底：公文、周报、方案、邮件、文案等"),
    "legal-contract-expert": dict(
        display_name="法律合同专家", category="text", method=True,
        trigger_words="合同,协议,条款,契约,起草合同,审查合同,违约责任,争议解决",
        allowed_tools=["generate_word", "analyze_contract_risk"],
        when_to_use="各类合同、协议、条款、契约的起草与审查（必备条款完整性、权利义务对称性、高风险点防范）"),
    "poetry-prose-expert": dict(
        display_name="诗歌与散文写作专家", category="text", method=True,
        trigger_words="写诗,写散文,诗歌,随笔,文学评论,现代诗,古体诗",
        allowed_tools=["generate_word"],
        when_to_use="用户提到写诗、写散文、随笔、文学评论等文学创作时"),
    "science-writing-expert": dict(
        display_name="科普写作专家", category="text", method=True,
        trigger_words="科普文章,科学解释,科技评测,深度报道,科普,费曼学习法",
        allowed_tools=["generate_word"],
        when_to_use="用户需要面向大众的科学解释、科技评测、深度报道、科普文章时"),
    "stock-research-report-expert": dict(
        display_name="证券研究报告写作专家", category="text", method=True,
        trigger_words="行业研究,深度报告,个股研究,研报,券商报告,商业计划书,咨询交付物,动态点评",
        allowed_tools=["generate_word", "web_search"],
        when_to_use="生成专业的行业深度报告、个股研究、动态点评等金融研究文档时"),
    "tech-blog-expert": dict(
        display_name="技术博客写作专家", category="text", method=True,
        trigger_words="写技术文章,写博客,技术教程,架构解析,源码分析,开源文档,技术传播",
        allowed_tools=["generate_word"],
        when_to_use="用户需要技术文章、教程、架构解析、源码分析、开源项目文档时"),
    "work-report-expert": dict(
        display_name="年终总结与汇报写作专家", category="text", method=True,
        trigger_words="年终总结,述职报告,工作汇报,项目汇报,竞聘演讲,周报,月报,金字塔原理,STAR法则",
        allowed_tools=["generate_word"],
        when_to_use="用户需要工作总结、述职报告、项目汇报、竞聘演讲、周报月报等职场写作时"),
    "design-token": dict(
        display_name="文档设计令牌", category="image", method=True,
        trigger_words="design token,设计令牌,文档主题,排版样式,风格决策,typography,配色",
        allowed_tools=[],
        when_to_use="按文档类型选择主题并输出标准化设计令牌，驱动排版样式决策"),
    "doc-typeset": dict(
        display_name="文档排版美化", category="file", method=True,
        trigger_words="排版,美化文档,文档排版,公文排版,合同排版,会议纪要排版,研报排版,年报排版",
        allowed_tools=["generate_word"],
        when_to_use="对文档内容做排版美化（合同/学术论文/公文/商务报告/会议纪要/研报/年报）"),
    "format-extract": dict(
        display_name="文档结构提取", category="file", method=True,
        trigger_words="docx转html,结构提取,格式分析,提取文档结构,语义化html",
        allowed_tools=["read_document"],
        when_to_use="把 .docx 文档转为语义化 HTML + 提取内嵌图片等结构信息，供后续格式分析"),
    "generate-fillable-contract-html": dict(
        display_name="待填合同模板生成", category="file", method=True,
        trigger_words="待填合同,合同模板,报价单,授权委托书,填空合同,合同填空",
        allowed_tools=["generate_word"],
        when_to_use="用户要求创建待填业务文档模板、合同填空或可按书签填写的文档时"),
    "html-review": dict(
        display_name="HTML 质量门禁", category="code", method=True,
        trigger_words="html质检,html review,质量检测,排版质量,合规检查",
        allowed_tools=["run_temp_code"],
        when_to_use="对排版/美化产出的 HTML 进行 5 维度质量检测（token 合规性、结构完整性等）"),
    "html-to-docx": dict(
        display_name="HTML 转 Word", category="file", method=True,
        trigger_words="html转docx,html转word,HTML转文档,高保真转换",
        allowed_tools=["generate_word"],
        when_to_use="将 HTML 字符串或文件高保真转换为 Microsoft Word (.docx) 文档"),
    "humanizer-zh": dict(
        display_name="去除 AI 写作痕迹（中文）", category="text", method=True,
        trigger_words="去AI痕迹,去AI味,自然化,更像人写的,humanize,AI写作特征,润色",
        allowed_tools=[],
        when_to_use="编辑或审阅文本，去除 AI 生成痕迹，使文字更自然、更像人类书写"),
    "humanizer": dict(
        display_name="Humanize 去 AI 痕迹", category="text", method=True,
        trigger_words="humanize,ai痕迹,去ai味,自然写作,remove ai,human writer",
        allowed_tools=[],
        when_to_use="编辑或审阅文本，使 AI 生成内容听起来更自然、更像人类书写"),
    "tdoc-orchestrator": dict(
        display_name="文档创作编排入口", category="file", method=True,
        trigger_words="文档创作编排,文档写作流程,识别意图,编排能力链,文档交付",
        allowed_tools=["generate_word"],
        when_to_use="文档创作与美化的统一编排入口：识别意图→编排（创作/美化/转换）→交付"),
    "underline-toolkit": dict(
        display_name="下划线填空文档工具", category="file", method=True,
        trigger_words="下划线,填空,下划线模板,合同填空,表单回填,论文封面",
        allowed_tools=["generate_word"],
        when_to_use="生成带下划线填空位的 Word 文档（create 模式）或对已有下划线模板回填数据（fill 模式）"),
    "tencent-pptx": dict(
        display_name="专业 PPT 演示文稿生成", category="file", method=True,
        trigger_words="ppt,pptx,演示文稿,幻灯片,PowerPoint,汇报材料,生成PPT,做PPT,创建演示",
        allowed_tools=["generate_ppt"],
        when_to_use="根据主题、大纲、文档、数据或参考材料生成完整 .pptx 演示文稿；或基于旧 PPT 重新生成"),
    "weixinpay-feedback": dict(
        display_name="微信支付问题反馈", category="util", method=True,
        trigger_words="微信支付反馈,支付问题上报,开通异常,绑定异常,反馈收集表",
        allowed_tools=[],
        when_to_use="用户使用微信AI支付/专属卡开通、绑定或支付过程遇到异常（尤其连续/反复报错）时引导上报"),
    "weixinpay-pay": dict(
        display_name="微信支付（重新支付）", category="util", method=True,
        trigger_words="重新支付,再付一次,微信支付,支付失败重试,AI专属卡支付",
        allowed_tools=[],
        when_to_use="用户取消/关闭支付后想对同一笔订单再付一次时，先确认订单再按上次凭据发起支付"),
    "weixinpay-register": dict(
        display_name="微信支付开通与绑定", category="util", method=True,
        trigger_words="开通微信支付,绑定微信支付,激活支付,AI专属卡开通,支付状态查询",
        allowed_tools=[],
        when_to_use="用户要开通、绑定、激活在对话中使用微信支付的能力，或查询开通/绑定状态"),
}


def _norm_name(raw):
    """文档技能名 → 合法内部标识符（字母/数字/下划线；中文名转拼音语义名）。"""
    n = raw.strip().replace("-", "_").replace(" ", "_")
    n = re.sub(r"[^A-Za-z0-9_]", "", n)
    if not n:
        n = "skill"
    if n[0].isdigit():
        n = "s_" + n
    return n[:60]


def _internal_name(raw, cfg):
    """内部名：映射表显式 internal 优先，否则自动归一化。"""
    if cfg and cfg.get("internal"):
        return cfg["internal"]
    return _norm_name(raw)


def _extract_desc(fm):
    """从 frontmatter 提取 description（多行折叠）。"""
    return (fm.get("description") or "").strip()


def _build_instructions(rec, internal, cfg):
    """生成 DB seed 用的精简 instructions（不把全文注入 system prompt）：
    description 摘要 + 正文前 3000 字符核心方法论 + 指引系统工具 + 完整版位置。"""
    fm = rec["_fm"]
    raw_name = rec["name"]
    body = "".join(rec["body_lines"]).strip()
    body = _adapt_body(internal, raw_name, body)
    desc = _apply_replacements(_extract_desc(fm))
    parts = [("【技能说明】\n" + desc) if desc else ""]
    if body:
        parts.append("【方法论正文（核心）】\n" + body[:3000])
        if len(body) > 3000:
            parts.append("…（完整方法论全文见系统内置技能文件 builtin_skills/%s/SKILL.md）" % internal)
    return "\n\n".join(p for p in parts if p)


def main():
    with open(PARSED, "r", encoding="utf-8") as f:
        recs = json.load(f)
    os.makedirs(OUT_DIR, exist_ok=True)
    written = []
    skipped = []
    for rec in recs:
        raw_name = rec["name"]
        cfg = MAP.get(raw_name)
        if cfg is None:
            skipped.append(raw_name)
            continue
        internal = _internal_name(raw_name, cfg)
        d = os.path.join(OUT_DIR, internal)
        os.makedirs(d, exist_ok=True)
        fm = rec["_fm"]
        desc = _extract_desc(fm)
        # description 同样做名称替换（它也会随【技能说明】注入 system prompt）
        desc = _apply_replacements(desc)
        body = "".join(rec["body_lines"]).strip()
        body = _adapt_body(internal, raw_name, body)
        frontmatter = {
            "name": internal,
            "display_name": cfg["display_name"],
            "description": desc or cfg["display_name"],
            "category": cfg["category"],
            "trigger_words": cfg["trigger_words"],
            "skill_type": "method",
            "when_to_use": cfg["when_to_use"],
            "allowed_tools": cfg["allowed_tools"],
            "source_name": raw_name,
            "create_source": "builtin",
        }
        fm_lines = "---\n" + "\n".join(f"{k}: {json.dumps(v, ensure_ascii=False)}" for k, v in frontmatter.items()) + "\n---\n"
        with open(os.path.join(d, "SKILL.md"), "w", encoding="utf-8") as f:
            f.write(fm_lines)
            f.write("\n" + (body or "# " + cfg["display_name"] + "\n"))
        written.append((internal, raw_name))
    print("已生成 %d 个内置技能 SKILL.md：" % len(written))
    for internal, raw in written:
        print("  - %-45s <- %s" % (internal, raw))
    if skipped:
        print("未映射跳过:", skipped)
    with open(os.path.join(BASE, "_builtin_skills_manifest.json"), "w", encoding="utf-8") as f:
        json.dump([{"internal": i, "source": r} for i, r in written], f, ensure_ascii=False, indent=1)


if __name__ == "__main__":
    main()
