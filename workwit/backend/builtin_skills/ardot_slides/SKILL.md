---
name: "ardot_slides"
display_name: "幻灯片视觉设计"
description: "\"Use this skill for Ardot canvas design tasks whose deliverable is a slide deck / presentation design (NOT a PowerPoint .pptx file). Covers presentations, decks, pitch decks, keynote-style slides, launch-event slides, roadshow decks, proposal/pitch slides rendered as Ardot design canvases. Trigger phrases: design a presentation, create a deck, generate slides, design slides, create a pitch deck, design a keynote, slide deck mockup, presentation mockup, pitch deck design, 幻灯片设计稿, 演示文稿设计, 发布会幻灯片, 路演 PPT 设计稿, keynote 设计, 提案稿, 宣讲稿, 制作 PPT, 做一份幻灯片. Pairs with ardot-design-core (injected alongside). IMPORTANT boundary: this skill OWNS slide/deck/幻灯片 requests whose output is a DESIGN (Ardot canvas, 设计稿, mockup). Defer to the pptx skill ONLY when the user explicitly requires a PowerPoint .pptx file (导出 pptx / 生成 PPT 文件 / references an existing .pptx). Words like 'slides', '幻灯片', 'PPT' alone do NOT trigger pptx.\""
category: "image"
trigger_words: "幻灯片设计,slide设计,演示文稿设计,PPT设计,视觉排版"
skill_type: "method"
when_to_use: "需要设计幻灯片/演示文稿的视觉与排版（区别于纯内容生成 PPT）时"
allowed_tools: ["generate_ppt"]
source_name: "ardot-slides"
create_source: "builtin"
---

> ⚠️ **环境适配**：本技能面向 Ardot 幻灯片画布，当前系统无画布；幻灯片产出用内置 `generate_ppt` 工具，方法论可参考。

> 📎 **参考文件说明**：正文中引用的 references/、rules/、workflows/ 等子文件未随技能导入（系统仅存单 SKILL.md），请直接依据本文方法论执行，不要尝试读取不存在的参考文件。

# Ardot Slides

Domain workflow and guidelines for **slide deck / presentation** deliverables on the Ardot canvas.

> **The general workflow and hard rules live in `ardot-design-core`**, which is injected alongside this skill. This skill carries the **slide-specific 6-phase workflow and design rules**. The slides workflow is more detailed and **takes precedence over the generic core workflow** for deck tasks — follow `slides-workflow.md` end-to-end, and use core for schema, editing rules, effects, and screenshot verification.

## Slide Workflow (follow strictly — do NOT improvise from the core skeleton)

- **Core slide workflow** → `{SKILL_ROOT}/workflows/slides-workflow.md` — the 6-phase process (Phase 0 Requirement Clarification → Phase 5 Final Review). Phases 0→4 are strictly sequential.
- **Mandatory slide design rules** → `{SKILL_ROOT}/references/guidelines-slides.md` — L01–L20 layout contracts, large typography, rich backgrounds, visual rhythm. This is the **authoritative source** for all slide visual decisions.

> Files referenced by the slide workflow that live in the core skill (`design-rules.md`, `ardot-workflow.md`, `ardot-schema.md`, `effects-guide.md`) are read from the **ardot-design-core** skill root — its absolute path is provided in the same prompt that injected this skill.

## pptx Boundary (read before starting)

This skill produces a **design** (Ardot canvas). If the user explicitly wants a PowerPoint `.pptx` file as the deliverable — "导出 pptx", "生成 PPT 文件", "export to PowerPoint", or references an existing `.pptx` filename — that is **not** this skill's job; defer to the `pptx` skill. If the user says "设计稿", "design", "Ardot", "canvas", "mockup", or gives no file-format constraint, stay here.