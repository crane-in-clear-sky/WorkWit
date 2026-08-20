"""纯数据层：不依赖 fastapi，便于单独单元测试。
所有数据库操作集中在此；app.py 只做 HTTP 接线。
"""
import os, sqlite3, hashlib, secrets, json
from datetime import datetime, timezone, timedelta

# 内置技能（文件化形态 builtin_skills/<name>/SKILL.md）：延迟导入避免循环依赖
try:
    from builtin_skills import BUILTIN_SKILLS
except Exception:  # pragma: no cover
    BUILTIN_SKILLS = []

DB_PATH = os.environ.get("DB_PATH", "/app/data/app.db")
os.makedirs(os.path.dirname(DB_PATH), exist_ok=True)

# 四个模型角色 -> 对应的 .env 变量（仅首次启动 seeding，之后以数据库为准）
SEED = {
    "chat":   ("MODEL_30B_BASE",   "MODEL_30B_KEY",   "MODEL_30B_NAME",   "本地30B主推理",  "local-30b"),
    "vision": ("MODEL_VLM_BASE",   "MODEL_VLM_KEY",   "MODEL_VLM_NAME",   "本地32B多模态", "local-32b-vlm"),
    "embed":  ("MODEL_EMBED_BASE", "MODEL_EMBED_KEY", "MODEL_EMBED_NAME", "本地嵌入模型",  "local-embed"),
    "rerank": ("MODEL_RERANK_BASE","MODEL_RERANK_KEY","MODEL_RERANK_NAME","本地重排序模型","local-rerank"),
}
ROLES = list(SEED.keys())
MASK = "********"
DEFAULT_ADMIN = ("admin", "admin123")

# 功能权限位：单一权威注册表。
# 规则：
#   - tuple 顺序：(key, label, group, nav_id)
#   - group ∈ {"biz", "ai", "memory", "admin"}，前端按 group 分组渲染
#   - nav_id 是导航元素的 DOM id（用于前端动态显示/隐藏对应入口；可为 null）
#   - 管理员(role=admin)在 has_permission 里直接放行，无需在表里写
#   - 新增任何导航/功能位时，**只在这里加一行**，用户管理表单与前端 nav 都会自动出现
FEATURE_REGISTRY = (
    # —— 业务功能（合同 / 简历）
    ("review",    "合同审核",   "biz",    "nav-review"),
    ("resume",    "简历筛选",   "biz",    "nav-resume"),
    # —— 智能相关
    ("agent",     "智能体",     "ai",     "nav-agent"),
    ("tools",     "全局工具库", "ai",     "nav-tools"),
    ("skills",    "技能广场",   "ai",     "nav-skills"),
    # —— 自动化 & 个人记忆
    ("automations", "自动化",   "memory", "nav-automations"),
    ("memories",  "我的记忆",   "memory", "nav-memories"),
    ("history",   "历史对话",   "memory", "nav-history"),
    ("profile",   "我的画像",   "memory", "nav-profile"),
    # —— 系统管理子页
    ("m_models",  "模型配置",   "admin",  "sub-model"),
    ("m_orgs",    "组织管理",   "admin",  "sub-org"),
    ("m_depts",   "部门管理",   "admin",  "sub-dept"),
    ("m_users",   "用户管理",   "admin",  "sub-user"),
    ("m_logs",    "日志管理",   "admin",  "sub-log"),
    ("m_smtp",    "邮件服务器", "admin",  "sub-smtp"),
    ("m_mcp",     "MCP 配置",   "admin",  "sub-mcp"),
    ("m_nav",     "导航设置",   "admin",  "sub-nav"),
    ("m_system",  "基础信息",   "admin",  "sub-system"),
)

PERM_GROUPS = ("biz", "ai", "memory", "admin")
PERM_GROUP_LABELS = {
    "biz":    "业务功能",
    "ai":     "智能与工具",
    "memory": "自动化与记忆",
    "admin":  "系统管理",
}
PERMS         = [k for k, *_ in FEATURE_REGISTRY]
PERM_LABELS   = {k: label for k, label, *_ in FEATURE_REGISTRY}
PERM_NAV_IDS  = {k: nav_id for k, _, _, nav_id in FEATURE_REGISTRY}
# 默认新建用户不勾的项（一般是高风险的管理类）
DEFAULT_DENY = {"admin"}


def _default_user_perms():
    """新用户（非管理员）默认授权的导航权限——所有非 admin 类。

    推导自注册表（category != "admin"），不再写死白名单：将来 FEATURE_REGISTRY
    加新的 biz/ai/memory 类权限会自动跟上，admin 类始终不发给普通用户。

    注意：仅在 create_user 时调用一次（不再放 init_db），否则每次部署都会把
    admin 手动从用户身上移除的权限又加回来——这是 2026-08-19 的 bug 修复。
    """
    return [k for k, _, cat, *_ in FEATURE_REGISTRY if cat != "admin"]


# ---------- 基础 ----------
def get_conn():
    conn = sqlite3.connect(DB_PATH, timeout=30)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA busy_timeout=30000")  # 并发写时短暂等待，避免 database is locked
    return conn


def now():
    """返回东八区（中国时区）的当前本地时间字符串，格式 '%Y-%m-%d %H:%M:%S'。

    设计动机：容器默认时区是 UTC，若直接 datetime.now() 会得到 UTC 时刻；
    而前端直接显示这个字符串而不做时区转换，会导致历史对话/记忆/工具更新时间
    比中国时区早 8 小时。强制 +8 时区后，新数据写入即正确。
    历史 UTC 数据通过 migrate_tz.py 一次性 +8 小时修正。
    """
    return datetime.now(timezone(timedelta(hours=8))).strftime("%Y-%m-%d %H:%M:%S")


def row_to_dict(row):
    return {k: row[k] for k in row.keys()}


def to_int(v):
    try:
        return int(v)
    except Exception:
        return None


def _migrate_models(conn):
    """给 models 表补加列（兼容升级前的旧库）。"""
    cols = {r[1] for r in conn.execute("PRAGMA table_info(models)").fetchall()}
    alters = [
        ("max_tokens", "INTEGER NOT NULL DEFAULT 0"),   # 0 = 不限制（用接口默认）
        ("temperature", "REAL NOT NULL DEFAULT -1"),      # -1 = 不设置（用各业务默认）
        ("top_p", "REAL NOT NULL DEFAULT -1"),            # -1 = 不设置
        ("thinking", "INTEGER NOT NULL DEFAULT 0"),       # 0/1：是否启用思考模式(enable_thinking)
        ("supports_tools", "INTEGER NOT NULL DEFAULT 1"),  # 0/1：是否支持原生 function calling（0=走文本模式降级）
        ("timeout", "INTEGER NOT NULL DEFAULT 0"),         # 单次请求超时(秒)，0=用默认(180)
        ("max_steps", "INTEGER NOT NULL DEFAULT 8"),       # 智能体单次任务最大规划-执行轮数（防失控/死循环），<1 按 8 兜底
        ("enabled", "INTEGER NOT NULL DEFAULT 1"),        # 0/1：模型是否启用（同一 role 可多启用）
        ("provider", "TEXT NOT NULL DEFAULT 'openai_compatible'"),  # 多模态供应商：openai / openai_compatible / tencent / local
        ("extra", "TEXT NOT NULL DEFAULT '{}'"),          # 供应商特定参数（JSON，如图片尺寸/采样步数等）
    ]
    for col, ddl in alters:
        if col not in cols:
            conn.execute(f"ALTER TABLE models ADD COLUMN {col} {ddl}")


def _migrate_smtp(conn):
    """邮件相关列兼容升级：automations 增加 notify_email（完成后邮件通知）。"""
    cols = {r[1] for r in conn.execute("PRAGMA table_info(automations)").fetchall()}
    if "notify_email" not in cols:
        conn.execute("ALTER TABLE automations ADD COLUMN notify_email TEXT NOT NULL DEFAULT ''")


