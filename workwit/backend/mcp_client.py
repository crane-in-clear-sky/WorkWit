"""MCP 连接器框架（P1④）：解析 /app/data/mcp.json，发现外部 MCP 工具并注册进能力目录。

设计要点：
- MCP 工具在应用启动时（或手动刷新时）经 sync_mcp_tools() 同步进 tools 表
  （backend_type='mcp', scope='global'），复用现有 list_tools→路由 LLM→capability catalog
  →build_session_tools 全链路；执行时由 build_session_tools 的 mcp 分支经 call_mcp_tool 转发到对应 MCP 服务。
- 支持的传输（配置项 transport）：
  - streamable-http（推荐，默认）：MCP 2025-03-26 标准 HTTP 传输。单端点 POST JSON-RPC，
    服务端以 application/json 或 text/event-stream 响应；自动维护 Mcp-Session-Id 会话头、
    支持 Last-Event-ID 断线重连；首个请求为 initialize，随后发 notifications/initialized。
    配置字段：url（必填）+ headers（可选，常用于 Authorization: Bearer）。
  - stdio：本地子进程，stdin/stdout 走 JSON-RPC（每操作独立会话：initialize→操作→退出）。
    配置字段：command + args + env。
  - 兼容别名：旧配置中的 http / sse 一律按 streamable-http 处理（向后兼容）。
- 纯标准库实现，无额外依赖；单服务失败不影响其他服务（优雅降级）。
- 配置持久化：默认 /app/data/mcp.json（随 ./data 挂载持久化，容器重建不丢）；
  兼容旧的 ~/.workbuddy/mcp.json（首次读取时若新路径不存在则回退旧路径，写入永远走新路径）。

标准配置格式（streamable-http 形式）：
{
  "mcpServers": {
    "my_remote": {
      "transport": "streamable-http",
      "url": "https://mcp.example.com/mcp",
      "headers": { "Authorization": "Bearer <token>" }
    },
    "my_local": {
      "transport": "stdio",
      "command": "npx",
      "args": ["-y", "@modelcontextprotocol/server-filesystem", "/data"],
      "env": {}
    }
  }
}
"""
import json
import os
import time
import logging
import shutil
import urllib.request
import subprocess

logger = logging.getLogger("mcp")

# 默认配置路径：/app/data 为 docker-compose 挂载卷，写此处容器重建不丢
DEFAULT_CFG = "/app/data/mcp.json"


# ────────────────────────────────────────────────────────────────────────────
# 配置加载 / 保存
# ────────────────────────────────────────────────────────────────────────────
def _default_cfg_path():
    return os.environ.get("MCP_CONFIG_PATH") or DEFAULT_CFG


def _legacy_cfg_path():
    return os.path.join(os.path.expanduser("~"), ".workbuddy", "mcp.json")


def _cfg_path():
    """写入永远走此路径；读取时若新路径不存在则回退旧路径（兼容迁移）。"""
    return _default_cfg_path()


def _read_cfg_path():
    p = _default_cfg_path()
    if os.path.exists(p):
        return p
    legacy = _legacy_cfg_path()
    if os.path.exists(legacy):
        return legacy
    return p


def load_mcp_config():
    """读取 MCP 配置（运行时用，排除 disabled），返回 [{name, transport, url?, command?, args?, env?}]。无配置/解析失败返回 []。"""
    return [s for s in _load_all_mcp_servers() if not s.get("disabled")]


