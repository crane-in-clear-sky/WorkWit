---
name: "recommend_connectors"
display_name: "连接器推荐"
description: "|\n当用户任务需要外部 App、服务、API、MCP、授权或第三方数据，而当前没有已连接工具覆盖时，搜索并用内联卡片推荐真实 Connector。"
category: "util"
trigger_words: "连接器,connector,外部应用,授权,第三方服务,API连接"
skill_type: "method"
when_to_use: "任务需要外部 App/服务/API/MCP/授权，而当前没有已连接工具覆盖时"
allowed_tools: []
source_name: "recommend-connectors"
create_source: "builtin"
---

> ⚠️ **环境适配**：本技能面向 WorkBuddy 连接器市场推荐（MCP 生态），当前系统未接入连接器市场；若用户需要外部服务，说明当前可用的工具与能力边界。

# Recommend Connectors

只在当前任务直接需要外部服务、授权或用户自己的第三方数据，且已有工具无法完成时使用。

## Workflow

1. 调用 `search_plugins`，`type` 必须是 `connector`；把用户请求原文或轻量改写放进 `userIntent`，必要时补充 `keywords`。
2. 只保留对当前任务直接有帮助、且搜索结果中仍未连接的候选。
3. 最多选择 3 个候选。`pluginId` 必须逐字来自本次 `search_plugins` 返回结果，不得猜测或改写。
4. 调用一次 `suggest_plugin_install` 渲染 Connector 卡片：


## Rules

- 不得直接安装、连接或授权 Connector；所有操作都由用户在卡片中触发。
- 不得用文字列表代替 `suggest_plugin_install` 卡片。
- 搜索无相关结果时静默继续原任务，不要向用户描述内部搜索过程。
- 用户跳过、超时或取消后，同一轮不得重复推荐相同候选。
- 不得使用网页搜索寻找 Plugin；`search_plugins` 是候选的唯一来源。