def init_db():
    conn = get_conn()
    conn.execute("""CREATE TABLE IF NOT EXISTS models (
        id INTEGER PRIMARY KEY AUTOINCREMENT, role TEXT NOT NULL, name TEXT NOT NULL,
        base_url TEXT NOT NULL, api_key TEXT NOT NULL, model_name TEXT NOT NULL,
        is_active INTEGER NOT NULL DEFAULT 0, enabled INTEGER NOT NULL DEFAULT 1,
        max_steps INTEGER NOT NULL DEFAULT 8,
        note TEXT, updated_at TEXT)""")
    _migrate_models(conn)
    conn.execute("""CREATE TABLE IF NOT EXISTS orgs (
        id INTEGER PRIMARY KEY AUTOINCREMENT, name TEXT NOT NULL,
        parent_id INTEGER, sort INTEGER DEFAULT 0, created_at TEXT)""")
    conn.execute("""CREATE TABLE IF NOT EXISTS departments (
        id INTEGER PRIMARY KEY AUTOINCREMENT, org_id INTEGER, name TEXT NOT NULL,
        parent_id INTEGER, created_at TEXT)""")
    conn.execute("""CREATE TABLE IF NOT EXISTS users (
        id INTEGER PRIMARY KEY AUTOINCREMENT, username TEXT UNIQUE NOT NULL,
        password_hash TEXT NOT NULL, display_name TEXT, org_id INTEGER, dept_id INTEGER,
        role TEXT NOT NULL DEFAULT 'user', status TEXT NOT NULL DEFAULT 'active',
        created_at TEXT)""")
    conn.execute("""CREATE TABLE IF NOT EXISTS logs (
        id INTEGER PRIMARY KEY AUTOINCREMENT, user_id INTEGER, username TEXT,
        action TEXT, target TEXT, detail TEXT, ip TEXT, created_at TEXT)""")
    conn.execute("""CREATE TABLE IF NOT EXISTS sessions (
        id INTEGER PRIMARY KEY AUTOINCREMENT, user_id INTEGER NOT NULL,
        token TEXT UNIQUE NOT NULL, expires_at TEXT, created_at TEXT)""")
    conn.execute("""CREATE TABLE IF NOT EXISTS user_permissions (
        id INTEGER PRIMARY KEY AUTOINCREMENT, user_id INTEGER NOT NULL,
        perm_key TEXT NOT NULL, UNIQUE(user_id, perm_key))""")
    conn.execute("""CREATE TABLE IF NOT EXISTS user_perm_denies (
        id INTEGER PRIMARY KEY AUTOINCREMENT, user_id INTEGER NOT NULL,
        perm_key TEXT NOT NULL, UNIQUE(user_id, perm_key))""")
    conn.execute("""CREATE TABLE IF NOT EXISTS app_meta (
        key TEXT PRIMARY KEY, value TEXT)""")
    conn.execute("""CREATE TABLE IF NOT EXISTS nav_settings (
        feature_key TEXT PRIMARY KEY,
        order_index INTEGER NOT NULL DEFAULT 0,
        visible INTEGER NOT NULL DEFAULT 1,
        updated_at TEXT)""")
    conn.execute("""CREATE TABLE IF NOT EXISTS system_settings (
        key TEXT PRIMARY KEY, value TEXT, updated_at TEXT)""")
    conn.execute("""CREATE TABLE IF NOT EXISTS tools (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        name TEXT UNIQUE NOT NULL,
        display_name TEXT NOT NULL,
        description TEXT NOT NULL,
        category TEXT NOT NULL DEFAULT 'general',
        params_json TEXT NOT NULL DEFAULT '{}',
        backend_type TEXT NOT NULL DEFAULT 'builtin',
        handler TEXT,
        target TEXT,
        trigger_words TEXT NOT NULL DEFAULT '',
        scope TEXT NOT NULL DEFAULT 'global',
        owner_id INTEGER,
        enabled INTEGER NOT NULL DEFAULT 1,
        builtin INTEGER NOT NULL DEFAULT 0,
        skip_skill INTEGER NOT NULL DEFAULT 0,
        call_count INTEGER NOT NULL DEFAULT 0,
        created_at TEXT)""")

    # 模型初始 seeding（仅当 models 表为空）
    if conn.execute("SELECT COUNT(*) AS c FROM models").fetchone()["c"] == 0:
        for role, (bk, kk, nk, label, dn) in SEED.items():
            base = os.environ.get(bk, "").strip()
            if not base:
                continue
            conn.execute(
                "INSERT INTO models (role,name,base_url,api_key,model_name,is_active,enabled,note,updated_at) "
                "VALUES (?,?,?,?,?,1,1,?,?)",
                (role, label, base, os.environ.get(kk, ""),
                 os.environ.get(nk, "") or dn, "初始配置（来自 .env）", now()),
            )

    # 默认管理员（仅当 users 表为空）
    if conn.execute("SELECT COUNT(*) AS c FROM users").fetchone()["c"] == 0:
        conn.execute(
            "INSERT INTO users (username,password_hash,display_name,role,status,created_at) "
            "VALUES (?,?,?,?,?,?)",
            (DEFAULT_ADMIN[0], hash_password(DEFAULT_ADMIN[1]), "系统管理员", "admin", "active", now()),
        )
    # 全局工具库：首次启动写入内置工具定义（之后以数据库为准，可经管理页增删）
    init_tools(conn)

    # 用户上传技能表（沙箱执行 + 审核 + 版本管理）
    conn.execute("""CREATE TABLE IF NOT EXISTS skills (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        name TEXT UNIQUE NOT NULL,
        display_name TEXT NOT NULL,
        description TEXT NOT NULL,
        category TEXT NOT NULL DEFAULT 'general',
        params_json TEXT NOT NULL DEFAULT '{}',
        trigger_words TEXT NOT NULL DEFAULT '',
        scope TEXT NOT NULL DEFAULT 'private',
        owner_id INTEGER,
        status TEXT NOT NULL DEFAULT 'private',
        code_text TEXT NOT NULL DEFAULT '',
        enabled INTEGER NOT NULL DEFAULT 1,
        version INTEGER NOT NULL DEFAULT 1,
        rules TEXT NOT NULL DEFAULT '',
        allowed_tools TEXT NOT NULL DEFAULT '[]',
        skill_type TEXT NOT NULL DEFAULT 'code',
        instructions TEXT NOT NULL DEFAULT '',
        when_to_use TEXT NOT NULL DEFAULT '',
        call_count INTEGER NOT NULL DEFAULT 0,
        reviewed_by INTEGER,
        reviewed_at TEXT,
        review_note TEXT,
        created_at TEXT,
        updated_at TEXT)""")

    # 技能版本历史（每次更新/回滚前快照，支持回溯）
    conn.execute("""CREATE TABLE IF NOT EXISTS skills_versions (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        skill_id INTEGER NOT NULL,
        version INTEGER NOT NULL,
        display_name TEXT,
        description TEXT,
        category TEXT,
        params_json TEXT,
        trigger_words TEXT,
        when_to_use TEXT,
        code_text TEXT,
        created_by INTEGER,
        note TEXT,
        created_at TEXT)""")

    # 技能安装关系（普通用户安装公开技能后才可用；轻量开关，不复制）
    conn.execute("""CREATE TABLE IF NOT EXISTS skill_installs (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        user_id INTEGER NOT NULL,
        skill_id INTEGER NOT NULL,
        created_at TEXT,
        UNIQUE(user_id, skill_id))""")

    # 会话级已激活能力（跨轮持久，防「已加载技能/工具脱落」）。
    # session_id 由前端稳定传递（同一连续对话复用，开新对话换新）；
    # 旧前端不传时使用 task_id 降级（单轮，无跨轮持久，向后兼容）。
    # cap_type 仅作调试标注，匹配时统一按 cap_name 在 library 中回查。
    # **用户隔离**：2026-08-19 加 user_id 列——避免不同用户复用同一 session_id
    # （如 UUID 撞车 / 前端全局变量 logout 未清空）时互相继承对方激活的能力集。
    conn.execute("""CREATE TABLE IF NOT EXISTS session_active_caps (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        user_id INTEGER NOT NULL DEFAULT 0,
        session_id TEXT NOT NULL,
        cap_name TEXT NOT NULL,
        cap_type TEXT NOT NULL DEFAULT 'skill',
        created_at TEXT DEFAULT CURRENT_TIMESTAMP,
        UNIQUE(user_id, session_id, cap_name))""")

    # 用户跨会话长期记忆（产品终端用户层，对标 WorkBuddy「云记忆自动注入」）。
    # 每轮对话自动注入该用户全部记忆条目，使 LLM 跨会话「记得」用户偏好/项目背景/关键事实。
    # mem_key 为可选主题键：有 key 时按 (user_id, mem_key) 去重更新（同一主题只保留一条）；
    # mem_key 为 NULL 时允许多条无键记忆并存（SQLite 的 UNIQUE 对 NULL 不约束）。
    # mem_type ∈ {preference, project, fact}：用户偏好 / 项目背景 / 关键事实，仅作分类展示。
    conn.execute("""CREATE TABLE IF NOT EXISTS user_memory (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        user_id INTEGER NOT NULL,
        mem_type TEXT NOT NULL DEFAULT 'fact',
        mem_key TEXT,
        content TEXT NOT NULL,
        updated_at TEXT,
        UNIQUE(user_id, mem_key))""")

    # 用户自动画像（P2⑫ 云记忆自动画像）：每轮对话结束后由 LLM 自动从对话中
    # 抽取并写入的结构化「用户是谁」画像，每轮注入 system prompt。
    # 与 user_memory（用户显式「记住」的零散记忆）互补：profile 是自动维护的稳定画像。
    # 每用户一条（user_id 主键），抽取时整体覆盖式更新。
    conn.execute("""CREATE TABLE IF NOT EXISTS user_profiles (
        user_id INTEGER PRIMARY KEY,
        profile TEXT,
        updated_at TEXT)""")

    # 任务管理（Task*）：长程任务的子任务拆分、状态追踪、跨步续接（补强规划·长程状态机）。
    # 绑定 user_id（归属）+ session_id（所属连续对话），列表可按会话过滤。
    # status ∈ {pending, in_progress, completed, deleted}；add_blocks/add_blocked_by 为任务依赖（JSON 数组）。
    conn.execute("""CREATE TABLE IF NOT EXISTS tasks (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        user_id INTEGER NOT NULL,
        session_id TEXT,
        title TEXT NOT NULL,
        description TEXT NOT NULL DEFAULT '',
        status TEXT NOT NULL DEFAULT 'pending',
        parent_id INTEGER,
        active_form TEXT NOT NULL DEFAULT '',
        add_blocks TEXT NOT NULL DEFAULT '[]',
        add_blocked_by TEXT NOT NULL DEFAULT '[]',
        created_at TEXT,
        updated_at TEXT)""")

    # 兼容旧库：补充 version 列（新库 CREATE 已含，此处幂等跳过）
    _cols = [r[1] for r in conn.execute("PRAGMA table_info(skills)").fetchall()]
    if "version" not in _cols:
        conn.execute("ALTER TABLE skills ADD COLUMN version INTEGER NOT NULL DEFAULT 1")
    # 兼容旧库：补充场景四所需的业务规则 / 工具清单列
    for col, ddl in (("rules", "TEXT NOT NULL DEFAULT ''"),
                     ("allowed_tools", "TEXT NOT NULL DEFAULT '[]'"),
                     ("skill_type", "TEXT NOT NULL DEFAULT 'code'"),
                     ("instructions", "TEXT NOT NULL DEFAULT ''"),
                     ("when_to_use", "TEXT NOT NULL DEFAULT ''")):
        if col not in _cols:
            conn.execute(f"ALTER TABLE skills ADD COLUMN {col} {ddl}")
    # 兼容旧库：补充 builtin_fp 列（内置技能文件指纹，用于「文件更新→DB 同步」，
    # 与 init_tools 的「代码即真相」一致：内置技能元数据随文件刷新，用户改动不覆盖的场景
    # 由「用户克隆成私有技能」承担——克隆走 clone 接口，与原内置技能解耦）
    if "builtin_fp" not in _cols:
        conn.execute("ALTER TABLE skills ADD COLUMN builtin_fp TEXT NOT NULL DEFAULT ''")

    # 兼容旧库：补充工具表的 skip_skill 列（场景二 A 方案的全局免 Skill 快通道标记）
    _tcols = [r[1] for r in conn.execute("PRAGMA table_info(tools)").fetchall()]
    if "skip_skill" not in _tcols:
        conn.execute("ALTER TABLE tools ADD COLUMN skip_skill INTEGER NOT NULL DEFAULT 0")
    # 兼容旧库：补充 is_user_created 列（用户用 create_tool 创建的私有工具标记，与 builtin/admin 创建的区分）
    if "is_user_created" not in _tcols:
        conn.execute("ALTER TABLE tools ADD COLUMN is_user_created INTEGER NOT NULL DEFAULT 0")
    # 兼容旧库：补充 code_text 列（用户私有工具存 Python 代码，沙箱执行用）
    if "code_text" not in _tcols:
        conn.execute("ALTER TABLE tools ADD COLUMN code_text TEXT NOT NULL DEFAULT ''")
    # 兼容旧库：补充「创建者审计」列（creator_name 冗余快照 + create_source 来源标记），
    # 方便审计 skill/工具的来源（用户手写创建 / 上传技能包 / 智能体自动创建）。
    for _c in ("creator_name", "create_source"):
        if _c not in _cols:
            conn.execute(f"ALTER TABLE skills ADD COLUMN {_c} TEXT NOT NULL DEFAULT ''")
        if _c not in _tcols:
            conn.execute(f"ALTER TABLE tools ADD COLUMN {_c} TEXT NOT NULL DEFAULT ''")

    # 兼容旧库：session_active_caps 加 user_id 列（2026-08-19 隔离加固）
    # 旧表 UNIQUE(session_id, cap_name) 缺 user_id → 不同用户撞同一 session_id 时
    # 会互相继承对方激活的能力（严重隔离漏洞）。
    # SQLite 不支持直接 ALTER UNIQUE 约束 → 采用"重建表"方案：把旧数据按 (session_id) 关联到 owner
    # 不可行（无 user_id），所以保守做法：旧数据 user_id 填 0 作"孤儿"，新 UNIQUE 重建。
    # 重建在 user_id 列首次添加时执行一次：旧激活集仅丢失（不影响功能，仅丢失"上一轮已激活"持久化）。
    _caps_cols = [r[1] for r in conn.execute("PRAGMA table_info(session_active_caps)").fetchall()]
    if "user_id" not in _caps_cols:
        # 1) 备份旧表数据
        conn.execute("CREATE TABLE IF NOT EXISTS _session_active_caps_backup AS "
                     "SELECT * FROM session_active_caps")
        # 2) 删旧表
        conn.execute("DROP TABLE session_active_caps")
        # 3) 重建新表（含 user_id + 新 UNIQUE）
        conn.execute("""CREATE TABLE session_active_caps (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL DEFAULT 0,
            session_id TEXT NOT NULL,
            cap_name TEXT NOT NULL,
            cap_type TEXT NOT NULL DEFAULT 'skill',
            created_at TEXT DEFAULT CURRENT_TIMESTAMP,
            UNIQUE(user_id, session_id, cap_name))""")
        # 4) 旧数据回填（user_id 默认 0，标孤儿；add_active_caps 后续按 user 排重，不影响新数据）
        conn.execute("""INSERT INTO session_active_caps (user_id, session_id, cap_name, cap_type, created_at)
                        SELECT 0, session_id, cap_name, cap_type, created_at FROM _session_active_caps_backup""")
        # 5) 删备份
        conn.execute("DROP TABLE _session_active_caps_backup")

    # 存量内置工具补创建者快照（新建库随 init_tools 写入；已部署旧库在此补）
    conn.execute("UPDATE tools SET creator_name='系统', create_source='builtin' "
                 "WHERE builtin=1 AND (creator_name IS NULL OR creator_name='')")

    # skills_versions 同步补充这两列，保证历史快照/回滚完整
    _vcols = [r[1] for r in conn.execute("PRAGMA table_info(skills_versions)").fetchall()]
    for col, ddl in (("rules", "TEXT NOT NULL DEFAULT ''"),
                     ("allowed_tools", "TEXT NOT NULL DEFAULT '[]'"),
                     ("skill_type", "TEXT NOT NULL DEFAULT 'code'"),
                     ("instructions", "TEXT NOT NULL DEFAULT ''"),
                     ("when_to_use", "TEXT NOT NULL DEFAULT ''")):
        if col not in _vcols:
            conn.execute(f"ALTER TABLE skills_versions ADD COLUMN {col} {ddl}")

    # 防御性兜底：任何非管理员身上若残留系统管理类权限（历史 bug 或显式误授），
    # 一律收回。系统管理(menu=系统管理)恒为管理员专属；幂等，无残留时无副作用。
    conn.execute("""
        DELETE FROM user_permissions
        WHERE perm_key IN ('m_smtp','m_mcp')
          AND user_id NOT IN (SELECT id FROM users WHERE role='admin')
    """)

    # ---------- 2026-08-19 权限模型升级：显式授权 → 默认授权 + 显式拒绝 ----------
    # 旧模型把每个普通用户的「默认权限」逐条写进 user_permissions；新模型改为读取时
    # 主线计算：FEATURE_REGISTRY 中除 admin 类外的全部 = 默认可见（含未来新增的导航
    # 功能，自动下发，管理员无需逐用户改权限）。仅把「管理员取消的权限」存进
    # user_perm_denies，把「显式授予的非默认(admin类)权限」留在 user_permissions。
    # 迁移只跑一次，用 app_meta.perm_deny_migrated 守卫；旧 user_permissions 数据据此
    # 无损转换为（denies, explicit grants）。历史 bug 错发的 m_smtp/m_mcp 直接丢弃，
    # 不转为显式授权。
    _migrated = conn.execute(
        "SELECT value FROM app_meta WHERE key='perm_deny_migrated'").fetchone()
    if not _migrated:
        _default_set = set(_default_user_perms())
        _legacy_bug = {"m_smtp", "m_mcp"}
        try:
            for urow in conn.execute(
                    "SELECT id FROM users WHERE role!='admin'").fetchall():
                uid = urow["id"]
                stored = {r["perm_key"] for r in conn.execute(
                    "SELECT perm_key FROM user_permissions WHERE user_id=?", (uid,)).fetchall()}
                stored -= _legacy_bug  # 历史 bug：丢弃错发的系统管理类权限
                denies = _default_set - stored
                grants = (stored & set(PERMS)) - _default_set
                conn.execute("DELETE FROM user_permissions WHERE user_id=?", (uid,))
                conn.execute("DELETE FROM user_perm_denies WHERE user_id=?", (uid,))
                for k in grants:
                    conn.execute("INSERT OR IGNORE INTO user_permissions (user_id,perm_key) VALUES (?,?)", (uid, k))
                for k in denies:
                    conn.execute("INSERT OR IGNORE INTO user_perm_denies (user_id,perm_key) VALUES (?,?)", (uid, k))
            conn.execute("INSERT OR REPLACE INTO app_meta (key,value) VALUES ('perm_deny_migrated','1')")
            conn.commit()
        except Exception:
            conn.rollback()
            raise

    # 导航设置种子：为每个注册表功能补齐 nav_settings 行（visible=1）。
    # 默认顺序保持与现有前端侧栏一致（避免升级后导航顺序跳变），见 _DEFAULT_NAV_SEQ。
    # 仅 INSERT OR IGNORE 缺失行（如新增功能自动补上，追加到组尾），绝不覆盖已保存配置。
    _DEFAULT_NAV_SEQ = ["review", "resume", "agent", "tools", "skills",
                        "memories", "profile", "automations", "history",
                        "m_models", "m_orgs", "m_depts", "m_users", "m_logs",
                        "m_smtp", "m_mcp", "m_nav"]
    _seq_pos = {k: i for i, k in enumerate(_DEFAULT_NAV_SEQ)}
    for _i, (_key, *_rest) in enumerate(FEATURE_REGISTRY):
        conn.execute(
            "INSERT OR IGNORE INTO nav_settings (feature_key, order_index, visible) VALUES (?,?,1)",
            (_key, _seq_pos.get(_key, _i)))

    # 系统基础设置种子：仅补缺失键，绝不覆盖管理员已保存的值
    _DEFAULT_SYS_KEYS = {
        "system_name":     "企业AI办公助手",
        "system_subtitle": "AI Office Assistant",
        "login_subtitle":  "请登录后使用相应功能",
        "footer_tip":      "内部系统 · 请妥善保管账号，不同用户的会话数据相互隔离",
        "icon_url":        "",   # 空 = 使用默认「AI」文本 logo
        "favicon_url":     "",   # 空 = 使用浏览器默认 favicon
        "login_bg_url":    "",   # 空 = 使用 CSS 默认渐变背景
    }
    for _k, _v in _DEFAULT_SYS_KEYS.items():
        conn.execute(
            "INSERT OR IGNORE INTO system_settings (key, value) VALUES (?,?)",
            (_k, _v))

    # ---------- P2⑩ 定时自动化（对标 WorkBuddy automation_update）----------
    conn.execute("""CREATE TABLE IF NOT EXISTS automations (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        name TEXT NOT NULL,
        prompt TEXT NOT NULL,
        schedule_type TEXT NOT NULL DEFAULT 'recurring',  -- 'once' | 'recurring'
        rrule TEXT NOT NULL DEFAULT '',                   -- RFC5545 RRULE（recurring 用）
        scheduled_at TEXT,                                -- ISO datetime（once 用）
        status TEXT NOT NULL DEFAULT 'ACTIVE',           -- 'ACTIVE' | 'PAUSED' | 'DONE'
        valid_from TEXT,
        valid_until TEXT,
        owner_id INTEGER NOT NULL,
        last_run TEXT,
        next_run TEXT,
        created_at TEXT,
        updated_at TEXT)""")
    conn.execute("""CREATE TABLE IF NOT EXISTS automation_runs (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        automation_id INTEGER NOT NULL,
        started_at TEXT,
        finished_at TEXT,
        status TEXT NOT NULL DEFAULT 'running',  -- 'running' | 'success' | 'error'
        result_text TEXT,
        error TEXT)""")

    # ---------- 系统配置键值表（SMTP 邮件服务器、站点开关等系统级配置） ----------
    conn.execute("""CREATE TABLE IF NOT EXISTS system_config (
        key TEXT PRIMARY KEY,
        value TEXT,
        updated_at TEXT,
        updated_by TEXT)""")

    # ---------- 邮件服务器（SMTP）配置：随库结构升级兼容补足列 ----------
    _migrate_smtp(conn)

    # ---------- P2⑪ 跨对话历史检索：消息落库底座 ----------
    conn.execute("""CREATE TABLE IF NOT EXISTS conversations (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        session_id TEXT NOT NULL,
        user_id INTEGER NOT NULL,
        role TEXT NOT NULL,                   -- 'user' | 'assistant' | 'system'
        content TEXT NOT NULL,
        created_at TEXT)""")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_conv_session ON conversations(session_id)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_conv_user ON conversations(user_id)")

    # ---------- P2⑪ 档B：会话元数据表（会话管理基座，支持按窗口检索 + 级联删除） ----------
    conn.execute("""CREATE TABLE IF NOT EXISTS conversation_sessions (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        session_id TEXT NOT NULL UNIQUE,
        user_id INTEGER NOT NULL,
        title TEXT,
        summary TEXT,
        created_at TEXT,
        updated_at TEXT,
        deleted_at TEXT)""")   # deleted_at 预留软删位；当前档B用物理级联删除
    conn.execute("CREATE INDEX IF NOT EXISTS idx_sess_user ON conversation_sessions(user_id)")

    # 内置技能 seed：必须在 skills 表 + 兼容列创建之后执行（幂等，仅首次插入）
    init_skills(conn)

    conn.commit()
    conn.close()


