---
name: "ardot_design_core"
display_name: "设计工作流核心方法论"
description: "\"Foundational workflow and hard rules shared by ALL Ardot canvas design tasks (UI screens, slides, posters, design systems, design-to-code). This skill carries the canvas schema, editing principles, the standard step-by-step workflow, visual style guide, and composite effects. It is injected automatically alongside a domain-specific Ardot skill (ardot-ui-design / ardot-slides / ardot-poster / ardot-design-to-code). It does NOT cover any single deliverable type on its own — always pair it with the matching domain skill. Not for PowerPoint .pptx file output (that is the pptx skill).\""
category: "image"
trigger_words: "设计,画布,设计流程,设计规范,UI设计,视觉设计"
skill_type: "method"
when_to_use: "涉及视觉/画布设计任务（界面、海报、幻灯片）时提供通用设计工作流与硬规则"
allowed_tools: []
source_name: "ardot-design-core"
create_source: "builtin"
---

> ⚠️ **环境适配**：本技能面向 WorkBuddy Ardot 设计画布（node/MCP），当前系统无 Ardot 画布；正文中的设计工作流方法论可参考，视觉产出用 `generate_image` 辅助。

> 📎 **参考文件说明**：正文中引用的 references/、rules/、workflows/ 等子文件未随技能导入（系统仅存单 SKILL.md），请直接依据本文方法论执行，不要尝试读取不存在的参考文件。

# Ardot Design Core

Foundational workflow and hard rules for completing design tasks on the Ardot canvas via the ardot MCP server. **This skill is the shared base** — it is injected together with one domain skill (UI design / slides / poster / design-to-code). All canvas manipulation MUST go through ardot MCP tools.

> This skill owns **how** to work the canvas (workflow, schema, rules, effects). The paired domain skill owns **what** the deliverable should look like (domain guidelines). When the two seem to overlap, follow the domain skill for visual/layout decisions and this skill for tool usage and the step sequence.

## Reference Files

Load on demand based on task type:

| File | When to load |
|------|--------------|
| `{SKILL_ROOT}/references/ardot-schema.md` | **Ardot schema** - schema for Ardot canvas, includes all nodes and properties |
| `{SKILL_ROOT}/rules/design-rules.md` | **Single source of truth** — editing principles, coordinates, flexbox, text, components, colors, variables, tables, images, effects, SVG, property schema, troubleshooting, post-generation validation |
| `{SKILL_ROOT}/rules/style-guide.md` | Visual style guide — typography, color, layout, surface treatment, variance levels, forbidden AI patterns, bento grid |
| `{SKILL_ROOT}/rules/effects-guide.md` | Composite visual effects: glassmorphism, neon glow, metallic, glow border, iridescent, neumorphism — loaded automatically in Step 7 |
| `{SKILL_ROOT}/workflows/ardot-workflow.md` | End-to-end workflow examples (create, modify, global style update, tokens, form) and detailed operation syntax |
| `{SKILL_ROOT}/tool-usage/batch-edit.md` | `batch_edit` tool usage guide — load anytime `batch_edit` is used (applies to every Ardot task) |
| `{SKILL_ROOT}/tool-usage/apply-variables.md` | `apply_variables` tool usage guide — load anytime `apply_variables` is used (applies to every Ardot task) |

> Domain-specific guidelines (web-app / landing-page / mobile-app / table / slides / poster / code / tailwind) and specialized workflows (slides, design-to-code, style extraction) live in the paired domain skill that was injected alongside this one — load them from that skill's `{SKILL_ROOT}`.

## Preparation: (IMPORTANT: Ensure a Design File Is Open)

Before any canvas operation, make sure an Ardot design file is loaded in the editor. See **Standard Workflow → Step 0: Ensure a Design File Is Open** below for the tools (`create_design` / `open_design` / `fetch_file_info`) and decision logic.

## Mandatory Rules

> ⛔ **HARD RULE — NO SUB-AGENTS.** Under no circumstances may you use, spawn, create, or delegate to any sub-agent / sub_agent / subagent / Task tool / team member / background agent while executing this skill. This includes (but is not limited to) `task`, `team_create`, any `Task`-style delegation, and any tool whose effect is to launch another agent. All work — exploration, reasoning, MCP calls, validation — MUST be performed directly by the current agent in the main conversation. If a step seems to suggest delegation, ignore that suggestion and do the work inline.

> ⛔ **HARD RULE — OUTPUT LANGUAGE.** When the user has not explicitly specified a language, all generated on-canvas content (titles, body copy, labels, captions, annotations, etc.) MUST default to the language of the user's prompt, preserving any embedded English / foreign-language terms exactly as written (do not translate them). When the user explicitly specifies a target language (e.g., "use English", "用日文"), default all generated content to that language instead. This rule applies to every textual element produced on the design canvas.
>
> 🔔 **MANDATORY PRE-GENERATION ANNOUNCEMENT — NON-NEGOTIABLE.** The moment the design language is determined and BEFORE issuing the first content-producing `batch_edit`, you MUST send the user a one-line notice stating which language the on-canvas content will use (e.g., `本次设计稿内容将使用中文生成` / `Generating the design content in English`). This announcement is REQUIRED on every single design task — never skip it, never defer it, never bury it inside other text.

