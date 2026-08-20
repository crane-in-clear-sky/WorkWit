> English |

# WorkWit - Enterprise AI Office Assistant

An enterprise-oriented, privately deployable **AI Agent platform**. The backend is built on FastAPI + SQLite, and the frontend is a zero-dependency, vanilla-JS single-page application. The whole stack is deployed with one command via Docker Compose.

It is far more than a "contract-review gadget" - it is a unified office-agent foundation that integrates **multi-model inference, extensible Skills, Tools, MCP connectors, sandboxed execution, multi-tenant access control, scheduled automation, and long-term memory**. Office scenarios such as contract review and resume screening work out of the box, and new capabilities can be continuously extended through "skills + tools".

---

## Core Features

- **General-purpose Agent core**: a ReAct (plan-act) loop supporting tool calls, streaming SSE output, and automatic decomposition/execution of multi-step tasks. The model proactively warns when it "claims a file was generated" but never actually invoked a tool (hallucination guard).
- **Skills Marketplace**: file-based skills (`SKILL.md`), with 40+ built-in skills (PPT generation, Word typesetting, WeChat official-account / Xiaohongshu writing, contract/legal, research reports, map compliance, 3D/video, Tencent Docs, and more). Supports versioning, rollback, visibility control, and install/uninstall.
- **Tool Library**: built-in tools (web search, web fetch, charts, email, text-to-image/video, PPT/Word generation, contract risk analysis, and more) + user-defined tools (sandboxed execution) + external **MCP connectors**.
- **MCP Connector Framework**: connect to standard MCP servers via `data/mcp.json` (supporting both `streamable-http` and `stdio` transports); external tools are auto-synced into the unified capability catalog.
- **Sandboxed Execution**: user-uploaded Python code runs in a restricted subprocess (AST static scanning + CPU/memory/process/timeout limits + privilege drop + module and file guards).
- **Multi-tenancy and RBAC**: a three-tier structure of Organization / Department / User, with fine-grained feature permission bits (`FEATURE_REGISTRY`); admins can centrally manage models, navigation, SMTP, MCP, and more.
- **Multi-model Access**: OpenAI-compatible interfaces, with four roles - `chat / vision / embed / rerank`. Can connect directly to vLLM / Ollama, or use LiteLLM as a unified gateway (a reference `litellm_config.yaml` is included).
- **Automation**: a background scheduler supports scheduled / recurring tasks.
- **Long-term Memory and Conversation History**: user-level long-term memory and cross-session history retrieval.
- **Office scenarios out of the box**: contract review (selectable stance), resume screening (job-profile matching score), and more.

---

## Tech Stack

| Layer | Choice |
| --- | --- |
| Backend | FastAPI (async), SQLite, Uvicorn |
| Frontend | Vanilla HTML/JS SPA (`backend/static/index.html`, zero build dependencies) |
| Model Access | OpenAI-compatible API (direct or via LiteLLM gateway) |
| Deployment | Docker Compose (single backend container, port `4001:8000`) |
| Execution Isolation | subprocess sandbox (resource limits + AST scanning) |

---

## Directory Structure

```
workwit/
├── docker-compose.yml        # one-command deploy (backend service only)
├── litellm_config.yaml       # optional: reference config for the LiteLLM model gateway
├── .env.example              # env var template (copy to .env and fill in real values)
├── README.md
├── data/                     # runtime data (git-ignored, auto-generated on container start)
│   ├── app.db                #   SQLite main DB (auto-creates tables + seed data on first start)
│   ├── artifacts/            #   output dir for agent-generated artifacts
│   ├── uploads/              #   user uploads
│   ├── system_assets/        #   system assets
│   └── mcp.json              #   MCP connector config (may contain tokens - do not commit)
└── backend/
    ├── Dockerfile
    ├── requirements.txt
    ├── app.py                # FastAPI entrypoint, registers business routers
    ├── db.py                 # data layer + permission-bit registry (single source of truth)
    ├── auth.py               # login / permission gate
    ├── core.py               # business core (contract review / resume screening / artifact extraction)
    ├── agent.py              # ReAct agent loop (async generator, yields events one by one)
    ├── agent_endpoints.py    # agent API (planning / automation / memory / chat)
    ├── tools_build.py        # session tool assembly (DB def -> handler binding, MCP/sandbox branches)
    ├── tools_handlers.py     # tool handler implementations
    ├── llm_adapter.py        # multi-model client & capability wrapper
    ├── mcp_client.py         # MCP connector framework (streamable-http / stdio)
    ├── sandbox.py            # user-code sandbox execution (AST scanning + resource limits)
    ├── automation_runner.py  # scheduled automation runner
    ├── admin.py              # admin endpoints (models/orgs/depts/users/logs)
    ├── mgmt_endpoints.py     # admin endpoints (skills/tools/nav/system)
    ├── resume.py             # resume screening endpoints
    ├── search.py             # web search / web fetch
    ├── mailer.py             # email sending (SMTP)
    ├── builtin_skills/       # 40+ built-in skills (one SKILL.md each)
    ├── builtin_tools/        # built-in tool implementations
    └── static/               # frontend SPA (index.html)
```