# ---------- 密码 & 会话 ----------
def hash_password(pw: str) -> str:
    salt = os.urandom(16)
    dk = hashlib.pbkdf2_hmac("sha256", pw.encode("utf-8"), salt, 100000)
    return "pbkdf2$" + salt.hex() + "$" + dk.hex()


def verify_password(pw: str, stored: str) -> bool:
    try:
        _, salt_hex, hash_hex = stored.split("$")
        salt = bytes.fromhex(salt_hex)
        dk = hashlib.pbkdf2_hmac("sha256", pw.encode("utf-8"), salt, 100000)
        return dk.hex() == hash_hex
    except Exception:
        return False


def create_session(user_id: int) -> str:
    token = secrets.token_hex(32)
    exp = (datetime.now() + timedelta(days=7)).strftime("%Y-%m-%d %H:%M:%S")
    conn = get_conn()
    conn.execute("INSERT INTO sessions (user_id,token,expires_at,created_at) VALUES (?,?,?,?)",
                 (user_id, token, exp, now()))
    conn.commit()
    conn.close()
    return token


def get_user_by_id(uid: int):
    if not uid:
        return None
    conn = get_conn()
    row = conn.execute(
        "SELECT id,username,display_name,role,org_id,dept_id,status FROM users WHERE id=?",
        (uid,)).fetchone()
    conn.close()
    return dict(row) if row else None


def get_user_by_token(token: str):
    if not token:
        return None
    conn = get_conn()
    row = conn.execute(
        "SELECT s.user_id,s.expires_at,u.id,u.username,u.display_name,u.role,u.status "
        "FROM sessions s JOIN users u ON u.id=s.user_id WHERE s.token=?", (token,)
    ).fetchone()
    conn.close()
    if not row:
        return None
    if row["expires_at"] < now():
        return None
    return row_to_dict(row)


def delete_session(token: str):
    conn = get_conn()
    conn.execute("DELETE FROM sessions WHERE token=?", (token,))
    conn.commit()
    conn.close()


def add_log(action, target="", detail="", user=None, ip=""):
    uid = user.get("user_id") or user.get("id") if user else None
    uname = (user.get("username") if user else None) or "匿名"
    conn = get_conn()
    conn.execute(
        "INSERT INTO logs (user_id,username,action,target,detail,ip,created_at) VALUES (?,?,?,?,?,?,?)",
        (uid, uname, action, target, detail, ip, now()),
    )
    conn.commit()
    conn.close()


# ---------- 模型配置 ----------
def get_active(role):
    """返回某 role 下当前默认启用的模型（is_active=1 且 enabled=1）。

    若没有显式默认，则回退到该 role 下第一个 enabled=1 的模型；
    仍没有则使用环境变量种子配置（兼容无数据库的旧部署）。
    """
    if role not in SEED:
        return None
    conn = get_conn()
    row = conn.execute(
        "SELECT * FROM models WHERE role=? AND is_active=1 AND enabled=1 LIMIT 1", (role,)
    ).fetchone()
    if not row:
        row = conn.execute(
            "SELECT * FROM models WHERE role=? AND enabled=1 ORDER BY id LIMIT 1", (role,)
        ).fetchone()
    conn.close()
    if row:
        return row_to_dict(row)
    bk, kk, nk, label, dn = SEED[role]
    base = os.environ.get(bk, "").strip()
    if base:
        return {"role": role, "name": label, "base_url": base,
                "api_key": os.environ.get(kk, ""),
                "model_name": os.environ.get(nk, "") or dn, "is_active": 1, "enabled": 1,
                "max_tokens": 0, "temperature": -1, "top_p": -1, "thinking": 0,
                "supports_tools": 1, "timeout": 0, "max_steps": 8}
    return None


def activate(role, model_id):
    """把指定模型设为该 role 下的默认模型（单选），同时自动启用它。"""
    conn = get_conn()
    conn.execute("UPDATE models SET is_active=0 WHERE role=?", (role,))
    conn.execute("UPDATE models SET is_active=1, enabled=1, updated_at=? WHERE id=?", (now(), model_id))
    conn.commit()
    conn.close()


def toggle_model_enabled(model_id, enabled):
    """启用/禁用模型（可多启用）。被禁用时同步取消默认，避免 get_active 回退到禁用模型。"""
    conn = get_conn()
    if not enabled:
        conn.execute("UPDATE models SET is_active=0, enabled=0, updated_at=? WHERE id=?", (now(), model_id))
    else:
        conn.execute("UPDATE models SET enabled=1, updated_at=? WHERE id=?", (now(), model_id))
    conn.commit()
    conn.close()


def list_models():
    conn = get_conn()
    rows = conn.execute("SELECT * FROM models ORDER BY role, enabled DESC, is_active DESC, id").fetchall()
    conn.close()
    return [dict(row_to_dict(r), api_key=MASK if r["api_key"] else "") for r in rows]


def save_model(p):
    role = p.get("role", "")
    if role not in SEED:
        raise ValueError(f"未知模型角色: {role}")
    name = (p.get("name") or "").strip()
    base_url = (p.get("base_url") or "").strip()
    model_name = (p.get("model_name") or "").strip()
    if not name or not base_url or not model_name:
        raise ValueError("名称、Base URL、模型名均为必填")
    incoming_key = p.get("api_key") or ""
    # 推理参数（带默认值，非法值回退）
    max_tokens = to_int(p.get("max_tokens"))
    if max_tokens is None or max_tokens < 0:
        max_tokens = 0
    temperature = p.get("temperature")
    try:
        temperature = float(temperature) if temperature not in (None, "") else -1
    except (TypeError, ValueError):
        temperature = -1
    top_p = p.get("top_p")
    try:
        top_p = float(top_p) if top_p not in (None, "") else -1
    except (TypeError, ValueError):
        top_p = -1
    thinking = 1 if p.get("thinking") else 0
    supports_tools = 1 if p.get("supports_tools", True) else 0
    timeout = to_int(p.get("timeout")) or 0
    max_steps = to_int(p.get("max_steps")) or 8
    if max_steps < 1:
        max_steps = 8
    enabled = 1 if p.get("enabled", True) else 0
    provider = (p.get("provider") or "openai_compatible").strip().lower()
    if provider not in ("openai", "openai_compatible", "tencent", "local"):
        provider = "openai_compatible"
    _extra = p.get("extra")
    if isinstance(_extra, dict):
        _extra = json.dumps(_extra, ensure_ascii=False)
    elif not isinstance(_extra, str):
        _extra = "{}"
    conn = get_conn()
    mid = to_int(p.get("id"))
    if mid:
        old = conn.execute("SELECT api_key,is_active FROM models WHERE id=?", (mid,)).fetchone()
        keep = (incoming_key in ("", MASK)) and old is not None
        new_key = old["api_key"] if keep else incoming_key
        conn.execute(
            "UPDATE models SET name=?,base_url=?,api_key=?,model_name=?,note=?,max_tokens=?,temperature=?,top_p=?,thinking=?,supports_tools=?,timeout=?,max_steps=?,enabled=?,provider=?,extra=?,updated_at=? WHERE id=?",
            (name, base_url, new_key, model_name, p.get("note") or "",
             max_tokens, temperature, top_p, thinking, supports_tools, timeout, max_steps, enabled,
             provider, _extra, now(), mid),
        )
    else:
        if not incoming_key:
            raise ValueError("新增模型必须填写 API Key")
        cur = conn.execute(
            "INSERT INTO models (role,name,base_url,api_key,model_name,note,max_tokens,temperature,top_p,thinking,supports_tools,timeout,max_steps,enabled,provider,extra,updated_at) "
            "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (role, name, base_url, incoming_key, model_name, p.get("note") or "",
             max_tokens, temperature, top_p, thinking, supports_tools, timeout, max_steps, enabled,
             provider, _extra, now()),
        )
        mid = cur.lastrowid
    # 先提交并关闭当前连接，再调用 activate（activate 会另开连接），
    # 否则两个连接同时写同一 SQLite 库会触发 "database is locked"。
    conn.commit()
    conn.close()
    if p.get("is_active"):
        activate(role, mid)
    return mid


def delete_model(model_id):
    conn = get_conn()
    conn.execute("DELETE FROM models WHERE id=?", (model_id,))
    conn.commit()
    conn.close()


# ---------- 组织 ----------
def list_orgs(q=None):
    conn = get_conn()
    if q:
        rows = conn.execute("SELECT * FROM orgs WHERE name LIKE ? ORDER BY sort, id",
                            ("%" + q + "%",)).fetchall()
    else:
        rows = conn.execute("SELECT * FROM orgs ORDER BY sort, id").fetchall()
    conn.close()
    return [row_to_dict(r) for r in rows]


def create_org(p):
    name = (p.get("name") or "").strip()
    if not name:
        raise ValueError("组织名称必填")
    conn = get_conn()
    cur = conn.execute(
        "INSERT INTO orgs (name,parent_id,sort,created_at) VALUES (?,?,?,?)",
        (name, to_int(p.get("parent_id")), to_int(p.get("sort")) or 0, now()),
    )
    conn.commit(); conn.close()
    return cur.lastrowid


def update_org(oid, p):
    conn = get_conn()
    conn.execute("UPDATE orgs SET name=?,parent_id=?,sort=? WHERE id=?",
                 ((p.get("name") or "").strip(), to_int(p.get("parent_id")), to_int(p.get("sort")) or 0, oid))
    conn.commit(); conn.close()


def delete_org(oid):
    conn = get_conn()
    conn.execute("UPDATE departments SET org_id=NULL WHERE org_id=?", (oid,))
    conn.execute("UPDATE users SET org_id=NULL WHERE org_id=?", (oid,))
    conn.execute("DELETE FROM orgs WHERE id=?", (oid,))
    conn.commit(); conn.close()


# ---------- 部门 ----------
def list_departments(q=None):
    conn = get_conn()
    if q:
        rows = conn.execute(
            "SELECT d.*, o.name AS org_name FROM departments d LEFT JOIN orgs o ON o.id=d.org_id "
            "WHERE d.name LIKE ? OR o.name LIKE ? ORDER BY d.id", ("%" + q + "%", "%" + q + "%")
        ).fetchall()
    else:
        rows = conn.execute(
            "SELECT d.*, o.name AS org_name FROM departments d LEFT JOIN orgs o ON o.id=d.org_id ORDER BY d.id"
        ).fetchall()
    conn.close()
    return [row_to_dict(r) for r in rows]


def create_department(p):
    org_id = to_int(p.get("org_id"))
    name = (p.get("name") or "").strip()
    if not org_id or not name:
        raise ValueError("部门和所属组织均为必填")
    conn = get_conn()
    cur = conn.execute(
        "INSERT INTO departments (org_id,name,parent_id,created_at) VALUES (?,?,?,?)",
        (org_id, name, to_int(p.get("parent_id")), now()),
    )
    conn.commit(); conn.close()
    return cur.lastrowid


def update_department(did, p):
    conn = get_conn()
    conn.execute("UPDATE departments SET org_id=?,name=?,parent_id=? WHERE id=?",
                 (to_int(p.get("org_id")), (p.get("name") or "").strip(), to_int(p.get("parent_id")), did))
    conn.commit(); conn.close()