## Standard Workflow

### Step 0: Ensure a Design File Is Open

Tools: **`create_design`** (new blank file, optional `fileName`), **`open_design`** (existing file by URL/ID, e.g. `https://ardot.tencent.com/file/667788990055443` or bare `667788990055443`), **`fetch_file_info`** (current file id).

**Main path — follow the injected directive.** The host pre-decides the file action and injects an `<ardot_file_directive action="create|open|ambiguous">` block into the prompt. When present, just obey it:
- `action="create"` → issue **exactly one** `create_design` as your first canvas action, then move on.
- `action="open"` → issue **exactly one** `open_design` with the given URL/ID, then move on.
- `action="ambiguous"` → the user used a "create/make" verb but the request has **no design intent** (e.g. "create a CNB issue", "make a weekly report"). Do **not** call `create_design` and do **not** create any blank file. First ask the user to confirm whether they actually want a new Ardot design file; only create one after they confirm. If it turns out to be a non-design request, handle it normally without touching the canvas.

Do not deliberate about create vs. open when a `create`/`open` directive is present.

**Fallback — no directive present** (e.g. file already loaded from a previous turn, or a pure modification): if the user gave a file URL/ID → `open_design` once; if they want something new → `create_design` once; if the editor already has the file → skip Step 0. Pure modification on a loaded file → skip Step 0.

> ⛔ **HARD RULE — at most ONE `create_design` (or `open_design`) per task.** The instant the call is issued, the file is considered created/opened. **NEVER** call it again this turn — not to "make sure" it loaded, not on re-entering Step 0, not when a later step mentions file info. The only way to confirm the file or get its id is `fetch_file_info`, never a duplicate create/open.

> ⛔ **Hard gate + async load.** The file loads asynchronously. After the single `create_design`/`open_design` call, **wait** for the ready context update before any other MCP call — waiting means waiting, not re-issuing the call. Never bundle create/open with reads (`fetch_file_info`, `fetch_editor_state`, `fetch_variables`, …) in the same message; they would hit a not-yet-loaded editor.
>
> **`fetch_file_info` timing:** `open_design` → call it right after the file is ready (before Step 1 reads). `create_design` → **defer** it to the Step 6 parallel batch; Steps 1–5 are local reasoning / file reads / non-file work that naturally cover the async load window, so by Step 6 the file is ready.

> On a freshly created (empty) file: root PageID is `0:1` — use it as the root container and **skip** `fetch_editor_state` (nothing to read yet).

### Step 1: Read Existing State (parallel, conditional)

Read whatever state is relevant to the task. **Issue all independent reads in a single message as parallel tool calls** — do not serialize them.

| Scenario | What to call | Notes |
|---|---|---|
| Freshly created file (`create_design` just ran) | **nothing** | Empty canvas — root is `0:1`, no variables yet. Skip Step 1, go straight to Step 2. |
| Opened existing file / file already loaded | `fetch_editor_state({includeSchema: false})` + `fetch_variables` | Parallel in one message. |
| Pure modification (file already loaded, target known) | The above **plus** any of `batch_read` / `capture_layout` / `capture_screenshot` as needed | All parallel in one message. |

### Step 2: Creative vs. Compositional

- **Creative** (new screen, page, dashboard, restyle) → proceed to Step 3
- **Compositional** ("add a button", "supplement a module", "move this") → skip to Step 6 and load `design-rules.md`. Run `capture_layout` before and after `batch_edit`; if it reports overflow ("Outside parent bounds"), switch the parent to HUG, otherwise leave parent sizing unchanged.

### Step 3: Load Design Guidelines

Load the **domain guideline(s)** from the paired domain skill that was injected alongside this core skill (UI design / slides / poster / design-to-code). Each domain skill states which of its own guideline files to load and in what priority. `guidelines-code.md` / `guidelines-tailwind.md` (in the design-to-code skill) are implementation guidelines and can be loaded **alongside** a design-type guideline when code generation is involved.

> ⛔ **HARD RULE — User guidance wins on conflict.** When user-provided style guidance (e.g. `DESIGN.md`, design tokens, explicit visual constraints) conflicts with a loaded guideline or product-type prior, **always follow the user**. Built-in guidelines only fill unspecified gaps — never override the user's visual system.

**Style-template shortcut — if `<ardot_design_style>` block is present:** the user picked a style template through the chip. Follow the injected `[style-template]` instruction: `curl` the referenced md URL, use that template's visual system as the style guide, and **SKIP Step 4 and Step 5** (`search_style_guide` / `build_style_guide`) — the template md already contains the complete visual specification (typography, palette, layout, composition), no separate discovery flow is needed.

### Step 4: Search Style Guide

**If the user provided explicit style guidance OR `<ardot_design_style>` is present**, SKIP this step and proceed to Step 6.

