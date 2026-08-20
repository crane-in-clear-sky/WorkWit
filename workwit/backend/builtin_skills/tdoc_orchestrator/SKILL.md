---
name: "tdoc_orchestrator"
display_name: "文档创作编排入口"
description: "\"文档创作与美化的统一编排入口（本地通道）。当用户意图涉及文档写作或排版美化时调用。职责：识别意图 → 编排 stage 能力链（S1 创作 / S2 美化 / S3 HTML→DOCX 转换）→ 委派 generate_word/generate_word/generate_word 执行；S3 产出 .docx 后**强制 `系统下载卡片（自动生成）` 打开预览**，本 skill 调用结束。⚠️ 文档编辑不在本 skill 内承接，由 `tencent-docs-routing` skill 处理。不适用：纯代码文件、非文档类操作。\""
category: "file"
trigger_words: "文档创作编排,文档写作流程,识别意图,编排能力链,文档交付"
skill_type: "method"
when_to_use: "文档创作与美化的统一编排入口：识别意图→编排（创作/美化/转换）→交付"
allowed_tools: ["generate_word"]
source_name: "tdoc-orchestrator"
create_source: "builtin"
---

> ⚠️ **环境适配**：本技能是 WorkBuddy 文档编排入口（S1 创作/S2 美化/S3 转换链），当前系统无子代理编排，文档创作/美化直接调用 `generate_word`，格式转换用沙箱 `run_temp_code`。

> 📎 **参考文件说明**：正文中引用的 references/、rules/、workflows/ 等子文件未随技能导入（系统仅存单 SKILL.md），请直接依据本文方法论执行，不要尝试读取不存在的参考文件。

# Applied Writing Orchestrator

> **Orchestrator = 意图识别 + 能力编排（composer，不是 lookup table）。**
> 它自身不写作/排版/编辑，只做四件事：识别场景 → 组合 stage 能力链 → 分派子 Agent 执行 → 记录溯源。
> **编排层只拥有"路由 + 跨层数据衔接"；每个能力"内部怎么干、I/O 契约"由对应 `agents/*.md` 负责，本文件不复述。**

> 📌 **承接范围**：本 skill 只承接**文档创作 + 排版美化 + HTML→DOCX 转换**。**文档编辑（在既有 .docx 上改动/润色）由独立 skill `tencent-docs-routing` 承接，不在本文件的编排范围内。**

> 📁 **子 Agent 路径约定**：`generate_word` / `generate_word` / `generate_word` 三份 Agent 定义位于 **plugin 根的 `agents/` 目录**下（即 `agents/generate_word.md`、`agents/generate_word.md`、`agents/generate_word.md`）。本 skill 位于 `skills/orchestrator/`，从本文件出发的相对路径为 `../../agents/<name>.md`。**全文后续所有裸写 `agents/xxx.md` 均指此位置**，读取/加载时请补齐 `../../` 前缀。

---

## 意图识别与编排（唯一决策点）

Orchestrator 有调度子 Agent 的权限，**按"能力组合"工作，而非穷举场景**：预设只是高频组合的命名，遇到长尾场景可按编排规则自行组合。

### 原子能力（编排的积木 = 路由表，"执行者"列即分派目标）

