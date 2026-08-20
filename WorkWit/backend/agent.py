"""自主智能体核心：通用 ReAct（规划-执行）循环。

设计目标：
- 与具体业务解耦：本模块只负责「调模型 → 解析 tool_calls → 执行 handler → 回灌结果 → 继续」的循环；
- 业务方（app.py）通过注册工具（name/description/parameters/handler）把现有能力暴露给智能体；
- 以 async generator 形式逐个 yield 事件（plan/call/result/final/error），便于 SSE 流式推给前端。

工具 handler 既可以是普通函数，也可以是 async 函数；LLM 类工具应在 handler 内部用
asyncio.to_thread 把阻塞的模型调用放到线程，避免卡住事件循环。
"""
import asyncio
import json
import re
import os
import importlib as _importlib
from llm_adapter import ModelCaps, build_text_tool_section, _parse_text_tool_call

# 产物文件扩展名（与 core._FILE_EXTS 保持一致，避免遗漏）
_PROD_EXTS = (".pptx", ".docx", ".pdf", ".png", ".jpg", ".jpeg", ".gif", ".mp4",
              ".mp3", ".wav", ".txt", ".xlsx", ".xls", ".csv", ".json", ".html", ".md")


def _extract_generated_files(text):
    """从工具返回文本提取「磁盘上真实存在」的产物绝对路径（去重，按出现顺序）。

    双路：① 中文前缀（已生成/已保存/...[:：]<path>，且允许关键字与冒号间有干扰字符，
    兼容「PPT 已生成（商务蓝风格，共 11 页）：/path/x.pptx」）；
    ② 通用扫描：按空白与常见分隔符切词，找「绝对路径 + 已知扩展名 + 磁盘文件存在」。
    仅返回真实存在的文件，避免把 URL / 临时文本当产物。

    这是闭环评估器「artifacts 事件」的数据来源——若抓不到路径，评估器会误判
    「未实际生成文件」而反复重跑工具，导致重复生成多个文件。
    """
    if not isinstance(text, str) or not text.strip():
        return []
    found, seen = [], set()

    def _accept(p):
        p = p.strip().strip("`*\"'<>|()[]{}。，、,;：:.").rstrip(".,;:)")
        if not p or p.lower().startswith(("http://", "https://")):
            return None
        if not p.lower().endswith(_PROD_EXTS):
            return None
        if os.path.isabs(p) and os.path.isfile(p) and p not in seen:
            seen.add(p)
            return p
        return None

    for m in re.finditer(
            r"(?:已生成|已保存|保存至|保存到|存放于|输出到|输出至|路径)[^：:]*[:：]\s*(\S+)", text):
        p = _accept(m.group(1))
        if p:
            found.append(p)
    for tok in re.split(r"[\s，。；、,;：]+", text):
        p = _accept(tok)
        if p:
            found.append(p)
    return found


# 方案C（2026-08-18）：防幻觉提示——模型声称「已生成文件」但实际未调用任何生成工具。
# 这些措辞只出现在「最终答复」文本里，若此时 _generated_files 为空，说明模型
# 在无工具能力（或没正确输出 tool_call）的情况下"假装完成"，需向用户明示。
_HALLUCINATED_CLAIM_RE = re.compile(
    r"(?:已生成|已保存|已导出|生成成功|创建成功|保存成功|点击下载|下载链接|文件已|文档已|PPT已|Word已|"
    r"已为您生成|已为你生成|文件路径|下载地址|生成完毕|已完成)[^。\n]{0,25}?"
    r"(?:docx|pptx|xlsx|pdf|png|jpg|mp4|word|ppt|excel|文件|文档|报告|图表|表格|图片|视频|PPT|Word)"
)

# 明确表示"我无法/未实际生成"的良性措辞（命中则不警告）
_BENIGN_CLAIM_RE = re.compile(
    r"(?:未能|无法|不能|没有|未成功|失败|暂未|无法生成|未生成|建议|请自行|需要你)"
)


def _detect_fake_file_claim(text):
    """检测 final 文本是否「声称生成了文件」，用于与真实 artifacts 对账。

    返回 True 表示文本里有疑似"已生成 xxx.docx/pptx"的措辞（且需进一步核对
    artifacts 是否为空）。良性措辞（未能/无法/建议）直接放行。
    """
    if not text:
        return False
    if _BENIGN_CLAIM_RE.search(text):
        return False
    return bool(_HALLUCINATED_CLAIM_RE.search(text))


def _guard_fake_file_claim(final_text, generated_files):
    """防幻觉守卫：模型声称已生成文件但实际无产物 → 追加警告提示。

    返回 (text, warned)。有真实产物或无可疑声称时原样返回。"""
    if not final_text:
        return final_text, False
    if generated_files:
        return final_text, False
    if not _detect_fake_file_claim(final_text):
        return final_text, False
    warn = ("\n\n"
            "> ⚠️ **提示**：检测到你提到了「已生成/下载文件」，但本次任务实际**没有调用任何"
            "生成工具**，上面并没有可下载的真实文件。这可能是当前模型未能正确调用工具所致。"
            "如需真正的 Word/PPT/Excel/图片文件，请换用支持工具调用的模型，或把上方内容"
            "手动复制保存。")
    return final_text + warn, True


def _to_openai_tools(tools):
    """把内部工具描述转换为 OpenAI function-calling 格式。"""
    out = []
    for t in tools:
        out.append({
            "type": "function",
            "function": {
                "name": t["name"],
                "description": t["description"],
                "parameters": t.get("parameters", {"type": "object", "properties": {}}),
            },
        })
    return out


