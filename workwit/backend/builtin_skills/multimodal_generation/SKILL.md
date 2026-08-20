---
name: "multimodal_generation"
display_name: "3D 模型与视频特效"
description: ">\n3D模型生成和基于模板的视频特效能力。仅支持：文生3D模型、图生3D模型、基于模板的图片视频特效（video-fx）。\n适用于用户需要 AI 创作 3D 模型或对图片应用模板特效（如拥抱、变身、万物归尘等动效模板）的场景。\n当用户提出以下意图时触发：生成/制作3D模型、对图片应用特效模板或动效模板。\n注意：本技能不处理图片生成、视频生成任务。生成图片请用 generate_image 工具，生成视频请用 generate_video 工具。"
category: "image"
trigger_words: "3D模型,3d模型,视频特效,特效,video-fx,图生视频,文生3D,图生3D"
skill_type: "method"
when_to_use: "用户需要生成 3D 模型或对图片应用视频特效模板时"
allowed_tools: ["generate_image", "generate_video"]
source_name: "3D模型与视频特效"
create_source: "builtin"
---

> ⚠️ **环境适配**：本技能原文调用 ImageGen/VideoGen（ToolSearch+DeferExecuteTool 两步），当前系统已内置 `generate_image` / `generate_video` 工具，直接调用即可；正文中 Bash/PowerShell 执行 SD WebUI 的部分在当前系统不可用，忽略。

# 3D模型与视频特效技能

通过云端服务生成多模态内容，包括 3D 模型生成，以及基于模板的图片视频特效。

> **⚠️ 本技能不处理以下任务（已迁移至内置工具）：**
> - **文生图 / 图生图 / 图片编辑 / 风格转换** → 使用 `generate_image` 工具（通过 工具路由（自动展开） 发现后用 直接调用工具 调用）
> - **文生视频 / 图生视频** → 使用 `generate_video` 工具（通过 工具路由（自动展开） 发现后用 直接调用工具 调用）
>
> 如果用户请求的是上述 4 类任务，**必须停止执行本技能**，改为引导模型使用对应工具。

## 能力概览

| 能力 | 命令 | 说明 |
|------|------|------|
| 视频特效 | `buddy-cloud.py video-fx` | 基于模板的图片转视频特效（支持多图） |
| 3D 模型生成 | `buddy-cloud.py 3d` | 文生3D / 图生3D（异步轮询） |