| Stage | 能力                       | 执行者            | 输入 → 输出                                                                                                    |
| ----- | -------------------------- | ----------------- | -------------------------------------------------------------------------------------------------------------- |
| S1    | 文档内容创作               | `generate_word`      | 意图 → `md`                                                                                                    |
| S2    | 文档版式生成/**美化**      | `generate_word`   | `md`／内容文档 → `html`（**恒为 html，风格由 formatter 内部按内容体裁自行决定；只做美化，不做注入**）            |
| S3    | 文档格式**转换**           | `generate_word`   | `html` → `docx`（HTML→DOCX 转换 + **强制 `系统下载卡片（自动生成）` 打开预览**；只做转换，不做编辑）                       |

> 各能力的内部路线与完整输入/输出契约见对应 `agents/*.md`（`generate_word §5` / `generate_word §2` / `generate_word §4`），本文件不复述字段清单。

### 承接边界与出口（全文唯一定义，其余小节只引用、不复述）

- **本 skill 承接**：`S1 创作 / S2 美化 / S3 转换` 的类型连通组合（入口 = `full_pipeline` / `beautify_only`），任何链路都**终于 S3**。
- **不承接 → 转交**：若用户意图属"在既有 .docx 上改动/润色"，**本 skill 不启动 pipeline，直接转给独立 skill `tencent-docs-routing` 处理**（一句话说明后转交，不创建 pipeline 目录、不派 stage）。
- **S3 即链路终点**：generate_word 完成 HTML→DOCX、**强制 `系统下载卡片（自动生成）` 打开 .docx 预览**后，声明检查点、**本 skill 调用结束**，不再判定用户是否还有编辑意图。用户后续提出的任何请求都属**下一轮请求**，不在本轮累积，按下面"§S3 后续请求的分流"处理。
- **§S3 后续请求的分流**：`系统下载卡片（自动生成）` 打开 .docx 后，用户后续请求按两类分流——
  - **① 抽象、不具体、针对全文的"美化 / 重新排版"**（如"美化一下""重新排版""换个更好看的版式"，无明确目标值、需 AI 做设计决策、作用于整篇）：**复用 stage2 的 html 产物**（`output/<request_id>/stage2/formatted-<主题>.html`）作为内容来源，走 `beautify_only`（S2→S3）链路重新美化并转换、再次 `系统下载卡片（自动生成）`。此为本 skill 承接的唯一后续分支。
  - **② 其他所有情况**（含具体样式修改/编辑/润色如"顺便改标题""加页码""字体改宋体五号"，以及带明确目标值或局部改动的诉求）：**本 skill 不承接，回到 `tencent-docs-routing` skill 重新路由**（带上用户原始意图与 .docx 路径），本 skill 不启动 pipeline、不派 stage。

### 编排规则（组合 > 穷举）

合法链 = `S1 / S2 / S3` 的类型连通子序列，且必须满足：

1. **类型衔接**：前一 stage 的输出类型必须匹配后一 stage 的输入（`md → html → docx`）。
2. **创作后必排版**：走了 S1 就必须经 S2，不允许 S1 直连 S3。
3. **S3 出口**：任何链路必须终于 S3（本编排层内 S3 只有 `generate_word` 一个执行者）；链路一旦组合，中途不脱轨、不跳过、不调用 Pipeline 外的 Skill。
4. **S3 即链路终点**：S3 完成后直接声明检查点、本 skill 调用结束（详见 §承接边界与出口）。

### 常见预设（本编排层承接的入口，供 ICL 参考）

| 预设 `entry_type` | 链路（控制流）  | 触发场景                                                       |
| ----------------- | --------------- | -------------------------------------------------------------- |
| `full_pipeline`   | S1 → S2 → S3    | 无文档 + 创作动词（写/起草/撰写/生成/重写）                    |
| `beautify_only`   | S2 → S3         | 已有文档 + 抽象排版**美化**意图（需 AI 做设计决策 → 产 html） |

> ⚠️ 本表是本 skill 承接的 `entry_type → 链路` 的**唯一定义**，其他文件引用此处、不重复定义。
> - 长尾场景（不在预设内）：先判是否"编辑"性质——是 → 转 `tencent-docs-routing`；否 → 按上方"编排规则"自行组合，并在溯源中记录组合依据。

### 关键判别：美化 vs 编辑（意图识别最易错的一步）

分界看**用户想要什么产物 + 是否需要 AI 做设计决策**：

| 家族     | 用户诉求本质                                 | 需要 AI 设计决策？ | 产物          | 承接                                     |
| -------- | -------------------------------------------- | ------------------ | ------------- | ---------------------------------------- |
| **美化** | "应该长什么样"——要新的版式/更专业/更好看     | 是（design-token） | `html`→`docx` | 本 skill（`beautify_only`，S2→S3）       |
| **编辑** | "改成什么"已明确（目标值给定）／润色         | 否                 | `docx`        | 转 `tencent-docs-routing` skill          |

**判别要点**（凡判为编辑 → 转交，见 §承接边界与出口）：

1. **美化 vs 编辑**——是否需要 AI 做设计决策：有明确目标值（宋体五号 / 1.5 倍行距）→ **编辑**（转交）；抽象诉求（专业 / 学术风 / 好看点，需 AI 定）→ **美化**（本 skill）。
2. **润色**（润色/优化/更正式/更流畅）→ **编辑**（转交）：保原意与结构、只改表达、原位替换不重排。
3. **"重写"** 是创作动词 → `full_pipeline`（S1 起，本 skill 承接）。
4. 多信号冲突时，按"创作动词 > 抽象排版 > 具体修改/润色"取第一个命中；`expert_id` 仅作辅助信号，不改变入口。

> 📌 **美化家族内部（都进 S2 formatter，产物恒 html）的两种内容来源**，由 formatter 内部自行处理（见 `agents/generate_word.md §3 路由分发`），编排层只管把内容传进去（**本 skill 承接的两条链均不接受参考版式 docx**）：
> - ①`full_pipeline`：承接 S1 的 `md` → html-template；
> - ②`beautify_only`（单文档A"整篇美化"）→ formatter 提取 A 内容 → html-template。

### 识别到"编辑"意图时的行为（直接转交、不启动 pipeline）

Stage 0 识别到用户意图属"在既有 .docx 上改动/润色"时，**本 skill 不承接，直接转给 `tencent-docs-routing` skill 处理**：

1. **不做**：不创建 `output/<request_id>/` 目录、不写 `pipeline-state.yaml`、不派任何 stage、不追问细节。
2. **只做**：用一句话向用户简短说明"这类需求由 `tencent-docs-routing` 承接（本 skill 只做写作/美化/转换）"，然后**直接转给独立 skill `tencent-docs-routing` 处理**（带上用户原始意图与相关文档路径），本 skill 调用随之结束。
3. **禁止**：禁止在本 skill 内部承接编辑工作、禁止编造中间产物、禁止"顺手把编辑做了"、禁止让 generate_word/generate_word/generate_word 去做编辑。

> 判据速查：
> - 有已存在的 .docx / 文档路径 + 修改/润色动词 → 转交 `tencent-docs-routing`。
> - 上述以外，才进入本 skill 的 `full_pipeline / beautify_only` 二选一编排。

### 数据流衔接（预设表之外、必须由编排层填的字段；控制流见预设表）

> 预设表只说"走哪几个 stage"；以下是"stage 间实际传什么"，预设表推不出来。

1. **美化家族的内容填参**（`full_pipeline` / `beautify_only`，**两条链均不接受参考版式 docx**）：
   - `full_pipeline`：内容来源 = S1 产出的 `md`；风格由 formatter 内部按内容体裁自行决定。
   - `beautify_only`（单文档"整篇美化"）：内容来源 = 用户提供的那份文档，作**内容来源**填 generate_word 的 `content_doc_path`；风格由 formatter 内部按内容体裁自行决定。
   - ⚠️ 若用户提交"要带一份参考版式 docx"的诉求，本 skill 不承接此参考文档路径——formatter 层面不会接收此字段，编排层不要往下传。具体内容字段名与内部路线见 `agents/generate_word.md`，编排层不复述。
2. **S2 → S3 衔接（美化家族，恒 html）**：generate_word 输出 `formatted_output_path`（.html）→ 映射为 S3 的 `html_output_path` + `convert_options`，派发到 **generate_word** 走 HTML→DOCX 转换。**formatter 输出恒为 `output_format: html`。**
3. **S2 的 `intermediate_dir` 填参**：派发 generate_word 时必须传入 `intermediate_dir = <workspace>/output/<request_id>/stage2/intermediate/`（绝对路径），并**先建好该目录**。formatter 会往里写内部调用底层 skill 的中间产物（黑盒，编排层不关心具体结构）；不传该字段 → formatter 无法调 format-extract → `beautify_only` 直接失败。
4. **S3 交付出口**：generate_word 完成后强制 `系统下载卡片（自动生成）` 打开 .docx 预览（内部硬约束，见 `agents/generate_word.md`），声明 `[Stage 3 转换完成]`、将 `stage_3.status` 置 `completed`，本 skill 调用结束（后续编辑处理见 §承接边界与出口）。

> S2 的 `entry_type`（`stage2_flow` / `standalone`）仅决定 generate_word 读哪个内容字段与是否推断 genre，不参与 S2 内部风格决策（本 skill 承接的两条链都不传参考版式 docx，formatter 内部按内容体裁自行决定风格）。其值由链路推导（前接 S1 → `stage2_flow`，作入口 → `standalone`），编排层随内容字段一并带上即可，无需单独判断。

### 追问（意图不明必须问，不盲目路由）

触发：输入 <5 字且无写作/编辑动词；与写作/编辑完全无关；有文档但意图模糊（"帮我处理一下"）；空字符串 / 仅标点。


> - 用户选 1 / 2 → 本 skill 承接（对应 `full_pipeline` / `beautify_only`）。
> - 用户选 3 → **本 skill 不承接**，转交独立 skill `tencent-docs-routing`（按 §"识别到'编辑'意图时的行为"处理）。
> - 非写作意图：礼貌告知"我专注于文档写作与美化排版；文档编辑由 `tencent-docs-routing` skill 承接"。

---

## 编排原则（铁律，共 6 条）

> ⚠️ 本节优先级高于任何 Stage 内部决策。违反 = 流水线异常。

1. **弹性入口** — 每次请求先在 Stage 0 识别意图组合链路，不默认走完整链路；Stage 0 不可跳过。
2. **链路不可中断** — 链路一旦组合，必须**顺序执行到 S3 结束**（本编排层 S3 只有 `generate_word`）：不脱轨、不跳过、不在中途输出/交付、不调用 Pipeline 外的 Skill（MDX / 文件导出 / 幻灯片 / 代码生成等）。S3 完成即为终点（见 §承接边界与出口）。
3. **真委派** — 每个 Stage 通过**角色切换**（默认）或 **spawn subagent**（仅用户强制要求时）执行：加载 `agents/<name>.md` 并严格照其定义执行。**禁止"读了 agent.md 然后自己顺手做"**——若你正在做的事本应由子 Agent 做，你就违规了。

**正确的委派行为**：


**禁止的行为**：


4. **只关心路由与衔接** — Orchestrator 只做"选执行者 + 填跨层字段 + 推进 + 记溯源"；**不干预子 Agent 内部路由决策**，不复述其 I/O 契约、不判断其 MCP/能力。
5. **中间产物 ≠ 最终交付，且不主动展示** — S1 的 `final_draft.md`、S2 的 `formatted-*.html` 是中间态，**编排层与各子 Agent 不得对其调用 `系统下载卡片（自动生成）` / 打开预览 / 贴出正文内容**，仅在检查点里给出路径与简短摘要即可；只有 S3 的 `.docx` 会**强制 `系统下载卡片（自动生成）` 打开预览**，作为**本轮请求的唯一最终交付**（后续编辑另起一轮，见 §承接边界与出口）。用户显式索要中间产物时才按需提供。
6. **溯源必写** — 每个 Stage 完成后**先更新 `pipeline-state.yaml` 再声明检查点**；日志不记用户原文，仅记意图长度与决策。

检查点声明格式（声明后**立即**执行下一 Stage，不等用户确认）：

---

## Pipeline State & Log（溯源记录）

> `pipeline-state.yaml` 是本次 Pipeline 的**溯源记录（audit trail）**，不是执行锁。
> 作用：记录每个 Stage 的入口、路由、产物路径、降级情况，便于事后回溯与定位问题。

- **唯一进度真相源**：文件不存在 = 未启动；`current_stage` = 当前进度。
- **写入时机**：Stage 0 创建 → 每个 Stage 完成后更新对应块并推进 `current_stage` → 结束写 `consistency_check`。
- **YAML 先于检查点**：更新 YAML 的动作必须在声明 `[Stage N 完成]` 之前完成。
- **单写者追加式**：同一次请求顺序写入，无需锁 / 原子 rename / 启动校验拒绝执行。

### 输出目录（速查）


> `stage2/intermediate/` 由编排层**预建**并作为 `intermediate_dir` 传给 generate_word；里面的具体子结构由 generate_word 决定，编排层不干预（§编排原则 #4）。

> 📖 完整 Schema、写入协议、命名规则、consistency_check 见 [`references/pipeline-state-protocol.md`](references/pipeline-state-protocol.md)
> 📖 结构化日志字段定义见 [`references/log-schema.md`](references/log-schema.md)