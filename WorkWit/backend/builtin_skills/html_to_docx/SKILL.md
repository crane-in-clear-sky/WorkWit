---
name: "html_to_docx"
display_name: "HTML 转 Word"
description: "|\n将 HTML 字符串或文件高保真转换为 Microsoft Word (.docx) 文档。\n支持 CSS 变量预处理、10+ 种 HTML 元素精确映射、14 种 CSS 属性映射、中文字体、4 种装饰组件、TOC 目录、页眉页脚、图片嵌入。\n提供 CLI（subprocess）和 Python API 双模式调用；在需要将 HTML 文档转为可编辑 Word 文件时使用此 skill。"
category: "file"
trigger_words: "html转docx,html转word,HTML转文档,高保真转换"
skill_type: "method"
when_to_use: "将 HTML 字符串或文件高保真转换为 Microsoft Word (.docx) 文档"
allowed_tools: ["generate_word"]
source_name: "html-to-docx"
create_source: "builtin"
---

> ⚠️ **环境适配**：本技能原文用本地 CLI/subprocess 转 HTML→DOCX，当前系统无该 CLI，改用沙箱 `run_temp_code`（python-docx）完成转换，或直接 `generate_word` 生成。

# html-to-docx Skill

## 概述

`html-to-docx` 是一个 Python 转换引擎，支持：

- CSS 变量（`var(--x)`）预处理
- 10+ 种 HTML 元素精确映射（标题/段落/表格/列表/图片/blockquote 等）
- 14 种 CSS 属性映射（字体/颜色/缩进/行距/对齐等）
- 中文字体支持（宋体/黑体/仿宋/楷体，含 `rFonts.eastAsia` 设置）
- 4 种装饰组件（callout / divider / section-marker / data-card）+ data-card-grid
- 语义 `<section role>` 分节 + CSS `@page` 页眉页脚 + 动态域（PAGE/NUMPAGES/STYLEREF）
- TOC 目录渲染（3 级）
- 页眉/页脚渲染
- 图片嵌入（base64 / 本地路径 / 远程 URL）
- CLI 和 Python API 双模式调用

---

## 调用方式

### 前置条件：Python 环境与依赖安装

本 Skill 使用 **托管 venv**（默认 `$HOME/.venv-html-to-docx`）运行，避免污染系统 Python，也绕开 IDE safe-delete 拦截与跨机器 venv 二进制损坏。**环境准备已封装为幂等脚本**，首次 ~30s、之后 ~0.5s，agent 无需自行 `python -m venv` / `pip install`。

> ⚠️ **首次调用需要外网**（幂等脚本首跑会连以下三处公网）：
> - `https://astral.sh/uv/install.sh` — 装 uv（若本机未装）
> - `astral-sh/python-build-standalone` GitHub Releases — 拉 Python 3.12 独立发行版（uv 托管）
> - `https://pypi.org/simple` — 下载 `python-docx` / `lxml` / `Pillow` 等 wheel
>
> 私有化 / 无外网环境：请在**首次调用前**导出以下环境变量指向内网镜像（uv 与 pip 均遵循）：
> ```bash
> export UV_INDEX_URL="https://<内网 PyPI 镜像>/simple"
> export UV_PYTHON_INSTALL_MIRROR="https://<内网 python-build-standalone 镜像>"
> # uv 本身请预先由运维分发到 $HOME/.local/bin/uv，脚本探测到已装则跳过 curl 安装
> ```
> 未配置镜像且无外网时，`setup-html-to-docx.sh` 首跑必然失败——这是**已知交付前置条件**，非 skill 缺陷。**Agent 在私有化交付场景下调用本 skill 前，应先确认 IOA/运维已完成上述环境变量或离线 wheel 分发**，否则应转 Markdown 降级（见"错误处理与降级"）而非重试。

**统一入口（推荐，workbuddy / local 通道一键装齐）**：


**单独准备本 Skill 的环境**：


> 说明：本 skill 的环境安装脚本已从 `<skill_root>/scripts/setup_env.sh` 挪到
>   
> `<workspace>/src/scripts/wb/local/setup-html-to-docx.sh`（workbuddy 本地通道专属）。
>   
> plugin 打包后位于 `<plugin_root>/scripts/wb/local/setup-html-to-docx.sh`。

脚本输出末尾会打印 `Python runner: <venv>/bin/python`，可直接 `export HTML_TO_DOCX_PY=<那个路径>` 供后续调用。

