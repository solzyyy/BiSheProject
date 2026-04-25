from __future__ import annotations

import hashlib
from pathlib import Path
from typing import Any
import traceback

from scripts.ui import utils


def _safe_get_list(d: dict[str, Any] | None, key: str) -> list[str]:
    if not d:
        return []
    v = d.get(key)
    if isinstance(v, list):
        return [str(x) for x in v if x is not None]
    return []


def _safe_get_dict(d: dict[str, Any] | None, key: str) -> dict[str, Any]:
    if not d:
        return {}
    v = d.get(key)
    if isinstance(v, dict):
        return v
    return {}


def _category_display(cat: Any) -> str:
    """首轮分类：JSON 里常用 branch/skip，界面显示中文。"""
    s = str(cat).strip().lower()
    if s == "branch":
        return "分叉点"
    if s == "skip":
        return "建议跳过"
    return str(cat).strip() if cat is not None and str(cat).strip() else ""


def _state_key_for_path(json_path: Path, suffix: str) -> str:
    h = hashlib.md5(str(json_path.resolve()).encode("utf-8")).hexdigest()[:16]
    return f"dp_preview_{h}_{suffix}"


def render_in_streamlit(
    st_obj,
    json_path: Path,
    *,
    project_root: Path | None = None,
    editable_branch_points: bool = False,
) -> None:
    """
    渲染 decision_points_analysis.json（分支候选 / 生效分叉点等）📌

    期望结构（新分析会多写字段；旧文件仍兼容）：
    {
      "critical_events": [...],
      "branch_point_candidates": [...],
      "recommended_branch_points": [...],
      "recommendation_reasoning": { "<event_id>": "二轮说明" },
      "branch_points": [...],
      "branch_points_user_overridden": false,
      "analysis": { ... }
    }
    """
    if not json_path.is_file():
        st_obj.warning(f"找不到分析结果文件：{json_path}")
        return

    try:
        data = utils.load_json(json_path)
    except Exception as exc:
        st_obj.error(f"加载 JSON 失败：{exc}")
        return

    critical_events = _safe_get_list(data, "critical_events")
    branch_points = _safe_get_list(data, "branch_points")
    candidates = _safe_get_list(data, "branch_point_candidates")
    recommended = _safe_get_list(data, "recommended_branch_points")
    rec_reason = _safe_get_dict(data, "recommendation_reasoning")
    analysis = _safe_get_dict(data, "analysis")

    if not candidates and branch_points:
        candidates = list(branch_points)
    if not recommended and branch_points:
        recommended = list(branch_points)

    # 多选 / 保存 等控件 key 必须**仅随分析文件路径**稳定，不能在每次 rerun 时自增序号；
    # 否则 Streamlit 会当成新 widget，init 会反复执行，用户在下拉栏的勾选无法保留。


    col_a, col_b, col_c, col_d = st_obj.columns(4)
    col_a.metric("关键事件数（最小骨架）", len(critical_events))
    col_b.metric("分支候选（首轮）", len(candidates) if candidates else len(branch_points))
    col_c.metric("模型推荐启用", len(recommended))
    col_d.metric("当前生效分叉点", len(branch_points))

    with st_obj.expander("路径怎么理解？", expanded=False):
        st_obj.markdown(
            """
这一步会先找出故事的**最小骨架**（一串最关键的事件），再在骨架之间标出**哪里适合让玩家做选择**。

可以把路径想成：

```text
关键事件：E1 → E2 → E3 → E4 …
            ↑       ↑
        可分叉   下一个关键事件（常作为合流参考）

生效分叉点：允许从这里长出支线选项
建议跳过：这里分叉意义不大，一般不推荐做选择点
```
            """.strip()
        )

    # 候选 + 推荐 + 首轮理由（全部分支候选）
    show_ids = candidates if candidates else branch_points
    if show_ids:
        st_obj.markdown("### 分支候选、推荐与是否生效")
        rows: list[dict[str, str]] = []
        bp_set = set(branch_points)
        rec_set = set(recommended)
        for eid in show_ids[:200]:
            r = analysis.get(eid, {})
            reasoning = r.get("reasoning") or ""
            confidence = r.get("confidence")
            cat = r.get("category") or ""
            conf_s = ""
            if isinstance(confidence, (int, float)):
                conf_s = f"{confidence:.2f}"
            r2 = rec_reason.get(eid, "")
            rows.append(
                {
                    "事件": eid,
                    "分类": _category_display(cat),
                    "置信度": conf_s,
                    "推荐": "是" if eid in rec_set else "",
                    "生效": "是" if eid in bp_set else "",
                    "首轮理由": (
                        (str(reasoning)[:120] + "...")
                        if reasoning and len(str(reasoning)) > 120
                        else str(reasoning)
                    ),
                    "二轮说明": (
                        (str(r2)[:120] + "...")
                        if r2 and len(str(r2)) > 120
                        else str(r2)
                    ),
                }
            )
        st_obj.table(rows)

    if editable_branch_points:
        try:
            safe_candidates = candidates if candidates is not None else []

            st_obj.divider()
            st_obj.subheader("✋ 最终分支点")

            sel_key = _state_key_for_path(json_path, "bp_multiselect")
            init_key = _state_key_for_path(json_path, "bp_inited")

            if init_key not in st_obj.session_state:
                seed = [x for x in branch_points if x in safe_candidates]
                if not seed:
                    seed = [x for x in recommended if x in safe_candidates]
                if not seed:
                    seed = list(safe_candidates)
                st_obj.session_state[sel_key] = seed
                st_obj.session_state[init_key] = True

            st_obj.multiselect(
                "勾选要启用的分支事件",
                options=safe_candidates,
                key=sel_key,
            )

            if st_obj.button(
                "保存到文件",
                type="primary",
                key=_state_key_for_path(json_path, "btn_save"),
            ):
                fresh = utils.load_json(json_path)
                picked = list(st_obj.session_state.get(sel_key) or [])
                order = {eid: i for i, eid in enumerate(safe_candidates)}
                picked_sorted = sorted(picked, key=lambda e: order.get(e, 9999))
                fresh["branch_points"] = picked_sorted
                fresh["branch_points_user_overridden"] = True
                utils.save_json(json_path, fresh)
                st_obj.success(
                    f"已按故事顺序写入 {len(picked_sorted)} 个分支点到文件。"
                )
        except Exception:
            st_obj.error("决策点交互区域崩溃（已捕获异常，避免整页消失）")
            st_obj.code(traceback.format_exc(), language="python")

