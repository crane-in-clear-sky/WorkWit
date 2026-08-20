> English | [中文版 / Chinese](./README.zh.md)

# WorkWit - Enterprise AI Office Assistant

An enterprise-oriented, privately deployable **AI Agent platform**. The backend is built on FastAPI + SQLite, and the frontend is a zero-dependency, vanilla-JS single-page application. The whole stack is deployed with one command via Docker Compose.

It is far more than a "contract-review gadget" - it is a unified office-agent foundation that integrates **multi-model inference, extensible Skills, Tools, MCP connectors, sandboxed execution, multi-tenant access control, scheduled automation, and long-term memory**. Office scenarios such as contract review and resume screening work out of the box, and new capabilities can be continuously extended through "skills + tools".

---

## Why WorkWit

WorkWit is a self-hosted, enterprise-grade AI Agent platform built to make an organization's office work smarter - without giving up control of your data.

- **Fully open source and self-hosted (MIT).** No subscription, no per-seat fees, no vendor lock-in. Run it on your own infrastructure; your data never leaves your servers.
- **Built for organizations, not just individuals.** Real multi-tenant RBAC (Organization / Department / User) with a single source of truth for permission bits (`FEATURE_REGISTRY`).
- **Office scenarios out of the box.** Contract review (with Party A / Party B stance) and resume screening (job-profile matching score) ship as built-in modules - no assembly required.
- **Trivial to deploy and audit.** One `docker compose up`, a single backend container, SQLite storage, and a zero-dependency frontend. No build step; easy to read, fork, and harden.
- **Unified capability catalog.** Skills, tools, and MCP connectors are merged into one catalog and kept in sync automatically, so adding a capability is one registration, not three integrations.
- **Safe by default.** User-supplied code runs in a restricted sandbox (AST scan + CPU/memory/process/timeout limits + privilege drop); secrets stay in `.env` / `data/` that are git-ignored.
- **Extensible by design.** 40+ built-in skills, a Skills Marketplace, a Tool Library, and the MCP connector framework let you grow capabilities continuously.

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
