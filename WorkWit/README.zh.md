> 🌐 English version: [README.md](./README.md)

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
    ├── mcp_client.py        # MCP 连接器框架（streamable-http / stdio）
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
