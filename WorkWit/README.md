> ?? ???:README.zh.md
WorkWit — Enterprise AI Office Assistant

An enterprise-oriented, privately deployable **AI Agent platform**. The backend is built on FastAPI + SQLite, and the frontend is a zero-dependency, vanilla-JS single-page application. The whole stack is deployed with one command via Docker Compose.

It is far more than a "contract-review gadget" â€” it is a unified office-agent foundation that integrates **multi-model inference, extensible Skills, Tools, MCP connectors, sandboxed execution, multi-tenant access control, scheduled automation, and long-term memory**. Office scenarios such as contract review and resume screening work out of the box, and new capabilities can be continuously extended through "skills + tools".

---

## âœ¨ Core Features

- **General-purpose Agent core**: a ReAct (planâ€“act) loop supporting tool calls, streaming SSE output, and automatic decomposition/execution of multi-step tasks. The model proactively warns when it "claims a file was generated" but never actually invoked a tool (hallucination guard).
- **Skills Marketplace**: file-based skills (`SKILL.md`), with 40+ built-in skills (PPT generation, Word typesetting, WeChat official-account / Xiaohongshu writing, contract/legal, research reports, map compliance, 3D/video, Tencent Docs, etc.). Supports versioning, rollback, visibility control, and install/uninstall.
- **Tool Library**: built-in tools (web search, web fetch, charts, email, text-to-image/video, PPT/Word generation, contract risk analysis, etc.) + user-defined tools (sandboxed execution) + external **MCP connectors**.
- **MCP Connector Framework**: connect to standard MCP servers via `data/mcp.json` (supporting both `streamable-http` and `stdio` transports); external tools are auto-synced into the unified capability catalog.
- **Sandboxed Execution**: user-uploaded Python code runs in a restricted subprocess (AST static scanning + CPU/memory/process/timeout limits + privilege drop + module & file guards).
- **Multi-tenancy & RBAC**: a three-tier structure of Organization / Department / User, with fine-grained feature permission bits (`FEATURE_REGISTRY`); admins can centrally manage models, navigation, SMTP, MCP, etc.
- **Multi-model Access**: OpenAI-compatible interfaces, with four roles â€” `chat / vision / embed / rerank`. Can connect directly to vLLM / Ollama, or use LiteLLM as a unified gateway (a reference `litellm_config.yaml` is included).
- **Automation**: a background scheduler supports scheduled / recurring tasks.
- **Long-term Memory & Conversation History**: user-level long-term memory and cross-session history retrieval.
- **Office scenarios out of the box**: contract review (selectable stance), resume screening (job-profile matching score), etc.

---

## ğŸ§± Tech Stack

| Layer | Choice |
| --- | --- |
| Backend | FastAPI (async), SQLite, Uvicorn |
| Frontend | Vanilla HTML/JS SPA (`backend/static/index.html`, zero build dependencies) |
| Model Access | OpenAI-compatible API (direct or via LiteLLM gateway) |
| Deployment | Docker Compose (single backend container, port `4001:8000`) |
| Execution Isolation | subprocess sandbox (resource limits + AST scanning) |

---

## ğŸ“‚ Directory Structure

```
workwit/
?ÄÄ docker-compose.yml # one-command deploy (backend service only)
?ÄÄ litellm_config.yaml # optional: reference config for the LiteLLM model gateway
?ÄÄ .env.example # env var template (copy to .env and fill in real values)
?ÄÄ README.md
?ÄÄ data/ # runtime data (git-ignored, auto-generated on container start)
³ ?ÄÄ app.db # SQLite main DB (auto-creates tables + seed data on first start)
³ ?ÄÄ artifacts/ # output dir for agent-generated artifacts
³ ?ÄÄ uploads/ # user uploads
³ ?ÄÄ system_assets/ # system assets
³ ÀÄÄ mcp.json # MCP connector config (may contain tokens — do not commit)
ÀÄÄ backend/
?ÄÄ Dockerfile
?ÄÄ requirements.txt
?ÄÄ app.py # FastAPI entrypoint, registers business routers
?ÄÄ db.py # data layer + permission-bit registry (single source of truth)
?ÄÄ auth.py # login / permission gate
?ÄÄ core.py # business core (contract review / resume screening / artifact extraction)
?ÄÄ agent.py # ReAct agent loop (async generator, yields events one by one)
?ÄÄ agent_endpoints.py # agent API (planning / automation / memory / chat)
?ÄÄ tools_build.py # session tool assembly (DB def  handler binding, MCP/sandbox branches)
?ÄÄ tools_handlers.py # tool handler implementations
?ÄÄ llm_adapter.py # multi-model client & capability wrapper
?ÄÄ mcp_client.py # MCP connector framework (streamable-http / stdio)
?ÄÄ sandbox.py # user-code sandbox execution (AST scanning + resource limits)
?ÄÄ automation_runner.py # scheduled automation runner
?ÄÄ admin.py # admin endpoints (models/orgs/depts/users/logs)
?ÄÄ mgmt_endpoints.py # admin endpoints (skills/tools/nav/system)
?ÄÄ resume.py # resume screening endpoints
?ÄÄ search.py # web search / web fetch
?ÄÄ mailer.py # email sending (SMTP)
?ÄÄ builtin_skills/ # 40+ built-in skills (one SKILL.md each)
?ÄÄ builtin_tools/ # built-in tool implementations
ÀÄÄ static/ # frontend SPA (index.html)
```