# P2⑮: 评估器/工具入口用的"文件类交付物请求"识别。
# 当用户需求含以下触发词时，artifacts 列表非空 = 视为交付物已满足，跳过 LLM 评估/自动数据准备。
# 关键词兼顾"明确生成"（做/生成/导出 PPT）和"对历史做转换"（整理/汇总/总结成文档）两类。
_FILE_DELIVERY_KEYWORDS = (
    # 显式生成类
    "ppt", "pptx", "powerpoint", "演示文稿", "幻灯片",
    "word", "docx", "word 文档",
    "excel", "xlsx", "表格", "电子表格",
    "pdf", "导出 pdf",
    "图片", "海报", "插图", "生图", "生成图", "画一张", "画一版",
    "视频", "生成视频", "拍一版",
    "做一份", "做一版", "做一款", "做一页", "出一份", "出一版", "制作", "生成一份",
    # 对历史做转换类（隐含文件交付物）
    "整理成", "整理为", "汇成", "归纳为", "输出为", "写成", "导成",
    "整理一份", "总结成", "总结为", "汇总成", "汇总为",
)


def _is_file_delivery_request(question):
    """判断用户需求是否要求生成"文件类交付物"（PPT/Word/图片/视频等）。

    严格只匹配含明确文件类型/动作的关键词，避免把"对话生成总结"误判为文件交付物。
    与 _evaluate_result 评估器短路和 generate_pptx 入口自动数据准备配套使用。
    """
    if not question or not isinstance(question, str):
        return False
    q = question.strip()
    if not q:
        return False
    ql = q.lower()
    return any(k in ql for k in _FILE_DELIVERY_KEYWORDS)


