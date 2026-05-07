"""generate-all-paths 产出目录（all_paths）的 Streamlit 预览。"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from scripts.ui import utils


def render_in_streamlit(
    st,
    paths_dir: Path,
    *,
    project_root: Path | None = None,
) -> None:
    _ = project_root

    if not paths_dir.is_dir():
        st.warning(f"找不到分支路径生成输出目录：{paths_dir}")
        return

    index_path = paths_dir / "index.json"
    if index_path.is_file():
        try:
            idx: dict[str, Any] = utils.load_json(index_path)
        except Exception as exc:
            st.error(f"读取 index.json 失败：{exc}")
            idx = {}

        with st.expander("这条数据是什么？", expanded=False):
            st.markdown(
                """
`generate-all-paths` 在本目录下为每条路径写一个 JSON，并用 **`index.json`** 汇总条数、文件名与结局等摘要。

- **仅缓存未落盘**：与提前结局去重相关，该条路径未再单独写文件。
- 下一步 **`complete-all-path-events`** 会读取本目录，补全事件后写入 `all_paths_completed`。
                """.strip()
            )

        paths = idx.get("paths")
        if not isinstance(paths, list):
            paths = []

        c1, c2, c3, c4 = st.columns(4)
        c1.metric("总路径数", int(idx.get("total_paths") or len(paths) or 0))
        c2.metric("已写入文件", int(idx.get("saved_paths") or 0))
        c3.metric("缓存跳过（未写盘）", int(idx.get("from_cache_skipped") or 0))
        persp = str(idx.get("narrative_perspective") or "").strip()
        c4.metric("叙述人称", persp[:12] + "…" if len(persp) > 12 else (persp or "—"))

        rows: list[dict[str, Any]] = []
        for p in paths:
            if not isinstance(p, dict):
                continue
            rows.append(
                {
                    "路径编号": str(p.get("path_id") or ""),
                    "文件名": str(p.get("filename") or ""),
                    "事件数": p.get("events_count", ""),
                    "有结局": "是" if p.get("has_ending") else "",
                    "结局名称": str(p.get("ending_name") or ""),
                    "仅缓存未落盘": "是" if p.get("from_cache_only") else "",
                }
            )

        if rows:
            st.dataframe(rows, use_container_width=True, hide_index=True)
        else:
            st.info("index.json 中暂无路径条目。")

        json_files = sorted(
            p for p in paths_dir.glob("*.json") if p.name != "index.json"
        )
        st.caption(f"目录内路径 JSON 文件数：**{len(json_files)}**（不含 index.json）。")
        return

    # 无 index：仅按文件名罗列
    json_files = sorted(
        p for p in paths_dir.glob("*.json") if p.name != "index.json"
    )
    if not json_files:
        st.info("该目录下没有路径 JSON（也未找到 index.json）。")
        return

    st.info("未找到 index.json，仅按文件名列出。")
    rows2 = []
    for p in json_files[:200]:
        rows2.append({"文件": p.name})
    st.dataframe(rows2, use_container_width=True, hide_index=True)
    if len(json_files) > 200:
        st.caption(f"仅显示前 200 个，共 {len(json_files)} 个文件。")