def delete_department(did):
    conn = get_conn()
    conn.execute("UPDATE departments SET parent_id=NULL WHERE parent_id=?", (did,))
    conn.execute("UPDATE users SET dept_id=NULL WHERE dept_id=?", (did,))
    conn.execute("DELETE FROM departments WHERE id=?", (did,))
    conn.commit(); conn.close()


# ---------- 用户 ----------
def list_users(q=None):
    conn = get_conn()
    if q:
        like = "%" + q + "%"
        rows = conn.execute(
            "SELECT u.*, o.name AS org_name, d.name AS dept_name FROM users u "
            "LEFT JOIN orgs o ON o.id=u.org_id LEFT JOIN departments d ON d.id=u.dept_id "
            "WHERE u.username LIKE ? OR u.display_name LIKE ? OR o.name LIKE ? OR d.name LIKE ? "
            "ORDER BY u.id", (like, like, like, like)
        ).fetchall()
    else:
        rows = conn.execute(
            "SELECT u.*, o.name AS org_name, d.name AS dept_name FROM users u "
            "LEFT JOIN orgs o ON o.id=u.org_id LEFT JOIN departments d ON d.id=u.dept_id ORDER BY u.id"
        ).fetchall()
    conn.close()
    out = []
    for r in rows:
        d = row_to_dict(r)
        d["password_hash"] = MASK  # 不向后端/前端泄露哈希
        d["permissions"] = get_user_permissions(d["id"])
        out.append(d)
    return out


def create_user(p):
    username = (p.get("username") or "").strip()
    pw = p.get("password") or ""
    if not username or not pw:
        raise ValueError("用户名和密码均为必填")
    conn = get_conn()
    cur = conn.execute(
        "INSERT INTO users (username,password_hash,display_name,org_id,dept_id,role,status,created_at) "
        "VALUES (?,?,?,?,?,?,?,?)",
        (username, hash_password(pw), p.get("display_name") or username,
         to_int(p.get("org_id")), to_int(p.get("dept_id")),
         p.get("role") or "user", p.get("status") or "active", now()),
    )
    uid = cur.lastrowid
    # 2026-08-19 权限模型：默认权限不再逐条写库。普通用户的「可见权限」主线由
    # _default_user_perms()（FEATURE_REGISTRY 中除 admin 类外的全部）在读取时实时计算，
    # 因此未来新增导航功能会自动下发，无需逐用户改权限。仅当管理员显式定制时才落库
    # （拒绝项存 user_perm_denies，显式授予的非默认项存 user_permissions），见 set_user_permissions。
    conn.commit(); conn.close()
    return uid


def update_user(uid, p):
    conn = get_conn()
    pw = p.get("password") or ""
    sets, vals = [], []
    if pw:
        sets.append("password_hash=?"); vals.append(hash_password(pw))
    if "display_name" in p: sets.append("display_name=?"); vals.append(p.get("display_name") or "")
    if "org_id" in p: sets.append("org_id=?"); vals.append(to_int(p.get("org_id")))
    if "dept_id" in p: sets.append("dept_id=?"); vals.append(to_int(p.get("dept_id")))
    if "role" in p: sets.append("role=?"); vals.append(p.get("role") or "user")
    if "status" in p: sets.append("status=?"); vals.append(p.get("status") or "active")
    if sets:
        conn.execute("UPDATE users SET " + ",".join(sets) + " WHERE id=?", vals + [uid])
    conn.commit(); conn.close()


def delete_user(uid, by_admin_id=None):
    """删除用户，并级联兜底其拥有的能力，杜绝「无归属且永久不可用」的孤儿资源：

    - 该用户的【私有】工具/技能（scope='private'/status='private' 且 owner_id=该用户）：
      转移 owner_id 给执行删除的管理员（by_admin_id），仍有人可见可用；
      若未传入管理员（理论不应发生），则兜底自动公开（scope='global'/status='approved'），保证不消失。
    - 该用户的【公开】工具/技能（scope='global'/status='approved'）：owner_id 置空，
      变为干净的全局资源，可见性不受影响（全局不依赖 owner_id）。
    - 顺带清理该用户的其他子表行（权限、技能安装、个人记忆），避免悬空。
    """
    conn = get_conn()
    try:
        if by_admin_id:
            conn.execute(
                "UPDATE tools SET owner_id=? WHERE scope='private' AND owner_id=?",
                (by_admin_id, uid))
            conn.execute(
                "UPDATE skills SET owner_id=? WHERE status='private' AND owner_id=?",
                (by_admin_id, uid))
        else:
            # 兜底：无管理员上下文时，私有资源直接转为全局，确保不丢失
            conn.execute(
                "UPDATE tools SET scope='global' WHERE scope='private' AND owner_id=?",
                (uid,))
            conn.execute(
                "UPDATE skills SET status='approved' WHERE status='private' AND owner_id=?",
                (uid,))
        # 公开资源：清除悬空 owner_id，保持为干净的全局资源
        conn.execute("UPDATE tools SET owner_id=NULL WHERE scope='global' AND owner_id=?", (uid,))
        conn.execute("UPDATE skills SET owner_id=NULL WHERE status='approved' AND owner_id=?", (uid,))
        # 清理该用户的其他子表行
        conn.execute("DELETE FROM user_permissions WHERE user_id=?", (uid,))
        conn.execute("DELETE FROM user_perm_denies WHERE user_id=?", (uid,))
        conn.execute("DELETE FROM skill_installs WHERE user_id=?", (uid,))
        conn.execute("DELETE FROM user_memory WHERE user_id=?", (uid,))
        conn.execute("DELETE FROM user_profiles WHERE user_id=?", (uid,))
        conn.execute("DELETE FROM users WHERE id=?", (uid,))
        conn.commit()
    finally:
        conn.close()


def admin_count():
    conn = get_conn()
    c = conn.execute("SELECT COUNT(*) AS c FROM users WHERE role='admin'").fetchone()["c"]
    conn.close()
    return c


# ---------- 功能权限 ----------
def get_user_permissions(user_id):
    """返回该用户「实际生效」的权限列表（已含默认权限、已剔除被拒绝项）。

    模型：管理员恒为全部 PERMS；普通用户 = 默认权限集(_default_user_perms)
    减去 user_perm_denies 中的拒绝项，再并上 user_permissions 中的显式授权
    （一般是管理员显式授予的非默认/admin类权限）。新功能只需加进 FEATURE_REGISTRY
    即自动对所有普通用户生效，无需逐用户改库。
    """
    conn = get_conn()
    try:
        u = conn.execute("SELECT role FROM users WHERE id=?", (user_id,)).fetchone()
        if u and u["role"] == "admin":
            return list(PERMS)
        denied = {r["perm_key"] for r in conn.execute(
            "SELECT perm_key FROM user_perm_denies WHERE user_id=?", (user_id,))}
        explicit = {r["perm_key"] for r in conn.execute(
            "SELECT perm_key FROM user_permissions WHERE user_id=?", (user_id,))}
    finally:
        conn.close()
    default = set(_default_user_perms())
    effective = (default - denied) | (explicit & set(PERMS))
    return sorted(effective)


def set_user_permissions(user_id, perm_keys):
    """按「期望生效权限集合」写库。perm_keys 即管理员在界面勾选的那些权限。

    新模型下不直接存全部授权，而是反存「差异」：
      - 拒绝项 denies = 默认权限集 - 勾选项（被管理员取消的默认权限）
      - 显式授权 grants = 勾选项 - 默认权限集（一般是被显式勾上的 admin 类权限）
    这样普通用户的默认权限始终由 _default_user_perms() 主线计算，新增导航功能
    自动下发；管理员取消的权限以 denies 形式持久保存，部署后不被恢复。
    自动忽略注册表里不存在的 key。管理员账号恒为全部权限，不落库。
    """
    keys = set(k for k in (perm_keys or []) if k in PERMS)
    conn = get_conn()
    try:
        u = conn.execute("SELECT role FROM users WHERE id=?", (user_id,)).fetchone()
        if u and u["role"] == "admin":
            conn.close()
            return
        default = set(_default_user_perms())
        denies = default - keys
        grants = keys - default
        conn.execute("DELETE FROM user_permissions WHERE user_id=?", (user_id,))
        conn.execute("DELETE FROM user_perm_denies WHERE user_id=?", (user_id,))
        for k in grants:
            conn.execute("INSERT OR IGNORE INTO user_permissions (user_id,perm_key) VALUES (?,?)", (user_id, k))
        for k in denies:
            conn.execute("INSERT OR IGNORE INTO user_perm_denies (user_id,perm_key) VALUES (?,?)", (user_id, k))
        conn.commit()
    finally:
        conn.close()


def get_available_features():
    """供前端权威清单使用：返回 [{key,label,group,nav_id}, ...]，按 group 顺序、组内按注册顺序。"""
    order = {g: i for i, g in enumerate(PERM_GROUPS)}
    items = [{"key": k, "label": label, "group": group, "nav_id": nav_id}
             for k, label, group, nav_id in FEATURE_REGISTRY]
    items.sort(key=lambda x: (order.get(x["group"], 99), FEATURE_REGISTRY.index(
        next(t for t in FEATURE_REGISTRY if t[0] == x["key"]))))
    return items


def get_nav_settings():
    """返回全部功能的导航配置（顺序 + 显隐），与注册表合并，缺行时用注册表默认值。

    返回 [{key, label, group, nav_id, order, visible}, ...]，按注册表顺序（真实顺序
    由前端按 order 排序）。visible 为 bool。管理员未配置过的功能取注册表默认。
    """
    conn = get_conn()
    try:
        rows = {r["feature_key"]: r for r in conn.execute(
            "SELECT feature_key, order_index, visible FROM nav_settings").fetchall()}
    finally:
        conn.close()
    out = []
    for i, (key, label, group, nav_id) in enumerate(FEATURE_REGISTRY):
        r = rows.get(key)
        out.append({
            "key": key, "label": label, "group": group, "nav_id": nav_id,
            "order": r["order_index"] if r else i,
            "visible": bool(r["visible"]) if r else True,
        })
    return out


def save_nav_settings(items):
    """保存导航配置：items = [{key, order, visible}, ...]。自动忽略注册表外 key。"""
    valid = {k for k, *_ in FEATURE_REGISTRY}
    conn = get_conn()
    try:
        for it in (items or []):
            k = (it.get("key") or "").strip()
            if k not in valid:
                continue
            conn.execute(
                "INSERT INTO nav_settings (feature_key, order_index, visible, updated_at) "
                "VALUES (?,?,?,?) "
                "ON CONFLICT(feature_key) DO UPDATE SET "
                "  order_index=excluded.order_index, visible=excluded.visible, updated_at=excluded.updated_at",
                (k, int(it.get("order", 0)), 1 if it.get("visible") else 0, now()))
        conn.commit()
    finally:
        conn.close()


# ---------- 系统基础设置 ----------
# 允许编辑的 key 集合（前端编辑表单与后端校验共用；防止脏数据）
_ALLOWED_SYS_KEYS = {
    "system_name", "system_subtitle", "login_subtitle", "footer_tip",
    "icon_url", "favicon_url", "login_bg_url",
}


def get_system_settings():
    """读取全部系统基础设置键值。缺键时回退到内置默认值（保证前端永不读到 None）。"""
    defaults = {
        "system_name":     "企业AI办公助手",
        "system_subtitle": "AI Office Assistant",
        "login_subtitle":  "请登录后使用相应功能",
        "footer_tip":      "内部系统 · 请妥善保管账号，不同用户的会话数据相互隔离",
        "icon_url":        "",
        "favicon_url":     "",
        "login_bg_url":    "",
    }
    conn = get_conn()
    try:
        rows = conn.execute("SELECT key, value FROM system_settings").fetchall()
    finally:
        conn.close()
    out = dict(defaults)
    for r in rows:
        if r["key"] in out:
            out[r["key"]] = r["value"] or ""
    return out


def save_system_settings(items):
    """批量保存系统基础设置：items = {key: value}。仅允许 _ALLOWED_SYS_KEYS 内的 key。"""
    if not isinstance(items, dict):
        return
    conn = get_conn()
    try:
        for k, v in items.items():
            if k not in _ALLOWED_SYS_KEYS:
                continue
            conn.execute(
                "INSERT INTO system_settings (key, value, updated_at) VALUES (?,?,?) "
                "ON CONFLICT(key) DO UPDATE SET value=excluded.value, updated_at=excluded.updated_at",
                (k, "" if v is None else str(v)[:500], now()))
        conn.commit()
    finally:
        conn.close()


def has_permission(user, perm_key):
    """管理员自动拥有全部权限；普通用户查 user_permissions 表。"""
    if user.get("role") == "admin":
        return True
    return perm_key in get_user_permissions(user["id"])


# ---------- 日志 ----------
def _day_range(start_date, end_date):
    """把日期范围转换成 created_at 可用的边界条件。返回 (params, where_clauses)。"""
    where, params = [], []
    if start_date:
        where.append("created_at >= ?"); params.append(start_date + " 00:00:00")
    if end_date:
        where.append("created_at <= ?"); params.append(end_date + " 23:59:59")
    return where, params


def list_logs(limit=100, offset=0, start_date=None, end_date=None):
    conn = get_conn()
    where, params = _day_range(start_date, end_date)
    w = (" WHERE " + " AND ".join(where)) if where else ""
    rows = conn.execute(
        f"SELECT * FROM logs{w} ORDER BY id DESC LIMIT ? OFFSET ?", params + [limit, offset]
    ).fetchall()
    conn.close()
    return [row_to_dict(r) for r in rows]


def list_logs_for_user(user_id, limit=500, start_date=None, end_date=None):
    """会话数据隔离：普通用户只看得到自己产生的日志（可按日期范围过滤）。"""
    conn = get_conn()
    where, params = ["user_id=?"], [user_id]
    wr, wp = _day_range(start_date, end_date)
    where += wr; params += wp
    rows = conn.execute(
        "SELECT * FROM logs WHERE " + " AND ".join(where) + " ORDER BY id DESC LIMIT ?", params + [limit]
    ).fetchall()
    conn.close()
    return [row_to_dict(r) for r in rows]


def delete_logs_range(start_date, end_date, user_id=None):
    """删除指定日期范围内的日志；user_id 非空时仅删该用户（会话隔离）。返回删除条数。"""
    conn = get_conn()
    where, params = ["created_at >= ?", "created_at <= ?"], [start_date + " 00:00:00", end_date + " 23:59:59"]
    if user_id is not None:
        where.append("user_id = ?"); params.append(user_id)
    cur = conn.execute("DELETE FROM logs WHERE " + " AND ".join(where), params)
    n = cur.rowcount
    conn.commit(); conn.close()
    return n


