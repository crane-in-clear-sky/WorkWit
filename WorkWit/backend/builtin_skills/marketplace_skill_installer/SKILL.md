---
name: "marketplace_skill_installer"
display_name: "技能市场安装"
description: "|\n在 WorkBuddy 对话中通过一句话从推荐市场（BuiltinMarket）搜索并安装 Skill。\n触发词：安装 skill、安装技能、install skill、添加技能、装个 X 技能、帮我安装 X、find skill、install marketplace skill、search skill。"
category: "util"
trigger_words: "安装技能,安装skill,添加技能,装个技能,find skill,install skill,市场技能"
skill_type: "method"
when_to_use: "用户希望从技能市场搜索并安装一个新技能时"
allowed_tools: ["create_skill"]
source_name: "marketplace-skill-installer"
create_source: "builtin"
---

> ⚠️ **环境适配**：本技能面向 WorkBuddy 技能推荐市场（BuiltinMarket），当前系统的技能广场即对应能力——技能创建用元工具 `create_skill`，技能浏览用 `list_skills`。

# Marketplace Skill Installer

帮助用户用一句话从 WorkBuddy 推荐市场（**BuiltinMarket**）安装 Skill。

## When to Use

只要用户表达"安装某个技能/skill"的意图就用此 Skill，例如：

- 「帮我安装飞书套件」
- 「安装一个 lark-unified 的 skill」
- 「我想用 X 技能，帮我装下」
- 「install Y skill」

## 严格的工具调用约束

- **禁止**直接构造任何 HTTP 请求，禁止涉及 token/cookie/Authorization。
- **只能**通过 `workbuddy_marketplace_skill` 这个内置工具完成全部操作；鉴权和后端调用由宿主处理。
- **禁止**伪造或猜测 `skillId`——必须从 search 结果中拿到。

## Workflow

### Step 1：识别关键字

从用户输入中抽取一个搜索 keyword：

- 如果用户说了**显示名**（例："飞书套件"），用显示名；
- 如果用户说了**技术名**（例："lark-unified"），用技术名；
- 既没说显示名也没说技术名（仅说"装个 skill"）→ 礼貌追问"想安装哪个技能？可以告诉我名字或关键字"。

### Step 2：搜索

调用 `workbuddy_marketplace_skill`，action 为 `search`：


`limit` 一般保持默认 5，必要时可上调到 10–20。

返回字段说明：

- `success: false` → 把 `error` 透传给用户，停下；
- `results: []`（空数组）→ 告诉用户"未在推荐市场找到匹配的 skill"，停下；
- `results.length === 1` → 直接进入 Step 3；
- `results.length >= 2` → 进入 Step 4 让用户挑。

### Step 3：单一命中

逻辑分支（`r = results[0]`）：

- **未安装**（`r.installed === false`）→ 直接调 install：
- **已安装且已是最新**（`r.installed === true && r.updateAvailable === false`）→ 告诉用户"该 Skill 已是最新版本 v<r.version>，无需重复安装"，停下；
- **已安装且有新版**（`r.installed === true && r.updateAvailable === true`）→ 询问用户"已安装 v<r.installedVersion>，最新版本是 v<r.version>，是否更新？"。用户同意后再调 install。

> ⚠️ **不要**自己用字符串相等判断版本新旧；`updateAvailable` 字段已用 semver 计算好。

### Step 4：多命中

把 results 列出来让用户选。展示时**只展示** `name`、`version`、`description` 的前 60 字、是否 `installed`。**不要**把 `skillId` 透露给用户——那是机器内部 id。例：


用户选定后取对应 `results[i].skillId`，按 Step 3 的分支处理。

### Step 5：调用 install 后

`workbuddy_marketplace_skill` 的 install action 返回：

- `success: true` → 用 `message` 告诉用户安装到了哪里、版本是什么；可附一句"在【技能管理】面板里能看到它"。**不要**手动构造路径——直接用 `message` 字段。
- `success: false` → 把 `error` 透传，并按错误类型给提示（见下表）。

## 常见错误透传

| `error` 前缀               | 含义                              | 建议提示                                                                         |
| ------------------------ | ------------------------------- | ---------------------------------------------------------------------------- |
| `[CONFIG_ERROR]`         | 主进程未配置 endpoint                 | "WorkBuddy 推荐市场暂未启用，可能需要更新到最新版客户端"                                           |
| `[BUILTIN_MARKET_ERROR]` | 后端业务错误                          | 直接展示 `error` 内容                                                              |
| `[UNKNOWN_SKILL_ID]`     | install 阶段发现该 skillId 在推荐市场已不存在 | **不要**重试相同的 skillId；重新调 search 拿新的 skillId 再安装                               |
| `[DOWNLOAD_ERROR]`       | 下载签名 URL 或 zip 失败               | "下载失败，可能是网络问题或权限不足"                                                          |
| `[EXTRACT_ERROR]`        | 解压失败                            | "解压安装包失败，建议清理 `$WORKBUDDY_CONFIG_DIR/skills/`（默认 `~/.workbuddy/skills/`）后重试" |
| `[INSTALL_ERROR]`        | 其他未知                            | 直接展示 `error` 内容                                                              |

## 重要规则

- 鉴权全部由宿主处理，**不要**让用户提供 token / cookie / 任何 header。
- 不要主动列举所有可用的 skill——只在用户明确想看时才用 search 拉。
- 安装成功后**不要**自我开启该 Skill，也不要尝试直接调用它的工具——让用户自己启用。
- 除了上述工具，本 Skill 不应触发 Bash、文件写入或网络请求。