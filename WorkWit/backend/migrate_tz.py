"""一次性迁移脚本：把所有 *_at 时间戳字段 +8 小时（UTC → 中国时区）。

[背景]
修复时间戳错位 bug——容器默认时区是 UTC，db.now() 之前用 datetime.now() 得到 UTC
字符串，前端 index.html:2328 直接显示字符串而不做时区转换，导致历史对话/记忆/工具更新
时间比中国时区早 8 小时（截图实测：容器 UTC 07:05 显示成 07:04:58，中国实际 15:05）。
修复方案：(1) db.now() 已强制 +8 时区（新数据正确）  (2) 本脚本把历史 UTC 数据 +8h 修正。

[幂等]
用 PRAGMA user_version 防重复迁移——脚本运行后 user_version=1，再次运行直接跳过。

[安全]
  - 迁移前自动备份 DB → <db>.bak.<timestamp>
  - 所有 UPDATE 在单一事务中
  - 不修改 schema、不删数据

[用法]
  python migrate_tz.py                    # 迁移默认 DB (DB_PATH 环境变量或 /app/data/app.db)
  python migrate_tz.py /path/to/your.db   # 迁移指定 DB
"""
import os
import shutil
import sqlite3
import sys
from datetime import datetime, timezone, timedelta

_TZ_OFFSET = "+8 hours"
_DB_PATH = sys.argv[1] if len(sys.argv) > 1 else os.environ.get("DB_PATH", "/app/data/app.db")

# (表名, [字段列表])——按用户高频查看的时间排序
_TABLES = [
    ("conversation_sessions", ["created_at", "updated_at"]),  # 你截图看到的"最后活动"
    ("conversation_messages", ["created_at"]),                # 消息级时间戳
    ("users", ["created_at"]),
    ("sessions", ["created_at", "expires_at"]),               # auth token
    ("models", ["updated_at"]),
    ("skills", ["created_at", "updated_at"]),
    ("tools", ["created_at", "updated_at"]),
    ("logs", ["created_at"]),
    ("automations", ["created_at", "updated_at", "next_run"]),
    ("user_profiles", ["updated_at"]),
    ("user_memory", ["updated_at"]),
]


def _table_exists(conn, table):
    cur = conn.execute("SELECT name FROM sqlite_master WHERE type='table' AND name=?", (table,))
    return cur.fetchone() is not None


def _backup_db(db_path):
    if not os.path.exists(db_path):
        return None
    ts = datetime.now(timezone(timedelta(hours=8))).strftime("%Y%m%d_%H%M%S")
    bak = f"{db_path}.bak.{ts}"
    shutil.copy2(db_path, bak)
    return bak


def migrate(db_path=_DB_PATH):
    if not os.path.exists(db_path):
        print(f"DB not found: {db_path}")
        return False
    bak = _backup_db(db_path)
    if bak:
        print(f"备份: {bak}")
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    try:
        cur = conn.execute("PRAGMA user_version")
        user_version = cur.fetchone()[0]
        if user_version >= 1:
            print(f"已迁移过（user_version={user_version}），跳过")
            return False
        total = 0
        for tbl, fields in _TABLES:
            if not _table_exists(conn, tbl):
                print(f"  跳过 {tbl}（表不存在）")
                continue
            sets = ", ".join(f"{f}=datetime({f}, '{_TZ_OFFSET}')" for f in fields)
            sql = f"UPDATE {tbl} SET {sets}"
            affected = conn.execute(sql).rowcount
            total += affected
            print(f"  {tbl} ({', '.join(fields)}): {affected} 行")
        conn.execute("PRAGMA user_version = 1")
        conn.commit()
        print(f"\n迁移完成：共 {total} 行时间戳 +8 小时；user_version=1 防重入")
        return True
    except Exception as e:
        conn.rollback()
        print(f"迁移失败：{e}（已回滚；备份在 {bak}）")
        return False
    finally:
        conn.close()


if __name__ == "__main__":
    migrate()