**调用（首选：环境变量方式）**：


**调用（备选：一行式，用于 subprocess）**：


> **Agent 集成提示**：
>
> - `setup-html-to-docx.sh` 是幂等的，可以每次调用前无脑跑一遍，已就绪时秒退。
> - `cwd` 需要设为 `<skill_root>/scripts`（`html_to_docx` 包在此），或用 `PYTHONPATH=<skill_root>/scripts`。
> - 需要把 venv 落到别处时，调用前 `export HTML_TO_DOCX_VENV=/自定义/路径` 即可。
> - **禁止**在 `<skill_root>/scripts/` 下自建 `.venv/`——那是过去踩过的坑（跨机器 shebang 失效、rm 被 safe-delete 拦）。
> - Plugin 部署态下，SessionStart hook 已经自动跑过一次安装脚本，agent 调用时通常无需再手动 bash。

**依赖清单**：以 `<skill_root>/scripts/requirements.txt` 为准，`setup-html-to-docx.sh` 用 `--only-binary=:all:` 强制装 wheel，规避 lxml 在无 libxml2/libxslt 环境下源码构建失败。

**Python 版本要求**：Python 3.12（由 `setup-html-to-docx.sh` 通过 `uv` 自动安装并锁定）。

---

### CLI（Subprocess 模式）

**路径定位**：`html_to_docx` 包位于本 Skill 的 `scripts/` 目录下。**必须**使用 `setup-html-to-docx.sh` 输出的托管 venv 解释器（下文简写为 `$HTML_TO_DOCX_PY`），并把工作目录切换到 `scripts/` 目录，或通过 `PYTHONPATH` 指定：


其中 `<skill_root>` 为本 Skill 的绝对路径，即：


> **Agent 集成提示**：在 subprocess 调用时，推荐使用 `cwd = path.join(skillRoot, "scripts")` + `executable = $HTML_TO_DOCX_PY`，无需设置 PYTHONPATH。**不要**使用系统 `python` / `python3`——依赖装在托管 venv 里，系统解释器会 `ModuleNotFoundError`。

**参数**：

| 参数                | 默认值        | 说明                       |
| ----------------- | ---------- | ------------------------ |
| `input_path`      | （必须）       | 输入 HTML 文件路径             |
| `--output / -o`   | 自动生成       | 输出 .docx 文件路径            |
| `--page-size`     | `A4`       | `A4` / `Letter` / `A3`   |
| `--orientation`   | `portrait` | `portrait` / `landscape` |
| `--margin-top`    | `2.54`     | 上边距（cm）                  |
| `--margin-bottom` | `2.54`     | 下边距（cm）                  |
| `--margin-left`   | `3.17`     | 左边距（cm）                  |
| `--margin-right`  | `3.17`     | 右边距（cm）                  |

**退出码**：

- `0` — 成功，stdout 输出 JSON `ConvertResult`
- `1` — 失败，stderr 输出 JSON 错误信息

**输出示例（成功）**：


**输出示例（失败）**：


### Python API 模式


---

## 输入契约

### HTML 字符串规范

支持：

- 标准 HTML5 结构（`<body>` 内容）
- CSS 变量（`<style>:root { --color: red; }</style>`）
- 特殊区域标签：
  - `<header class="doc-header">` — 文档页眉
  - `<footer class="doc-footer">` — 文档页脚
  - `<nav class="doc-toc">` — 目录
  - `<div data-component="callout|divider|section-marker|data-card">` — 装饰组件
  - `<section role="...">` — 语义分节（自动映射 docx section，详见下）
  - `<style>@page { ... }</style>` — CSS Paged Media 页眉页脚模型（详见下）
  - `<div class="data-card-grid">` — 卡片网格容器
  - `<span data-docx-field="party_a" data-placeholder="请输入甲方名称"></span>` — 行内待填写字段，转换后写为 DOCX 书签

#### 待填写字段与 DOCX 书签

对合同、报价单等需要后续精确填写的位置，使用行内 `span`：


- `data-docx-field` 必填，须在同一 HTML 中唯一，并匹配
    
  `^[A-Za-z][A-Za-z0-9_-]{0,63}$`。
