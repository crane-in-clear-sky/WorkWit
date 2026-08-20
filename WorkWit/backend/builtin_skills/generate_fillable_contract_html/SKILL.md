---
name: "generate_fillable_contract_html"
display_name: "待填合同模板生成"
description: "生成适于 HTML 转 DOCX 的中文待填合同、报价单和授权委托书 HTML。用户要求创建待填业务文档模板、合同填空或可按书签填写的 HTML 时使用。"
category: "file"
trigger_words: "待填合同,合同模板,报价单,授权委托书,填空合同,合同填空"
skill_type: "method"
when_to_use: "用户要求创建待填业务文档模板、合同填空或可按书签填写的文档时"
allowed_tools: ["generate_word"]
source_name: "generate-fillable-contract-html"
create_source: "builtin"
---

# 待填合同 HTML 生成

生成完整、真实的中文业务 HTML，适合交给 `html-to-docx` 转换。

## 待填字段规则

硬性要求：

- 表格里面不要用横线当书签，用空格当书签。
- 书签必须换成中文名。

每个字段保留稳定的英文 `data-docx-field`，并使用 `data-docx-bookmark` 指定唯一中文书签名。重复业务字段在中文名后添加 `_01`、`_02`。


表格字段使用 `&nbsp;` 提供可见空白书签范围，不使用下划线：


不得使用正文普通空格、只有冒号的空值、`请输入`/`待填写` 提示文案、英文书签名或表格下划线书签。

## 输出要求

- 输出完整 HTML5 文档，包含 `html`、`head`、`style` 与 `body`。

## 自检

- 每个字段同时包含英文 `data-docx-field` 和唯一中文 `data-docx-bookmark`。
- 正文书签范围使用连续下划线；表格书签范围只使用 `&nbsp;`。
- 中文书签名无空格；重复字段使用 `_01`、`_02` 区分。
- 不含提示性占位文案、正文普通空格填空或表格下划线书签。