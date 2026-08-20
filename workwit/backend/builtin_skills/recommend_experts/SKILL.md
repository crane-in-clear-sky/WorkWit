---
name: "recommend_experts"
display_name: "专家推荐"
description: "|\n当任务需要专业判断、深度研究、专业角色或多角色协作，且当前会话尚未选择 Expert 时，搜索并用内联卡片推荐真实专家或专家团。"
category: "util"
trigger_words: "专家,expert,专家团,专业角色,深度研究"
skill_type: "method"
when_to_use: "任务需要专业判断、深度研究、专业角色或多角色协作，且当前会话尚未选择专家时"
allowed_tools: []
source_name: "recommend-experts"
create_source: "builtin"
---

> ⚠️ **环境适配**：本技能面向 WorkBuddy 专家市场，当前系统无专家市场；相关需求说明能力边界。

# Recommend Experts

只在专业角色或多角色工作流会明显提升当前任务质量，且当前会话没有 Expert 时使用。

## Workflow

1. 先确认当前会话未选择 Expert；已有 Expert 时立即停止，不得搜索、推荐或替换。
2. 调用 `search_plugins`，`type` 必须是 `expert`；把用户请求放进 `userIntent`，必要时补充 `keywords`。
3. 根据精选场景和候选描述匹配当前任务。候选可能是单专家 `expert`，也可能是专家团 `expert_team`，二者合计最多选择 3 个。
4. `pluginId` 必须逐字来自本次 `search_plugins` 返回结果。
5. 调用一次 `suggest_plugin_install` 渲染 Expert 卡片：


## Rules

- 用户只能启用一个专家或专家团；不得静默启用、替换当前 Expert。
- 不得用文字列表代替 `suggest_plugin_install` 卡片。
- 搜索返回 `expertAlreadySelected: true` 时立即继续原任务，不得再次推荐。
- 搜索无相关结果时静默继续原任务。
- 用户跳过、超时或取消后，同一轮不得重复推荐相同候选。
- 不得使用网页搜索寻找 Plugin；`search_plugins` 是候选的唯一来源。