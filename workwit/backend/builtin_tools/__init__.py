"""内置工具加载器。

自动扫描本目录下的 *.py 工具模块（以下划线开头的文件视为私有/共享模块，跳过），
每个模块需声明：
    META = {name, display_name, category, description, params, backend_type, handler, trigger_words, [skip_skill]}
    async def run(ctx, **kwargs)   # 同步/异步均可

扫描在 import 时执行，用 try/except 守护：单文件导入失败不影响整体启动，
便于「丢一个 .py 文件即新增一个工具」且隔离故障。

导出的两个对象：
    BUILTIN_TOOLS  —— 元数据列表，供 db.init_tools 写入 tools 表
    TOOL_HANDLERS —— name -> run 可调用对象，供 tools_handlers.HANDLERS 合并
"""
import importlib
import logging
import os
import traceback

logger = logging.getLogger("builtin_tools")

# 汇总给 db.init_tools 用的元数据列表
BUILTIN_TOOLS = []
# name -> run 可调用对象，供 tools_handlers.HANDLERS 合并
TOOL_HANDLERS = {}


def _scan():
    here = os.path.dirname(os.path.abspath(__file__))
    for fn in sorted(os.listdir(here)):
        if not fn.endswith(".py") or fn == "__init__.py":
            continue
        mod = fn[:-3]
        if mod.startswith("_"):
            # 私有/共享模块（如 _shared），不单独注册为工具
            continue
        full = "builtin_tools." + mod
        try:
            m = importlib.import_module(full)
        except Exception as e:
            logger.error("内置工具加载失败，已跳过 %s: %s\n%s", full, e, traceback.format_exc())
            continue
        meta = getattr(m, "META", None)
        run = getattr(m, "run", None)
        if not isinstance(meta, dict) or not callable(run):
            logger.warning("内置工具 %s 缺少 META 或 run，已跳过", full)
            continue
        name = meta.get("name")
        if not name:
            logger.warning("内置工具 %s 的 META.name 为空，已跳过", full)
            continue
        if name in TOOL_HANDLERS:
            logger.warning("内置工具名冲突：%s 已被占用，已跳过 %s", name, full)
            continue
        TOOL_HANDLERS[name] = run
        BUILTIN_TOOLS.append(meta)
        logger.info("已注册内置工具: %s", name)


_scan()