async def run_agent(client, model_name, messages, tools, params=None, max_steps=8,
                    on_tool_call=None, cancel_check=None, ctx=None, caps=None):
    """异步生成器：逐个产出事件字典。

    client     : 已初始化的 OpenAI 兼容客户端
    model_name : 模型名
    messages   : 初始对话（含 system + user/assistant 历史）
    tools      : 工具列表 [{name, description, parameters, handler}]
    params     : 推理参数（temperature 等）
    max_steps  : 最大规划-执行轮数，防止失控/死循环
    on_tool_call: 可选回调，工具真正执行时以 tool 名为参数调用（用于统计调用次数）
    ctx        : ToolContext（可选），用于 create_tool 成功后动态注入新工具到候选池
    caps       : ModelCaps（P4·多模型兼容），声明模型是否支持原生 function calling 等；
                 为 None 时默认按「支持工具调用」处理，向后兼容。
    """
    if caps is None:
        caps = ModelCaps({"supports_tools": 1})
    supports_tools = caps.supports_tools()
    params = dict(params or {})
    tool_map = {t["name"]: t for t in tools}
    openai_tools = _to_openai_tools(tools)
    conv = [dict(m) for m in messages]
    # 文本模式（不支持原生 function calling）：每轮用「当前 tools」重建工具清单注入 system prompt
    text_mode = not supports_tools
    base_system = (conv[0]["content"]
                   if (text_mode and conv and conv[0].get("role") == "system") else None)

    # 重复调用防护
    _last_create_failed = False   # 上一步是否为创建类工具且执行失败
    _create_result = None         # 本步 create_tool/create_skill 的自身返回值（独立缓存，不受其他工具污染）
    _META_CREATE_TOOLS = {"create_tool", "create_skill"}
    _META_LIST_TOOLS = {"list_available_tools", "list_available_skills"}
    # 全局调用计数：防止 LLM 在元工具上空转耗尽步数
    _meta_call_count = {}         # tool_name -> 调用次数
    _META_CALL_LIMIT = 2         # 单个元工具最多允许调用 2 次（收紧：3→2）
    _create_success_seen = False  # 是否已见过 create_tool 成功——成功后禁止再创建
    # P1⑦ 显式反思/重规划：工具失败→强制反思改道。反映次数封顶，避免无限反思空转。
    _reflection_count = 0
    _MAX_REFLECTIONS = 2
    _generated_files = []   # 本轮对话已生成文件绝对路径，供 run_temp_code 对已生成图片做后处理
    _last_injected_count = 0   # 上次注入产物提醒时的 _generated_files 长度（去重注入用）

    for step in range(1, max_steps + 1):
        _step_tool_fail_hint = False   # Step7-clause2：本步内「工具执行失败→提示[PROPOSE_TOOL]」只注入一次
        # 任务中止检查：前端点击「停止」后，在下一决策点（轮次起点）退出
        if cancel_check and cancel_check():
            yield {"type": "aborted"}
            return

        # 2026-08-19 修复「run_temp_code 反复生成同一主题文件」bug：
        # 之前 _generated_files 仅在 LLM 调用 run_temp_code 时作为 artifacts 参数注入，
        # 是被动机制——LLM 经常忽略 artifacts 自己重写新代码、新沙箱会话里产出新文件
        # （hash 前缀不同）。现在改为在每次 LLM 决策前把已有产物注入对话上下文，
        # 强烈鼓励 LLM 复用已有路径（open()+修改+写回同路径），仅在新任务时才创建新文件。
        # 去重：仅在 _generated_files 增长后注入一次，避免每轮重复同样消息。
        if _generated_files and len(_generated_files) > _last_injected_count:
            _existing = _generated_files[-3:] if len(_generated_files) > 3 else _generated_files
            _total = len(_generated_files)
            conv.append({"role": "user", "content":
                f"[系统·本对话已生成 {_total} 个文件]{(' 最近：' if _total <= 3 else '（仅展示最近3个）：')}\n"
                + "\n".join(f"- {p}" for p in _existing)
                + ("\n\n如本任务与上述任一文件相关（修改、补充、覆盖），请优先 open() 读取后修改并写回同一路径；"
                   "只有完全独立的新任务才应创建新文件（不要重新生成同名/同主题文件）。"
                   if _total <= 5 else
                   "\n\n如本任务与上述任一文件相关（修改、补充、覆盖），请优先 open() 读取后修改并写回同一路径；"
                   "只有完全独立的新任务才应创建新文件（注意：本对话产物已较多，禁止再次生成同名/同主题文件）。")})
            _last_injected_count = len(_generated_files)

        # 文本模式：每轮用「当前 tools（可能已动态新增）」重建工具清单注入 system prompt
        if text_mode:
            conv[0] = {"role": "system",
                       "content": (base_system or "") + "\n\n" + build_text_tool_section(tools)}

        # 1) 调用模型，让它决定是直接回答还是调用工具
        #    用 wait_for 包一层超时保护：由 caps.call_timeout() 驱动（可逐模型配置）。
        #    函数模式传 tools/tool_choice；文本模式不传（靠 system prompt 里的清单约束）。
        try:
            call_kwargs = dict(model=model_name, messages=conv, **params)
            if supports_tools:
                call_kwargs["tools"] = openai_tools
                call_kwargs["tool_choice"] = "auto"
            resp = await asyncio.wait_for(
                asyncio.to_thread(client.chat.completions.create, **call_kwargs),
                timeout=caps.call_timeout(),
            )
        except asyncio.TimeoutError:
            # 区分超时与真实连接错误：超时通常是模型慢/中转限流，给用户可操作的提示
            yield {"type": "error", "message":
                f"调用模型 {model_name} 超时（>{caps.call_timeout()}s）。"
                f"可能原因：① 该模型生成速度慢（大模型生成长文本耗时高）；"
                f"② 中转 API 限流/排队（429/503）；③ 该模型单次生成耗时本就较长。"
                f"建议：在「系统管理 → 模型配置」调大该模型的超时(秒)，或换更快的模型。"}
            return
        except Exception as e:
            yield {"type": "error", "message": f"{type(e).__name__}: {e}"}
            return

        msg = resp.choices[0].message

        # 2) 解析本轮要执行的工具调用：函数模式取 tool_calls；文本模式解析回复块。
        #    统一归一化为 [(name, args, call_id)]，后续执行逻辑两模式共用。
        if supports_tools:
            normalized = []
            for tc in (msg.tool_calls or []):
                fn = getattr(tc, "function", None)
                if not fn or not getattr(fn, "name", None):
                    continue
                try:
                    _a = json.loads(fn.arguments or "{}")
                except Exception:
                    _a = {}
                normalized.append((fn.name, _a, tc.id))
            final_text = msg.content or ""
        else:
            _tname, _targs, _cleaned = _parse_text_tool_call(msg.content or "")
            normalized = [(_tname, (_targs or {}), None)] if _tname else []
            final_text = _cleaned

        # 无任何工具调用 → 视为最终回答
        if not normalized:
            # P2⑮ 根因修复：早返回前必须 yield artifacts 事件，让 run_agent_with_retry 的
            # 短路逻辑能看到本轮已生成的文件。**之前只在 max_steps 耗尽路径 yield artifacts
            # （第 484 行），导致正常 final 早返回路径永远拿不到 artifacts → 评估器只能看
            # final_text 自然语言总结，LLM 即使生成了 PPT 文件也常被误判"未基于输入资料"→
            # 触发无谓的闭环重试 → 同主题 PPT 被反复生成 3-5 份**。
            # 修复后：所有 final 早返回路径前都先 yield artifacts，短路逻辑形同虚设的根因被根除。
            yield {"type": "artifacts", "files": list(_generated_files)}
            # 方案C：模型声称已生成文件但实际无产物 → 追加防幻觉警告（无工具能力模型的兜底）
            final_text, _warned = _guard_fake_file_claim(final_text, _generated_files)
            yield {"type": "final", "text": final_text}
            return

        # 3.0) ask_user 中断：模型需要向用户澄清 → 暂停本轮，等用户回复后续跑
        #    不真正执行该工具，而是把问题/选项下发给前端弹窗；用户回复以新消息进入下一轮，
        #    模型在历史中看到答案后继续。这样把「人机确认」做成可控原语（AskUserQuestion）。
        #
        #    自动化场景（ctx.is_automation=True）：无人在线，ask_user 已在
        #    _build_run_context 从 meta 工具列表移除；这里是兜底——把 ask_user 项
        #    从 normalized 中移除并向对话注入"已忽略"提示，让 agent 继续推理，
        #    避免任务挂起（原本会 yield final "⏸ 已向你提出问题..." 结束任务）。
        _is_automation = bool(getattr(ctx, "is_automation", False))
        if _is_automation:
            _ask_filtered = [(n, a, c) for (n, a, c) in normalized if n != "ask_user"]
            if len(_ask_filtered) != len(normalized):
                _n_skip = len(normalized) - len(_ask_filtered)
                yield {"type": "plan", "text":
                       f"第 {step} 步：自动化模式忽略 ask_user ×{_n_skip}，继续自主完成"}
                conv.append({"role": "user", "content":
                    "[系统·自动化] 本轮 ask_user 在自动化任务中无人在线，已被忽略。"
                    "请基于现有信息与可用工具继续完成；不要再次调用 ask_user；"
                    "若信息确实无法获取，请尽力作答并在最终回复中说明原因。"})
                normalized = _ask_filtered

        for (_n, _a, _cid) in normalized:
            if _n == "ask_user":
                _aq = _a or {}
                _q = _aq.get("question", "")
                _opts = _aq.get("options") or []
                if isinstance(_opts, str):
                    _opts = [_opts]
                yield {"type": "ask_user", "question": _q, "options": _opts}
                # P2⑮ 同上：ask_user 早返回前也 yield artifacts，避免短路失效
                yield {"type": "artifacts", "files": list(_generated_files)}
                yield {"type": "final",
                       "text": "⏸ 已向你提出问题，等待你的回复后继续完成任务。"}
                return

        # 3) 记录助手消息（保持多轮上下文完整）
        if supports_tools:
            conv.append({
                "role": "assistant",
                "content": msg.content or "",
                "tool_calls": [
                    {"id": _cid, "type": "function",
                     "function": {"name": _n, "arguments": json.dumps(_a, ensure_ascii=False)}}
                    for (_n, _a, _cid) in normalized
                ],
            })
        else:
            conv.append({"role": "assistant", "content": final_text or "(调用工具)"})
        yield {"type": "plan", "text": f"第 {step} 步：计划调用 {len(normalized)} 个工具"}

        # 3.5) 重复调用防护：仅拦截「连续失败后重试创建」模式
        #    规则：只在上一步是创建类工具**且失败**时，本步又想创建才拦截。
        #    正常流程（用户确认→create_tool成功→list_available_tools→调用新工具）不受影响。
        _step_tool_names = [_n for (_n, _, _) in normalized]
        _has_create = any(n in _META_CREATE_TOOLS for n in _step_tool_names)
        if _has_create and _last_create_failed:
            yield {"type": "error", "message":
                f"检测到上一步 create_tool/create_skill 执行失败（{_create_result[:120] if _create_result else '未知'}），"
                f"你又在尝试重新创建。请停止创建新工具："
                f"如果之前提示「功能相似的工具已存在」，请直接使用该已有工具执行任务；"
                f"如果是代码/参数问题，请在调用时修正代码和参数。不要反复用相同参数创建。"}
            return

        # 3.5b) 成功后封禁：如果之前 create_tool 已经成功过，禁止再次创建
        #    （LLM 常因"对已有工具不满意"而反复 create——这是模型能力问题，必须硬拦截）
        if _has_create and _create_success_seen:
            yield {"type": "error", "message":
                f"你之前已经成功创建过工具了（{_create_result[:100] if _create_result else '详情见上文'}）。"
                f"请立即使用已创建的工具执行任务，不要再创建新工具。"
                f"如果该工具执行结果不理想，请调整传入的参数（如 content/text），而非重新创建。"
                f"禁止继续调用 create_tool 或 create_skill。"}
            return

        # 3.6) 元工具全局频率限制：软拦截（不再硬终止 run_agent 循环）
        #    超限的元工具从本步执行列表中移除，生成拦截消息回灌给模型，
        #    模型收到拦截提示后应在下一步重新决策：若确缺能力则走 [PROPOSE_TOOL]→创建→使用，
        #    绝不切换到与任务无关的其他工具来凑数（与既定「缺工具兜底」方向保持一致）。
        #    这修复了之前"yield error + return 直接杀死循环"导致任务异常终止的问题。
        _step_meta_calls = [n for n in _step_tool_names
                            if n in _META_CREATE_TOOLS or n in _META_LIST_TOOLS]
        _blocked_details = []  # 收集被拦截的工具信息，用于前端展示
        if _step_meta_calls:
            _will_exceed = []
            for n in set(_step_meta_calls):
                if _meta_call_count.get(n, 0) + _step_meta_calls.count(n) > _META_CALL_LIMIT:
                    _will_exceed.append((n, _meta_call_count.get(n, 0)))
            if _will_exceed:
                _blocked_names = {n for n, c in _will_exceed}
                _detail = ", ".join(f"{n}（已调{c}次）" for n, c in _will_exceed)

                # 从执行列表中拆分：超限的 vs 未超限的
                _blocked_items = []
                _remaining = []
                for item in normalized:
                    if item[0] in _blocked_names:
                        _blocked_items.append(item)
                    else:
                        _remaining.append(item)

                # 构造拦截消息——告诉模型为什么被拦、下一步该怎么做
                _intercept_msg = (
                    f"⚠️ 系统拦截：以下元工具调用过于频繁({_detail})，已被阻止执行，禁止再调用它们（{', '.join(_blocked_names)}）。\n"
                    f"请按既定流程继续，且只调用与「当前任务」直接匹配的能力：\n"
                    f"1. 若你之前调用 list_available_tools 已确认工具库中「没有能完成当前任务的工具」"
                    f"（即确实缺少某项能力），请在最终回复末尾追加 [PROPOSE_TOOL] 标记（仅一句工具用途描述）；"
                    f"系统会请用户确认后创建该工具，用户确认后你必须立即调用新创建的工具执行任务。\n"
                    f"2. 若工具库中本就有与当前任务直接匹配的工具，直接调用它即可；"
                    f"严禁为绕过限流而调用与当前任务无关的其他工具来凑数。\n"
                    f"3. 绝对禁止再尝试调用被拦截的元工具（{', '.join(_blocked_names)}）。"
                )

                # 把拦截消息注入对话上下文，让模型在下一轮看到并据此决策
                if supports_tools and _blocked_items:
                    # 函数模式：为每个被拦截的 tool_call 注入 tool result
                    for (_name, _args, _cid) in _blocked_items:
                        if _cid:
                            conv.append({
                                "role": "tool",
                                "tool_call_id": _cid,
                                "content": _intercept_msg
                            })
                        else:
                            conv.append({"role": "user", "content": f"[系统拦截] {_intercept_msg}"})
                elif _blocked_items:
                    # 文本模式：作为 user 消息追加
                    conv.append({"role": "user", "content": f"[系统拦截] {_intercept_msg}"})

                normalized = _remaining
                _blocked_details = _will_exceed

                # 通知前端：有工具被拦截（不是 error，是 plan 级别的信息）
                yield {"type": "plan",
                       "text": f"第 {step} 步：{len(_blocked_items)} 个元工具被频率限制拦截（{_detail}），已回灌拦截提示"}

        # 如果本步所有工具都被拦截了，跳过执行循环进入下一轮（不终止！）
        # 模型会在下一轮看到拦截消息后重新决策——这是"自愈"的关键。
        if not normalized:
            continue

        # 4) 逐个执行工具，并把结果回灌为 tool 角色消息（函数模式）/ user 消息（文本模式）
        for (_name, _args, _cid) in normalized:
            try:
                _args = dict(_args)
            except Exception:
                _args = {}
            # 把已生成文件路径注入 run_temp_code，使其能读取 generate_image 等产出的图片做后处理
            if _name == "run_temp_code" and _generated_files:
                _args["artifacts"] = list(_generated_files)
            yield {"type": "call", "tool": _name, "args": _args,
                   "kind": (tool_map.get(_name) or {}).get("kind") or "tool"}

            # 工具执行前再次检查中止（避免长耗时工具 / 沙箱操作无法及时中断）
            if cancel_check and cancel_check():
                yield {"type": "aborted"}
                return

            tool = tool_map.get(_name)
            if not tool:
                result = f"未知工具：{_name}"
            else:
                try:
                    if on_tool_call:
                        try:
                            on_tool_call(_name)
                        except Exception:
                            pass
                    h = tool["handler"](**_args)
                    if asyncio.iscoroutine(h):
                        h = await h
                    result = h
                except Exception as e:
                    result = f"工具执行失败：{type(e).__name__}: {e}"

            # 结果统一转字符串，避免超大对象撑爆上下文
            if not isinstance(result, str):
                try:
                    result = json.dumps(result, ensure_ascii=False)
                except Exception:
                    result = str(result)
            # 截断前提取产物文件路径，供 run_temp_code 后处理与闭环「artifacts」事件识别。
            # 使用双路提取（前缀 + 通用扫描），兼容 generate_pptx 等
            # 「PPT 已生成（...风格，共 N 页）：/path/x.pptx」格式（关键字与冒号间有括号干扰）。
            _prod_paths = _extract_generated_files(result)
            if len(result) > 16000:
                # 保留产物路径行 + 前 16000 字符主体
                _tail = "\n".join(_prod_paths)
                result = result[:16000] + (f"\n…(结果已截断)\n{_tail}"
                                          if _tail else result[:16000] + "\n…(结果已截断)")

            # 收集已生成文件路径，供后续 run_temp_code 对已生成图片做后处理，也供 artifacts 事件使用
            for _fp in _prod_paths:
                if _fp not in _generated_files:
                    _generated_files.append(_fp)

            yield {"type": "result", "tool": _name, "text": result}
            # P2⑭ inline 可视化：工具返回含 __viz__ 字段时，额外推送 visualization 事件
            if isinstance(result, str) and result.strip().startswith("{"):
                try:
                    _rv = json.loads(result)
                    if isinstance(_rv, dict) and _rv.get("__viz__"):
                        yield {"type": "visualization", "viz": _rv["__viz__"]}
                except Exception:
                    pass
            # P1⑤+ 子 Agent 事件冒泡：若工具是 spawn_subagent，把子 Agent 的中间事件
            #（plan/call/result/final）逐条 yield 给前端渲染为可折叠子树。
            _sub_evts = getattr(ctx, "_subagent_events", None)
            if _sub_evts:
                for _se in list(_sub_evts):
                    yield _se
                ctx._subagent_events.clear()
                if hasattr(ctx, "_subagent_current_id"):
                    delattr(ctx, "_subagent_current_id")
            # 回灌：函数模式用 tool 角色（携带 tool_call_id），文本模式用 user 角色带回结果
            if supports_tools:
                conv.append({"role": "tool", "tool_call_id": _cid, "content": result})
            else:
                conv.append({"role": "user", "content":
                    f"【工具 {_name} 执行结果】\n{result}\n"
                    f"请基于以上结果继续；若任务已完成，请直接给出最终答复，"
                    f"不要输出新的 tool_call 代码块。"})

            # P1⑦ 显式反思/重规划：非元工具执行失败/未产出有效结果时，
            # 强制模型进入「反思」——先分析失败原因，再改道（换工具/换方法/提议创建），
            # 取代旧版仅一句"别凑合"的隐式回灌。每步只触发一次；整体反思次数封顶，避免空转死循环。
            if (not _step_tool_fail_hint
                    and (tool_map.get(_name) or {}).get("kind") != "meta"
                    and not _name.startswith("create_") and not _name.startswith("list_available_")):
                _failed = (isinstance(result, str) and (
                    result.startswith("工具执行失败") or result.startswith("未知工具")
                    or "错误：" in result or "执行失败" in result or not result.strip()
                ))
                if _failed:
                    _step_tool_fail_hint = True
                    if _reflection_count < _MAX_REFLECTIONS:
                        # 未达上限：显式反思步骤——要求分析原因并改道（不要原样重试同一调用）
                        _reflection_count += 1
                        yield {"type": "plan", "text":
                               f"第 {step} 步反思：工具「{_name}」未产出有效结果，正在分析原因并改道"}
                        conv.append({"role": "user", "content":
                            "[系统·反思] 工具「%s」执行失败或未产出有效结果（原因见上方工具结果）。\n"
                            "请执行一次反思(ReAct 的 Reflect 步)：\n"
                            "1. 先分析失败原因：是参数传错？工具能力不匹配当前子任务？还是上下文/输入缺失？\n"
                            "2. 基于原因选择**不同的**下一步：换一个更合适的工具、调整参数重试、"
                            "或拆解出更小的子任务；不要原样重复刚才的那一次调用。\n"
                            "3. 若确认是该工具能力本身不完整、库里也没有能替代的现成工具，"
                            "请在最终回复末尾追加 [PROPOSE_TOOL] 标记提议创建更完整的工具。\n"
                            "4. 若失败原因是缺少关键业务信息，请调用 ask_user 向用户澄清，不要猜测。" % _name})
                    else:
                        # 反思次数封顶：不再空转，强制收敛到「提议创建 / 向用户澄清 / 尽力作答」
                        yield {"type": "plan", "text":
                               f"第 {step} 步：反思已达上限，收敛到兜底（提议创建/澄清/尽力作答）"}
                        conv.append({"role": "user", "content":
                            "[系统·反思超限] 同一工具链已多次失败且反思未果，请停止原路重试。\n"
                            "立即收敛（三选一）：\n"
                            "① 若确属能力缺失，按缺工具兜底在最终回复末尾追加 [PROPOSE_TOOL] 标记；\n"
                            "② 若缺关键业务信息，调用 ask_user 向用户澄清；\n"
                            "③ 若两者都不是，请用你自身能力尽量完成任务并给出结论，不要再调用会失败的工具。"})

            # 独立缓存 create 类工具的自身返回值，用于步骤结束后的失败判定
            # （避免被同步骤内其他工具的 result 覆盖/污染）
            if _name in _META_CREATE_TOOLS:
                _create_result = result
            # 元工具调用计数（用于全局频率限制）
            if _name in _META_CREATE_TOOLS or _name in _META_LIST_TOOLS:
                _meta_call_count[_name] = _meta_call_count.get(_name, 0) + 1

        # 追踪本步是否有创建类工具执行失败（用于下一步的连续失败检测）
        # 注意：用 _create_result（create 自身返回值）而非 result（循环最后一个工具的返回值）
        # 判定覆盖三种情况：
        #   A) handler 自身 checked return（"创建工具失败"/"功能相似的工具已存在"等）
        #   B) handler 抛非预期异常被 run_agent 通用 try/except 捕获 → "工具执行失败：..."
        #   C) handler 被用户/系统拒绝
        _cr = _create_result
        _last_create_failed = (_cr is not None) and bool(
            isinstance(_cr, str) and (
                _cr.startswith("创建工具失败") or
                _cr.startswith("创建技能失败") or
                "功能相似的工具已存在" in _cr or
                ("已被拒绝" in _cr and "创建" in _cr) or
                _cr.startswith("工具执行失败")   # [B] handler 异常被外层捕获
            )
        )
        # 追踪 create_tool 是否已成功——成功后禁止再创建（防 LLM "完美主义"反复重建）
        if (_create_result is not None and isinstance(_create_result, str)
                and "已创建成功" in _create_result):
            _create_success_seen = True

        # 动态注入：create_tool 成功后，将新工具追加到候选池，让 LLM 下一轮能看到并调用
        # （否则新工具只在 DB 里、运行时工具列表不会更新 → LLM 看不到 → 反复 create）
        if ctx and _create_success_seen and hasattr(ctx, 'created_tools') and ctx.created_tools:
            _existing_names = set(tool_map.keys())
            for _ct in ctx.created_tools:
                _cname = _ct.get("name", "")
                if _cname and _cname not in _existing_names:
                    _ccode = _ct.get("code", "")
                    _cdesc = _ct.get("description", "") or ""
                    _cdisp = _ct.get("display_name", "") or _cname
                    if not _ccode:
                        continue
                    # 构建沙箱执行 wrapper（与 build_session_tools 中 is_user_created 逻辑一致）
                    def _make_dyn_wrap(code, nm):
                        async def _dyn_wrap(**a):
                            _sb = _importlib.import_module("sandbox")
                            return await asyncio.to_thread(_sb.run_code, code, a, nm)
                        return _dyn_wrap
                    _new_tool = {
                        "name": _cname,
                        "description": _cdesc,
                        "parameters": {"type": "object", "properties": {
                            "content": {"type": "string", "description": "要处理的内容/文本"},
                            "text": {"type": "string", "description": "文本输入"},
                            "title": {"type": "string", "description": "标题（可选）"},
                        }},
                        "handler": _make_dyn_wrap(_ccode, _cname),
                        "kind": "tool",
                    }
                    tools.append(_new_tool)
                    tool_map[_cname] = _new_tool
                    _existing_names.add(_cname)
            # 重新生成 openai_tools（包含新注入的工具）
            openai_tools = _to_openai_tools(tools)

    # 5) 超出步数：软提示而非硬终止——给出阶段总结 + 续跑钩子，由用户下一轮「继续」接力
    #    （本轮仍优雅结束，避免无限空转；但不再是 error 终止，符合「不随意终止」原则）
    # 对外暴露本轮已生成文件路径（供 run_agent_with_retry 的闭环评估器识别「交付物已产出」）
    yield {"type": "artifacts", "files": list(_generated_files)}
    yield {"type": "plan",
           "text": f"已执行 {max_steps} 步，任务较复杂尚未完全收敛，已进行的进展见上方结果。"}
    yield {"type": "final",
           "text": f"（本段已执行 {max_steps} 步，任务较长未能一次完成。如需继续，请回复「继续」，"
                   f"我将沿用已激活的能力接着完成剩余步骤；如某一步结果不理想，也可直接补充说明。）"}
    return


