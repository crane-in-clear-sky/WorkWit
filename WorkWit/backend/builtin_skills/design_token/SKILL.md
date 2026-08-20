---
name: "design_token"
display_name: "文档设计令牌"
description: "|\n按文档类型（genre）选择主题并输出标准化设计令牌（Design Tokens），驱动 doc-typeset skill 的所有样式决策。\n支持公文、学术论文、商务报告、创意营销、通用五大场景，输出含 typography/color/spacing/layout 的 W3C DTCG 格式 JSON 和 CSS 变量映射。\n当 generate_word 流水线需要确定排版风格、需要字体/颜色/间距/页边距决策时，必须调用此 skill；\n不要跳过它直接写裸值——所有样式决策的唯一入口就是这里。"
category: "image"
trigger_words: "design token,设计令牌,文档主题,排版样式,风格决策,typography,配色"
skill_type: "method"
when_to_use: "按文档类型选择主题并输出标准化设计令牌，驱动排版样式决策"
allowed_tools: []
source_name: "design-token"
create_source: "builtin"
---

> ⚠️ **环境适配**：本技能原输出设计令牌驱动 HTML 排版，当前系统排版由 `generate_word` 完成，正文中的字体/配色规范可作方法论参考。

> 📎 **参考文件说明**：正文中引用的 references/、rules/、workflows/ 等子文件未随技能导入（系统仅存单 SKILL.md），请直接依据本文方法论执行，不要尝试读取不存在的参考文件。

# design-token Skill

## 1. 职责

根据文档类型（genre）选择合适的设计令牌主题和版式规则，输出标准化的 DesignTokenOutput，供 doc-typeset skill 消费。版式规则文件作为补充上下文，描述该 genre 对应的国标排版规范（如 GB/T 9704）。

## 2. 输入/输出

### 输入

### 输出


## 3. Genre → 主题映射

| genre             | 说明          | theme_file                              | 预编译产物（直接查表）                            | rules_file                                        |
| ----------------- | ----------- | --------------------------------------- | -------------------------------------- | ------------------------------------------------- |
| `government-doc`  | 党政机关公文      | `tokens/themes/formal-government.json`  | `tokens/compiled/government-doc.json`  | `tokens/rules/gb-t-9704-government.md`            |
| `academic-paper`  | 学术论文 / 学位论文 | `tokens/themes/academic-paper.json`     | `tokens/compiled/academic-paper.json`  | `tokens/rules/gb-t-7713-academic.md`（含 7714 引用规则） |
| `business-report` | 商务报告 / 分析报告 | `tokens/themes/business-modern.json`    | `tokens/compiled/business-report.json` | —                                                 |
| `marketing-doc`   | 创意营销 / 活动方案 | `tokens/themes/creative-marketing.json` | `tokens/compiled/marketing-doc.json`   | —                                                 |
| `general`（默认）     | 其他/未明确场景    | `tokens/themes/modern-minimal.json`     | `tokens/compiled/general.json`         | —                                                 |

当 genre 不在上表时，使用 `general` 兜底策略。

## 4. 执行方式（预编译查表，0 次 LLM 往返）

所有主题的 `DesignTokenOutput`（含 `theme_name` / `tokens` / `css_variables` / `typography_rules`）已由 `scripts/build_tokens.py` **预编译**为静态产物，存放在 `tokens/compiled/`。运行时本 skill **只做静态查表**：

1. 按 genre 从 `tokens/compiled/index.json` 定位对应产物文件（未命中 → `general`）；
2. **直接读取该 compiled JSON 作为 `DesignTokenOutput` 返回**，无需任何转换或 LLM 推理。

> 主题 JSON 的字面值已内含精确规范（如 GB/T 9704 页边距、行距磅数），`typography_rules` 仅作可选溯源上下文，运行时**无需读取**。

**CSS 变量命名 = token 路径扁平化**（与 doc-typeset 模板的 token 注入层一致，语义变量 `--fs-*`/`--ff-*` 由模板通过 `var(--typography-*, fallback)` 消费）：


> **维护**：修改 `tokens/themes/*.json` 后，须重跑 `python3 scripts/build_tokens.py` 重建 `tokens/compiled/`。

## 5. 文件结构