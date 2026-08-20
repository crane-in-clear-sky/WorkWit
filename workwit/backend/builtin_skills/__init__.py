"""内置技能加载器（文件化形态，2026-08-18 落地）。

每个技能一个目录 backend/builtin_skills/<name>/SKILL.md：
- YAML frontmatter：name/display_name/description/category/trigger_words/
  skill_type/when_to_use/allowed_tools/source_name/create_source
- 正文：方法论全文（method 型，不执行代码）

扫描在 import 时执行，用 try/except 守护：单文件解析失败不影响整体启动，
便于「丢一个目录即新增一个内置技能」且隔离故障。

导出的对象：
    BUILTIN_SKILLS —— 元数据列表（含 seed 用 instructions 精简版），供 db.init_skills 写入 skills 表
"""
import json
import logging
import os
import re
import traceback

logger = logging.getLogger("builtin_skills")

BUILTIN_SKILLS = []


def _parse_frontmatter(text):
    """解析 SKILL.md 的 YAML 前端（仅支持 JSON 风格键值 + JSON 数组，由 convert_skills.py 保证格式）。"""
    out = {}
    m = re.match(r"^---\s*\n(.*?)\n---\s*\n?", text, re.S)
    if not m:
        return out
    for line in m.group(1).splitlines():
        line = line.rstrip()
        if not line or line.startswith("#") or ":" not in line:
            continue
        k, _, v = line.partition(":")
        k = k.strip()
        v = v.strip()
        if not k:
            continue
        if v.startswith("[") or v.startswith('"') or v.startswith("{"):
            try:
                out[k] = json.loads(v)
            except Exception:
                out[k] = v.strip('"')
        else:
            out[k] = v.strip('"')
    return out


def _build_instructions(desc, body, name, display_name):
    """生成 seed 用 instructions（精简版，不把全文注入 system prompt）：
    description 摘要 + 正文前 3000 字符核心方法论 + 完整版位置。"""
    parts = []
    if desc:
        parts.append("【技能说明】\n" + desc)
    body = (body or "").strip()
    if body:
        core = body[:3000]
        parts.append("【方法论正文（核心）】\n" + core)
        if len(body) > 3000:
            parts.append("（完整方法论全文见系统内置技能文件 builtin_skills/%s/SKILL.md）" % name)
    return "\n\n".join(parts) or ("# " + (display_name or name))


def _scan():
    here = os.path.dirname(os.path.abspath(__file__))
    if not os.path.isdir(here):
        return
    for fn in sorted(os.listdir(here)):
        skill_dir = os.path.join(here, fn)
        skill_file = os.path.join(skill_dir, "SKILL.md")
        if not os.path.isdir(skill_dir) or not os.path.isfile(skill_file):
            continue
        try:
            with open(skill_file, "r", encoding="utf-8") as f:
                text = f.read()
            fm = _parse_frontmatter(text)
            name = fm.get("name")
            if not name or not re.match(r"^[A-Za-z_][A-Za-z0-9_]*$", name):
                logger.warning("内置技能 %s 的 name 非法，已跳过", fn)
                continue
            body = re.sub(r"^---\s*\n.*?\n---\s*\n?", "", text, flags=re.S).strip()
            desc = fm.get("description") or ""
            display_name = fm.get("display_name") or name
            meta = {
                "name": name,
                "display_name": display_name,
                "description": desc if isinstance(desc, str) else str(desc),
                "category": fm.get("category") or "general",
                "trigger_words": fm.get("trigger_words") or "",
                "skill_type": "method",
                "when_to_use": fm.get("when_to_use") or "",
                "allowed_tools": fm.get("allowed_tools") or [],
                "source_name": fm.get("source_name") or name,
                "create_source": "builtin",
                "instructions": _build_instructions(desc, body, name, display_name),
            }
            if not isinstance(meta["allowed_tools"], list):
                meta["allowed_tools"] = []
            BUILTIN_SKILLS.append(meta)
            logger.info("已注册内置技能: %s", name)
        except Exception as e:
            logger.error("内置技能加载失败，已跳过 %s: %s\n%s", fn, e, traceback.format_exc())


_scan()