async def _evaluate_result(client, model_name, question, result_text, artifacts=None):
    """评估最终回答是否满足原始需求。返回 (passed:bool, reason:str)。

    任何异常都放行（passed=True），绝不让评估器阻断主流程——评估是「锦上添花」的
    安全网，不是「卡死任务」的闸门。

    artifacts: 本轮工具实际产出的文件绝对路径列表。当用户明确要求生成文件/图片/文档
    等交付物时，只要 artifacts 中出现对应的生成产物，即视为该交付物已满足，
    评估器不得以「未生成文件」为由判未达标（防止工具已成功、却因最终自然语言总结
    没复述路径而被误判，进而触发无谓的闭环重跑与重复生成）。
    """
    _artifacts = artifacts or []
    # P2⑮ 根因修复：artifacts 非空且用户需求是「生成文件类交付物」时，**跳过 LLM 评估
    # 直接 passed=True**。这从评估器内部补上短路——之前仅有 run_agent_with_retry
    # 的 artifacts 短路（line 597），但因为 agent.py 早返回路径不 yield artifacts 导致
    # 短路永远走不到，评估器只能判 final_text 文本、看不准是否真的交付了文件。
    # 现在评估器本身也具备"看到 artifacts 就放行"的能力，与外层短路互为双保险。
    # 关键：必须严格匹配"文件类交付物"语义（PPT/Word/图片/视频/PDF/导出报告等），
    # 否则会把"对话生成的总结"误判为文件交付物。
    if _artifacts and _is_file_delivery_request(question):
        return True, "已生成文件类交付物（%d 个），视为满足用户需求（不重复评估/重试）" % len(_artifacts)
    # 方案C 防幻觉闭环：最终回答已被 _guard_fake_file_claim 追加了「未真正调用工具」警告
    # → 说明是模型能力问题（无工具调用/文本模式未正确输出 tool_call），重跑也是同样结果。
    # 直接放行，避免评估器判未达标 → 无谓闭环重跑 → 同模型再次幻觉的死循环。
    if (result_text or "").find("没有调用任何生成工具") != -1:
        return True, "模型未正确调用工具（已向用户明示防幻觉警告），不重复重试"
    if not result_text or not result_text.strip():
        return False, "未产生有效结果"
    _art_hint = ""
    if _artifacts:
        _art_hint = ("\n\n[本轮工具已实际生成的文件产物，路径如下]\n"
                     + "\n".join("- " + a for a in _artifacts[:20])
                     + "\n若用户需求是生成此类文件/图片/文档，则这些产物即代表交付物已满足，"
                       "不要再以「未生成文件」为由判未达标。")
    try:
        _sys = ("你是任务完成度评估器。只允许判断【最终回答】是否真正满足【用户需求】。"
                "注意：你看到的【最终回答】只是智能体的自然语言总结，可能并未复述工具产出的具体文件路径；"
                "真正的文件/图片/文档交付物是否生成，请以【本轮工具已实际生成的文件产物】为准。"
                "若用户需求是生成文件/图片/文档，且产物列表中已有对应文件，则该交付物视为已满足。"
                "仅当回答明显偏离主题、只完成部分要求、或遗漏关键（非文件类）信息时，才判为未达标。"
                "只输出一个 JSON 对象：{\"passed\": true 或 false, \"reason\": \"不超过30字的简短原因\"}，"
                "不要输出任何额外文字。")
        _user = (f"用户需求：\n{question}\n\n最终回答：\n{result_text[:3000]}\n"
                 f"{_art_hint}\n\n请输出评估 JSON：")
        resp = await asyncio.to_thread(client.chat.completions.create,
                                       model=model_name,
                                       messages=[{"role": "system", "content": _sys},
                                                 {"role": "user", "content": _user}],
                                       temperature=0.0, max_tokens=200)
        _txt = resp.choices[0].message.content or ""
        _m = re.search(r'\{.*\}', _txt, re.DOTALL)
        if not _m:
            return True, ""
        _obj = json.loads(_m.group(0))
        return bool(_obj.get("passed", True)), str(_obj.get("reason", ""))
    except Exception:
        return True, ""