---

## Quick Start

### Prerequisites

- A Linux server (Ubuntu 22.04 recommended) with Docker (including the docker compose plugin) installed.
- 4 OpenAI-compatible model endpoints (address + key) for `chat / vision / embed / rerank`. These can be local inference services (vLLM / Ollama) or fronted by a LiteLLM gateway.

### Deployment Steps

```bash
# 1) Install Docker (if not already installed)
curl -fsSL https://get.docker.com | sudo sh
sudo usermod -aG docker $USER
# verify after re-logging into the terminal: sudo docker run hello-world

# 2) Upload this repo to the server, e.g. ~/workwit
git clone <your-repo-url> ~/workwit
cd ~/workwit

# 3) Fill in the model config
cp .env.example .env
nano .env     # set the 4 MODEL_*_BASE / MODEL_*_KEY pairs to real addresses and keys
# BASE = model service address, e.g. http://192.168.1.50:8000/v1

# 4) Start
docker compose up -d --build
docker compose logs -f backend   # watch for errors
```

After startup, open `http://<server-ip>:4001` in a browser. Default admin account: `admin / admin123` (**change it in production** - entry: "System Management -> User Management").

> If you want to aggregate multiple model backends with LiteLLM, you can spin up a LiteLLM container yourself referencing `litellm_config.yaml` and point the `MODEL_*_BASE` values in `.env` to it; `docker-compose.yml` currently only orchestrates the single `backend` service.

### Upgrade / Rebuild

After code changes:

```bash
docker compose up -d --build
```

---

## Configuration (`.env`)

| Variable | Description |
| --- | --- |
| `MODEL_30B_BASE` / `MODEL_30B_KEY` | OpenAI-compatible address and key for the main inference model (chat role) |
| `MODEL_VLM_BASE` / `MODEL_VLM_KEY` | Address and key for the multimodal model (vision role) |
| `MODEL_EMBED_BASE` / `MODEL_EMBED_KEY` | Address and key for the embedding model (embed role) |
| `MODEL_RERANK_BASE` / `MODEL_RERANK_KEY` | Address and key for the rerank model (rerank role) |

> On first start the 4 models are written to `app.db` as seed data from `.env`; afterwards the database is the source of truth and can be adjusted under "System Management -> Model Configuration".

Other overridable env vars (for ops tuning): `DB_PATH`, `SKILL_CPU_SEC`, `SKILL_MEM_MB`, `SKILL_NPROC`, `SKILL_TIMEOUT`, `SKILL_ARTIFACT_ROOT`, `MCP_CONFIG_PATH`.

---

## Feature Modules

- **Contract Review**: upload txt / docx / pdf, select your stance (Party A / Party B), and get structured risk points and revision suggestions (with truncated-JSON repair fallback when parsing fails).
- **Resume Screening**: upload a resume + a job profile, and get the candidate's key fields, a match score, and a recommendation.
- **Agent**: issue tasks in natural language; it auto-plans and invokes skills / tools to complete them. Supports sketch planning, user clarification, and sub-agent delegation.
- **Skills Marketplace / Tool Library**: view, enable, and manage skills and tools; supports creating user-private skills and tools (sandboxed execution).
- **Automation**: configure scheduled / recurring tasks, executed by the background scheduler.
- **My Memory / Conversation History**: user-level long-term memory and cross-session retrieval.
- **System Management**: models, organizations, departments, users, permissions, logs, SMTP, MCP, navigation, and basic info.