# ---------- 全局工具库 ----------
# 内置工具元数据：真实执行代码已迁移到 builtin_tools/ 包（每个工具一个 .py，自动扫描注册），
# 本文件只持有元数据列表，供 init_tools 写入 tools 表。业务逻辑与 handler 映射见 builtin_tools 与 tools_handlers。
from builtin_tools import BUILTIN_TOOLS


def _tool_row_to_dict(row):
    d = row_to_dict(row)
    try:
        d["params"] = json.loads(d["params_json"]) if d.get("params_json") else {}
    except Exception:
        d["params"] = {}
    return d


def init_tools(conn=None):
    """建库首次调用：把内置工具写入 tools 表。
    已存在的 builtin 工具会同步最新的元数据（display_name/description/category/
    trigger_words/params_json/skip_skill），保持「代码即真相」——builtin 工具由
    builtin_tools/ 包定义，DB 仅作缓存，重启后须与代码一致（否则 UI 会残留旧文案，
    如 generate_ppt 旧版「需配置 PPTX_GEN_SCRIPT」说明）。"""
    own = conn is None
    c = conn or get_conn()
    try:
        existing = {r["name"] for r in c.execute("SELECT name FROM tools").fetchall()}
        for t in BUILTIN_TOOLS:
            if t["name"] in existing:
                # 同步全部元数据（builtin 工具以代码定义为准，覆盖 DB 旧值）
                c.execute(
                    "UPDATE tools SET params_json=?, display_name=?, description=?, "
                    "category=?, trigger_words=?, skip_skill=? WHERE name=? AND builtin=1",
                    (json.dumps(t["params"], ensure_ascii=False), t["display_name"],
                     t["description"], t["category"], t.get("trigger_words", ""),
                     int(bool(t.get("skip_skill"))), t["name"]))
                continue
            c.execute(
                "INSERT INTO tools (name,display_name,description,category,params_json,"
                "backend_type,handler,trigger_words,scope,builtin,enabled,skip_skill,created_at) "
                "VALUES (?,?,?,?,?,?,?,?,?,1,1,?,?)",
                (t["name"], t["display_name"], t["description"], t["category"],
                 json.dumps(t["params"], ensure_ascii=False), t["backend_type"],
                 t["handler"], t["trigger_words"], "global",
                 int(bool(t.get("skip_skill"))), now()),
            )
        if own:
            c.commit()
    finally:
        if own:
            c.close()


def init_skills(conn=None):
    """内置技能（builtin_skills/ 文件化）seed / 同步进 skills 表，供技能广场展示。

    策略（与 init_tools 的「代码即真相」一致，2026-08-18 二轮修复增强）：
    - name 不存在 → 首次插入：scope='public'、status='approved'（直接可用，无需审核）、
      skill_type='method'、create_source='builtin'、builtin_fp=文件指纹。
    - name 已存在 且 create_source='builtin'（系统内置，非用户克隆）→ 指纹同步：
      文件指纹 ≠ 库内 builtin_fp 时，把元数据 + instructions 刷新为文件最新版
      （环境适配修复等改动随之生效）。文件指纹一致则跳过。
    - 用户克隆的内置技能（create_source != 'builtin'）→ 永不触碰，完全用户自治。
    """
    own = conn is None
    c = conn or get_conn()
    try:
        existing = {r["name"]: r for r in c.execute(
            "SELECT name, create_source, scope, status, builtin_fp FROM skills").fetchall()}
        for s in BUILTIN_SKILLS:
            fp = _builtin_fp(s)
            row = existing.get(s["name"])
            if row is None:
                c.execute(
                    "INSERT INTO skills (name,display_name,description,category,params_json,"
                    "trigger_words,scope,owner_id,status,enabled,skill_type,instructions,"
                    "when_to_use,allowed_tools,create_source,creator_name,builtin_fp,created_at,updated_at) "
                    "VALUES (?,?,?,?,?,?,?,?,?,1,?,?,?,?,?,?,?,?,?)",
                    (s["name"], s["display_name"], s["description"], s["category"], "{}",
                     s["trigger_words"], "public", None, "approved",
                     "method", s["instructions"], s["when_to_use"],
                     json.dumps(s["allowed_tools"], ensure_ascii=False),
                     "builtin", "系统", fp, now(), now()))
                continue
            # 判定「内置技能」：create_source=builtin，或 v1 旧库升级场景
            # （create_source 为空串但 scope=public + status=approved——当时 seed 的形态）。
            # 用户克隆/自建的同名技能（private/其他 source）永不触碰。
            is_builtin = row["create_source"] == "builtin" or (
                not row["create_source"]
                and row["scope"] == "public" and row["status"] == "approved")
            if not is_builtin:
                continue
            if row["builtin_fp"] == fp:
                # 文件未变：跳过
                continue
            c.execute(
                "UPDATE skills SET display_name=?, description=?, category=?, trigger_words=?, "
                "status='approved', skill_type='method', instructions=?, when_to_use=?, "
                "allowed_tools=?, create_source='builtin', builtin_fp=?, updated_at=? WHERE name=?",
                (s["display_name"], s["description"], s["category"], s["trigger_words"],
                 s["instructions"], s["when_to_use"],
                 json.dumps(s["allowed_tools"], ensure_ascii=False), fp, now(), s["name"]))
        if own:
            c.commit()
    finally:
        if own:
            c.close()


def _builtin_fp(s):
    """内置技能文件指纹：对 seed 入 DB 的关键字段（instructions）取 sha1 前 16 位，
    用于「文件更新 → DB 同步」判断。"""
    raw = (s.get("instructions") or "") + "|" + (s.get("display_name") or "")
    return hashlib.sha1(raw.encode("utf-8")).hexdigest()[:16]


def list_tools(include_disabled=False, scope=None, for_user_id=None):
    """列出工具库中的工具。

    - include_disabled: 是否包含已禁用的工具
    - scope: 'global' 仅公共库；'private' 仅私有库；None 不限定
    - for_user_id: 传入用户 id 时，公共库 + 该用户的私有库都返回（用户视角默认）；
      不传时与旧行为兼容——返回全部工具

    私有工具按 (scope='private' AND owner_id=?) 过滤：仅 owner 自己可见可用。
    """
    conn = get_conn()
    sql = "SELECT * FROM tools"
    where, params = [], []
    if not include_disabled:
        where.append("enabled=1")
    if scope == "global":
        where.append("scope='global'")
    elif scope == "private":
        where.append("scope='private'")
        if for_user_id is not None:
            where.append("owner_id=?")
            params.append(for_user_id)
    elif for_user_id is not None:
        # 用户视角默认：公共工具 + 自己创建的私有工具
        where.append("(scope='global' OR (scope='private' AND owner_id=?))")
        params.append(for_user_id)
    # else: 既不传 scope 也不传 for_user_id → 返回全部（兼容旧行为）
    if where:
        sql += " WHERE " + " AND ".join(where)
    sql += " ORDER BY is_user_created ASC, category, id"
    rows = conn.execute(sql, params).fetchall()
    conn.close()
    out = [_tool_row_to_dict(r) for r in rows]
    for d in out:
        d.setdefault("is_user_created", 0)
    return out


def get_tool(name):
    conn = get_conn()
    row = conn.execute("SELECT * FROM tools WHERE name=?", (name,)).fetchone()
    conn.close()
    return _tool_row_to_dict(row) if row else None


def save_tool(p):
    """新增或更新一个工具定义（不含业务逻辑，仅元数据 + handler 名）。
    审计字段：creator_name 创建者显示名（冗余快照）；create_source 来源
    （manual=用户手写/管理员创建 / upload=上传工具包 / agent_auto=智能体自动创建，默认 manual）。"""
    p = dict(p)
    _source = (p.get("create_source") or "manual").strip()
    p["create_source"] = _source
    _creator = (p.get("creator_name") or "").strip()
    name = (p.get("name") or "").strip()
    if not name:
        raise ValueError("工具名(name)必填")
    display_name = (p.get("display_name") or name).strip()
    description = (p.get("description") or "").strip()
    if not description:
        raise ValueError("工具描述必填")
    category = (p.get("category") or "general").strip()
    try:
        params = p.get("params") or {}
        if isinstance(params, str):
            params = json.loads(params or "{}")
    except Exception:
        raise ValueError("params 必须是合法 JSON")
    backend_type = (p.get("backend_type") or "builtin").strip()
    handler = (p.get("handler") or "").strip() or None
    trigger_words = (p.get("trigger_words") or "").strip()
    skip_skill = 1 if p.get("skip_skill") else 0
    owner_id = to_int(p.get("owner_id"))  # 用户私有工具的所有者；None=全局/admin
    is_user_created = 1 if p.get("is_user_created") else 0
    # 创建者显示名：优先用调用方显式传入的 creator_name，否则按 owner_id 查用户名冗余快照
    if not _creator and owner_id:
        try:
            _c0 = get_conn()
            _u = _c0.execute("SELECT display_name, username FROM users WHERE id=?",
                             (owner_id,)).fetchone()
            _c0.close()
            if _u:
                _creator = (_u["display_name"] or _u["username"] or "")
        except Exception:
            pass
    conn = get_conn()
    tid = to_int(p.get("id"))
    if tid:
        conn.execute(
            "UPDATE tools SET display_name=?,description=?,category=?,params_json=?,"
            "backend_type=?,handler=?,trigger_words=?,skip_skill=? WHERE id=?",
            (display_name, description, category, json.dumps(params, ensure_ascii=False),
             backend_type, handler, trigger_words, skip_skill, tid))
    else:
        # 用户用 create_tool 创建的私有工具：scope='private'、owner_id=用户、is_user_created=1、code_text 存代码
        # code_text 用于沙箱执行，handler 字段存 'user_code_<id>' 之类的标识供 build_session_tools 识别
        code_text = (p.get("code_text") or "").strip()
        if is_user_created and not code_text:
            raise ValueError("用户私有工具必须提供 code_text（Python 代码，定义 def run(a)）")
        scope = p.get("scope") or ("private" if is_user_created else "global")
        conn.execute(
            "INSERT INTO tools (name,display_name,description,category,params_json,"
            "backend_type,handler,trigger_words,scope,owner_id,is_user_created,code_text,"
            "builtin,enabled,skip_skill,creator_name,create_source,created_at) "
            "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,0,1,?,?,?,?)",
            (name, display_name, description, category, json.dumps(params, ensure_ascii=False),
             backend_type, handler, trigger_words, scope, owner_id, is_user_created, code_text,
             skip_skill, _creator, _source, now()))
    conn.commit(); conn.close()


def delete_tool(tool_id):
    conn = get_conn()
    conn.execute("DELETE FROM tools WHERE id=? AND builtin=0", (tool_id,))
    conn.commit(); conn.close()


def toggle_tool(tool_id, enabled):
    conn = get_conn()
    conn.execute("UPDATE tools SET enabled=? WHERE id=?", (1 if enabled else 0, tool_id))
    conn.commit(); conn.close()


def inc_tool_calls(name, n=1):
    conn = get_conn()
    conn.execute("UPDATE tools SET call_count = call_count + ? WHERE name=?", (n, name))
    conn.commit(); conn.close()


# ---------- 用户上传技能（沙箱执行 + 审核） ----------
# status 取值：
#   private  —— 私有技能，仅上传者本人可用（沙箱执行），无需审核
#   pending  —— 申请发布到公共广场，等待管理员审核
#   approved —— 审核通过，公共可用
#   rejected —— 审核驳回
_SKILL_NAME_RE = __import__("re").compile(r"^[A-Za-z_][A-Za-z0-9_]*$")


def _skill_row_to_dict(row):
    d = row_to_dict(row)
    try:
        d["params"] = json.loads(d["params_json"]) if d.get("params_json") else {}
    except Exception:
        d["params"] = {}
    d["rules"] = (d.get("rules") or "")
    try:
        d["allowed_tools"] = json.loads(d["allowed_tools"]) if d.get("allowed_tools") else []
    except Exception:
        d["allowed_tools"] = []
    if not isinstance(d["allowed_tools"], list):
        d["allowed_tools"] = []
    d["kind"] = "skill"
    d["skill_type"] = d.get("skill_type") or "code"
    return d


def save_skill(p, owner_id):
    """新增一个用户上传技能。name 须为合法标识符；code_text 由调用方先做静态扫描。

    审计字段：creator_name 为创建者显示名（冗余快照，按 owner_id 查 users 的
    display_name/username）；create_source 标记来源：manual=用户手写创建 /
    upload=上传技能包 / agent_auto=智能体自动创建（默认 manual）。"""
    p = dict(p)
    _source = (p.get("create_source") or "manual").strip()
    p["create_source"] = _source
    _creator = (p.get("creator_name") or "").strip()
    if not _creator and owner_id:
        try:
            _c0 = get_conn()
            _u = _c0.execute("SELECT display_name, username FROM users WHERE id=?",
                             (owner_id,)).fetchone()
            _c0.close()
            if _u:
                _creator = (_u["display_name"] or _u["username"] or "")
        except Exception:
            pass
    p["creator_name"] = _creator
    name = (p.get("name") or "").strip()
    if not _SKILL_NAME_RE.match(name):
        raise ValueError("技能名(name)须为字母/数字/下划线组成的有效标识符（如 my_skill）")
    display_name = (p.get("display_name") or name).strip()
    description = (p.get("description") or "").strip()
    if not description:
        raise ValueError("技能描述必填（用于智能体检索匹配）")
    category = (p.get("category") or "general").strip()
    code_text = p.get("code_text") or ""
    skill_type = (p.get("skill_type") or "code").strip().lower()
    if skill_type not in ("method", "code"):
        skill_type = "code"
    if skill_type == "code" and not code_text.strip():
        raise ValueError("code 类技能代码必填（须定义 run(args) 函数）")
    instructions = (p.get("instructions") or "").strip()
    if skill_type == "method" and not instructions:
        raise ValueError("method 类技能需提供提示词/流程（instructions），不执行代码")
    try:
        params = p.get("params") or {}
        if isinstance(params, str):
            params = json.loads(params or "{}")
    except Exception:
        raise ValueError("params 必须是合法 JSON")
    scope = (p.get("scope") or "private").strip()
    if scope not in ("private", "public"):
        scope = "private"
    # 场景四：业务规则 + 工具清单（白名单）。allowed_tools 统一存为 JSON 数组文本。
    rules = (p.get("rules") or "").strip()
    at = p.get("allowed_tools") or []
    if isinstance(at, str):
        try:
            at = json.loads(at)
        except Exception:
            at = []
    if not isinstance(at, list):
        at = []
    at_text = json.dumps(at, ensure_ascii=False)
    # 私有技能立即可用；申请公开则进入待审核（允许调用方显式传入 status 覆盖，如对话自动创建直接 approved）
    status = p.get("status") or ("private" if scope == "private" else "pending")
    conn = get_conn()
    try:
        cur = conn.execute(
            "INSERT INTO skills (name,display_name,description,category,params_json,"
            "trigger_words,scope,owner_id,status,code_text,enabled,skill_type,instructions,when_to_use,rules,allowed_tools,creator_name,create_source,created_at,updated_at) "
            "VALUES (?,?,?,?,?,?,?,?,?,?,1,?,?,?,?,?,?,?,?,?)",
            (name, display_name, description, category, json.dumps(params, ensure_ascii=False),
             (p.get("trigger_words") or "").strip(), scope, owner_id, status,
             code_text, skill_type, instructions, (p.get("when_to_use") or "").strip(),
             rules, at_text, _creator, _source, now(), now()))
        sid = cur.lastrowid
        conn.commit()
    except sqlite3.IntegrityError:
        conn.close()
        raise ValueError("技能名(name)已存在，请换一个唯一名称")
    conn.close()
    return sid


