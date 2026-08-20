---
name: "format_extract"
display_name: "文档结构提取"
description: ">\n把 .docx 文档转为语义化 HTML + 提取内嵌图片（标题层级、表格样式、段落缩进、字体颜色等结构信息），供调用方做后续格式分析；也可独立评估 HTML 转换质量。适用场景（**技术性底层调用**）：\"docx 转 HTML\"、\"提取 docx 结构为 HTML\"、\"HTML 转换质量评估\"。本 Skill 是底层格式提取工具，**不自行判断是否应被触发**、**不猜测输出位置**——是否调用、何时调用、产物落在哪一律由上层调用方（如 generate_word）决定，调用时必须显式传入 `--output-dir`。用户关于\"文档排版 / 套模板 / 参考这份 docx 生成\"等需求，应由 orchestrator 统一编排入口接管，**不要旁路直点本 Skill**。"
category: "file"
trigger_words: "docx转html,结构提取,格式分析,提取文档结构,语义化html"
skill_type: "method"
when_to_use: "把 .docx 文档转为语义化 HTML + 提取内嵌图片等结构信息，供后续格式分析"
allowed_tools: ["read_document"]
source_name: "format-extract"
create_source: "builtin"
---

> ⚠️ **环境适配**：本技能原文用 npm/npx CLI 转换 docx→HTML，当前系统无 Node 运行时，改用沙箱 `run_temp_code`（Python + python-docx）提取结构，或 `read_document` 读取。

> 📎 **参考文件说明**：正文中引用的 references/、rules/、workflows/ 等子文件未随技能导入（系统仅存单 SKILL.md），请直接依据本文方法论执行，不要尝试读取不存在的参考文件。

# format-extract — DOCX 格式提取与质量评估

<goal>
把一个 .docx 文件转为**语义化 HTML**（根元素 `<article class="docx-content">`）+ **图片资产**（`images/` 目录），让调用方能基于 HTML 分析源文档的排版风格（标题 / 表格 / 段落 / 字体 / 版面）。

**职责边界**：本 Skill 只负责"docx → HTML/images"与"HTML 质量评估"两件事。产物交付后如何使用（登记到哪个 yaml、喂给哪个下游流程、在什么阶段触发）完全由调用方决定，本 Skill 不做任何假设。
</goal>

<tools>
本 Skill 封装两项能力，统一通过本地预构建 `dist/cli.cjs` 执行，**不依赖 npm 全局安装、不联网**：

| 入口 | 用途 | 何时选用 |
|------|------|----------|
| `run.py`（一键入口） | docx → HTML，自动做门禁检查 + artifact_id 目录隔离 + 产物校验 | **默认首选**，适合 Agent 调用 |
| `node dist/cli.cjs convert` | 底层 CLI，支持单文件/批量/自定义输出路径 | 需要完全自定义路径或批量处理 |
| `node dist/cli.cjs assess` | 独立评估 HTML 质量（5 项检查） | 怀疑转换质量、需要量化报告 |

底层 CLI 参数、编程式 API、返回结构详见 [`references/cli-reference.md`](references/cli-reference.md)，日常调用无需阅读。
</tools>

---

<input-contract>

## 输入契约（调用方需提供）

本 Skill 不负责判断"是否应该触发"或"如何找到要转的 docx"——这些都是调用方的职责。调用方在确定要用本 Skill 时，提供：

| 参数 | 必填 | 说明 |
|------|------|------|
| `<docx-path>` | 是 | 要转换的 `.docx` 文件路径，**优先绝对路径**以规避 cwd 差异 |
| `--output-dir` | **是** | 自定义输出的**父目录**；脚本会在其下自动追加 `<artifact_id>/` 子目录做隔离 |

> ⚠️ **本 Skill 不再自行猜测输出位置**：调用方（如 generate_word）必须显式传入 `--output-dir`，通常应指向 pipeline 目录下 stage 的中间产物区（例如 `<workspace>/output/<request_id>/stage2/intermediate/format-extract/`）。不传直接报错退出（exit code 2）。这是"关注点分离"——产物落点由编排层拥有，脚本只负责转换。

> ⚠️ 为什么不在 Skill 里做"是否触发"判定：触发时机强依赖调用方的业务语义（是用户上传的模板？是批处理任务？是质量抽检？），放在本 Skill 里会与调用方耦合。**调用方自己判断"现在要不要跑这个 Skill"，跑的时候把 docx 路径和输出目录都传进来。**

</input-contract>

---

<workflow>

## 标准流程（推荐走 `run.py` 一键入口）

### 步骤 1 — 定位 `run.py`

Skill 的安装位置与当前 Agent 的 cwd 可能不一致，按优先级尝试：

| 优先级 | 策略 |
|--------|------|
| L1 | 调用方显式告知路径（最稳） |
| L2 | 相对 cwd 拼 `src/skills/format-extract/run.py` |
| L3 | 相对 cwd 拼历史笔误目录 `workpace/...`（兼容） |
| L4 | 从 cwd 向上最多 10 级查找包含本 Skill 的祖先目录 |

四级都找不到 → 走 `<failure-fallback>`。

### 步骤 2 — 环境前置（`run.py` 内部会自动检查，手动调 CLI 时需自检）

**P1 — Node.js ≥ 18.0.0**：跑 `node -v`，不达标则参照 [`references/node-install.md`](references/node-install.md) 安装。bundled CLI 依赖 Node 18+ 的原生 API（ESM / `structuredClone` / `fetch`），低版本直接抛 `SyntaxError`。