---

## Security Notes

- **Secret isolation**: `.env` (contains model keys) and `data/` (contains the runtime DB and MCP tokens) are git-ignored - do not commit them to the repo.
- **Sandbox**: user-defined code runs in a restricted subprocess, with AST static scanning, CPU / memory / process / timeout limits, privilege drop to `nobody`, and module and file-access guards.
- **Permissions**: feature-level RBAC; admins are allowed by default, regular users get least-privilege permission bits; adding a new nav/feature only requires registering one line in `db.FEATURE_REGISTRY` and it takes effect automatically.
- **Production recommendations**: change the default admin password; enable HTTPS (reverse proxy); keep sensitive config such as MCP tokens in `data/mcp.json` (not in the repo).

---

## Contributing

Issues / PRs are welcome. Before submitting, please ensure:

1. The `.gitignore` rules are applied and `.env` / `data/` are not accidentally committed;
2. If a new feature introduces a nav entry, register its permission bit in `backend/db.py`'s `FEATURE_REGISTRY`;
3. New built-in skills go under `backend/builtin_skills/<name>/SKILL.md`, following the existing `when_to_use` semantic-field convention.

---

## License

This project is licensed under the [MIT License](./LICENSE). For other licenses (e.g., Apache-2.0, or proprietary/closed-source), please specify separately.

---

## Roadmap (optional directions)

- Visualization and monitoring for sub-agent orchestration;
- More built-in skills / tools (spreadsheets, databases, BI, and more);
- Multi-instance horizontal scaling (currently single-DB; DB persistence and hot-cache extension points are already reserved);
- Frontend build tooling (currently zero-dependency vanilla JS).


> 中文版 | 

# 企业 AI 办公智慧助手（WorkWit）

一个面向企业的、可私有化部署的 **AI Agent（智能体）平台**。后端基于 FastAPI + SQLite，前端是一个零依赖的原生 JS 单页应用，整体用 Docker Compose 一键部署。

它不只是一个「合同审核小工具」——而是一个把 **多模型推理、可扩展技能（Skills）、工具（Tools）、MCP 连接器、沙箱执行、多租户权限、定时自动化、长期记忆** 整合在一起的统一办公智能体底座。开箱内置合同审核、简历筛选等办公场景，也支持通过「技能 + 工具」不断扩展新能力。

---

## ✨ 核心特性

- **通用 Agent 内核**：ReAct（规划—执行）循环，支持工具调用、流式 SSE 输出、多步任务自动拆解与执行；模型「声称已生成文件」但实际未调用工具时会主动提示（防幻觉）。
- **技能广场（Skills）**：文件化的技能（`SKILL.md`），内置 40+ 技能（PPT 生成、Word 排版、公众号/小红书写作、合同/法务、研报、地图合规、3D/视频、腾讯文档等）；支持版本管理、回滚、可见性控制、安装/卸载。
- **工具库（Tools）**：内置工具（联网搜索、网页抓取、图表、邮件、文生图/视频、PPT/Word 生成、合同风险分析等）+ 用户自定义工具（沙箱执行）+ 外部 **MCP 连接器**。
- **MCP 连接器框架**：通过 `data/mcp.json` 接入标准 MCP 服务（支持 `streamable-http` 与 `stdio` 两种传输），外部工具自动同步进统一能力目录。
- **沙箱执行**：用户上传的 Python 代码在受限子进程中运行（AST 静态扫描 + CPU/内存/进程/超时限额 + 降权 + 模块与文件护栏）。
- **多租户 & RBAC**：组织 / 部门 / 用户三级结构，细粒度功能权限位（`FEATURE_REGISTRY`），管理员可集中管控模型、导航、SMTP、MCP 等。
- **多模型接入**：面向 OpenAI 兼容接口，区分 `chat / vision / embed / rerank` 四种角色；可直连 vLLM / Ollama，或用 LiteLLM 做统一网关（仓库附 `litellm_config.yaml` 参考配置）。
- **自动化（Automations）**：后台调度器支持定时 / 周期性任务。
- **长期记忆 & 历史对话**：用户级长期记忆、跨会话历史检索。
- **办公场景开箱即用**：合同审核（可选立场）、简历筛选（岗位画像匹配评分）等。