def get_skill(skill_id, with_code=False):
    conn = get_conn()
    row = conn.execute(
        "SELECT s.*, u.username AS owner_name FROM skills s "
        "LEFT JOIN users u ON s.owner_id=u.id WHERE s.id=?", (skill_id,)).fetchone()
    conn.close()
    if not row:
        return None
    d = _skill_row_to_dict(row)
    if not with_code:
        d.pop("code_text", None)
    return d


def get_skill_by_name(name, with_code=True):
    conn = get_conn()
    row = conn.execute("SELECT * FROM skills WHERE name=?", (name,)).fetchone()
    conn.close()
    return _skill_row_to_dict(row) if row else None



def delete_skill(skill_id, for_user_id=None, is_admin=False):
    conn = get_conn()
    if is_admin:
        conn.execute("DELETE FROM skills WHERE id=?", (skill_id,))
    else:
        conn.execute("DELETE FROM skills WHERE id=? AND owner_id=?", (skill_id, for_user_id))
    n = conn.total_changes
    conn.commit(); conn.close()
    return n > 0


def toggle_skill(skill_id, enabled):
    conn = get_conn()
    conn.execute("UPDATE skills SET enabled=?, updated_at=? WHERE id=?",
                 (1 if enabled else 0, now(), skill_id))
    conn.commit(); conn.close()


def set_skill_visibility(skill_id, visibility, user_id=None, is_admin=False):
    """设置技能可见性：公开（所有人可用）/ 私有（仅我可用）。

    visibility == 'public'  -> scope='public', status='approved', enabled=1
    visibility == 'private' -> scope='private', status='private',  enabled=1
    仅本人或管理员可操作；公开即直接对所有人可用，私有即仅所有者可用。
    """
    if visibility not in ("public", "private"):
        raise ValueError("visibility 只能是 public 或 private")
    conn = get_conn()
    try:
        row = conn.execute("SELECT * FROM skills WHERE id=?", (skill_id,)).fetchone()
        if not row:
            raise ValueError("技能不存在")
        d = _skill_row_to_dict(row)
        if d["owner_id"] != user_id and not is_admin:
            raise ValueError("无权修改该技能可见性")
        if visibility == "public":
            scope, status = "public", "approved"
        else:
            scope, status = "private", "private"
        conn.execute(
            "UPDATE skills SET scope=?, status=?, enabled=1, updated_at=? WHERE id=?",
            (scope, status, now(), skill_id))
        conn.commit()
    finally:
        conn.close()


def review_skill(skill_id, reviewer_id, action, note=""):
    """管理员审核：approve -> approved(启用)；reject -> rejected(停用)。"""
    action = (action or "").strip().lower()
    if action not in ("approve", "reject"):
        raise ValueError("action 只能是 approve 或 reject")
    status = "approved" if action == "approve" else "rejected"
    enabled = 1 if action == "approve" else 0
    conn = get_conn()
    conn.execute(
        "UPDATE skills SET status=?, enabled=?, reviewed_by=?, reviewed_at=?, review_note=? "
        "WHERE id=?", (status, enabled, reviewer_id, now(), (note or "").strip(), skill_id))
    conn.commit(); conn.close()


def inc_skill_calls(name, n=1):
    conn = get_conn()
    conn.execute("UPDATE skills SET call_count = call_count + ? WHERE name=?", (n, name))
    conn.commit(); conn.close()


def _version_row_to_dict(row):
    d = row_to_dict(row)
    try:
        d["params"] = json.loads(d["params_json"]) if d.get("params_json") else {}
    except Exception:
        d["params"] = {}
    d["rules"] = (d.get("rules") or "")
    try:
        d["allowed_tools"] = json.loads(d["allowed_tools"]) if d.get("allowed_tools") else []
    except Exception:
        d["allowed_tools"] = []
    if not isinstance(d["allowed_tools"], list):
        d["allowed_tools"] = []
    return d


def _snapshot_skill(conn, skill_id, version, fields, by, note):
    """把技能某一时刻的快照写入 skills_versions（调用方负责提交/关闭）。"""
    at = fields.get("allowed_tools") or []
    if isinstance(at, str):
        try:
            at = json.loads(at)
        except Exception:
            at = []
    if not isinstance(at, list):
        at = []
    conn.execute(
            "INSERT INTO skills_versions "
            "(skill_id,version,display_name,description,category,params_json,trigger_words,when_to_use,code_text,"
            "skill_type,instructions,rules,allowed_tools,created_by,note,created_at) "
            "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (skill_id, version, fields.get("display_name"), fields.get("description"),
             fields.get("category"), fields.get("params_json"), fields.get("trigger_words"),
             (fields.get("when_to_use") or ""), fields.get("code_text"),
             (fields.get("skill_type") or "code"),
             (fields.get("instructions") or ""), (fields.get("rules") or ""),
             json.dumps(at, ensure_ascii=False), by, note, now()))


def update_skill(skill_id, p, user_id, is_admin=False):
    """更新技能并升版本：先快照当前版本到历史，再覆盖字段。version+1。
    若公开(approved)技能改了代码，则重回待审核(pending)以确保重新审核。"""
    conn = get_conn()
    try:
        row = conn.execute("SELECT * FROM skills WHERE id=?", (skill_id,)).fetchone()
        if not row:
            raise ValueError("技能不存在")
        d = _skill_row_to_dict(row)
        if d["owner_id"] != user_id and not is_admin:
            raise ValueError("无权修改该技能")
        # 解析参数
        params = p.get("params") if isinstance(p.get("params"), dict) else {}
        if isinstance(p.get("params"), str):
            try:
                params = json.loads(p.get("params") or "{}")
            except Exception:
                raise ValueError("params 必须是合法 JSON")
        code = (p.get("code_text") if p.get("code_text") is not None else d["code_text"])
        skill_type = (p.get("skill_type") if p.get("skill_type") is not None else (d.get("skill_type") or "code"))
        skill_type = (skill_type or "code").strip().lower()
        if skill_type not in ("method", "code"):
            skill_type = "code"
        instructions = (p.get("instructions") if p.get("instructions") is not None else (d.get("instructions") or ""))
        instructions = (instructions or "").strip()
        # 场景四字段：业务规则 + 工具白名单（缺省沿用原值）
        rules = p.get("rules") if p.get("rules") is not None else (d.get("rules") or "")
        at = p.get("allowed_tools") if p.get("allowed_tools") is not None else (d.get("allowed_tools") or [])
        if isinstance(at, str):
            try:
                at = json.loads(at)
            except Exception:
                at = d.get("allowed_tools") or []
        if not isinstance(at, list):
            at = d.get("allowed_tools") or []
        at_text = json.dumps(at, ensure_ascii=False)
        # 快照当前版本
        _snapshot_skill(conn, skill_id, d["version"], d, user_id, "更新前快照")
        new_version = d["version"] + 1
        status, enabled = d["status"], d["enabled"]
        rules_changed = (rules or "") != (d.get("rules") or "")
        at_changed = at_text != json.dumps(d.get("allowed_tools") or [], ensure_ascii=False)
        if d["status"] == "approved" and (code != d["code_text"] or skill_type != (d.get("skill_type") or "code") or rules_changed or at_changed):
            # 公开技能改代码 / 改业务规则 / 改工具白名单 → 需重新审核
            status, enabled = "pending", 0
        conn.execute(
            "UPDATE skills SET display_name=?,description=?,category=?,params_json=?,"
            "trigger_words=?,code_text=?,skill_type=?,instructions=?,when_to_use=?,rules=?,allowed_tools=?,version=?,status=?,enabled=?,updated_at=? WHERE id=?",
            (p.get("display_name", d["display_name"]), p.get("description", d["description"]),
             p.get("category", d["category"]), json.dumps(params, ensure_ascii=False),
             p.get("trigger_words", d["trigger_words"]), code, skill_type, instructions,
             (p.get("when_to_use") or d.get("when_to_use") or "").strip(), (rules or ""),
             at_text, new_version, status, enabled, now(), skill_id))
        conn.commit()
    finally:
        conn.close()
    return new_version


def list_skill_versions(skill_id):
    conn = get_conn()
    rows = conn.execute(
        "SELECT * FROM skills_versions WHERE skill_id=? ORDER BY version DESC", (skill_id,)
    ).fetchall()
    conn.close()
    return [_version_row_to_dict(r) for r in rows]


def rollback_skill(skill_id, version, user_id, is_admin=False):
    """回滚到指定历史版本：先快照当前版本，再覆盖为历史快照。version+1。
    若当前为公开(approved)，回滚改了代码 → 重回待审核。"""
    conn = get_conn()
    try:
        row = conn.execute("SELECT * FROM skills WHERE id=?", (skill_id,)).fetchone()
        if not row:
            raise ValueError("技能不存在")
        d = _skill_row_to_dict(row)
        if d["owner_id"] != user_id and not is_admin:
            raise ValueError("无权回滚该技能")
        v = conn.execute(
            "SELECT * FROM skills_versions WHERE skill_id=? AND version=?",
            (skill_id, version)).fetchone()
        if not v:
            raise ValueError("目标版本不存在")
        vd = _version_row_to_dict(v)
        # 快照当前版本
        _snapshot_skill(conn, skill_id, d["version"], d, user_id, "回滚前快照")
        new_version = d["version"] + 1
        status, enabled = d["status"], d["enabled"]
        vd_at = json.dumps(vd.get("allowed_tools") or [], ensure_ascii=False)
        d_at = json.dumps(d.get("allowed_tools") or [], ensure_ascii=False)
        if d["status"] == "approved" and (vd["code_text"] != d["code_text"]
                                          or (vd.get("rules") or "") != (d.get("rules") or "")
                                          or vd_at != d_at):
            status, enabled = "pending", 0
        conn.execute(
            "UPDATE skills SET display_name=?,description=?,category=?,params_json=?,"
            "trigger_words=?,code_text=?,when_to_use=?,rules=?,allowed_tools=?,version=?,status=?,enabled=?,updated_at=? WHERE id=?",
            (vd.get("display_name"), vd.get("description"), vd.get("category"),
             vd.get("params_json"), vd.get("trigger_words"), vd.get("code_text"),
             (vd.get("when_to_use") or ""), (vd.get("rules") or ""), vd_at, new_version, status, enabled, now(), skill_id))
        conn.commit()
    finally:
        conn.close()
    return new_version


def clone_skill(skill_id, user_id):
    """把已发布的公开技能收藏/复制为本人私有技能（私有立即可用，免审核）。"""
    conn = get_conn()
    try:
        row = conn.execute("SELECT * FROM skills WHERE id=?", (skill_id,)).fetchone()
        if not row:
            raise ValueError("技能不存在")
        d = _skill_row_to_dict(row)
        if d["status"] != "approved":
            raise ValueError("仅可收藏已发布的公开技能")
        base = d["name"]
        n = 1
        new_name = "%s_fork%d" % (base, n)
        while conn.execute("SELECT 1 FROM skills WHERE name=?", (new_name,)).fetchone():
            n += 1
            new_name = "%s_fork%d" % (base, n)
        cur = conn.execute(
            "INSERT INTO skills (name,display_name,description,category,params_json,"
            "trigger_words,scope,owner_id,status,code_text,enabled,skill_type,instructions,when_to_use,version,created_at,updated_at) "
            "VALUES (?,?,?,?,?,?,?,?,?,?,1,?,?,?,1,?,?)",
            (new_name, (d["display_name"] or d["name"]) + " (副本)", d["description"],
             d["category"], d["params_json"], d["trigger_words"], "private", user_id,
             "private", d["code_text"], (d.get("skill_type") or "code"), (d.get("instructions") or ""),
             (d.get("when_to_use") or ""), now(), now()))
        sid = cur.lastrowid
        conn.commit()
    finally:
        conn.close()
    return sid


def install_skill(user_id, skill_id):
    """普通用户把已发布的公开技能安装到自己的可用列表（轻量开关，不复制副本）。"""
    conn = get_conn()
    try:
        row = conn.execute("SELECT * FROM skills WHERE id=?", (skill_id,)).fetchone()
        if not row:
            raise ValueError("技能不存在")
        d = _skill_row_to_dict(row)
        if d["status"] != "approved" or d.get("scope") != "public":
            raise ValueError("仅可安装已发布的公开技能")
        conn.execute(
            "INSERT OR IGNORE INTO skill_installs (user_id, skill_id, created_at) VALUES (?,?,?)",
            (user_id, skill_id, now()))
        conn.commit()
    finally:
        conn.close()


def uninstall_skill(user_id, skill_id):
    """卸载技能（取消安装，之后该用户不可再调用该技能）。"""
    conn = get_conn()
    try:
        conn.execute("DELETE FROM skill_installs WHERE user_id=? AND skill_id=?",
                     (user_id, skill_id))
        conn.commit()
    finally:
        conn.close()


