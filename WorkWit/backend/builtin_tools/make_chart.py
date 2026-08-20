"""生成对话内可视化图表（柱状 bar / 折线 line / 饼图 pie）。返回结构化 spec，由前端渲染。"""
import json
import re

META = {
    "name": "make_chart", "display_name": "生成图表", "category": "visualization",
    "description": "在分析数据、对比指标、展示趋势或占比时，生成对话内可视化图表（柱状图 bar、折线图 line、饼图 pie）。传入类别标签与对应数值即可，前端自动渲染。",
    "params": {"type": "object",
               "properties": {
                   "chart_type": {"type": "string", "description": "图表类型：bar(柱状)/line(折线)/pie(饼图)，默认 bar"},
                   "title": {"type": "string", "description": "图表标题"},
                   "labels": {"type": "string", "description": "类别标签，数组JSON或逗号分隔，如 ['一月','二月'] 或 '一月,二月'"},
                   "values": {"type": "string", "description": "对应数值，数组JSON或逗号分隔，如 [30,45] 或 '30,45'"},
                   "unit": {"type": "string", "description": "数值单位(可选)，如 '%'、'万元'"}
               },
               "required": ["labels", "values"]},
    "backend_type": "builtin", "handler": "make_chart",
    "trigger_words": "图表,可视化,柱状图,折线图,饼图,趋势,对比,占比,数据展示",
    "skip_skill": 1,
}


def run(ctx, chart_type="bar", title="", labels="", values="", unit=""):
    chart_type = (chart_type or "bar").lower().strip()
    if chart_type not in ("bar", "line", "pie"):
        chart_type = "bar"

    def _parse_list(s):
        if s is None:
            return []
        if isinstance(s, (list, tuple)):
            return [str(x) for x in s]
        s = str(s).strip()
        if not s:
            return []
        try:
            _v = json.loads(s)
            if isinstance(_v, list):
                return [str(x) for x in _v]
        except Exception:
            pass
        return [x.strip() for x in re.split(r"[,，;；、\n]", s) if x.strip()]

    _labels = _parse_list(labels)
    _values = []
    for x in _parse_list(values):
        try:
            float(x)
            _values.append(float(x))
        except Exception:
            pass
    if not _labels or not _values:
        return "图表生成失败：labels 与 values 均需提供且非空（可传数组 JSON 或逗号分隔）。"
    _n = min(len(_labels), len(_values))
    _labels = _labels[:_n]
    _values = _values[:_n]
    spec = {
        "type": chart_type,
        "title": (title or "").strip(),
        "labels": _labels,
        "values": _values,
        "unit": (unit or "").strip(),
    }
    return {"__viz__": spec, "message": "图表已生成：" + (title or chart_type) + f"（共 {_n} 项）"}
