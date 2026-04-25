"""
Streamlit 内嵌知识图谱：extract_chain.json 为节点、extract_relations.json 为边，
使用 vis-network 物理引擎（引力/斥力）自动分散布局。

节点为 **圆角矩形（box + borderRadius）**，文字绘制在框内。
vis-network 的 image/circularImage 会把标签画在图片下方，故不用图片形状作节点。

边：因果 → 红棕，其它 → 深灰。
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

_TOOLTIP_SOURCE_MAX = 4000

# 统一节点：柔粉填充；悬停为深粉
_NODE_BG = "#F8D0DC"
_NODE_BORDER = "#C7708C"
_NODE_HIGHLIGHT_BG = "#E8A0B5"
_NODE_HIGHLIGHT_BORDER = "#A83256"
_NODE_FONT = "#4A2C32"

_EDGE_TEMPORAL = "#666666"
_EDGE_CAUSAL = "#C0392B"
_CANVAS_BG = "#FFF5F8"

# 圆角矩形节点（仅 box 形状才能把多行 label 画在框内）
_BOX_BORDER_RADIUS = 12
_LABEL_MAX_WIDTH = 200

_NODE_COLOR: dict[str, Any] = {
    "background": _NODE_BG,
    "border": _NODE_BORDER,
    "highlight": {
        "background": _NODE_HIGHLIGHT_BG,
        "border": _NODE_HIGHLIGHT_BORDER,
    },
    "hover": {
        "background": _NODE_HIGHLIGHT_BG,
        "border": _NODE_HIGHLIGHT_BORDER,
    },
}


def _rel_color(typ: str) -> str:
    t = str(typ or "").strip()
    if t == "因果":
        return _EDGE_CAUSAL
    return _EDGE_TEMPORAL


def _tooltip_plain(event: dict[str, Any]) -> str:
    parts: list[str] = []
    eid = str(event.get("event_id") or "").strip()
    if eid:
        parts.append(f"【{eid}】")
    inner = event.get("事件") or {}
    for key in ("人物", "行动", "目标", "结果", "情绪", "场景"):
        val = inner.get(key)
        if val:
            if isinstance(val, list):
                val = ", ".join(str(v) for v in val)
            text = str(val).strip().replace("\r\n", "\n").replace("\r", "\n")
            if text:
                parts.append(f"{key}：{text}")
    src = inner.get("source_text") or ""
    if src:
        src = str(src).strip().replace("\r\n", "\n").replace("\r", "\n")
        if len(src) > _TOOLTIP_SOURCE_MAX:
            src = src[:_TOOLTIP_SOURCE_MAX] + "\n…（原文过长，已截断）"
        parts.append("")
        parts.append("—— 原文 ——")
        parts.append(src)
    return "\n".join(parts) if parts else eid or "（无正文）"


def _short_label(event: dict[str, Any], max_len: int = 32) -> str:
    eid = str(event.get("event_id") or "?")
    inner = event.get("事件") or {}
    action = str(inner.get("行动") or "").strip().replace("\n", " ")
    if action:
        if len(action) > max_len:
            action = action[: max_len - 1] + "…"
        return f"{eid}\n{action}"
    return eid


def build_graph_payload(
    project_root: Path, output_dir_rel: str
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]] | None:
    od = project_root / output_dir_rel.replace("\\", "/").strip().rstrip("/")
    chain_path = od / "extract_chain.json"
    rel_path = od / "extract_relations.json"
    if not chain_path.exists():
        return None
    data = json.loads(chain_path.read_text(encoding="utf-8"))
    events = data.get("new_events") or []
    relations: list[dict[str, Any]] = []
    if rel_path.exists():
        rd = json.loads(rel_path.read_text(encoding="utf-8"))
        relations = list(rd.get("relations") or [])

    nodes: list[dict[str, Any]] = []
    seen: set[str] = set()
    for ev in events:
        eid = str(ev.get("event_id") or "")
        if not eid:
            continue
        seen.add(eid)
        tip = _tooltip_plain(ev)
        nodes.append(
            {
                "id": eid,
                "label": _short_label(ev),
                "title": tip,
                "color": _NODE_COLOR,
                "font": {
                    "size": 11,
                    "face": "Microsoft YaHei, sans-serif",
                    "multi": True,
                    "color": _NODE_FONT,
                },
            }
        )

    edges: list[dict[str, Any]] = []
    for r in relations:
        f0, t0 = r.get("from"), r.get("to")
        if not f0 or not t0:
            continue
        f, t = str(f0), str(t0)
        typ = str(r.get("类型") or "")
        desc = str(r.get("描述") or "").replace("\r\n", "\n")[:400]
        ec = _rel_color(typ)
        edges.append(
            {
                "from": f,
                "to": t,
                "label": typ,
                "title": typ + ("\n" + desc if desc else ""),
                "color": {"color": ec, "highlight": ec},
                "font": {"size": 9, "color": ec, "strokeWidth": 0},
            }
        )
        for x in (f, t):
            if x not in seen:
                seen.add(x)
                nodes.append(
                    {
                        "id": x,
                        "label": x,
                        "title": x,
                        "color": _NODE_COLOR,
                        "font": {
                            "size": 10,
                            "color": _NODE_FONT,
                            "multi": True,
                        },
                    }
                )

    return nodes, edges


def knowledge_graph_html(project_root: Path, output_dir_rel: str) -> str | None:
    payload = build_graph_payload(project_root, output_dir_rel)
    if not payload:
        return None
    nodes, edges = payload
    if not nodes:
        return None

    nodes_j = json.dumps(nodes, ensure_ascii=False)
    edges_j = json.dumps(edges, ensure_ascii=False)

    kg_bg_css = f"#kg{{background:{_CANVAS_BG};}}"
    body_bg = f"background:{_CANVAS_BG};"

    nodes_options_js = f"""
      shape: 'box',
      margin: 10,
      borderWidth: 1.5,
      widthConstraint: {{ maximum: {_LABEL_MAX_WIDTH} }},
      shapeProperties: {{ borderRadius: {_BOX_BORDER_RADIUS} }},
      shadow: {{ enabled: true, size: 10, x: 0, y: 2, color: 'rgba(199,112,140,0.14)' }}