def list_skills(for_user_id=None, include_all=False, usable_only=False, with_code=False,
                category=None, keyword=None, sort=None):
    """列出技能（P3 扩展 category/keyword/sort 发现筛选）。
    include_all : 管理员查看全部（含各状态）。
    usable_only : 仅返回「当前可用」技能（已安装公开技能 + 本人私有），供智能体注入。
    其余        : 返回「已发布 + 本人全部（含待审/驳回）」，供个人广场页展示；
                  若传入 for_user_id，则额外标注每个技能的 installed（用户是否已安装）。
    sort='popular' : 按调用次数降序（热门排行）。
    """
    conn = get_conn()
    sql = "SELECT * FROM skills"
    where, params = [], []
    if include_all:
        pass  # 全量
    elif usable_only:
        if for_user_id:
            # 仅已安装的公开技能 + 本人私有技能可用
            where.append("((owner_id=? AND status='private') OR "
                         "(status='approved' AND id IN (SELECT skill_id FROM skill_installs WHERE user_id=?)))")
            params.extend([for_user_id, for_user_id])
        else:
            where.append("status='approved'")
    else:
        if for_user_id:
            where.append("((status='approved') OR owner_id=?)")
            params.append(for_user_id)
        else:
            where.append("status='approved'")
    if category:
        where.append("category=?")
        params.append(category)
    if keyword:
        where.append("(name LIKE ? OR display_name LIKE ? OR description LIKE ? OR trigger_words LIKE ?)")
        params.extend(["%" + keyword + "%"] * 4)
    if where:
        sql += " WHERE " + " AND ".join(where)
    if sort == "popular":
        sql += " ORDER BY call_count DESC, id DESC"
    else:
        sql += " ORDER BY created_at DESC, id DESC"
    rows = conn.execute(sql, params).fetchall()
    # 标注「我的智能体是否可用」（供前端展示开关 / composer 下拉筛选）：
    #   - 本人私有技能（status=private AND owner_id==for_user_id）→ True（私有天然可用）
    #   - 公开技能 → 看 skill_installs（装→True，否则 False；本人发布的公开技能也不例外，
    #     以保持与「点击安装/卸载」开关一致语义）
    # 与前端列表「私有 = 已安装」兜底规则、usable_only SQL 子句语义一致
    installed = set()
    if for_user_id:
        installed = {r["skill_id"] for r in
                     conn.execute("SELECT skill_id FROM skill_installs WHERE user_id=?",
                                  (for_user_id,)).fetchall()}
    conn.close()
    out = []
    for r in rows:
        d = _skill_row_to_dict(r)
        if not with_code:
            d.pop("code_text", None)
        if for_user_id:
            # 本人私有技能天然可用（不依赖 skill_installs）
            if r["status"] == "private" and r["owner_id"] == for_user_id:
                d["installed"] = True
            else:
                d["installed"] = d["id"] in installed
        out.append(d)
    return out


# ────────────────────────────────────────────────────────────────────────
# 会话级已激活能力（session_active_caps）：跨轮持久，匹配只增不减
# ────────────────────────────────────────────────────────────────────────
def get_active_caps(session_id, user_id=None):
    """返回该会话已激活的能力名称集合（cap_name 集合）。
    **用户隔离**：传入 user_id 时按 (user_id, session_id) 查——防止不同用户撞
    session_id 时继承对方激活的能力集。不传 user_id 时按 session_id 查（旧接口兼容）。

    匹配层用它把「上一轮已加载、本轮路由未选中」的能力补回选集，
    从而保证已加载技能/工具在多轮对话中持续在场、不脱落。
    """
    if not session_id:
        return set()
    conn = get_conn()
    try:
        if user_id is not None:
            rows = conn.execute(
                "SELECT cap_name FROM session_active_caps WHERE user_id=? AND session_id=?",
                (user_id, session_id)).fetchall()
        else:
            rows = conn.execute(
                "SELECT cap_name FROM session_active_caps WHERE session_id=?",
                (session_id,)).fetchall()
    finally:
        conn.close()
    return {r["cap_name"] for r in rows}


def add_active_caps(session_id, names, user_id=None, cap_type="skill"):
    """把本轮激活的能力名称写入会话激活集（已存在则忽略，幂等）。
    **用户隔离**：必须传 user_id，按 (user_id, session_id, cap_name) 排重。

    names: 能力 name 列表。cap_type 仅标注（'skill'/'tool'），
    匹配时统一用 name 回查 library，不依赖 type。
    """
    if not session_id or not names:
        return
    if user_id is None:
        # 防御性兜底：未传 user_id 时拒绝写入，避免历史全局共享行为。
        # 调用方应传 u["id"]。这里直接 return 不抛异常，避免影响主流程。
        return
    conn = get_conn()
    try:
        for n in names:
            if not n:
                continue
            conn.execute(
                "INSERT OR IGNORE INTO session_active_caps "
                "(user_id, session_id, cap_name, cap_type) VALUES (?, ?, ?, ?)",
                (user_id, session_id, n, cap_type))
        conn.commit()
    finally:
        conn.close()


def clear_active_caps(session_id, user_id=None):
    """会话结束/开新对话时清空激活集（由前端开新会话时调用，或后端按需）。
    **用户隔离**：传 user_id 时仅清当前用户的激活集；不传时清整个 session_id 的（兼容旧接口，但生产应传）。"""
    if not session_id:
        return
    conn = get_conn()
    try:
        if user_id is not None:
            conn.execute(
                "DELETE FROM session_active_caps WHERE session_id=? AND user_id=?",
                (session_id, user_id))
        else:
            conn.execute(
                "DELETE FROM session_active_caps WHERE session_id=?",
                (session_id,))
        conn.commit()
    finally:
        conn.close()


# ────────────────────────────────────────────────────────────────────────────
# 用户跨会话长期记忆（user_memory）：对标 WorkBuddy「云记忆自动注入」的产品终端
# 用户层。每轮对话自动读该用户全部条目注入 system prompt；写入口为 save_memory /
# forget_memory 元工具（agent 在用户要求「记住/忘掉」时调用）。
# ────────────────────────────────────────────────────────────────────────────

def get_user_memory(user_id):
    """返回该用户的全部长期记忆条目（id, mem_type, mem_key, content, updated_at），按更新时间倒序。"""
    if not user_id:
        return []
    conn = get_conn()
    try:
        rows = conn.execute(
            "SELECT id, mem_type, mem_key, content, updated_at FROM user_memory "
            "WHERE user_id=? ORDER BY updated_at DESC, id DESC",
            (user_id,)).fetchall()
    finally:
        conn.close()
    return [dict(r) for r in rows]


def save_user_memory(user_id, mem_type, content, mem_key=None):
    """写入/更新一条用户长期记忆。

    - mem_key 非空时按 (user_id, mem_key) 去重：已存在则更新内容（同一主题只保留一条）；
    - mem_key 为空则直接新增（允许多条无键记忆并存）；
    - mem_type ∈ {preference, project, fact}，缺省 fact。
    返回 True 表示写入成功；入参非法返回 None。
    """
    if not user_id or not content or not content.strip():
        return None
    mem_type = (mem_type or "fact").strip() or "fact"
    content = content.strip()
    mem_key = (mem_key or "").strip() or None
    conn = get_conn()
    try:
        if mem_key:
            conn.execute(
                "INSERT INTO user_memory (user_id, mem_type, mem_key, content, updated_at) "
                "VALUES (?,?,?,?,?) "
                "ON CONFLICT(user_id, mem_key) DO UPDATE SET "
                "mem_type=excluded.mem_type, content=excluded.content, updated_at=excluded.updated_at",
                (user_id, mem_type, mem_key, content, now()))
        else:
            conn.execute(
                "INSERT INTO user_memory (user_id, mem_type, content, updated_at) VALUES (?,?,?,?)",
                (user_id, mem_type, content, now()))
        conn.commit()
    finally:
        conn.close()
    return True


def delete_user_memory(user_id, mem_id=None, mem_key=None):
    """删除该用户的一条长期记忆。按 mem_id 优先，否则按 mem_key。返回删除条数。"""
    if not user_id:
        return 0
    conn = get_conn()
    try:
        if mem_id:
            cur = conn.execute("DELETE FROM user_memory WHERE user_id=? AND id=?", (user_id, mem_id))
        elif mem_key:
            cur = conn.execute("DELETE FROM user_memory WHERE user_id=? AND mem_key=?", (user_id, mem_key))
        else:
            return 0
        conn.commit()
        return cur.rowcount
    finally:
        conn.close()


# ────────────────────────────────────────────────────────────────────────────
# 用户自动画像（P2⑫ 云记忆自动画像）：对话后由 LLM 自动抽取，覆盖式维护。
# 与 user_memory（零散、显式记忆）互补，是结构化的「用户是谁 / 偏好 / 领域」画像。
# ────────────────────────────────────────────────────────────────────────────

def get_user_profile(user_id):
    """返回该用户的自动画像文本（Markdown），无则返回 None。"""
    if not user_id:
        return None
    conn = get_conn()
    try:
        row = conn.execute(
            "SELECT profile FROM user_profiles WHERE user_id=?", (user_id,)).fetchone()
    finally:
        conn.close()
    return row["profile"] if row and row["profile"] else None


def save_user_profile(user_id, profile):
    """覆盖式写入/更新该用户的自动画像（UPSERT）。profile 为空则删除该画像。返回 True/None。"""
    if not user_id:
        return None
    profile = (profile or "").strip()
    conn = get_conn()
    try:
        if not profile:
            conn.execute("DELETE FROM user_profiles WHERE user_id=?", (user_id,))
        else:
            conn.execute(
                "INSERT INTO user_profiles (user_id, profile, updated_at) VALUES (?,?,?) "
                "ON CONFLICT(user_id) DO UPDATE SET profile=excluded.profile, updated_at=excluded.updated_at",
                (user_id, profile, now()))
        conn.commit()
    finally:
        conn.close()
    return True


# ---------- 任务管理（Task*）----------

def create_task(user_id, session_id, title, description="", status="pending",
                parent_id=None, active_form="", add_blocks=None, add_blocked_by=None):
    """创建一条任务，返回新任务 id。"""
    if not user_id or not (title or "").strip():
        return None
    ts = now()
    _blocks = json.dumps(add_blocks or [], ensure_ascii=False)
    _blocked = json.dumps(add_blocked_by or [], ensure_ascii=False)
    conn = get_conn()
    try:
        cur = conn.execute(
            "INSERT INTO tasks (user_id, session_id, title, description, status, "
            "parent_id, active_form, add_blocks, add_blocked_by, created_at, updated_at) "
            "VALUES (?,?,?,?,?,?,?,?,?,?,?)",
            (user_id, session_id, title.strip(), (description or "").strip(), status or "pending",
             parent_id, (active_form or "").strip(), _blocks, _blocked, ts, ts))
        conn.commit()
        return cur.lastrowid
    finally:
        conn.close()


def get_task(task_id, user_id=None):
    """按 id 取任务；传入 user_id 时做归属校验。返回 dict 或 None。"""
    if not task_id:
        return None
    conn = get_conn()
    try:
        if user_id:
            row = conn.execute("SELECT * FROM tasks WHERE id=? AND user_id=?",
                               (task_id, user_id)).fetchone()
        else:
            row = conn.execute("SELECT * FROM tasks WHERE id=?", (task_id,)).fetchone()
        return dict(row) if row else None
    finally:
        conn.close()


_VALID_TASK_STATUS = {"pending", "in_progress", "completed", "deleted"}


def update_task(task_id, user_id=None, **fields):
    """更新任务字段（标题/描述/状态/依赖等），返回更新后的任务 dict 或 None。"""
    if not task_id:
        return None
    _allowed = {"title", "description", "status", "parent_id", "active_form",
                "add_blocks", "add_blocked_by"}
    sets, params = [], []
    for k, v in fields.items():
        if k not in _allowed:
            continue
        if k == "status" and v not in _VALID_TASK_STATUS:
            continue
        if k in ("add_blocks", "add_blocked_by") and not isinstance(v, str):
            v = json.dumps(v or [], ensure_ascii=False)
        sets.append(f"{k}=?")
        params.append(v)
    if not sets:
        return get_task(task_id, user_id)
    sets.append("updated_at=?")
    params.append(now())
    params.append(task_id)
    if user_id:
        params.append(user_id)
    conn = get_conn()
    try:
        sql = "UPDATE tasks SET " + ", ".join(sets) + " WHERE id=?"
        if user_id:
            sql += " AND user_id=?"
        conn.execute(sql, params)
        conn.commit()
        return get_task(task_id, user_id)
    finally:
        conn.close()


def list_tasks(user_id, session_id=None, include_deleted=False):
    """列出该用户的任务；传入 session_id 时仅返回该连续对话的任务。返回 dict 列表。"""
    if not user_id:
        return []
    conn = get_conn()
    try:
        if session_id:
            sql = "SELECT * FROM tasks WHERE user_id=? AND session_id=?"
            if not include_deleted:
                sql += " AND status!='deleted'"
            sql += " ORDER BY id ASC"
            rows = conn.execute(sql, (user_id, session_id)).fetchall()
        else:
            sql = "SELECT * FROM tasks WHERE user_id=?"
            if not include_deleted:
                sql += " AND status!='deleted'"
            sql += " ORDER BY id ASC"
            rows = conn.execute(sql, (user_id,)).fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()


# =================== P2⑩ 定时自动化 CRUD ===================
def _compute_next_run(schedule_type, rrule, base_dt):
    """计算下一次执行时间（ISO 字符串）。无依赖、支持常用 RRULE 子集。"""
    from datetime import timedelta
    if schedule_type == "once" or not rrule:
        return base_dt.strftime("%Y-%m-%d %H:%M:%S") if schedule_type == "once" else None
    # 解析 RRULE 子集：FREQ=DAILY|WEEKLY|MONTHLY|HOURLY [INTERVAL=n] [BYDAY=MO,WE,...]
    try:
        kv = {}
        for part in rrule.replace("\n", ",").split(","):
            part = part.strip()
            if "=" in part and part.upper().startswith("FREQ") or "=" in part:
                if ":" in part:
                    k, v = part.split(":", 1)
                elif "=" in part:
                    k, v = part.split("=", 1)
                else:
                    continue
                kv[k.strip().upper()] = v.strip()
        freq = (kv.get("FREQ") or "DAILY").upper()
        interval = int(kv.get("INTERVAL", "1") or "1")
        if freq == "HOURLY":
            nxt = base_dt + timedelta(hours=interval)
        elif freq == "WEEKLY":
            nxt = base_dt + timedelta(weeks=interval)
            byday = kv.get("BYDAY")
            if byday:
                order = {"MO": 0, "TU": 1, "WE": 2, "TH": 3, "FR": 4, "SA": 5, "SU": 6}
                wanted = [order.get(d.strip()[:2].upper(), base_dt.weekday())
                          for d in byday.split(",") if d.strip()[:2].upper() in order]
                if wanted:
                    while nxt.weekday() not in wanted:
                        nxt += timedelta(days=1)
        elif freq == "MONTHLY":
            # 简单加月份（按 30 天近似，避免 calendar 依赖）
            nxt = base_dt + timedelta(days=30 * interval)
        else:  # DAILY
            nxt = base_dt + timedelta(days=interval)
        return nxt.strftime("%Y-%m-%d %H:%M:%S")
    except Exception:
        return (base_dt + timedelta(days=1)).strftime("%Y-%m-%d %H:%M:%S")