def _load_all_mcp_servers():
    """读取 MCP 配置（含 disabled），返回全部 server 字典列表。供管理 UI 展示所有配置。"""
    p = _read_cfg_path()
    if not os.path.exists(p):
        logger.info("MCP 配置文件不存在: %s", p)
        return []
    try:
        with open(p, "r", encoding="utf-8") as f:
            data = json.load(f)
    except Exception as e:
        logger.warning("读取 MCP 配置失败 %s: %s", p, e)
        return []
    if not isinstance(data, dict):
        return []
    raw = data.get("mcpServers") if "mcpServers" in data else data
    if not isinstance(raw, dict):
        return []
    servers = []
    for name, cfg in raw.items():
        if not isinstance(cfg, dict):
            continue
        entry = {"name": name, "disabled": bool(cfg.get("disabled"))}
        raw_t = (cfg.get("transport") or "streamable-http").lower()
        # 归一化：旧 http/sse 与多种连字符写法统一为 streamable-http
        if raw_t in ("http", "sse", "streamablehttp", "streamable_http", "streamable-http"):
            raw_t = "streamable-http"
        entry["transport"] = raw_t
        if "url" in cfg:
            entry["url"] = cfg["url"]
            if cfg.get("headers"):
                entry["headers"] = cfg["headers"]
        elif "command" in cfg:
            entry["command"] = cfg["command"]
            entry["env"] = cfg.get("env") or {}
            entry["args"] = cfg.get("args") or []
            entry["transport"] = "stdio"
        else:
            continue
        servers.append(entry)
    logger.info("MCP 配置读取 %s: 解析到 %d 个服务 → %s", p, len(servers), [s["name"] for s in servers])
    return servers


def save_mcp_config(servers):
    """写回 MCP 配置（servers: {name: {transport,url/command,args,env,disabled}}），落盘前自动备份 .bak。返回最终路径。"""
    p = _default_cfg_path()
    d = os.path.dirname(p)
    if d and not os.path.exists(d):
        os.makedirs(d, exist_ok=True)
    if os.path.exists(p):
        try:
            shutil.copyfile(p, p + ".bak")
        except Exception as e:
            logger.warning("MCP 配置备份失败: %s", e)
    data = {"mcpServers": servers}
    try:
        with open(p, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2, ensure_ascii=False)
        logger.info("MCP 配置已写入 %s（%d 个服务）: %s", p, len(servers), list(servers.keys()))
    except Exception as e:
        logger.error("MCP 配置写入失败 %s: %s", p, e)
        raise
    return p


def get_mcp_servers_with_status():
    """管理 UI 用：列出所有已配置 MCP server + 已同步进 tools 表的工具数。"""
    servers = _load_all_mcp_servers()
    try:
        from db import get_conn
        conn = get_conn()
        rows = conn.execute("SELECT target FROM tools WHERE backend_type='mcp'").fetchall()
        conn.close()
        tool_map = {}
        for r in rows:
            tgt = r["target"]
            tool_map[tgt] = tool_map.get(tgt, 0) + 1
    except Exception:
        tool_map = {}
    out = []
    for s in servers:
        item = {
            "name": s["name"],
            "transport": s.get("transport"),
            "url": s.get("url"),
            "command": s.get("command"),
            "disabled": s.get("disabled", False),
            "synced_tools": tool_map.get(s["name"], 0),
        }
        if s.get("headers"):
            item["headers"] = s["headers"]
        out.append(item)
    return out


# ────────────────────────────────────────────────────────────────────────────
# JSON-RPC 传输层
# ────────────────────────────────────────────────────────────────────────────
def _parse_jsonrpc(body):
    if not body or not str(body).strip():
        return None
    obj = json.loads(body)
    if obj.get("error"):
        raise RuntimeError("MCP error: %s" % obj["error"])
    return obj.get("result")


def _parse_sse(text, session=None):
    """解析 text/event-stream 响应：逐行取最后一个 data: 负载作为结果。
    同时捕获 SSE 的 id: 字段，写入 session['headers']['Last-Event-ID'] 以支持断线重连（可恢复性）。"""
    last = None
    last_id = None
    for line in text.splitlines():
        line = line.strip()
        if line.startswith("id:"):
            last_id = line[3:].strip()
        elif line.startswith("data:"):
            last = line[5:].strip()
    if session is not None and last_id:
        session.setdefault("headers", {})["Last-Event-ID"] = last_id
    if not last:
        return None
    return _parse_jsonrpc(last)


