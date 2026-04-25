"""
Streamlit：展示 ``character_personas.json``（静态人设列表）。
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from scripts.ui import utils as ui_utils


def resolve_character_personas_path(
    project_root: Path, step_name: str, params: dict[str, Any]
) -> Path | None:
    """单步执行后根据步骤名与表单参数解析人设 JSON 路径。"""
    rel: str | None = None
    if step_name == "aggregate-personas":
        o = params.get("output")
        if o is not None and str(o).strip():
            rel = str(o).strip()
    elif step_name == "aggregate-characters-and-personas":
        o = params.get("personas_output")
        if o is not None and str(o).strip():
            rel = str(o).strip()
    if not rel:
        return None
    return ui_utils.resolve_input_path(project_root, Path(rel))


def render_in_streamlit(st, json_path: Path) -> None:
    """读取人设 JSON 并渲染卡片式预览；文件不存在或格式错误时给出提示。"""
    if not json_path.is_file():
        st.warning(f"未找到人设文件：`{json_path}`")
        return
    try:
        data = json.loads(json_path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError) as exc:
        st.error(f"无法读取人设 JSON：{exc}")
        return
    personas = data.get("personas")
    if not isinstance(personas, list):
        st.error("人设 JSON 格式异常：顶层应为对象且含 `personas` 数组。")
        return

    def _has_meaningful_worldview(row: dict[str, Any]) -> bool:
        """有实质世界观内容才视为「有」；空串、占位「世界观未明」等同无。"""
        w = row.get("worldview")
        if w is None:
            return False
        s = str(w).strip()
        if not s:
            return False
        return s != "世界观未明"

    dict_rows = [p for p in personas if isinstance(p, dict)]
    # 有实质世界观的排在前面；组内保持 JSON 原顺序（稳定排序）
    dict_rows.sort(key=lambda r: (0 if _has_meaningful_worldview(r) else 1))

    for i, row in enumerate(dict_rows):
        name = str(row.get("name") or "—").strip()
        pid = str(row.get("id") or "").strip()
        title = name if not pid else f"{name}（{pid}）"
        with st.expander(title, expanded=i < 4):
            m1, m2 = st.columns(2)
            with m1:
                st.markdown(f"**角色** `{row.get('role') or '—'}`")
                st.markdown(f"**性情** `{row.get('disposition') or '—'}`")
            with m2:
                st.markdown(f"**社会位置** `{row.get('social_position') or '—'}`")
                st.markdown(f"**世界观** `{row.get('worldview') or '—'}`")
            summary = row.get("persona_summary")
            if summary is not None and str(summary).strip():
                st.markdown("**人设摘要**")
                st.markdown(str(summary).strip())