def create_automation(owner_id, name, prompt, schedule_type="recurring", rrule="",
                      scheduled_at=None, valid_from=None, valid_until=None, status="ACTIVE",
                      notify_email=""):
    conn = get_conn()
    t = now()
    nr = _compute_next_run(schedule_type, rrule, datetime.now()) if status == "ACTIVE" else None
    cur = conn.execute(
        "INSERT INTO automations (name,prompt,schedule_type,rrule,scheduled_at,status,"
        "valid_from,valid_until,owner_id,next_run,created_at,updated_at,notify_email) "
        "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
        (name.strip(), prompt.strip(), schedule_type, rrule or "", scheduled_at, status,
         valid_from, valid_until, owner_id, nr, t, t, notify_email or ""))
    aid = cur.lastrowid
    conn.commit(); conn.close()
    return aid


def get_automation(aid, owner_id=None):
    conn = get_conn()
    if owner_id:
        row = conn.execute("SELECT * FROM automations WHERE id=? AND owner_id=?", (aid, owner_id)).fetchone()
    else:
        row = conn.execute("SELECT * FROM automations WHERE id=?", (aid,)).fetchone()
    conn.close()
    return dict(row) if row else None


def list_automations(owner_id=None, include_all=False):
    conn = get_conn()
    if include_all:
        rows = conn.execute("SELECT * FROM automations ORDER BY id DESC").fetchall()
    elif owner_id:
        rows = conn.execute("SELECT * FROM automations WHERE owner_id=? ORDER BY id DESC",
                            (owner_id,)).fetchall()
    else:
        rows = []
    conn.close()
    return [dict(r) for r in rows]


def update_automation(aid, **fields):
    allowed = {"name", "prompt", "schedule_type", "rrule", "scheduled_at",
               "status", "valid_from", "valid_until", "notify_email"}
    sets, vals = [], []
    for k, v in fields.items():
        if k in allowed and v is not None:
            sets.append(f"{k}=?")
            vals.append(v)
    if not sets:
        return False
    sets.append("updated_at=?")
    vals.append(now())
    # 改调度/状态后重算 next_run
    if "status" in fields or "rrule" in fields or "schedule_type" in fields:
        row = get_automation(aid)
        if row:
            nr = _compute_next_run(fields.get("schedule_type", row["schedule_type"]),
                                   fields.get("rrule", row["rrule"]), datetime.now()) \
                if fields.get("status", row["status"]) == "ACTIVE" else None
            sets.append("next_run=?")
            vals.append(nr)
    conn = get_conn()
    conn.execute(f"UPDATE automations SET {','.join(sets)} WHERE id=?", vals + [aid])
    conn.commit(); conn.close()
    return True


def delete_automation(aid):
    conn = get_conn()
    conn.execute("DELETE FROM automations WHERE id=?", (aid,))
    conn.execute("DELETE FROM automation_runs WHERE automation_id=?", (aid,))
    conn.commit(); conn.close()
    return True


def record_automation_run(aid, status, result_text=None, error=None):
    """写入一条执行历史，并回写 automations.last_run / next_run。"""
    conn = get_conn()
    t = now()
    cur = conn.execute(
        "INSERT INTO automation_runs (automation_id,started_at,finished_at,status,result_text,error) "
        "VALUES (?,?,?,?,?,?)", (aid, t, t, status, result_text, error))
    run_id = cur.lastrowid
    row = conn.execute("SELECT schedule_type,rrule,status FROM automations WHERE id=?", (aid,)).fetchone()
    if row:
        nr = _compute_next_run(row["schedule_type"], row["rrule"], datetime.now()) \
            if row["status"] == "ACTIVE" else None
        conn.execute("UPDATE automations SET last_run=?, next_run=? WHERE id=?", (t, nr, aid))
    conn.commit(); conn.close()
    return run_id


# ---------- 系统配置（键值）与 SMTP 邮件服务器配置 ----------
def get_system_config(key):
    """读取系统配置（JSON 字符串或普通文本），不存在返回 None。"""
    conn = get_conn()
    row = conn.execute("SELECT value FROM system_config WHERE key=?", (key,)).fetchone()
    conn.close()
    return row["value"] if row else None


def set_system_config(key, value, by=None):
    """写入/更新系统配置（value 为字符串，复杂结构请先 json.dumps）。"""
    conn = get_conn()
    t = now()
    conn.execute(
        "INSERT INTO system_config (key,value,updated_at,updated_by) VALUES (?,?,?,?) "
        "ON CONFLICT(key) DO UPDATE SET value=excluded.value, "
        "updated_at=excluded.updated_at, updated_by=excluded.updated_by",
        (key, value, t, by or ""))
    conn.commit(); conn.close()


# SMTP 配置键
SMTP_KEY = "smtp"
# SMTP 配置对外字段（get_smtp_config 返回）；密码在对外返回时掩码
SMTP_FIELDS = ["host", "port", "username", "password", "sender", "use_tls", "use_ssl",
               "timeout", "enabled"]


def get_smtp_config(mask_password=True):
    """返回 SMTP 配置 dict；未配置返回空 dict。mask_password=True 时 password 返回 '********'。"""
    raw = get_system_config(SMTP_KEY)
    if not raw:
        return {}
    try:
        cfg = json.loads(raw)
    except Exception:
        return {}
    if not isinstance(cfg, dict):
        return {}
    if mask_password and cfg.get("password"):
        cfg = dict(cfg)
        cfg["password"] = MASK
    return cfg


def save_smtp_config(cfg, by=None):
    """保存 SMTP 配置。cfg 为 dict（含 host/port/username/password/sender/use_tls/use_ssl/timeout/enabled）。
    若 password 为掩码占位符则保留库中现有值（避免回写把密码清空）。
    """
    if not isinstance(cfg, dict):
        raise ValueError("SMTP 配置必须是 dict")
    # 合并现有配置，避免掩码回写清空密码
    existing = get_smtp_config(mask_password=False) or {}
    merged = dict(existing)
    for k in SMTP_FIELDS:
        if k in cfg and cfg[k] is not None:
            merged[k] = cfg[k]
    if cfg.get("password") == MASK:
        # 前端未改动密码：沿用库中现有值
        merged["password"] = existing.get("password", "")
    # 类型与默认值规范化
    merged["host"] = (merged.get("host") or "").strip()
    merged["port"] = int(merged.get("port") or 0)
    merged["username"] = (merged.get("username") or "").strip()
    merged["password"] = merged.get("password") or ""
    merged["sender"] = (merged.get("sender") or "").strip()
    merged["use_tls"] = bool(int(merged.get("use_tls") or 0))
    merged["use_ssl"] = bool(int(merged.get("use_ssl") or 0))
    merged["timeout"] = int(merged.get("timeout") or 30)
    merged["enabled"] = bool(int(merged.get("enabled") or 0))
    set_system_config(SMTP_KEY, json.dumps(merged, ensure_ascii=False), by=by)
    return merged


def list_automation_runs(aid, limit=10):
    """查询某自动化的执行历史（倒序，最新在前）。"""
    conn = get_conn()
    rows = conn.execute(
        "SELECT id,automation_id,started_at,finished_at,status,result_text,error "
        "FROM automation_runs WHERE automation_id=? ORDER BY id DESC LIMIT ?",
        (aid, limit)).fetchall()
    conn.close()
    return [dict(r) for r in rows]


# =================== P2⑪ 跨对话历史检索 ===================
def save_conversation_message(session_id, user_id, role, content):
    """落库一条对话消息（用户/助手/系统），供跨会话检索。"""
    if not session_id or not content:
        return
    conn = get_conn()
    conn.execute(
        "INSERT INTO conversations (session_id,user_id,role,content,created_at) "
        "VALUES (?,?,?,?,?)",
        (session_id, user_id, role, content, now()))
    conn.commit(); conn.close()


def search_conversations(user_id, query, limit=10):
    """跨会话检索历史消息：多词 AND 匹配 + 按时间倒序。无外部依赖，LIKE 降级。"""
    if not query or not query.strip():
        return []
    terms = [t for t in query.replace("，", " ").split() if t]
    if not terms:
        return []
    conn = get_conn()
    like = " AND ".join(["content LIKE ?"] * len(terms))
    params = [f"%{t}%" for t in terms]
    sql = (f"SELECT id,session_id,user_id,role,content,created_at FROM conversations "
           f"WHERE user_id=? AND {like} ORDER BY id DESC LIMIT ?")
    rows = conn.execute(sql, [user_id] + params + [limit]).fetchall()
    conn.close()
    return [dict(r) for r in rows]


# ---------- P2⑪ 档B：会话管理 CRUD ----------

def ensure_session(user_id, session_id, first_user_msg=""):
    """首条消息时建立会话记录；若该用户已有该 session 则仅刷新 updated_at。
    **用户隔离**：查重按 (user_id, session_id) 而非仅 session_id——避免不同用户的
    session_id 撞车时（A 用户创建后 B 用户复用同 id）B 用户的更新影响 A 的记录。
    title 为空时取首条用户消息前 40 字；后续可用 update_session_title 重命名。"""
    if not user_id or not session_id:
        return
    conn = get_conn()
    cur = conn.execute(
        "SELECT id,title FROM conversation_sessions WHERE session_id=? AND user_id=?",
        (session_id, user_id)).fetchone()
    ts = now()
    if cur is None:
        title = (first_user_msg or "").strip()[:40] or "未命名对话"
        conn.execute(
            "INSERT INTO conversation_sessions (session_id,user_id,title,created_at,updated_at) "
            "VALUES (?,?,?,?,?)", (session_id, user_id, title, ts, ts))
    else:
        conn.execute(
            "UPDATE conversation_sessions SET updated_at=? WHERE session_id=? AND user_id=?",
            (ts, session_id, user_id))
    conn.commit(); conn.close()


def update_session_title(user_id, session_id, title):
    """重命名会话（用户在前端「历史对话」面板操作）。
    **用户隔离**：必须传 user_id，仅当该 session 归属当前用户时改成功；否则视作 not_found。
    返回 True/False（表示是否真的改了）。"""
    if not user_id or not session_id or not title:
        return False
    conn = get_conn()
    try:
        cur = conn.execute(
            "UPDATE conversation_sessions SET title=?,updated_at=? WHERE session_id=? AND user_id=?",
            (title.strip()[:80], now(), session_id, user_id))
        conn.commit()
        return cur.rowcount > 0
    finally:
        conn.close()


def list_sessions(user_id):
    """列出用户全部会话，含消息数与最后活动时间（按最近活跃倒序）。"""
    conn = get_conn()
    rows = conn.execute(
        """SELECT s.session_id, s.title, s.created_at, s.updated_at,
                  COUNT(c.id) AS msg_count,
                  MAX(c.created_at) AS last_at
           FROM conversation_sessions s
           LEFT JOIN conversations c ON c.session_id=s.session_id AND c.user_id=s.user_id
           WHERE s.user_id=?
           GROUP BY s.session_id
           ORDER BY COALESCE(MAX(c.created_at), s.updated_at) DESC""",
        (user_id,)).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def delete_session(user_id, session_id):
    """按所有权校验后级联删除会话及其全部消息（物理删除，对应「删窗口=清历史」）。"""
    if not session_id:
        return False
    conn = get_conn()
    cur = conn.execute(
        "SELECT id FROM conversation_sessions WHERE session_id=? AND user_id=?",
        (session_id, user_id)).fetchone()
    if cur is None:
        conn.close(); return False
    conn.execute("DELETE FROM conversations WHERE session_id=? AND user_id=?",
                 (session_id, user_id))
    conn.execute("DELETE FROM conversation_sessions WHERE session_id=? AND user_id=?",
                 (session_id, user_id))
    conn.commit(); conn.close()
    return True


def get_session_messages(user_id, session_id, limit=200):
    """续聊：取该会话历史消息（按时间正序），供前端恢复上下文。"""
    conn = get_conn()
    rows = conn.execute(
        "SELECT role,content,created_at FROM conversations "
        "WHERE session_id=? AND user_id=? ORDER BY id ASC LIMIT ?",
        (session_id, user_id, limit)).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def search_conversations_grouped(user_id, query, limit=10):
    """跨会话检索：命中消息 → 回溯 session_id → 按会话窗口聚合（标题/命中数/预览/时间）。"""
    if not query or not query.strip():
        return []
    terms = [t for t in query.replace("，", " ").split() if t]
    if not terms:
        return []
    conn = get_conn()
    like = " AND ".join(["c.content LIKE ?"] * len(terms))
    like2 = " AND ".join(["c2.content LIKE ?"] * len(terms))
    params = [f"%{t}%" for t in terms]
    sql = (f"SELECT c.session_id, s.title,"
           f" COUNT(*) AS hit_count,"
           f" MAX(c.created_at) AS last_hit_at,"
           f" (SELECT c2.content FROM conversations c2"
           f"   WHERE c2.session_id=c.session_id AND c2.user_id=c.user_id AND {like2}"
           f"   ORDER BY c2.id DESC LIMIT 1) AS preview"
           f" FROM conversations c"
           f" LEFT JOIN conversation_sessions s ON s.session_id=c.session_id"
           f" WHERE c.user_id=? AND {like}"
           f" GROUP BY c.session_id"
           f" ORDER BY last_hit_at DESC LIMIT ?")
    rows = conn.execute(sql, params + [user_id] + params + [limit]).fetchall()
    conn.close()
    return [dict(r) for r in rows]

