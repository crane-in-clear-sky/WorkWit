---
name: "library"
display_name: "资料库与在线文档"
description: "当要写/整理在线文档、建数据表增删改查、导入 CSV·Excel、做看板/dashboard/运营页/汇报页、md 转网页或演示 HTML 发布、建目录、上传下载网盘文件、审阅修订、分享协作，以及提到资料库/知识库/网盘/空间/workbuddy.cn/space 链接时使用。资料库是 WorkBuddy 原生的内容管理、分享协作与轻发布模块，覆盖在线文档（doc）、网页链接（link）、结构化数据表（database）、数据/汇报页面（page）、网盘文件（drive）与空间管理（manage）；凡说资料库/知识库或出现 space 链接、未显式点名腾讯文档/飞书/Notion/乐享/金山文档/iWiki 等外部产品时一律走本 skill，默认落原生库、不反问存到哪个产品。"
category: "util"
trigger_words: "资料库,知识库,网盘,在线文档,空间,数据表,看板,dashboard,运营页,汇报页"
skill_type: "method"
when_to_use: "要写/整理在线文档、建数据表增删改查、导入 CSV·Excel、做看板/运营页/汇报页、上传下载网盘文件、分享协同时"
allowed_tools: []
source_name: "资料库"
create_source: "builtin"
---

> ⚠️ **环境适配**：本技能面向腾讯文档资料库平台（ToolSearch/DeferExecuteTool/CLI），当前系统未接入该平台；正文中的资料库方法论可参考，涉及在线文档/网盘操作需说明能力边界。

# 资料库使用指南

## 调用前置

静默读取运行环境，仅用于内部路由和鉴权；除非用户明确询问，不在过程说明、确认或成功回执中提及：

`python3 "${CODEBUDDY_SKILL_DIR}/runtime_context.py"`

- 输出 `mode=sandbox`：不调 `connect_open_platform`、不传 token；`mode=client`：按客户端模式鉴权。
- `mode=client` 的鉴权调用必须严格执行：先用 `工具路由（自动展开）` 精确查询 `{"tool_names":["connect_open_platform"]}`，再用 `直接调用工具` 调用 `{"toolName":"connect_open_platform","params":{"skill_id":"library"}}`。拿到 `authenticated=true` 且非空的 `token` 后，才能执行本 skill 的网络脚本。
- `connect_open_platform` 是 deferred MCP 工具，不是 shell/CLI 命令，也不是 `space_api.py` 子命令。禁止通过 Bash、`cbc`、`codebuddy`、Python 或 `space_api.py connect_open_platform` 调用；禁止在环境变量、本地文件、凭证目录、进程、二进制或 RPC 中搜索/探测 token；禁止用空值或伪造值调用 `--token-stdin`。`工具路由（自动展开）` 未找到工具或 `直接调用工具` 换票失败时立即停止，不改走其它凭证路径。
- 目标空间由所有模块按以下顺序统一决定：
  1. 操作已有节点：用户给出 `nodeId` 或节点链接时，按该节点的实际归属执行，不套用默认空间。
  2. 用户显式指定空间或目录：以该目标为准；已给出或解析出 `spaceId` 时传实际值，仅明确指定我的文档但未给 ID 时可省略可选 `spaceId`；按 `parentId` 新建时，先取得其所属 `spaceId`，确认二者匹配后同时传入。
  3. 无显式目标：创建、导入、上传时省略可选的 `--space-id` / `space_id`，由后端默认落我的文档。

以脚本输出为准。脚本失败时停止，简要说明无法继续即可。

## 远端变更

凡创建/上传、修改、删除、移动或重命名远端资料，先静默执行 `mutation.md`，按其规则判定实际目标空间分类与是否需要停下确认。

## 检索授权

明确给出单个节点时只读取该节点，不扩展成全库搜索。“写报告”“做方案”等泛化创作不触发检索。写入目标不等于检索授权。

## 按 kind 直取入口

访问/处理任一资料库节点前，先归一化为 nodeId（`/space/d/{id}` 或 `*.workbuddy.link/p/{id}` 直接提取 `{id}` 为 nodeId、搜索结果取 nodeId），再用 `space.workspace.node-info` 读 `kind`。

