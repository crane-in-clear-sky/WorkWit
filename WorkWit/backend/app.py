import os
from fastapi import FastAPI
from fastapi.responses import FileResponse

from db import init_db

# ---- 业务域子模块（按职责拆分，详见各 *.py）----
import core
import auth
import search
import tools_handlers
import tools_build
import resume
import agent_endpoints
import admin
import mgmt_endpoints

app = FastAPI()
init_db()
# P1④ 启动时把 MCP 配置里的外部工具同步进 tools 表（优雅降级：无配置/失败不阻断启动）
try:
    import mcp_client
    mcp_client.sync_mcp_tools()
except Exception as _e:
    print("MCP 同步跳过：", _e)

# P2⑩ 启动后台调度器（定时自动化）：优雅降级，失败不阻断启动
try:
    import asyncio
    import automation_runner

    @app.on_event("startup")
    async def _start_automation_scheduler():
        asyncio.create_task(automation_runner.scheduler_loop())
except Exception as _e:
    print("自动化调度器启动跳过：", _e)

# ---- 路由注册：各子模块通过 APIRouter 暴露端点 ----
app.include_router(auth.router)
app.include_router(resume.router)
app.include_router(agent_endpoints.router)
app.include_router(admin.router)
app.include_router(mgmt_endpoints.router)


@app.get("/")
async def index():
    return FileResponse("static/index.html")
