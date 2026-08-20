---
name: "tencent_pptx"
display_name: "专业 PPT 演示文稿生成"
description: "创建专业的 PowerPoint 演示文稿。适用于根据主题、大纲、文档、数据或参考材料生成完整 .pptx；在新建演示文稿时参考上传PPTX 的视觉风格；也可基于材料或旧 PPT 内容重新生成一版演示文稿。"
category: "file"
trigger_words: "ppt,pptx,演示文稿,幻灯片,PowerPoint,汇报材料,生成PPT,做PPT,创建演示"
skill_type: "method"
when_to_use: "根据主题、大纲、文档、数据或参考材料生成完整 .pptx 演示文稿；或基于旧 PPT 重新生成"
allowed_tools: ["generate_ppt"]
source_name: "tencent-pptx"
create_source: "builtin"
---

> ⚠️ **环境适配**：本技能原文面向 WorkBuddy Node 托管环境（slidep-start / slidep-validate 通过 plugin hook 安装）。当前系统使用内置工具 `generate_ppt` （python-pptx 渲染）完成 PPT 生成，请直接调用 `generate_ppt` 工具，不要寻找或调用 slidep 命令；正文中 references/*.md 子文件未导入，按本文方法论执行。

> 📎 **参考文件说明**：正文中引用的 references/、rules/、workflows/ 等子文件未随技能导入（系统仅存单 SKILL.md），请直接依据本文方法论执行，不要尝试读取不存在的参考文件。

# Tencent-PPTX Skill

## 快速导航
| 任务类型 | 执行路径 |
|---| --- | 
| 从零开始创建、生成PPT      | 阅读 [create-from-scratch](references/create-from-scratch.md) |
| 基于上传的参考材料创建、生成PPT | 阅读 [create-from-material](references/create-from-material.md) |

**核心原则：倘若基于选择的参考材料的生成PPT时，严格使用本SKILL的材料解析能力，避免寻求外部解析能力。**


## PPTX文件的编辑

**核心原则：PPTX 文件完全生成后，所有后续的内容修改/编辑通过阅读使用 `tencent-local-office-edit` SKILL完成。禁止回退到 JSX 源文件进行修改。**

## 需求澄清

1. **只问缺失的，文字提问 ≤ 3 个**，一轮问完，禁止多轮追问
2. **风格预览卡按需触发**：默认弹出，但当用户已明确风格偏好、要求快速出稿、或命中预设风格分支时可跳过（详见 human-alignment.md §2.3）

沟通术语统一用「页 / 封面 / 目录 / 这一页」，不暴露内部实现。

完整对齐逻辑与关键信息优先级见 [human-alignment.md](references/human-alignment.md)。


## 目录结构规范

每个 PPT 对应一个独立的 `project_dir`，多个 PPT 项目并存于用户的 `workspace_dir` 下：


命名约定：

- 项目目录名：与 `--filename` 去 `.pptx` 后缀一致（如 `--filename "季度汇报.pptx"` → 目录名 `季度汇报/`）。
- 页面文件：`{两位序号}.slide`，如 `01.slide`。
- 资源文件：禁用中文、空格、特殊字符。

## 环境介绍

slidep-start, slidep-validate 通过plugin hook被安装到 Node托管环境中，可以直接使用。