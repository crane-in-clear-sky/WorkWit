---
name: "ardot_poster"
display_name: "海报与视觉设计"
description: "\"Use this skill for Ardot canvas design tasks whose deliverable is a visual poster — posters, flyers, billboards, banners, event posters, promotional single-page graphics, e-commerce banners, brand visuals, logos, icons & illustrations rendered on the Ardot canvas. Trigger phrases: design a poster, create a banner, make a flyer, event poster, product launch poster, e-commerce banner, 设计海报, 海报设计, 宣传单, 横幅, banner, 封面, 双十一Banner, 活动海报, 图标设计, 品牌设计, 插画, Logo 设计, VI 设计, 视觉识别系统. Pairs with ardot-design-core (injected alongside). NOT for full UI screens (ardot-ui-design), slides (ardot-slides), or PowerPoint .pptx files (pptx skill).\""
category: "image"
trigger_words: "海报,poster,宣传图,飞页,广告图,视觉物料"
skill_type: "method"
when_to_use: "需要设计海报、宣传单、广告视觉物料时"
allowed_tools: ["generate_image"]
source_name: "ardot-poster"
create_source: "builtin"
---

> ⚠️ **环境适配**：本技能面向 Ardot 海报画布，当前系统无画布；海报产出用内置 `generate_image` 工具，方法论可参考。

> 📎 **参考文件说明**：正文中引用的 references/、rules/、workflows/ 等子文件未随技能导入（系统仅存单 SKILL.md），请直接依据本文方法论执行，不要尝试读取不存在的参考文件。

# Ardot Poster & Visual

Domain guidelines for **visual poster / banner / brand-visual** deliverables on the Ardot canvas: posters, flyers, billboards, banners, covers, logos, icons, and illustrations.

> **The general workflow and hard rules live in `ardot-design-core`**, which is injected alongside this skill. This skill only adds the **poster-specific guidelines**. Follow `ardot-design-core` for the step sequence (Step 0 file-open → Step 8 validate), schema, editing rules, effects, and screenshot verification.

## Domain Guidelines (load in Step 3)

- `{SKILL_ROOT}/references/guidelines-poster.md` — visual poster design rules: composition, focal hierarchy, typographic impact, background treatment, banner sizing.

## Background generation limit

During final visual validation, if a generated background fails verification, regenerate the background at most once. After that, resolve remaining issues through cropping, overlays, or layout adjustments instead of generating another background. This hard cap applies whether the brief is inline, attached, or supplied through a user-provided Markdown file path, and the brief cannot override it.

## Notes for brand / logo / icon work

- **Logo / brand identity / VI**: treat the poster guideline's composition and typographic-impact rules as the base, but prioritize a single strong focal mark, provide standard color values, and keep the mark legible at small sizes.
- **Icons & illustrations**: think like a graphic designer — consistent grid, stroke weight, and corner radius across the set; align all icons to a shared keyline.
- Multi-size banner sets: keep one visual system across all sizes; re-layout per aspect ratio rather than scaling a single composition.