def _http_jsonrpc(url, method, params, headers=None, timeout=20, _session=None):
    payload = {"jsonrpc": "2.0", "id": 1, "method": method, "params": params or {}}
    data = json.dumps(payload).encode("utf-8")
    hdrs = {"Content-Type": "application/json",
            "Accept": "application/json, text/event-stream"}
    if headers:
        hdrs.update(headers)
    req = urllib.request.Request(url, data=data, headers=hdrs)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            ctype = resp.headers.get("Content-Type", "")
            sid = resp.headers.get("Mcp-Session-Id") or resp.headers.get("mcp-session-id")
            body = resp.read().decode("utf-8", "ignore")
    except Exception as e:
        raise RuntimeError("HTTP MCP 调用失败: %s" % e)
    result = _parse_sse(body, _session) if "text/event-stream" in ctype else _parse_jsonrpc(body)
    if _session is not None and sid:
        _store = _session.setdefault("headers", {})
        _store["Mcp-Session-Id"] = sid
    return result


def _http_with_session(url, method, params, timeout=20, extra_headers=None):
    """Streamable HTTP 传输：先 initialize 建会话（拿 Mcp-Session-Id），再发 initialized 通知，
    最后带会话头发业务请求。extra_headers 透传自定义请求头（如鉴权 Bearer）。单请求失败不影响流程。"""
    sess = {}
    if extra_headers:
        sess["headers"] = dict(extra_headers)
    try:
        # 1) initialize（首请求，尚无为 Mcp-Session-Id；需带鉴权头）
        _http_jsonrpc(url, "initialize",
                      {"protocolVersion": "2024-11-05", "capabilities": {},
                       "clientInfo": {"name": "ai-office", "version": "1.0"}},
                      headers=sess.get("headers"), timeout=timeout, _session=sess)
        # 2) initialized 通知（无响应期望，失败忽略）
        try:
            _http_jsonrpc(url, "notifications/initialized", {},
                          headers=sess.get("headers"), timeout=timeout)
        except Exception as e:
            logger.debug("MCP initialized 通知无响应（忽略）: %s", e)
    except Exception as e:
        logger.warning("MCP Streamable HTTP initialize 失败（忽略，继续）: %s", e)
    # 3) 业务请求（携带 Mcp-Session-Id 与自定义头）
    return _http_jsonrpc(url, method, params, headers=sess.get("headers"), timeout=timeout)


def _read_json_line(stream, timeout=20):
    """从 stdio stdout 读取一行并解析为 JSON；跳过非 JSON 行（如日志/通知）。"""
    import select
    buf = ""
    end = time.time() + timeout
    while time.time() < end:
        r, _, _ = select.select([stream], [], [], 0.5)
        if not r:
            continue
        ch = stream.read(1)
        if not ch:
            break
        if ch == "\n":
            line = buf.strip()
            buf = ""
            if not line:
                continue
            try:
                return json.loads(line)
            except Exception:
                continue
        else:
            buf += ch
    return None


def _stdio_jsonrpc(cfg, method, params, timeout=25):
    cmd = [cfg["command"]] + list(cfg.get("args") or [])
    env = dict(os.environ)
    env.update(cfg.get("env") or {})
    proc = subprocess.Popen(cmd, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                            stderr=subprocess.PIPE, env=env, text=True)
    try:
        def send(obj):
            proc.stdin.write(json.dumps(obj) + "\n")
            proc.stdin.flush()

        send({"jsonrpc": "2.0", "id": 0, "method": "initialize",
              "params": {"protocolVersion": "2024-11-05", "capabilities": {},
                         "clientInfo": {"name": "ai-office", "version": "1.0"}}})
        _read_json_line(proc.stdout, timeout)
        send({"jsonrpc": "2.0", "id": 1, "method": method, "params": params or {}})
        out = _read_json_line(proc.stdout, timeout)
        return out.get("result") if isinstance(out, dict) else None
    finally:
        try:
            proc.terminate()
        except Exception:
            pass


# ────────────────────────────────────────────────────────────────────────────
# 发现 / 调用（对外接口）
# ────────────────────────────────────────────────────────────────────────────
def discover_mcp_tools():
    """返回 [{server, transport, name, description, input_schema}]。单服务失败不影响其他。"""
    out = []
    for s in load_mcp_config():
        try:
            if s["transport"] == "stdio":
                res = _stdio_jsonrpc(s, "tools/list", {})
            else:
                res = _http_with_session(s["url"], "tools/list", {},
                                         extra_headers=s.get("headers"))
            for t in (res or {}).get("tools") or []:
                out.append({
                    "server": s["name"],
                    "transport": s["transport"],
                    "name": t.get("name"),
                    "description": t.get("description") or "",
                    "input_schema": t.get("inputSchema") or {"type": "object", "properties": {}},
                })
        except Exception as e:
            logger.warning("发现 MCP 服务 %s 工具失败: %s", s.get("name"), e)
    return out


