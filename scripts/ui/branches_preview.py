from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from scripts.ui import utils


def _coerce_branch_list(data: Any) -> list[dict[str, Any]]:
    if isinstance(data, list):
        return [x for x in data if isinstance(x, dict)]
    if isinstance(data, dict):
        inner = data.get("branches")
        if isinstance(inner, list):
            return [x for x in inner if isinstance(x, dict)]
    return []


def _truncate(s: str, max_len: int = 120) -> str:
    s = (s or "").strip()
    if len(s) <= max_len:
        return s
    return s[: max_len - 1] + "…"


def render_in_streamlit(
    st,
    json_path: Path,
    *,
    project_root: Path | None = None,
) -> None:
    _ = project_root

    if not json_path.is_file():
        st.warning(f"找不到 branches 文件：{json_path}")
        return

    try:
        raw = utils.load_json(json_path)
    except Exception as exc:
        st.error(f"加载 JSON 失败：{exc}")
        return

    branches = _coerce_branch_list(raw)

    with st.expander("这条数据是什么？", expanded=False):
        st.markdown(
            """
`branches.json` 是 **JSON 数组**：每个元素是一条「在某决策点不走主线、而走替代选择」的支线记录。

典型字段含义：
- **fork_event_id**：在哪个事件处分叉
- **canonical_choice**：主线当时选中的选项 id
- **branch_choice**：本支线采用的替代选项 id
- **description / reasoning**：对替代选择的叙述与理由（叙述人称与 generate-branch 阶段一致）
- **generated_state_changes**：若走这条支线，事件处会产生哪些状态变化（**尚未 apply**，后续路径生成再消费）
- **is_critical_fork**：该分叉点是否落在决策分析里的「关键事件/最小骨架」上
            """.strip()
        )

    n = len(branches)
    if n == 0:
        st.info("当前 `branches.json` 为空（没有生成任何支线记录）。")
        return

    fork_ids = {str(b.get("fork_event_id") or "") for b in branches if b.get("fork_event_id")}
    fork_ids.discard("")
    critical = sum(1 for b in branches if b.get("is_critical_fork"))

    c1, c2, c3 = st.columns(3)
    c1.metric("支线条数", n)
    c2.metric("分叉事件数（去重）", len(fork_ids))
    c3.metric("标记为关键分叉的条数", critical)

    rows: list[dict[str, Any]] = []
    for b in branches:
        rows.append(
            {
                "branch_id": b.get("branch_id", ""),
                "fork_event_id": b.get("fork_event_id", ""),
                "canonical_choice": b.get("canonical_choice", ""),
                "branch_choice": b.get("branch_choice", ""),
                "critical": bool(b.get("is_critical_fork")),
                "description": _truncate(str(b.get("description") or ""), 100),
            }
        )

    st.dataframe(rows, use_container_width=True, hide_index=True)