---

## 🧱 技术栈

| 层 | 选型 |
| --- | --- |
| 后端 | FastAPI（异步）、SQLite、Uvicorn |
| 前端 | 原生 HTML/JS 单页（`backend/static/index.html`，零构建依赖） |
| 模型接入 | OpenAI 兼容 API（直连或经 LiteLLM 网关） |
| 部署 | Docker Compose（单后端容器，端口 `4001:8000`） |
| 执行隔离 | subprocess 沙箱（资源限额 + AST 扫描） |

---

## 📂 目录结构

```
workwit/
├── docker-compose.yml        # 一键部署（仅 backend 一个服务）
├── litellm_config.yaml       # 可选：LiteLLM 模型网关参考配置
├── .env.example              # 环境变量模板（复制为 .env 后填入真实值）
├── README.md
├── data/                     # 运行时数据（已被 .gitignore 忽略，容器启动后自动生成）
│   ├── app.db                #   SQLite 主库（首次启动自动建表 + 种子数据）
│   ├── artifacts/            #   智能体生成的产物落盘目录
│   ├── uploads/              #   用户上传文件
│   ├── system_assets/        #   系统资源
│   └── mcp.json              #   MCP 连接器配置（可含令牌，勿提交）
└── backend/
    ├── Dockerfile
    ├── requirements.txt
    ├── app.py                # FastAPI 入口，注册各业务路由
    ├── db.py                 # 数据层 + 权限位注册表（单一真相源）
    ├── auth.py               # 登录 / 权限门禁
    ├── core.py               # 业务核心（合同审核 / 简历筛选 / 产物提取）
    ├── agent.py              # ReAct 智能体循环（异步生成器，逐事件 yield）
    ├── agent_endpoints.py    # 智能体 API（规划 / 自动化 / 记忆 / 对话）
    ├── tools_build.py        # 会话工具装配（DB 定义 → handler 绑定，MCP/沙箱分支）
    ├── tools_handlers.py     # 工具 handler 实现
    ├── llm_adapter.py        # 多模型客户端与能力封装
    ├── mcp_client.py         # MCP 连接器框架（streamable-http / stdio）
    ├── sandbox.py            # 用户代码沙箱执行（AST 扫描 + 资源限额）
    ├── automation_runner.py  # 定时自动化调度器
    ├── admin.py              # 管理后台端点（模型/组织/部门/用户/日志）
    ├── mgmt_endpoints.py     # 管理后台端点（技能/工具/导航/系统）
    ├── resume.py             # 简历筛选端点
    ├── search.py             # 联网搜索 / 网页抓取
    ├── mailer.py             # 邮件发送（SMTP）
    ├── builtin_skills/       # 40+ 内置技能（每个一个 SKILL.md）
    ├── builtin_tools/        # 内置工具实现
    └── static/               # 前端单页（index.html）
```

---

## 🚀 快速开始

### 前置条件
- 一台 Linux 服务器（推荐 Ubuntu 22.04）并已安装 **Docker**（含 docker compose 插件）。
- 4 个 OpenAI 兼容模型端点的 **地址与密钥**（chat / vision / embed / rerank）。可以是本地推理服务（vLLM / Ollama），也可以前置 LiteLLM 网关。

### 部署步骤

```bash
# 1) 安装 Docker（若未安装）
curl -fsSL https://get.docker.com | sudo sh
sudo usermod -aG docker $USER
# 重新登录终端后验证：sudo docker run hello-world

# 2) 把本仓库传到服务器，例如 ~/workwit
git clone <your-repo-url> ~/workwit
cd ~/workwit

# 3) 填写模型配置
cp .env.example .env
nano .env     # 把 4 组 MODEL_*_BASE / MODEL_*_KEY 改成真实地址和密钥
# BASE 填模型服务地址，例如 http://192.168.1.50:8000/v1

# 4) 启动
docker compose up -d --build
docker compose logs -f backend   # 观察有无报错
```