> 所有能力统一使用 `connect_cloud_service` 认证，详见下方[认证流程](#认证流程)。

---

## 1. 视频特效

将静态图片转化为动态视频片段，基于预设特效模板驱动人物或物体产生动作、变身、互动等效果。目前支持 60+ 个模板，覆盖人物互动、变装变身、物理特效、风格转换等多种类型。

**调用示例：**


**示例用法：**

- 上传一张人像，应用"万物归尘"让人物化作粒子消散（单图：`--image URL`）
- 上传两张人脸照片，应用"亲吻"生成双人互动视频（多图：`--image URL1 --image URL2`）
- 上传一张全身照，应用"变身机甲"将人物改造为未来战士

**部分模板一览（template 取值）：**

| 类型   | 模板名                              | template                                                            |
| ---- | -------------------------------- | ------------------------------------------------------------------- |
| 人物互动 | 拥抱 / 亲吻 / 比心 / 公主抱 / 脸颊贴贴        | `hug` / `kissing` / `hearting` / `bridalcarry` / `cheeks`           |
| 变装变身 | 变身机甲 / 变身美人鱼 / 埃及变装 / 毕业啦 / 赛博朋克 | `futuresoldier` / `mermaidme` / `egyptme` / `graduation` / `cyber`  |
| 物理特效 | 万物归尘 / 飞走了 / 被拽走了 / 面对疾风 / 膨胀    | `return2dust` / `balloonfly` / `dragme` / `windonface` / `morphlab` |
| 风格转换 | 动漫视频 / 卡通视频 / 3D手办风 / 毛茸茸        | `animelive` / `cartoonlive` / `3dfigure` / `fuzzy`                  |
| 趣味互动 | 吃我一拖鞋 / 倒头就是睡 / 被骷髅抓走了 / 捏脸      | `shoehit` / `napme` / `atomy` / `facepinch`                         |

完整模板列表见[官方文档](https://cloud.tencent.com/document/product/1616/119194)。

---

## 2. 3D 模型生成

基于腾讯混元大模型，将文本描述或图片生成高精度 3D 模型。支持文生3D、图生3D、多视角生3D、白模（Geometry）、草图生3D。

### 调用方式


### 参数说明

| 参数                | 类型         | 默认值        | 说明                                                                                                           |
| ----------------- | ---------- | ---------- | ------------------------------------------------------------------------------------------------------------ |
| `prompt`          | positional | —          | 文本描述，中文推荐，≤1024 字符（与 `--image-url` / `--image-base64` 三选一）                                                   |
| `--image-url`     | string     | —          | 图片 URL，分辨率 128~5000px，≤8MB                                                                                   |
| `--image-base64`  | string     | —          | 图片 Base64，分辨率 128~5000px，≤6MB                                                                                |
| `--multi-view`    | JSON       | —          | 多视角图片，格式：`[{"ViewType":"back","ViewImageUrl":"..."}]`。视角可选：left/right/back/top/bottom/left_front/right_front |
| `--model`         | string     | `3.1`      | 模型版本（3.0/3.1），3.1 不支持 LowPoly                                                                                |
| `--enable-pbr`    | flag       | off        | 开启 PBR 材质生成                                                                                                  |
| `--face-count`    | int        | 500000     | 面数，范围 10000~1500000                                                                                          |
| `--generate-type` | string     | `Normal`   | `Normal` / `LowPoly` / `Geometry`（白模）/ `Sketch`（草图）                                                          |
| `--polygon-type`  | string     | `triangle` | 仅 LowPoly 有效：`triangle` / `quadrilateral`                                                                    |
| `--result-format` | string     | obj+glb    | 额外输出格式：`STL` / `USDZ` / `FBX`                                                                                |
| `--no-poll`       | flag       | off        | 仅提交任务，不等待结果，返回 JobId                                                                                         |

### 典型示例


### 输出示例


> **注意**：3D 生成通常需要 1~5 分钟。

---

## Agent 执行规范

### 平台与路径规则（必须遵守）

> **⚠️ Windows WorkBuddy Desktop 必须使用安装包解包目录 `app.asar.unpacked`。禁止猜测或使用 `app.asar/resources/...` 路径。**

1. **macOS / Linux**：使用通用写法 `python3 <SKILL_DIR>/scripts/buddy-cloud.py ...`，不要改成 Windows 路径。
2. **Windows WorkBuddy Desktop**：如果需要显式拼接脚本路径，必须使用：
   或等价的正斜杠路径：
3. **Windows 上优先使用 Bash 工具执行 Python 命令**。如果处于支持 ConPTY 的 WorkBuddy Desktop Windows 环境，也可以使用 PowerShellTool 直接调用 Python。示例：
4. **如果 Windows 环境没有 Bash 工具，只能使用 PowerShell 工具时，禁止使用 `Start-Process` / `saps` / `start` / `cmd /c start`；必须直接调用 Python，让输出通过 PowerShellTool 回传。示例：**
5. **禁止路径**：以下路径在安装包中不存在，绝对不要使用：
6. 如果 `python3` 不可用，可尝试 `python`；但不要改用 WindowsApps 下的 `python.exe` 存根路径。如果仍不可用，或继续执行会导致弹窗/无法兼容 Windows，立即停止当前生成流程，不要继续尝试其他绕过方式，并主动询问用户是否需要协助安装或配置 Git Bash / Python 来创造满足条件的环境；未经用户确认不得自动安装。

### 认证流程

> **⚠️ 每次执行任务前必须重新调用 `connect_cloud_service` 获取最新凭证，禁止复用。**

1. 调用 `connect_cloud_service`（无参数），返回 `tempToken` 和 `token` 两个字段
2. **优先使用 `tempToken`**（临时密钥），不存在时使用 `token`（JWT）兜底
3. **通过 `--token-stdin` 管道方式传入脚本**（可避免 Token 作为 `buddy-cloud.py` 参数或临时文件传入；执行时仍必须避免向用户展示 Token）：
4. **禁止使用以下方式传递 Token**：
   - ❌ `BUDDY_CLOUD_TOKEN="<token>"` 环境变量前缀（Token 会出现在 bash 命令行中）
   - ❌ `--token <value>` 命令行参数（Token 会暴露在进程列表和日志中）
   - ❌ `--token-file` 临时文件方式（存在文件遗留风险）
5. 不向用户暴露 Token 内容，静默处理
6. **禁止缓存或复用**：连续多次生成也必须每次重新获取

### 能力选择规则

| 用户意图               | 执行命令                                                                 |
| ------------------ | -------------------------------------------------------------------- |
| 视频特效（基于模板的图片转视频动效） | `buddy-cloud.py video-fx --template X --image Y [--image Z ...]`     |
| 生成 3D 模型           | `buddy-cloud.py 3d "prompt"` 或 `buddy-cloud.py 3d --image-url "..."` |

> **⚠️ 以下意图不属于本技能，禁止使用 buddy-cloud.py 处理：**
>
> - "生成图片"、"画一张图"、"文生图"、"图生图"、"图片编辑"、"风格转换" → **使用 generate_image 工具**
> - "生成视频"、"文生视频"、"图生视频"、"做一个视频" → **使用 generate_video 工具**
>
> 遇到上述意图时，直接回复引导用户使用对应工具，不要执行任何 bash 命令。

### 首次加载演示

当用户尚未指定具体任务（如说"试试"/"看看效果"）时，Agent 应主动演示一个能力：

1. 优先选择 3D 模型生成
2. 自主设计一个有视觉冲击力的 Prompt
3. 直接执行，不询问确认
4. 输出结果后，简要介绍其他可用能力（视频特效）

### 核心约束

- **零交互原则**：直接执行，不向用户确认参数选择
- **禁止伪造结果**：调用失败时必须返回明确的错误信息，不得编造 URL 或描述生成内容
- **必须下载到本地**：所有生成的资源（视频、3D 模型）都必须先下载到本地文件再展示给用户，禁止直接将远程 URL 作为结果回复
- **Token 安全传输**：必须通过 `--token-stdin` 管道方式传递 Token，不得向用户展示 Token 明文
- **Windows 执行方式**：优先使用 Bash 工具执行命令；处于支持 ConPTY 的 WorkBuddy Desktop Windows 环境时，可使用 PowerShellTool 直接运行普通 console-native 命令
- **视频生成默认**：用户只需提供 Prompt，Agent 自动选择最优模型，不询问版本

### 超时与重试规范（关键！必须严格遵守）

> **⚠️ `buddy-cloud.py` 脚本内部已自带轮询等待机制（默认每 5 秒查一次，最多等 600 秒）。Agent 绝对禁止在脚本外部添加任何 `sleep` + 重新调用的重试逻辑。**

**禁止的模式（绝对不允许）：**


**正确的执行方式：**

1. **直接调用一次，耐心等待脚本自行完成轮询**：
   脚本会自动提交任务并持续轮询直到完成（最多 600 秒），无需 Agent 做任何额外等待或重试。
2. **如果执行命令工具超时**（工具执行有时间限制导致脚本被中断），改用两步模式：
   如果 status 返回仍在处理中（QUEUED/PROCESSING），再等待 30 秒后重新查询 status，**不要重新提交任务**。
3. **重试限制**：
   - 同一个生成请求最多重新提交 **1 次**（即首次 + 1 次重试 = 最多 2 次提交）
   - 2 次提交后仍失败，直接向用户报告错误，不再重试
   - 查询 status 可以多次（最多 10 次，每次间隔 30 秒），但**不要重新提交**

### 产物下载与本地展示（必须执行）

> **⚠️ 关键规则：`buddy-cloud.py` 返回的 `result_url` 是远程 URL，必须先下载到本地文件，再展示给用户。绝对禁止直接将远程 URL 作为结果回复给用户。**

脚本执行成功后（`status` 为 `DONE` 或 `success`），Agent **必须**按以下流程处理产物：

#### 步骤 1：解析脚本输出

从 `buddy-cloud.py` 的 stdout JSON 输出中提取产物 URL：

- **视频生成 / 视频特效**：读取 `result_url` 字段。该字段可能是单个 URL 字符串，也可能是 URL 数组
- **3D 模型生成**：读取 `result_files` 数组，每个元素包含 `type`（文件类型如 glb/obj）、`url`（下载地址）、`preview_image_url`（可选预览图）

#### 步骤 2：下载产物到本地

使用 `execute_command` 工具执行 `curl` 命令将文件下载到**当前工作目录**：


**命名规则：**

- `<timestamp>` 使用当前时间戳（如 `20260421_224500`），确保文件名唯一
- 视频：`generated_video_<timestamp>.mp4`
- 3D 模型：`generated_model_<timestamp>.<ext>`（ext 取自 result_files 中的 type）
- 如果 `result_url` 是数组（多段视频），依次下载为 `generated_video_<timestamp>_1.mp4`、`generated_video_<timestamp>_2.mp4` ...

#### 步骤 2.5：为 3D 模型生成 viewer.html 并启动本地预览（仅 3D 模型必须执行）

当生成的是 3D 模型时，**必须**在下载完 `.glb` 文件后，生成一个 `viewer.html` 并通过本地 HTTP 服务器提供预览。

> **⚠️ 重要**：`model-viewer` 组件内部使用 `fetch()` 加载模型文件。浏览器安全策略不允许 `file://` 协议跨域请求本地文件，且 `.glb` 模型通常有几十 MB，Base64 内联会导致 HTML 过大、浏览器加载失败。因此 **必须通过本地 HTTP 服务器提供文件访问**。

**文件命名**：`generated_model_<timestamp>_viewer.html`（与对应的 `.glb` 文件使用相同的 `<timestamp>`）

**viewer.html 要求**：

- 使用 [model-viewer](https://modelviewer.dev/) 组件（通过 CDN 引入）
- `<model-viewer>` 的 `src` 使用**相对路径**引用同目录下的 `.glb` 文件（如 `src="generated_model_20260421_224500.glb"`）
- 启用以下属性：`camera-controls`、`auto-rotate`、`shadow-intensity="1"`
- 页面全屏展示模型（`<model-viewer>` 宽高设为 `100vw` / `100vh`），背景色使用浅灰（如 `#f0f0f0`）
- 页面 `<title>` 设为 `3D Model Viewer`

**示例 viewer.html 结构**：


**启动本地 HTTP 服务器并预览**：

生成 `viewer.html` 后，**必须**在模型文件所在目录启动一个本地 HTTP 服务器，然后通过 `http://localhost:<port>` 访问：


> **注意**：
>
> - 端口号使用 `18899`（避免与常用端口冲突），如果被占用则依次尝试 `18900`、`18901`
> - HTTP 服务器会在后台运行，在回复用户时告知关闭命令：
>   - macOS / Linux：`kill $(lsof -ti:18899)`
>   - Windows Git Bash（无 lsof）：`netstat -ano | grep :18899 | awk '{print $5}' | head -1 | xargs -I{} taskkill //F //PID {}`
> - 使用 `preview_url` 工具打开 `http://localhost:18899/generated_model_<timestamp>_viewer.html` 让用户直接在 IDE 内预览

#### 步骤 3：展示产物给用户

下载完成后，使用 `系统下载卡片（自动生成）` 工具展示本地文件：

- 视频：调用 `系统下载卡片（自动生成）` 传入本地视频路径
- 3D 模型：调用 `系统下载卡片（自动生成）` 传入 `.glb` 文件路径（优先展示 glb 格式）

#### 步骤 4：回复用户

- 告知用户产物已保存到本地，给出文件的完整路径
- 如果是 3D 模型，告知用户已生成 `viewer.html` 并启动了本地预览服务，可在 IDE 预览窗口或浏览器中查看和交互（支持旋转、缩放）；同时告知关闭服务器的命令：`kill $(lsof -ti:18899)`
- 如果 `result_url` 是数组（多段视频），在回复中列出所有本地文件路径

#### 异常处理

- 如果 `curl` 下载失败（如网络超时、URL 过期），向用户说明下载失败原因，并提供原始 URL 供用户手动下载
- 如果 `系统下载卡片（自动生成）` 工具不可用或调用失败，直接告知用户文件保存路径，由用户自行打开

---

## buddy-cloud.py 脚本参考

### 命令

| 命令                                                | 说明     | 示例                                                                 |
| ------------------------------------------------- | ------ | ------------------------------------------------------------------ |
| `video-fx --template T --image URL`               | 单图视频特效 | `buddy-cloud.py video-fx --template return2dust --image URL`       |
| `video-fx --template T --image URL1 --image URL2` | 多图视频特效 | `buddy-cloud.py video-fx --template hug --image URL1 --image URL2` |
| `3d "prompt"`                                     | 文生3D   | `buddy-cloud.py 3d "一条巨龙"`                                         |
| `3d --image-url URL`                              | 图生3D   | `buddy-cloud.py 3d --image-url "https://..."`                      |
| `status <job_id> --type TYPE`                     | 查询任务状态 | `buddy-cloud.py status abc123 --type 3d`                           |

### 全局选项

Token 传递方式详见上方[认证流程](#认证流程)。Agent **必须使用 `--token-stdin` 管道方式**，禁止在命令行中直接暴露 Token。

| 选项                | 类型     | 默认值      | 说明        |
| ----------------- | ------ | -------- | --------- |
| `--endpoint`      | string | 生产环境 URL | 服务端点      |
| `--no-poll`       | flag   | off      | 仅提交，不等待结果 |
| `--poll-interval` | int    | 5        | 轮询间隔（秒）   |
| `--max-poll-time` | int    | 600      | 最大等待时间（秒） |

### 输出格式

所有输出为 JSON（stdout），进度日志输出到 stderr。

**成功：**


**失败：**


---

## 依赖

- Python 3.7+
- `requests`（脚本自动安装）