def call_mcp_tool(server_name, tool_name, arguments, timeout=30):
    """调用某 MCP 服务的工具，返回结果字符串。"""
    cfg = next((s for s in load_mcp_config() if s["name"] == server_name), None)
    if not cfg:
        return "MCP 服务不存在：%s" % server_name
    try:
        if cfg["transport"] == "stdio":
            res = _stdio_jsonrpc(cfg, "tools/call",
                                 {"name": tool_name, "arguments": arguments or {}}, timeout=timeout)
        else:
            res = _http_with_session(cfg["url"], "tools/call",
                                     {"name": tool_name, "arguments": arguments or {}},
                                     timeout=timeout, extra_headers=cfg.get("headers"))
    except Exception as e:
        return "MCP 工具调用失败：%s" % e
    if res is None:
        return "MCP 工具无返回"
    if isinstance(res, dict):
        if res.get("isError"):
            return "MCP 工具返回错误：" + json.dumps(res.get("content", ""), ensure_ascii=False)
        parts = []
        for c in (res.get("content") or []):
            if isinstance(c, dict) and c.get("type") == "text":
                parts.append(c.get("text", ""))
            else:
                parts.append(json.dumps(c, ensure_ascii=False))
        return "\n".join(parts) if parts else json.dumps(res, ensure_ascii=False)
    return str(res)


def _call_mcp_tool_sync(meta, args):
    """供 build_session_tools 的 mcp 分支在线程中调用（同步接口）。"""
    return call_mcp_tool(meta["server"], meta["mcp_tool"], args)


# ────────────────────────────────────────────────────────────────────────────
# 注册进 DB（复用现有 tools 全链路）
# ────────────────────────────────────────────────────────────────────────────
def sync_mcp_tools():
    """发现 MCP 工具并 upsert 进 tools 表（backend_type='mcp'）。返回同步数量。"""
    try:
        from db import get_conn
    except Exception as e:
        logger.warning("MCP 同步导入 db 失败: %s", e)
        return 0
    discovered = discover_mcp_tools()
    conn = get_conn()
    now = time.strftime("%Y-%m-%d %H:%M:%S")
    cur = set()
    n = 0
    for t in discovered:
        if not t.get("name") or not t.get("server"):
            continue
        full = "mcp__%s__%s" % (t["server"], t["name"])
        cur.add(full)
        code = json.dumps({"mcp_tool": t["name"], "transport": t["transport"]}, ensure_ascii=False)
        params = json.dumps(t.get("input_schema") or {"type": "object", "properties": {}},
                            ensure_ascii=False)
        trig = "%s,%s" % (t["name"], t["server"])
        conn.execute(
            """INSERT INTO tools (name,display_name,description,category,params_json,
                   backend_type,handler,target,trigger_words,scope,owner_id,enabled,builtin,skip_skill,created_at)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
               ON CONFLICT(name) DO UPDATE SET
                 display_name=excluded.display_name, description=excluded.description,
                 params_json=excluded.params_json, target=excluded.target,
                 trigger_words=excluded.trigger_words, enabled=1""",
            (full, t["name"], t["description"], "mcp", params, "mcp", None,
             t["server"], trig, "global", None, 1, 0, 0, now))
        n += 1
    # 清理已失效的（配置移除的服务对应的工具）
    existing = [r["name"] for r in
                conn.execute("SELECT name FROM tools WHERE backend_type='mcp'").fetchall()]
    for name in existing:
        if name not in cur:
            conn.execute("DELETE FROM tools WHERE name=?", (name,))
    conn.commit()
    logger.info("MCP 同步完成：发现 %d 个工具，落库 %d 个", len(discovered), n)
    return n