---

## ğŸš€ Quick Start

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

After startup, open `http://<server-ip>:4001` in a browser. Default admin account: `admin / admin123` (**change it in production** â€” entry: "System Management â†’ User Management").

> If you want to aggregate multiple model backends with LiteLLM, you can spin up a LiteLLM container yourself referencing `litellm_config.yaml` and point the `MODEL_*_BASE` values in `.env` to it; `docker-compose.yml` currently only orchestrates the single `backend` service.

### Upgrade / Rebuild
After code changes:
```bash
docker compose up -d --build
```

---

## âš™ï¸ Configuration (`.env`)

| Variable | Description |
| --- | --- |
| `MODEL_30B_BASE` / `MODEL_30B_KEY` | OpenAI-compatible address & key for the main inference model (chat role) |
| `MODEL_VLM_BASE` / `MODEL_VLM_KEY` | Address & key for the multimodal model (vision role) |
| `MODEL_EMBED_BASE` / `MODEL_EMBED_KEY` | Address & key for the embedding model (embed role) |
| `MODEL_RERANK_BASE` / `MODEL_RERANK_KEY` | Address & key for the rerank model (rerank role) |

> On first start the 4 models are written to `app.db` as seed data from `.env`; afterwards the database is the source of truth and can be adjusted under "System Management â†’ Model Configuration".

Other overridable env vars (for ops tuning): `DB_PATH`, `SKILL_CPU_SEC`, `SKILL_MEM_MB`, `SKILL_NPROC`, `SKILL_TIMEOUT`, `SKILL_ARTIFACT_ROOT`, `MCP_CONFIG_PATH`.

---

## ğŸ§© Feature Modules

- **Contract Review**: upload txt / docx / pdf, select your stance (Party A / Party B), and get structured risk points and revision suggestions (with truncated-JSON repair fallback when parsing fails).
- **Resume Screening**: upload a resume + a job profile, and get the candidate's key fields, a match score, and a recommendation.
- **Agent**: issue tasks in natural language; it auto-plans and invokes skills / tools to complete them. Supports sketch planning, user clarification, and sub-agent delegation.
- **Skills Marketplace / Tool Library**: view, enable, and manage skills & tools; supports creating user-private skills and tools (sandboxed execution).
- **Automation**: configure scheduled / recurring tasks, executed by the background scheduler.
- **My Memory / Conversation History**: user-level long-term memory and cross-session retrieval.
- **System Management**: models, organizations, departments, users, permissions, logs, SMTP, MCP, navigation, and basic info.

---

## ğŸ”’ Security Notes

- **Secret isolation**: `.env` (contains model keys) and `data/` (contains the runtime DB and MCP tokens) are git-ignored â€” do not commit them to the repo.
- **Sandbox**: user-defined code runs in a restricted subprocess, with AST static scanning, CPU / memory / process / timeout limits, privilege drop to `nobody`, and module & file-access guards.
- **Permissions**: feature-level RBAC; admins are allowed by default, regular users get least-privilege permission bits; adding a new nav/feature only requires registering one line in `db.FEATURE_REGISTRY` and it takes effect automatically.
- **Production recommendations**: change the default admin password; enable HTTPS (reverse proxy); keep sensitive config such as MCP tokens in `data/mcp.json` (not in the repo).

---

## ğŸ¤ Contributing

Issues / PRs are welcome. Before submitting, please ensure:

1. The `.gitignore` rules are applied and `.env` / `data/` are not accidentally committed;
2. If a new feature introduces a nav entry, register its permission bit in `backend/db.py`'s `FEATURE_REGISTRY`;
3. New built-in skills go under `backend/builtin_skills/<name>/SKILL.md`, following the existing `when_to_use` semantic-field convention.

---

## ğŸ“œ License

This project is licensed under the [MIT License](./LICENSE). For other licenses (e.g., Apache-2.0, or proprietary/closed-source), please specify separately.

---

## ğŸ—ºï¸ Roadmap (optional directions)

- Visualization & monitoring for sub-agent orchestration;
- More built-in skills / tools (spreadsheets, databases, BI, etc.);
- Multi-instance horizontal scaling (currently single-DB; DB persistence and hot-cache extension points are already reserved);
- Frontend build tooling (currently zero-dependency vanilla JS).