按下表读取对应的 entry：

| kind | 场景 | 入口 |
|---|---|---|
| doc | 在线文档读/改 | `doc/entry.md` |
| web / page | HTML 页面 | `page/entry.md` |
| database | 结构化表 | `database/entry.md` |
| link | 网页剪藏 | `link/entry.md` |
| drive | 网盘文件 | `drive/entry.md` |
| smh | 历史媒体节点 | `smh/entry.md` |
| 目录 / 空间导航 / 全局搜索 | 管理 | `manage/entry.md` |

## 目标选择

- 说「存到资料库」且未点名外部产品时，默认且唯一是原生资料库，不反问存哪、不把外部产品拼成选择题。
- 只在缺文档主题时问主题。
- 仅写入失败/权限不足/多个同名空间/语义指向团队空间但名称不全时消歧，且只列原生空间（`space.workspace.list-user-spaces`）。

## 文件夹意图

说「建文件夹/做目录/把文档归到一起/建知识库」时，不默认建空间；先让用户在两种做法间选，措辞用「挂到下面/目录树」，不用「父/子节点」：

| 做法 | 怎么做 | 适合 |
|---|---|---|
| 文档目录（默认） | 新建一篇文档当目录页，其余文档挂到它下面成目录树 | 灵活聚合 |
| 独立空间 | 单独建空间统一管理；长期+多人协同推荐团队空间 | 长期沉淀 |

回执给对应链接（空间 `/space/s/{spaceId}`、节点 `/space/d/{nodeBlockId}`）；落地命令见 `manage/entry.md`。

## 场景路由表

| 场景 | 入口 |
|---|---|
| 列/看目录子节点（按父节点拉一层，不传取空间根目录） | `manage/entry.md` |
| 列全部资料库位置（我的文档/团队空间） | `manage/entry.md` |
| 给节点链接/nodeId 问「这是什么」 | `manage/entry.md` |
| 查询空间/文档/节点的协作成员、参与者或权限角色 | `manage/entry.md` |
| 获取文件下载链接 / 下载文件本体（`kind=drive` 或历史 `kind=smh` 节点） | `drive/entry.md` 或 `smh/entry.md` |
| 给某已存在节点插入附件，或下载其附件（有 attachmentId） | `attachment/entry.md` |
| 上传本地/外链图片或文件到资料库 | 按类型分流：图片 → `manage/entry.md`；csv → `database/entry.md`；html/zip → `page/entry.md`；md → `doc/entry.md`；docx/pdf 等 → `drive/entry.md`；给已有节点挂附件 → `attachment/entry.md` |
| 移动节点（同空间换父） | `manage/entry.md` |
| 重命名节点 / 改标题 / 网盘文件改名 | `manage/entry.md` |
| 全库检索（历史沉淀、历史决策、报错排查、术语） | `manage/entry.md` |
| 给 spaceId 或 `/space/s/{spaceId}` 要梳理/整理/总结某主题 | `manage/entry.md` |
| 在指定文档/空间内搜内容片段 | `manage/entry.md` |
| 创建在线文档 / 整理成文档 / 据某空间·素材生成文档·讲稿·报告 | `doc/entry.md` |
| 保存/收藏/剪藏一个 http(s) 网页为 `kind=link` 节点 | `link/entry.md` |
| 读取/总结已有 `kind=link` 节点的网页正文 | `link/entry.md` |
| 建表 / 字段增删改 / 插改查记录 / 看 schema / 导入 CSV | `database/entry.md` |
| 从零生成单页 HTML/网页/汇报/报告/分析/可视化/单页数据应用（未点名外部产品、未明确只要本地文件） | `page/entry.md` |
| 制作可视化 HTML（非做站/做应用） / 本地 doc·ppt·pdf·excel·图片（单个或综合素材）说做成页面·美化·做汇报分享 | `page/beautify-flow.md` |
| 把 md/文档/表格/csv做成一页汇报 / PPT 风格 / 数据报告页（md→html） | `page/entry.md` |
| 做运营页/看板/dashboard/给团队看的展示页 | `page/entry.md` |
| 上传 HTML/ZIP / 接入数据库 / 创建数据驱动页 | `page/entry.md` |
| 贴 page 详情页（`/space/d/`）或发布态链接（`*.workbuddy.link/p/`，`/p/` 后段即 nodeId）让编辑修改 | `page/edit-flow.md` |
| 管理 page 与 database 的数据关联绑定（某 page 引用了哪些 database / 某 database 被哪些 page 引用 / 建立·解除 page↔database 关联，非父子节点·非目录归属） | `page/entry.md` |