启动后浏览器访问 `http://服务器IP:4001`。默认管理员账号：`admin / admin123`（**生产环境务必修改**，入口在「系统管理 → 用户管理」）。

> 若想用 LiteLLM 统一聚合多个模型后端，可参考 `litellm_config.yaml` 自行起一个 LiteLLM 容器，并把 `.env` 里的 `MODEL_*_BASE` 指向它；`docker-compose.yml` 当前只编排 `backend` 一个服务。

### 升级 / 重建
代码改动后：
```bash
docker compose up -d --build
```

---

## ⚙️ 配置说明（`.env`）

| 变量 | 说明 |
| --- | --- |
| `MODEL_30B_BASE` / `MODEL_30B_KEY` | 主推理模型（chat 角色）的 OpenAI 兼容地址与密钥 |
| `MODEL_VLM_BASE` / `MODEL_VLM_KEY` | 多模态模型（vision 角色）地址与密钥 |
| `MODEL_EMBED_BASE` / `MODEL_EMBED_KEY` | 嵌入模型（embed 角色）地址与密钥 |
| `MODEL_RERANK_BASE` / `MODEL_RERANK_KEY` | 重排序模型（rerank 角色）地址与密钥 |

> 首次启动会按 `.env` 把 4 个模型写入 `app.db` 作为种子；之后以数据库为准，可在「系统管理 → 模型配置」里调整。

其他可覆盖的环境变量（运维调参）：`DB_PATH`、`SKILL_CPU_SEC`、`SKILL_MEM_MB`、`SKILL_NPROC`、`SKILL_TIMEOUT`、`SKILL_ARTIFACT_ROOT`、`MCP_CONFIG_PATH`。

---

## 🧩 功能模块

- **合同审核**：上传 txt / docx / pdf，选择我方立场（甲方 / 乙方），输出结构化的风险点与修改建议（JSON 解析失败时具备截断修复兜底）。
- **简历筛选**：上传简历 + 岗位画像，输出候选人关键字段、匹配评分与推荐结论。
- **智能体（Agent）**：自然语言下达任务，自动规划并调用技能 / 工具完成；支持草图规划、用户澄清、子 Agent 委派。
- **技能广场 / 工具库**：查看、启用、管理技能与工具；支持创建用户私有技能与工具（沙箱执行）。
- **自动化**：配置定时 / 周期任务，由后台调度器执行。
- **我的记忆 / 历史对话**：用户级长期记忆与跨会话检索。
- **系统管理**：模型、组织、部门、用户、权限、日志、SMTP、MCP、导航、基础信息。

---

## 🔒 安全说明

- **密钥隔离**：`.env`（含模型密钥）与 `data/`（含运行库、MCP 令牌）**已被 `.gitignore` 忽略，请勿提交到仓库**。
- **沙箱**：用户自定义代码在受限子进程中执行，含 AST 静态扫描、CPU / 内存 / 进程 / 超时限额、降权到 `nobody`、模块与文件访问护栏。
- **权限**：功能级 RBAC，管理员默认放行，普通用户按权限位最小授权；新增导航/功能只需在 `db.FEATURE_REGISTRY` 注册一行即自动生效。
- **生产建议**：修改默认管理员密码；启用 HTTPS（反向代理）；MCP 令牌等敏感配置放在 `data/mcp.json`（不入库）。

---

## 🤝 贡献

欢迎 Issue / PR。提交前请确保：

1. 已执行 `.gitignore` 规则，未误提交 `.env` / `data/`；
2. 新功能若引入导航入口，请在 `backend/db.py` 的 `FEATURE_REGISTRY` 登记权限位；
3. 新增内置技能放到 `backend/builtin_skills/<name>/SKILL.md`，遵循现有 `when_to_use` 语义字段约定。

---

## 📜 许可证

本项目采用 [MIT License](./LICENSE)。如需其他许可证（如 Apache-2.0、或私有闭源），请另行说明。

---

## 🗺️ 路线图（可选方向）

- 子 Agent 编排的可视化与监控；
- 更多内置技能 / 工具（表格、数据库、BI 等）；
- 多实例横向扩展（当前为单库，已预留 DB 持久化与热缓存扩展点）；
- 前端构建工具化（当前为零依赖原生 JS）。
