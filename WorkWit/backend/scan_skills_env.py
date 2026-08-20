# -*- coding: utf-8 -*-
"""全量扫描 46 个 SKILL.md，找出与当前系统不符的引用：
① 环境依赖（Node/slidep/npm/npx/CLI/plugin hook/editor_sdk/Bash 命令执行）
② WorkBuddy 专有工具/能力名（Read/Write/Edit/Glob/Grep/Bash/PowerShell/WebFetch/WebSearch/
   Task*/Agent/AskUserQuestion/Skill/ToolSearch/DeferExecuteTool/ImageGen/VideoGen/
   agentic_search/spawn_subagent/doc-writer 子代理等）
③ 外部平台名（CloudStudio/微信支付/Ardot 画布/腾讯文档在线/专家市场/连接器市场）
④ MCP 工具引用（mcp__* / "MCP" 字样）
⑤ 参考文件引用（references/xxx.md —— 我们只有单 SKILL.md，没有子文件）
输出每个技能命中的关键词清单。
"""
import json
import os
import re
import sys

BASE = os.path.dirname(os.path.abspath(__file__))
SKILLS_DIR = os.path.join(BASE, "builtin_skills")

# ① 环境依赖类（宿主命令/运行时）
ENV_KW = [
    "slidep", "Node托管", "node ", "npm ", "npx ", "CLI", "命令行", "终端执行",
    "plugin hook", "editor_sdk", "exec(", "subprocess", "shell", "Bash", "PowerShell",
    "python -m", "pip install", "npm install", "yarn ",
]
# ② WorkBuddy 宿主工具/能力名（非我们系统的工具名）
WB_TOOLS = [
    "Read", "Write", "Edit", "Glob", "Grep", "TaskCreate", "TaskGet", "TaskUpdate",
    "TaskList", "TaskOutput", "automation_update", "AskUserQuestion", "conversation_search",
    "ToolSearch", "DeferExecuteTool", "present_files", "show_widget", "read_me",
    "ImageGen", "VideoGen", "agentic_search", "spawn_subagent", "SendMessage",
    "doc-writer", "doc-formatter", "doc-converter", "sheet-agent", "orchestrator",
    "MCP", "mcp__",
]
# ③ 外部平台
PLATFORMS = [
    "CloudStudio", "cloudstudio", "Ardot", "ardot", "微信支付", "weixinpay", "Weixin",
    "腾讯文档", "docs.qq.com", "saas.docs.qq.com", "专家市场", "连接器市场", "BuiltinMarket",
    "腾讯地图", "地图API", "Tencent Maps", "AMap", "Gaode", "Tianditu",
]
# ④ 参考文件
REF_KW = ["references/", "SKILL.md", "skill.md", "rules/", "workflows/"]


def scan():
    results = {}
    for fn in sorted(os.listdir(SKILLS_DIR)):
        d = os.path.join(SKILLS_DIR, fn)
        f = os.path.join(d, "SKILL.md")
        if not os.path.isdir(d) or not os.path.isfile(f):
            continue
        with open(f, "r", encoding="utf-8") as fh:
            text = fh.read()
        hits = {}
        for kw in ENV_KW:
            # 单词边界更宽松
            n = len(re.findall(re.escape(kw), text, re.IGNORECASE))
            if n:
                hits.setdefault("环境依赖", []).append((kw, n))
        for kw in WB_TOOLS:
            n = len(re.findall(r"\b" + re.escape(kw) + r"\b", text))
            if n:
                hits.setdefault("WB宿主工具", []).append((kw, n))
        for kw in PLATFORMS:
            n = len(re.findall(re.escape(kw), text))
            if n:
                hits.setdefault("外部平台", []).append((kw, n))
        for kw in REF_KW:
            n = len(re.findall(re.escape(kw), text))
            if n:
                hits.setdefault("参考文件", []).append((kw, n))
        if hits:
            results[fn] = hits
    return results


def main():
    results = scan()
    print("=== 命中系统外引用的技能 (%d 个) ===" % len(results))
    for fn, hits in results.items():
        print(f"\n## {fn}")
        for cat, kws in hits.items():
            detail = ", ".join(f"{k}×{n}" for k, n in kws[:12])
            print(f"  [{cat}] {detail}")
    with open(os.path.join(BASE, "_skills_scan_report.json"), "w", encoding="utf-8") as f:
        json.dump(results, f, ensure_ascii=False, indent=1)
    print("\n已保存 _skills_scan_report.json")


if __name__ == "__main__":
    main()
