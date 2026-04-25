from __future__ import annotations

import json
from pathlib import Path
from typing import Any

def _fmt_list(v: Any, limit: int = 40) -> str:
    if not isinstance(v, list):
        return ""
    s = ", ".join(str(x) for x in v[:limit])
    if len(v) > limit:
        s += ", …"
    return s


def _coerce_path_results(data: dict[str, Any]) -> list[dict[str, Any]]:
    pr = data.get("path_results", [])
    if isinstance(pr, list):
        return [x for x in pr if isinstance(x, dict)]
    return []


def _load_json_any(path: Path) -> Any:
    """允许 JSON 顶层为 dict 或 list。"""
    return json.loads(path.read_text(encoding="utf-8"))


def _coerce_to_dict(data: Any) -> dict[str, Any]:
    """
    ending_candidates.json 旧版可能直接写入 path_results 列表。
    这里统一成 dict 结构，方便预览逻辑复用。
    """
    if isinstance(data, dict):
        return data
    if isinstance(data, list):
        return {"path_results": data}
    return {}


def _group_branches_by_fork(branches: list[Any]) -> dict[str, list[dict[str, Any]]]:
    grouped: dict[str, list[dict[str, Any]]] = {}
    for b in branches:
        if not isinstance(b, dict):
            continue
        fork = str(b.get("fork_event_id") or "").strip()
        if not fork:
            continue
        grouped.setdefault(fork, []).append(b)
    return grouped


def render_in_streamlit(
    st,
    json_path: Path,
    *,
    project_root: Path | None = None,
) -> None:
    _ = project_root

    if not json_path.is_file():
        st.warning(f"找不到结局候选文件：{json_path}")
        return

    try:
        raw_any = _load_json_any(json_path)
        data = _coerce_to_dict(raw_any)
    except Exception as exc:
        st.error(f"加载文件失败：{exc}")
        return

    default_endings = data.get("default_endings", [])
    if not isinstance(default_endings, list):
        default_endings = []

    path_results = _coerce_path_results(data)
    total_paths = int(data.get("total_paths") or len(path_results) or 0)
    canonical_count = int(data.get("canonical_path_count") or 0)

    col1, col2, col3 = st.columns(3)
    col1.metric("默认结局池条目数", len(default_endings))
    col2.metric("路径组合总数", total_paths)
    col3.metric("其中主线组合数", canonical_count)

    with st.expander("核心逻辑：从倾向到候选结局（可选阅读）", expanded=False):
        st.markdown(
            """
这一步把**每一种路径组合**当成一道筛选题，用两套标签去匹配结局：

1. 先得到**默认结局池**（含主线真结局等兜底项）。  
2. 对每条路径组合：  
   - 汇总**倾向标签**（情绪、成长、关系等走向）  
   - 汇总**冲突标签**（不宜同时成立的方向）  
3. 用倾向 + 冲突去过滤候选：  
   - 吻合的保留为候选；若为空，会从非主线结局里**兜底补一条**。  
4. 结果里**每条路径组合**对应一组候选结局及说明。
            """.strip()
        )

    if not path_results:
        st.info("文件中没有路径结果列表，无法展示明细。")
        return

    base_dir = json_path.parent
    branches_path = base_dir / "branches.json"
    if branches_path.is_file():
        try:
            branches_any = _load_json_any(branches_path)
            branches_raw = branches_any if isinstance(branches_any, list) else []
            branches = [x for x in branches_raw if isinstance(x, dict)]
        except Exception:
            branches = []
        if branches:
            grouped = _group_branches_by_fork(branches)
            fork_ids = sorted(
                grouped.keys(),
                key=lambda x: int("".join([c for c in x if c.isdigit()]) or 0),
            )
            with st.expander("分叉点与组合数怎么算？", expanded=False):
                st.caption(
                    "根据同目录下的支线记录：按**分叉事件**分组；"
                    "每个分叉点 = 1 条主线走向 + N 条分支走向，"
                    "全部组合数 = 各点 **(1+N)** 连乘。"
                )
                rows = []
                combo = 1
                for fid in fork_ids:
                    n_branch = len(grouped.get(fid) or [])
                    combo *= 1 + n_branch
                    rows.append(
                        {
                            "分叉事件": fid,
                            "分支条数N": str(n_branch),
                            "该点选项数(1+N)": str(1 + n_branch),
                            "累计组合数": str(combo),
                            "分支选项编号": _fmt_list(
                                [b.get("branch_choice") for b in (grouped.get(fid) or [])],
                                limit=10,
                            ),
                        }
                    )
                st.table(rows)
                st.write(
                    f"按支线文件推算的组合数：**{combo}**；"
                    f"本结局文件中的路径总数：**{total_paths}**"
                )

    show_limit = 30
    display_pr = path_results[:show_limit]
    path_options = [
        f"{(i + 1)}：{x.get('path_id', '') or '（未命名）'}"
        for i, x in enumerate(display_pr)
    ]
    default_idx = 0
    if len(path_results) > show_limit:
        st.caption(
            f"下方下拉仅含前 **{show_limit}** 条路径组合；"
            f"文件中共有 **{len(path_results)}** 条。"
        )

    choice_label = st.selectbox(
        "选择一条路径组合查看候选结局",
        options=path_options,
        index=default_idx,
    )
    chosen_idx = path_options.index(choice_label)
    chosen = display_pr[chosen_idx]

    st.markdown("### 本条路径的筛选结果")

    st.markdown("#### 倾向与冲突")
    st.write(f"倾向标签：{_fmt_list(chosen.get('tendency_tags'), limit=60)}")
    st.write(f"冲突标签：{_fmt_list(chosen.get('conflict_tags'), limit=60)}")
    st.write(f"偏离程度：{chosen.get('deviation_level', '')}")
    st.write(f"合并压力：{chosen.get('merge_pressure', '')}")
    trig = chosen.get("is_immediate_trigger", False)
    st.write(f"是否即时触发：{'是' if trig else '否'}")

    candidate_details = chosen.get("candidate_details", [])
    if not isinstance(candidate_details, list):
        candidate_details = []

    if candidate_details:
        st.markdown("#### 候选结局明细")
        rows: list[dict[str, str]] = []
        for cd in candidate_details[:30]:
            if not isinstance(cd, dict):
                continue
            rows.append(
                {
                    "编号": str(cd.get("id", "")),
                    "名称": str(cd.get("name", "")),
                    "主线结局": "是" if cd.get("is_canonical") else "",
                    "提前结局": "是" if cd.get("is_premature") else "",
                    "标签": _fmt_list(cd.get("tags"), limit=30),
                    "冲突标签": _fmt_list(cd.get("conflict_tags"), limit=30),
                    "摘要": str(cd.get("description", ""))[:120],
                }
            )
        st.table(rows)
    else:
        st.info("本条路径没有候选结局明细（可能只写了结局编号列表）。")
