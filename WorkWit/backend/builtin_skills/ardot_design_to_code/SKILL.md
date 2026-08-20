---
name: "ardot_design_to_code"
display_name: "设计转代码"
description: "\"Use this skill for Ardot canvas tasks that convert a design into frontend code, or extract a design system / style guide from a website. Covers: design-to-code, design → HTML/CSS/JS, export as webpage, pixel-perfect reproduction, generate an Application from a design, slide transitions, responsive scaling; and website → design-guide / design-token extraction. Trigger phrases: convert design to code, design to HTML, export as webpage, pixel-perfect reproduction, design to App, generate Application, create design system from website, extract design tokens, 设计稿转代码, 转为前端代码, 生成HTML, 导出为网页, 一比一还原, 复刻设计稿, 设计稿出码, 设计稿转应用, 转应用, 网站风格转设计稿, 提取设计风格, 生成设计指南, 提取设计 token. Pairs with ardot-design-core (injected alongside).\""
category: "code"
trigger_words: "设计转代码,design to code,切图,样式提取,设计系统提取"
skill_type: "method"
when_to_use: "把视觉设计稿转换为前端代码，或从设计中提取设计系统/样式规范时"
allowed_tools: []
source_name: "ardot-design-to-code"
create_source: "builtin"
---

> ⚠️ **环境适配**：本技能面向 Ardot 设计转代码，当前系统无 Ardot 画布；方法论可参考，实际产出用沙箱 `run_temp_code` 生成前端代码。

> 📎 **参考文件说明**：正文中引用的 references/、rules/、workflows/ 等子文件未随技能导入（系统仅存单 SKILL.md），请直接依据本文方法论执行，不要尝试读取不存在的参考文件。

# Ardot Design-to-Code & Style Extraction

Domain workflows for **design → frontend code** conversion and **website → style-guide** extraction on the Ardot canvas.

> **The general workflow and hard rules live in `ardot-design-core`**, which is injected alongside this skill. Follow `ardot-design-core` for the step sequence, schema, editing rules, effects, and screenshot verification. This skill carries the **specialized conversion/extraction workflows and implementation guidelines**.

## Specialized Workflows (follow strictly — do NOT improvise the procedure)

- **Design → frontend code** → `{SKILL_ROOT}/workflows/design-to-code-workflow.md` — design → HTML/CSS/JS, generate Application, to code, slide transitions, responsive scaling.
- **Website → style guide extraction** → `{SKILL_ROOT}/workflows/extract-style-guide-from-web.md` — pull a design guide / tokens from an existing website.

## Implementation Guidelines (load alongside a design-type guideline when generating code)

- `{SKILL_ROOT}/references/guidelines-code.md` — design-to-code implementation rules.
- `{SKILL_ROOT}/references/guidelines-tailwind.md` — Tailwind v4 implementation (load alongside `guidelines-code.md`).

> Files referenced by these workflows that live in the core skill are read from the **ardot-design-core** skill root — its absolute path is provided in the same prompt that injected this skill. This includes `design-rules.md` etc. and the shared **tool-usage guides** (`tool-usage/batch-edit.md`, `tool-usage/apply-variables.md`), which now live in `ardot-design-core` because `batch_edit` / `apply_variables` are used by every Ardot task.