## 调用方式与运行模式

网络能力经 `space_api.py` 或各模块脚本调用，命令与参数以对应 `entry.md` 为准。运行态以 `runtime_context.py` 的输出为准；业务脚本内部由 `_common.is_sandbox()` 分派。公共请求层不自动补 `spaceId`。

| 当前上下文 | 模式 | 鉴权 | 调用形态 |
|---|---|---|---|
| `mode=sandbox` | 沙箱模式 | 不调 `connect_open_platform`、不传 token；auth-proxy 注入身份 | `python3 .../script.py [args]` |
| `mode=client` | 客户端模式 | 每次网络命令前按“`工具路由（自动展开）` 精确查询 → `直接调用工具` 调用”的顺序执行 `connect_open_platform` 取 `token`；agent 直接把 token 通过 stdin 首行传给脚本，同时带 `--token-stdin` 开关；token 不落地、不写文件、不跨进程复用 | `printf '%s' "<token>" \| python3 .../script.py --token-stdin [args]` |

- 需要业务 stdin payload 时 token 占首行，业务 JSON 从第二行起（或 `--content-file` 走文件通道）。
- `--help` 查看某 API 参数（不需要 token）；`--raw` 调试原始响应（鉴权与普通调用相同）。


客户端模式访问显式的 `staging.workbuddy.cn` 目标时，为对应网络命令设置 `LIBRARY_ENV=staging`；其他客户端目标不设置该变量并默认走生产。沙箱模式固定走 auth-proxy，不使用 `LIBRARY_ENV`。

**响应约定**：成功 → stdout 输出结构化文本（`KS_*` 前缀行 / 单行 JSON / XML 块）；失败 → stdout 仅一行 `{"error":"<脱敏错误>"}` 后 `exit 0`（含 header `traceid` 时附带）。后端 / HTTP 失败的错误内容为 `code=<错误码>; msg=<安全业务说明>`；无后端码的本地参数校验可保留明确文本。脚本只读取后端信封的 `code/msg`；`msg` 经截断和脱敏后透传，不透传其它响应字段、requestId、请求体、Token、Cookie、堆栈、内部路径或完整签名 URL。SKILL 层据「stdout 是 `KS_*` 还是 `{"error":...}`」判定走堆。

### 错误码行动表

收到 `{"error": ...}` 时读 `error_handling.md` 按表执行：取最内层 `code=<错误码>` 只执行一条动作，未列出码按临时故障处理；给用户转自然语言，不展示内部码。

特例：脚本报本地 `AUTH_REQUIRED`（当前网络命令前尚未通过 `直接调用工具` 成功取得非空 token）属于客户端鉴权前置未完成，按上文「调用前置」规定补做一次 `工具路由（自动展开）` → `直接调用工具` 换票；不向用户误报为资料权限不足，也不搜索其它凭证来源；正确换票最多 1 次，工具不可用或换票失败即停止。

## 安全底线

1. 数据敏感（客户数据/合同金额/密钥/隐私）立即停止，提示走合规通道；拿不准按敏感处理。
2. Token 与内部细节（原始响应/错误堆栈/内部 URL）永不进入用户回复、日志或落库产物。
3. 产物只搬面向读者的成品；token、工具调用、思考过程、本地路径绝不写进产物。
4. 每轮只走一条模块分支，模块协作通过脚本输出串联。
5. 只处理用户显式给出的路径，不自动扫描目录。