async def run_agent_with_retry(client, model_name, messages, tools, params=None,
                               max_steps=8, on_tool_call=None, cancel_check=None,
                               ctx=None, caps=None, original_question=None,
                               max_attempts=2):
    """外层闭环（P1⑦-增强·第①层轻量闭环）：执行→评估→反思→重规划→再执行。

    仅当 original_question 非空时启用「评估→不达标则重跑」闭环；
    否则退化为单次 run_agent（等价于未启用本层的原行为），保证无回归。

    每次尝试都从原始 messages 快照出发，重试轮在末尾追加一条反思引导 user 消息，
    避免把上一轮的工具调用历史污染进新尝试的上下文。
    非末轮且未达标的尝试，其 final 事件会被扣留（避免「错误答案闪现」），
    仅当达标或到达末轮时才输出 final（尽力作答）。
    """
    if not original_question:
        # 未启用评估：直接透传，行为与旧版完全一致
        # （同时过滤内部 artifacts 事件，避免未知事件泄漏到前端）
        async for ev in run_agent(client, model_name, messages, tools, params,
                                  max_steps=max_steps, on_tool_call=on_tool_call,
                                  cancel_check=cancel_check, ctx=ctx, caps=caps):
            if ev.get("type") == "artifacts":
                continue
            yield ev
        return

    _base = list(messages)
    _last_reason = ""
    for _attempt in range(1, max_attempts + 1):
        _attempt_msgs = list(_base)
        if _attempt > 1:
            _attempt_msgs.append({"role": "user", "content":
                f"[系统·闭环反思] 上一轮结果未满足原始需求，请重新规划并严格执行。\n"
                f"原始需求：{original_question}\n"
                f"上轮评估结论：{_last_reason}\n"
                "本次请调整方法或工具，确保最终输出真正满足原始需求。"})
        _final_ev = None
        _final_text = ""
        _attempt_artifacts = []
        _ask_user_seen = False   # 2026-08-19：标记本轮是否调用过 ask_user（用户交互暂停）
        _stop = False
        async for ev in run_agent(client, model_name, _attempt_msgs, tools, params,
                                  max_steps=max_steps, on_tool_call=on_tool_call,
                                  cancel_check=cancel_check, ctx=ctx, caps=caps):
            _t = ev.get("type")
            if _t == "ask_user":
                # 智能体在等用户回答——这是「暂停等用户」信号，**不是任务失败**。
                # 必须立即透传 final 并退出本层，**严禁**进入评估/重试：
                # 旧逻辑把 final 扣留→评估器见 _attempt_artifacts 空→判失败→重跑→SSE 持续打开→
                # 前端 agentStreaming=true→用户确认弹窗的 sendAgentMessage 被 doAgent 守卫拦死→
                # 输入框卡死、智能体收不到回复（生产截图复现：会议邀请任务死锁）。
                _ask_user_seen = True
                yield ev   # 把 ask_user 事件透传给前端（弹窗）
            elif _t == "final":
                _final_ev = ev
                _final_text = ev.get("text", "")
                if _ask_user_seen:
                    # 立即输出 final 并结束，让前端流关闭、解除 agentStreaming 锁，
                    # 用户回复才能真正送到下一轮。
                    yield ev
                    return
            elif _t == "artifacts":
                # 内部信号：本轮工具产出的文件，供评估器识别交付物；不透传给前端
                _attempt_artifacts = ev.get("files", []) or []
            elif _t in ("aborted", "error"):
                yield ev
                _stop = True
            else:
                yield ev
        if _stop:
            return
        # ask_user 后 run_agent 必然已 return；正常代码路径不会到这里（_ask_user_seen
        # 分支已 return）。但保险起见，若本轮调过 ask_user 却没收到 final（异常分支），）
        # 也直接返回，绝不进入评估重试——同上根因。
        if _ask_user_seen:
            return
        # 短路：本轮已实际产出文件类交付物（artifacts 非空），视为交付要求已满足，
        # 直接输出终答并结束，不再跑评估器 / 闭环重跑——避免对已生成文件反复重跑
        # 造成重复生成多个文件（这正是 PPT 任务被调用 5 次、生成 4 个文件的元凶）。
        if _attempt_artifacts:
            if _final_ev is not None:
                yield _final_ev
            return
        # 最后一轮：无论达标与否，都输出本轮 final（尽力作答，不再重试）
        if _attempt >= max_attempts:
            if _final_ev is not None:
                yield _final_ev
            return
        # 评估达标：输出本轮 final 并结束
        _passed, _reason = await _evaluate_result(client, model_name,
                                                  original_question, _final_text,
                                                  _attempt_artifacts)
        _last_reason = _reason
        if _passed:
            if _final_ev is not None:
                yield _final_ev
            return
        # 不达标且非最后一轮：扣留 final，发反思提示，进入下一轮重试
        yield {"type": "plan", "text": f"🔄 闭环反思（第{_attempt}轮未达标）：{_reason} → 重新规划执行"}