**P2 — bundled CLI 可用**：跑 `node <skill-dir>/dist/cli.cjs --version`，若报 `Cannot find module` / `ENOENT` 说明分发异常，走失败兜底。**不要用 `npx docx2html` 绕过**——本 Skill 已脱离 npm 全局安装，绕过会引入未知版本差异。

### 步骤 3 — 执行


不传 `--output-dir` 会以 exit code 2 报错退出——本 Skill 不再猜测输出位置。

`run.py` 内部自动完成：

1. **计算 `artifact_id`**：`<docx_stem>_<md5_8>`，例如 `财报_38fb08de`
   - 同一 docx 多次处理 → 相同 artifact_id（**幂等**，复用同一目录）
   - 不同 docx → 不同 artifact_id（**永不互相覆盖**）
   - docx 内容变更 → 哈希变化，自动隔离新旧版本
2. 产物落到 `<--output-dir 值>/<artifact_id>/`（若 `--output-dir` 末尾已是 artifact_id，则原样使用不重复嵌套）：
   - `reference_format.html` — 语义化 HTML
   - `format_artifact.json` — manifest（artifact_id / 源 docx 路径与哈希 / images_dir / 时间戳）
   - `images/` — 提取的图片资产
3. P1 / P2 门禁检查
4. 调用 bundled CLI 完成转换
5. 存在性校验（输出目录 + HTML 产物 + manifest）

> 脚本会在 `--output-dir` 下自动追加 artifact_id，**调用方无需自己拼接哈希**。

### 步骤 4 — 捕获产物路径

**成功时 stdout 最后一行即 HTML 绝对路径**，调用方直接读这一行拿到结果。


同级的 `images/` 与 `format_artifact.json` 与 HTML 在同一 artifact 隔离目录下。
  
若要拿到 manifest 路径，从 HTML path 推导：`Path(html_path).parent / "format_artifact.json"`。

### 步骤 5 — 单次重试策略

首次调用失败（exit ≠ 0）时看 stderr 关键词：

| 错误关键词                               | 应对                                     |
| ----------------------------------- | -------------------------------------- |
| `输入文件不存在`                           | 转绝对路径后重试 1 次                           |
| `缺少必传参数 --output-dir` / exit code 2 | 调用方 bug——补上 `--output-dir` 后重试；不再兜底猜位置 |
| 其它                                  | 不再重试，走失败兜底                             |

> 反复重试超过一次没有意义——同类错误重跑只是在耗时间预算。

## 交付边界

本 Skill 在返回 HTML 绝对路径那一刻**职责结束**。

**调用方接手做的事**（本 Skill 不规定，仅列出常见用法供参考）：

- 读 HTML 分析排版特征（h1/h2 层级、表格样式、段落缩进、字体色……）
- 把产物路径写入自己的上下文 / yaml / 数据库
- 决定如何在下游流程中使用这些特征
- 决定 HTML 或 images 的生命周期（保留 / 清理 / 归档）

Skill 不登记任何外部字段、不修改调用方的任何文件。

</workflow>

---

<failure-fallback>

## 设计理念

格式提取失败不应阻塞调用方的主流程。本 Skill 通过**清晰的退出码 + 详细的 stderr** 让调用方自己决定如何降级——是跳过格式分析继续走、还是直接给用户报错，由调用方的业务逻辑决定，本 Skill 不越俎代庖。

## Skill 侧的契约

| 场景            | exit code | stdout           | stderr                 |
| ------------- | --------- | ---------------- | ---------------------- |
| 转换成功          | 0         | 最后一行 = HTML 绝对路径 | 进度信息                   |
| P1 Node 版本不达标 | 1         | —                | 版本信息 + 提示升级            |
| P2 CLI 不可用    | 1         | —                | `Cannot find module` 等 |
| 输入文件不存在 / 损坏  | 1         | —                | `ENOENT` / 格式错误        |
| 超时 / 其它       | 1         | —                | 具体错误栈                  |

## 调用方侧的建议降级动作


## 对用户的透明化建议

如果选择"继续主流程"，建议调用方在最终响应里向用户透明标&#x6CE8;**"本次未成功提取参考文档格式特征"**，而不是静默降级——对用户隐瞒降级比降级本身更糟糕。

</failure-fallback>

---

<restrictions>

- ❌ **不要自行实现 docx → HTML 逻辑**：必须通过 `run.py` / `cli.cjs` 统一入口，绕开会破坏产物可重现性和后续的 assess 质量检查
- ❌ **不要反复重试超过 2 次**（首次 + 策略性重试 1 次）：同类错误重跑是在耗光 Agent 的时间预算
- ❌ **不要用 `npx docx2html` 绕过 bundled CLI**：本 Skill 已完全脱离 npm 全局安装，绕过会引入未知版本差异
- ❌ **不要在本 Skill 内登记调用方的字段**（如某个 yaml、某个数据库）：产物路径返回给调用方，后续写入由调用方自己决定
- ❌ **不要在 SKILL.md 里引用任何调用方的具体阶段 / 字段 / 脚本名**：Skill 应对调用方保持无知，文档里只讨论"输入 docx / 输出 HTML"

</restrictions>

---

## 可选：独立 HTML 质量评估

怀疑转换质量（如表格错位、冗余 `mso-*` 属性）时，单独跑 assess：


- exit code 0 = 5 项检查全过
- exit code 1 = 有未过项（**不是程序崩溃**），看报告里 `❌` 的条目定位问题

详见 [`references/cli-reference.md`](references/cli-reference.md) 的 assess 章节。

---

## 相关文档

- [`references/cli-reference.md`](references/cli-reference.md) — CLI 参数 / 编程式 API / 返回结构 / 5 项检查维度 / 反模式
- [`references/node-install.md`](references/node-install.md) — Node ≥ 18 跨平台安装脚本