- `data-placeholder` 可选；未提供时显示默认待填写提示。
- 仅支持同一段落或表格单元格内的行内字段；不支持跨段落、页眉页脚或重复行字段。
- 成功转换的 `ConvertResult.fields` 和 CLI 成功 JSON 会返回字段键与实际 `bookmark_name` 映射。后续编辑必须按该书签定位；字段缺失时不得回退为全文文本替换。

#### 语义 Section 与 CSS @page 页面模型

**1. 语义分节 `<section>`**

`<body>` 下的**顶层** `<section>` 按 DOM 顺序映射为独立 docx section，节边界自动插入分节符（`nextPage`）。无 `<section>` 时按单节处理（向后兼容）。


| 属性                                  | 说明                                       | 默认值  |
| ----------------------------------- | ---------------------------------------- | ---- |
| `role`                              | 开放字符串；用于 `section[role=X]{page:Y}` 命名页绑定 | 无    |
| `data-orientation`                  | `portrait` / `landscape`（尺寸取文档默认并交换 w/h） | 文档默认 |
| `data-margin-top/bottom/left/right` | 该节页边距（cm），per-property 合并，未设项回落文档默认      | 文档默认 |
| `data-page-restart`                 | 该节页码起始值（写入 `pgNumType@w:start`）          | 不重起  |

> 嵌套 `<section>` 仅顶层计为节，内层降级为普通块 + warning。请把封面/正文内的语义块用 `<div>` 表达。

**2. CSS `@page` 页眉页脚（CSS Paged Media 有界子集）**

在 `<style>` 中用 `@page` 声明 6 个 margin-box（`@top-left/center/right`、`@bottom-left/center/right`）的页眉页脚内容；命名页 `@page <name>` + `section[role=X]{page:Y}` 实现"封面无、正文有"。


| `content` 取值     | 渲染为                    | 说明                                                         |
| ---------------- | ---------------------- | ---------------------------------------------------------- |
| `counter(page)`  | Word `PAGE` 域          | 当前页码                                                       |
| `counter(pages)` | Word `NUMPAGES` 域      | 总页数（原生物理总数，不减一）                                            |
| `string(<name>)` | `STYLEREF <styleId>` 域 | 配合 `string-set`，引用样式 styleId（如裸 `h1`→`Heading1`，locale 无关） |
| `"文本"` / `'文本'`  | 普通文本                   | 字符串字面量                                                     |
| `none`           | 不产内容                   | 某节相关 box 全 `none` → 真正无页眉/页脚（封面）                           |

> 域写入后自动置位 `<w:updateFields val="true"/>`，Word 打开/按 F9 即刷新。
>   
> 同区域多个 box 合并进单段落，用 Tab 制表位（左/中/右）定位；连续节共用同一 `@page` 时继承同一 header/footer part（横向节也继承、不重算制表位）。
>
> **边界（Python 解析有界子集 C-CON-001）**：仅支持上述子集；不支持的语法（如 `@page :first` 伪类、`@page { size: ... }`）→ 忽略 + warning，绝不中断转换。`content: ""`（空字符串）非法 → 等同未设置 + warning（沿用上一节继承）。

### ConvertOptions 参数


---

## 输出契约


---

## 错误处理与降级

| 场景                | 行为                                                                  |
| ----------------- | ------------------------------------------------------------------- |
| 整体转换失败            | `success=False` + `error` + `markdown_fallback`（bs4 提取文本转 Markdown） |
| 单个组件渲染失败          | 跳过该组件 + 追加 `warnings`，不中断整体转换                                       |
| 图片下载超时（5s）        | 插入占位段落 `[图片: URL]`                                                  |
| 图片文件不存在           | 插入占位段落 `[图片无法加载]`                                                   |
| 未知 data-component | 跳过 + 追加警告                                                           |

---

## 性能

| 文档规模     | P95 目标   | 实测参考  |
| -------- | -------- | ----- |
| 10 页（典型） | ≤ 3000ms | ~87ms |

---

## 组件扩展

添加新组件只需：

1. 创建 `html_to_docx/components/<name>.py`
2. 使用 `@register("<component-type>")` 装饰渲染函数
3. 无需修改其他文件（自动发现机制）


调用方式：`<div data-component="my-widget">内容</div>`

---

## 已知限制

- 复杂嵌套 colspan/rowspan 表格可能有偏差
- CSS `background-color` 仅部分支持（需 python-docx 表格单元格）
- 远程图片依赖网络，超时（5s）时降级为占位文本
- Word 2007 以下格式不支持