async def resolve_session_tools(task, library, client, model_name, params, top_k=5):
    """从全局工具库 library 中，根据任务文本挑选相关工具（ReAct 会话级注入）。

    优先用 LLM 解析本次任务的能力缺口，返回需要调用的工具名列表；若 LLM 调用
    失败或返回为空，降级为「触发词/名称」关键词匹配；若两者都无命中，则
    返回空列表 []（不再灌入整库能力）。模型将直接作答，并通过常驻元工具
    （list_available_tools / ask_user / run_temp_code / [PROPOSE_TOOL] 创建）
    与 active_caps 持久化的已激活能力继续；盲目注入全部工具会污染上下文、
    诱导模型误调无关工具，这是"理解错"的直接根因。

    返回：library 中相关工具的子集（dict 列表）。
    """
    if not library:
        return []
    catalog = "\n".join(
        f"- {t['name']}（{'方法论技能' if t.get('skill_type') == 'method' else '工具/代码技能'}）：{t['description']}"
        + (f"；适用情形：{t['when_to_use']}" if t.get("when_to_use") else "")
        + f"；触发词：{t.get('trigger_words', '')}"
        for t in library
    )
    prompt = (
        "你是智能体的能力调度器。下面是可用的执行能力清单（工具 / 代码技能）：\n" + catalog +
        f"\n用户任务：{task}\n"
        f"请判断完成该任务需要哪些执行能力，输出 JSON 数组（元素为 name），最多 {top_k} 个。\n"
        "选择原则：\n"
        "1. 多步骤复杂任务：优先选与任务核心动作直接匹配的能力，再补充辅助能力；"
        "方法论技能（skill_type=method，即思考框架类）也应被选中——选中后系统会将其作为思考框架注入 system prompt，请一并判断是否需要。\n"
        "2. 单步执行任务（如「算 1+1」「总结这段话」）：只选直接对应的能力即可。\n"
        "3. 纯闲聊 / 通用知识类问题（天气、百科、常识、定义）：输出空数组 []，留给大模型直接作答。\n"
        "4. 若任务确实需要执行能力但清单中无任何匹配，输出空数组 []——系统会让大模型基于已注入的思考框架自行处理。\n"
        "5. 每个能力都附有「适用情形（when_to_use）」与「触发词」两类线索：请结合任务语义综合判断——"
        "即使任务字面未命中触发词，只要语义符合某能力的适用情形，也应选中该能力（双路匹配：触发词 + 语义）。"
    )
    try:
        raw = await asyncio.to_thread(
            client.chat.completions.create,
            model=model_name,
            messages=[{"role": "system", "content": "只输出 JSON 数组，不要任何解释。"},
                      {"role": "user", "content": prompt}],
            temperature=0,
            **params,
        )
        names = json.loads(raw.choices[0].message.content or "[]")
        if isinstance(names, list):
            sel = [t for t in library if t["name"] in names][:top_k]
            if sel:
                return sel
    except Exception:
        pass

    # 降级：触发词 / 名称 / 描述 关键词匹配
    task_low = (task or "").lower()
    scored = []
    for t in library:
        trigs = (t.get("trigger_words") or "").replace(",", " ").lower().split()
        hay = trigs + t["name"].lower().split() + (t.get("description") or "").lower().split()
        if any(k.strip() and len(k.strip()) >= 2 and k.strip() in task_low for k in hay):
            scored.append(t)
    if scored:
        return scored[:top_k]

    # 完全无匹配（路由 LLM 异常或全无语义命中）→ 不灌整库，返回空列表。
    # 模型直接作答，并可通过常驻元工具（list_available_tools / ask_user /
    # run_temp_code / [PROPOSE_TOOL] 创建）与 active_caps 持久化的已激活能力继续；
    # 盲目注入全部工具会污染上下文、诱导模型误调无关工具（"理解错"的根因）。
    return []
