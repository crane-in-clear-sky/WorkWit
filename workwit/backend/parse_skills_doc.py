# -*- coding: utf-8 -*-
"""解析 workbuddy_builtin_skills_full.md，提取每个技能的 frontmatter + 正文。
输出 JSON 供导入脚本使用。"""
import json
import re
import sys

DOC = r"D:\WorkBuddy\企业AI智能体助手\workbuddy_builtin_skills_full.md"

def parse():
    with open(DOC, "r", encoding="utf-8") as f:
        lines = f.readlines()
    skills = []
    cur = None  # {name, file_hint, fm_lines, body_lines, started}
    i = 0
    n = len(lines)
    while i < n:
        line = lines[i]
        # 技能标题：#### `name`  —  包/路径
        m = re.match(r"^####\s*`([^`]+)`\s*(?:—|-)?\s*(.*)$", line)
        if m:
            # 结束上一个
            if cur and (cur["fm_lines"] or cur["body_lines"]):
                skills.append(cur)
            cur = {
                "name": m.group(1).strip(),
                "file_hint": m.group(2).strip(),
                "fm_lines": [],
                "body_lines": [],
                "in_body": False,
                "fm_done": False,
            }
            i += 1
            continue
        if cur is None:
            i += 1
            continue
        # 正文代码块开始：````markdown / ```markdown（三或四个反引号）
        if not cur["in_body"] and re.match(r"^```+", line.strip()):
            cur["in_body"] = True
            i += 1
            continue
        if cur["in_body"]:
            if re.match(r"^```+", line.strip()):
                # 围栏结束
                cur["in_body"] = False
                i += 1
                continue
            # frontmatter：--- 结束前为 YAML
            if not cur["fm_done"]:
                if line.strip() == "---" and not cur["fm_lines"]:
                    cur["fm_lines"].append(line)
                elif line.strip() == "---" and cur["fm_lines"]:
                    cur["fm_lines"].append(line)
                    cur["fm_done"] = True
                else:
                    cur["fm_lines"].append(line)
            else:
                cur["body_lines"].append(line)
        i += 1
    if cur and (cur["fm_lines"] or cur["body_lines"]):
        skills.append(cur)
    return skills


def parse_frontmatter(fm_lines):
    """简单 YAML frontmatter 解析（name/description/version 等标量 + 多行 > 折叠）。"""
    txt = "".join(fm_lines)
    txt = re.sub(r"^---\s*$", "", txt, flags=re.M)
    out = {}
    cur_key = None
    cur_val = []
    for raw in txt.splitlines():
        line = raw.rstrip()
        if not line.strip():
            if cur_key:
                out[cur_key] = "\n".join(cur_val).strip()
                cur_key = None
                cur_val = []
            continue
        m = re.match(r"^([A-Za-z_][A-Za-z0-9_-]*)\s*:\s*(.*)$", line)
        if m and not line.startswith(" "):
            if cur_key:
                out[cur_key] = "\n".join(cur_val).strip()
            cur_key = m.group(1)
            cur_val = [m.group(2)]
        else:
            if cur_key:
                cur_val.append(line.lstrip())
    if cur_key:
        out[cur_key] = "\n".join(cur_val).strip()
    return out


def main():
    skills = parse()
    print("共解析到技能数:", len(skills))
    for s in skills:
        fm = parse_frontmatter(s["fm_lines"])
        body = "".join(s["body_lines"]).strip()
        s["_fm"] = fm
        s["_body_len"] = len(body)
        s["_body_head"] = body[:120].replace("\n", " ")
        print(f"- {s['name']:45s} fm={sorted(fm.keys())} body={s['_body_len']}字符")
    with open(r"D:\WorkBuddy\企业AI智能体助手\ai-office-mvp\backend\_skills_parsed.json", "w", encoding="utf-8") as f:
        json.dump(skills, f, ensure_ascii=False, indent=1)
    print("\n已保存 _skills_parsed.json")


if __name__ == "__main__":
    main()
