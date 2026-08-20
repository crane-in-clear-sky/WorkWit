---
name: "underline_toolkit"
display_name: "下划线填空文档工具"
description: "生成带下划线填空位的 Word 文档（create 模式）或对已有下划线模板回填数据（fill 模式）。适用于合同、协议、申请表、有封面的文档（如毕业论文）等。触发关键词：下划线、填空、下划线模板、合同填空、表单回填、下划线回填。"
category: "file"
trigger_words: "下划线,填空,下划线模板,合同填空,表单回填,论文封面"
skill_type: "method"
when_to_use: "生成带下划线填空位的 Word 文档（create 模式）或对已有下划线模板回填数据（fill 模式）"
allowed_tools: ["generate_word"]
source_name: "underline-toolkit"
create_source: "builtin"
---

> 📎 **参考文件说明**：正文中引用的 references/、rules/、workflows/ 等子文件未随技能导入（系统仅存单 SKILL.md），请直接依据本文方法论执行，不要尝试读取不存在的参考文件。

<goal>
为 Expert 的 docx 生成阶段提供**下划线填空**的统一能力。所有 API 实现在 `src/skills/underline-toolkit/toolkit.py`。
</goal>

<workflow>

### 1. 判断模式

| 模式 | 触发条件 | 详细规则 |
|------|----------|----------|
| **create** | 需要生成**带空白下划线填空位**的新文档 | → 查阅 `src/skills/underline-toolkit/references/create-rules.md` |
| **fill** | 需要对已有下划线模板**回填数据** | → 查阅 `src/skills/underline-toolkit/references/fill-rules.md` |

### 2. 在生成脚本中 import


### 3. 按对应 references 文件执行

- **create 模式**：`add_underline_run()` 不传 `value`（或 `value=""`）→ 空白下划线
- **fill 模式**：`add_underline_run(value="实际内容")` → 内容 + 下划线保留

两种模式使用完全相同的函数，唯一区别是 `value` 参数是否传值。

</workflow>

<restrictions>
  
\- ❌ 永远不要使用 \`\_\_\_\_\_\_\_\_\`（连续下划线字符）制作填空下划线
  
\- ❌ 不得跳过 \`rFonts.eastAsia\` 设置
  
\- ❌ 不得硬编码绝对路径
  
</restrictions>
  
\`\`\`

---

### 📦 包：`tencent-pptx`