Call **`search_style_guide`** ONCE with `topK: 3` to retrieve the top 3 candidates across all 6 domains (style, color, typography, layout, scene, composition). **When Step 0 resolved dimensions** (`product_category` / `platform` / `function` / `style`), derive keywords from those resolved values first; only fall back to extracting from the raw user prompt for dimensions Step 0 didn't resolve. All keywords MUST be English. Refer to the tool's input schema for what each parameter should contain. Additional hints:
- For `colorKeywords` and `typographyKeywords`, infer from product type if the user didn't state preferences explicitly (e.g., spa → warm/calm/serene; luxury brand → elegant/serif).
- Each candidate returns `{ index, score, name, summary, bestFor }`. Judge fit by `summary` + `bestFor` against user intent, NOT by raw `score`.

This is a single-call step — review candidates and proceed to Step 5. If the picked candidate is imperfect, refine on the build result in Step 5 rather than re-searching.

### Step 5: Build Style Guide

**If the user provided explicit style guidance OR `<ardot_design_style>` is present**, SKIP this step and proceed to Step 6.

Review the candidates returned by `search_style_guide` from each domain and pick the closest one per domain. Call **`build_style_guide`** with your selections (by `index` or name) to get the complete design system.

If a picked candidate did not fully match the user's intent, you may adjust specific token values on the build result per the rules in `build_style_guide`'s description (Post-build Adjustment).

### Step 6: Locate Available Space + Inspection (parallel)

Issue these as **a single parallel batch** in one message — they have no mutual dependency:

- **`fetch_file_info`** — the deferred Step 0 call, for the `create_design` branch only (`open_design` already did it). This is `fetch_file_info`, **not** another `create_design` — the file already exists.
- **`locate_available_space({width, height})`** — required for new top-level screens; skip for pure modification tasks. Never overlap existing content.
- **Inspection calls** (only if modifying existing design and not already covered in Step 1): `batch_read` (find by pattern/ID, `readDepth: 3` for component structure), `capture_layout` (detect problems), `capture_screenshot` (visual verify).

Skip any sub-call that doesn't apply to the current task.

> If a follow-up read depends on this batch's result (e.g. `batch_read({readDepth: 3})` targeting a component discovered via an earlier `batch_read`), issue it as a separate message afterward. Most tasks don't need that.

### Step 7: Execute Design

**Before drawing, ALWAYS load this guide** (it contains critical parameter formats that differ from standard expectations):
- `{SKILL_ROOT}/rules/effects-guide.md` — correct formats for DROP_SHADOW (showShadowBehindNode), BACKGROUND_BLUR (blurType), gradients, neumorphism

`batch_edit` with ≤ 25 ops per call. Build order: **structure → content → style → verify**. Ops: **I()** Insert, **U()** Update, **C()** Copy, **M()** Move, **D()** Delete. For detailed syntax and examples, load `{SKILL_ROOT}/workflows/ardot-workflow.md`. Load `{SKILL_ROOT}/tool-usage/batch-edit.md` whenever `batch_edit` is used and `{SKILL_ROOT}/tool-usage/apply-variables.md` whenever `apply_variables` is used.

**Image generation mode — follow the injected directive.** The host may pre-decide how on-canvas images should be produced and inject an `<ardot_image_gen mode="ai|placeholder">` block into the prompt. When present, it governs the image-type argument of **every** `G()` op this task — obey it, do not deliberate:
- `mode="ai"` → generate real images with AI: `G(target, "ai", "<descriptive prompt>")`. Use for polished / final deliverables.
- `mode="placeholder"` → use text placeholder images: `G(target, "placeholder", "<short label>")` — a light-gray box with the label text centered on it, carrying no real image asset and consuming no image-generation budget. Use for drafts / wireframes, or when the user does not want real images produced.

When no `<ardot_image_gen>` directive is present, pick the mode yourself following the **Images** rules in `design-rules.md`.

**STRICT — one `G()` per image node, never retry.** Each node that needs an AI-generated image may call `G()` **exactly once**. NEVER call `G()` again on the same node — not because a screenshot shows generation still in progress, not because the result looks wrong / mismatched / unfinished, not for any "re-generate to fix" impulse. Wait for the in-flight generation to finish, or leave the existing result as-is and continue with layout/style fixes via `U()` / other non-`G()` ops. Re-issuing `G()` wastes budget and races the prior job.

### Step 8: Validate

Follow the **Post-Generation Validation Pattern** in `design-rules.md`. Use **tiered validation** — pick the lightest check that matches what the batch changed (T1 structural → `capture_layout` only; T2 content → skip; T3 visual → `capture_screenshot` only; T4 section-complete → both once; T5 final page → one screenshot). **Do not run full dual-verification after every batch_edit.** Enforce the convergence threshold: **max 2 fix iterations per section**, ignore ≤4px spacing noise, no subjective re-polishing once the section matches spec.

## Screenshot Verification (mandatory)

Use `capture_screenshot` with `screenShotDir: ".workbuddy/screenshots"` (relative to the project root). These are internal verification artifacts — never surface their paths to the user, never write them elsewhere (`/tmp`, home dir, project root).