"""

    return (
        "<!DOCTYPE html><html><head><meta charset='utf-8'/>"
        '<script src="https://unpkg.com/vis-network/standalone/umd/vis-network.min.js"></script>'
        "<style>"
        "html,body{margin:0;padding:0;height:100%;font-family:'Microsoft YaHei',sans-serif;}"
        f"body{{{body_bg}}}"
        + kg_bg_css
        + "div.vis-network-tooltip,div.vis-tooltip{white-space:pre-wrap!important;max-width:min(440px,90vw)!important;"
        "max-height:70vh!important;overflow:auto!important;line-height:1.5!important;font-size:13px!important;"
        "background:#fffdf6!important;border:1px solid rgba(24,48,72,0.12)!important;border-radius:10px!important;"
        "box-shadow:0 6px 20px rgba(24,48,72,0.1)!important;color:#183048!important;padding:10px 12px!important;}"
        "#kg{width:100%;height:520px;}"
        "</style></head><body>"
        "<div id='kg'></div>"
        "<script>"
        "var NODES = "
        + nodes_j
        + "; var EDGES = "
        + edges_j
        + ";"
        + """
const NODE_OPTS = {"""
        + nodes_options_js
        + """
    };
"""
        + r"""
(function () {
  var container = document.getElementById('kg');
  var nodes = new vis.DataSet(NODES);
  var edges = new vis.DataSet(EDGES);
  var options = {
    nodes: NODE_OPTS,
    edges: {
      arrows: { to: { enabled: true, scaleFactor: 0.65 } },
      width: 1.3,
      smooth: { type: 'continuous' }
    },
    physics: {
      enabled: true,
      solver: 'forceAtlas2Based',
      forceAtlas2Based: {
        theta: 0.5,
        gravitationalConstant: -55,
        centralGravity: 0.01,
        springLength: 200,
        springConstant: 0.06,
        damping: 0.58,
        avoidOverlap: 0.72
      },
      stabilization: {
        enabled: true,
        iterations: 320,
        updateInterval: 20,
        fit: true
      }
    },
    interaction: {
      hover: true,
      tooltipDelay: 120,
      navigationButtons: true,
      keyboard: true,
      zoomView: true,
      dragView: true
    }
  };
  var network = new vis.Network(container, { nodes: nodes, edges: edges }, options);
  var layoutDone = false;
  function freezeAndFit() {
    if (layoutDone) return;
    layoutDone = true;
    network.setOptions({ physics: false });
    network.fit({ padding: 40, animation: { duration: 450, easingFunction: 'easeOutQuad' } });
  }
  network.once('stabilizationIterationsDone', freezeAndFit);
  setTimeout(freezeAndFit, 3500);
})();
</script></body></html>"""
    )


def render_in_streamlit(st, project_root: Path, output_dir_rel: str) -> None:
    try:
        import streamlit.components.v1 as components
    except ImportError:
        st.warning("无法加载 streamlit.components。")
        return

    html = knowledge_graph_html(project_root, output_dir_rel)
    if not html:
        st.info("未找到 `extract_chain.json` 或事件为空，无法绘制图谱。")
        return
    components.html(html, height=540, scrolling=False)
