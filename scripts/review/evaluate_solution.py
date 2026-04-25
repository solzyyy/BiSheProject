"""
对“小说抽取 -> 文游脚本”方案做量化评估。

目标：
1. 给出可比较的子问题指标（覆盖率、交互密度、分支可玩性、资产支撑）。
2. 输出统一 JSON 报告，便于 pipeline 纳入和横向对比。
"""

from __future__ import annotations

import argparse
import json
import re
from pathlib import Path
from typing import Any


def _safe_load_json(path: Path) -> Any | None:
    if not path.is_file():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return None


def _clamp01(value: float) -> float:
    return max(0.0, min(1.0, value))


def _count_event_like_items(data: Any) -> int:
    if isinstance(data, list):
        count = 0
        for item in data:
            if isinstance(item, dict) and ("event_id" in item or "id" in item):
                count += 1
        return count or len(data)
    if isinstance(data, dict):
        for key in ("events", "event_chain", "canonical_branch"):
            value = data.get(key)
            if isinstance(value, list):
                return _count_event_like_items(value)
    return 0


def _count_decision_points(data: Any) -> int:
    if data is None:
        return 0
    if isinstance(data, dict):
        if isinstance(data.get("decision_points"), list):
            return len(data["decision_points"])
        count = 0
        for value in data.values():
            count += _count_decision_points(value)
        return count
    if isinstance(data, list):
        count = 0
        for item in data:
            if isinstance(item, dict):
                if (
                    "decision_id" in item
                    or "point_id" in item
                    or ("options" in item and isinstance(item.get("options"), list))
                ):
                    count += 1
                else:
                    count += _count_decision_points(item)
        return count
    return 0


def _count_background_defs(images_doubao_path: Path) -> int:
    if not images_doubao_path.is_file():
        return 0
    count = 0
    for line in images_doubao_path.read_text(encoding="utf-8", errors="ignore").splitlines():
        if line.strip().startswith("image bg_"):
            count += 1
    return count


def _collect_scene_mentions(paths_dir: Path) -> set[str]:
    scene_refs: set[str] = set()
    if not paths_dir.is_dir():
        return scene_refs
    pattern = re.compile(r"\bscene\s+(bg_[A-Za-z0-9_]+)")
    for path_file in paths_dir.glob("*.rpy"):
        content = path_file.read_text(encoding="utf-8", errors="ignore")
        for match in pattern.findall(content):
            scene_refs.add(match)
    return scene_refs


def _metric(value: float | int, score: float, description: str) -> dict[str, Any]:
    return {"value": value, "score": round(_clamp01(score), 4), "description": description}


def evaluate_solution(project_root: Path) -> dict[str, Any]:
    out_dir = project_root / "out"
    game_dir = project_root / "wangfo" / "game"
    paths_dir = game_dir / "paths"
    endings_dir = game_dir / "endings"

    extract_chain = _safe_load_json(out_dir / "extract_chain.json")
    canonical_branch = _safe_load_json(out_dir / "canonical_branch.json")
    decision_analysis = _safe_load_json(out_dir / "decision_points_analysis.json")

    extract_event_count = _count_event_like_items(extract_chain)
    canonical_event_count = _count_event_like_items(canonical_branch)
    decision_point_count = _count_decision_points(decision_analysis)
    path_script_count = len(list(paths_dir.glob("*.rpy"))) if paths_dir.is_dir() else 0
    ending_script_count = len(list(endings_dir.glob("*.rpy"))) if endings_dir.is_dir() else 0

    scene_refs = _collect_scene_mentions(paths_dir)
    scene_reference_count = len(scene_refs)
    background_asset_count = _count_background_defs(game_dir / "images_doubao.rpy")

    # 子问题 1：抽取是否足够支撑脚本主线
    scriptability_ratio = canonical_event_count / max(extract_event_count, 1)
    scriptability_score = _clamp01(scriptability_ratio)

    # 子问题 2：交互密度是否在可玩区间（目标约 0.15）
    decision_density = decision_point_count / max(extract_event_count, 1)
    target_density = 0.15
    decision_density_score = _clamp01(1.0 - abs(decision_density - target_density) / target_density)

    # 子问题 3：分支/结局是否覆盖决策结果
    playable_artifact_count = path_script_count + ending_script_count
    branch_support_ratio = playable_artifact_count / max(decision_point_count, 1)
    branch_support_score = _clamp01(branch_support_ratio)

    # 子问题 4：脚本场景引用是否有资产支撑
    asset_support_ratio = (
        background_asset_count / max(scene_reference_count, 1) if scene_reference_count > 0 else 1.0
    )
    asset_support_score = _clamp01(asset_support_ratio)

    weights = {
        "scriptability": 0.4,
        "decision_density": 0.2,
        "branch_support": 0.25,
        "asset_support": 0.15,
    }
    overall_score = (
        scriptability_score * weights["scriptability"]
        + decision_density_score * weights["decision_density"]
        + branch_support_score * weights["branch_support"]
        + asset_support_score * weights["asset_support"]
    )

    notes: list[str] = []
    if extract_chain is None:
        notes.append("缺少 out/extract_chain.json，抽取层评估可信度下降。")
    if canonical_branch is None:
        notes.append("缺少 out/canonical_branch.json，主线覆盖率按 0 处理。")
    if decision_analysis is None:
        notes.append("缺少 out/decision_points_analysis.json，交互密度按 0 处理。")
    if path_script_count == 0:
        notes.append("未检测到路径脚本（wangfo/game/paths/*.rpy）。")
    if ending_script_count == 0:
        notes.append("未检测到结局脚本（wangfo/game/endings/*.rpy）。")

    report = {
        "version": "1.0",
        "metrics": {
            "extract_event_count": _metric(extract_event_count, 1.0 if extract_event_count > 0 else 0.0, "抽取事件总数"),
            "canonical_event_count": _metric(
                canonical_event_count,
                scriptability_score,
                "主线事件数（用于衡量对抽取事件的覆盖）",
            ),
            "decision_point_count": _metric(
                decision_point_count,
                decision_density_score,
                "决策点数量（用于衡量交互密度）",
            ),
            "path_script_count": _metric(path_script_count, branch_support_score, "路径脚本数量"),
            "ending_script_count": _metric(ending_script_count, branch_support_score, "结局脚本数量"),
            "scene_reference_count": _metric(scene_reference_count, asset_support_score, "路径脚本中的场景引用数"),
            "background_asset_count": _metric(background_asset_count, asset_support_score, "可用背景资源数"),
            "scriptability_ratio": _metric(
                round(scriptability_ratio, 4),
                scriptability_score,
                "主线事件覆盖率 = canonical_event_count / extract_event_count",
            ),
            "decision_density": _metric(
                round(decision_density, 4),
                decision_density_score,
                "交互密度 = decision_point_count / extract_event_count",
            ),
            "branch_support_ratio": _metric(
                round(branch_support_ratio, 4),
                branch_support_score,
                "可玩产物覆盖 = (路径脚本+结局脚本) / 决策点数",
            ),
            "asset_support_ratio": _metric(
                round(asset_support_ratio, 4),
                asset_support_score,
                "场景资产支撑率 = 背景资源数 / 路径场景引用数",
            ),
        },
        "overall": {
            "score": round(_clamp01(overall_score), 4),
            "weights": weights,
            "level": "good" if overall_score >= 0.75 else ("fair" if overall_score >= 0.5 else "weak"),
            "notes": notes,
        },
    }
    return report


def report_to_markdown(report: dict[str, Any]) -> str:
    overall = report["overall"]
    rows = []
    for name, metric in report["metrics"].items():
        rows.append(f"| {name} | {metric['value']} | {metric['score']} | {metric['description']} |")

    notes_md = "\n".join(f"- {note}" for note in overall["notes"]) if overall["notes"] else "- 无"
    return "\n".join(
        [
            "# 方案量化评估报告",
            "",
            f"- 综合得分：**{overall['score']}**",
            f"- 评级：**{overall['level']}**",
            "",
            "## 指标明细",
            "",
            "| 指标 | 数值 | 分数 | 说明 |",
            "| --- | ---: | ---: | --- |",
            *rows,
            "",
            "## 备注",
            "",
            notes_md,
            "",
        ]
    )


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="评估小说抽取到文游脚本方案的量化表现")
    parser.add_argument(
        "--project-root",
        default=str(Path(__file__).resolve().parent.parent.parent),
        help="项目根目录",
    )
    parser.add_argument(
        "--write-json",
        default="out/solution_evaluation.json",
        help="评估结果 JSON 输出路径（相对项目根）",
    )
    parser.add_argument(
        "--write-markdown",
        default="",
        help="可选：评估结果 Markdown 输出路径（相对项目根）",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    project_root = Path(args.project_root).resolve()
    report = evaluate_solution(project_root)
    print(json.dumps(report, ensure_ascii=False, indent=2))

    if args.write_json:
        json_path = project_root / args.write_json
        json_path.parent.mkdir(parents=True, exist_ok=True)
        json_path.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    if args.write_markdown:
        markdown_path = project_root / args.write_markdown
        markdown_path.parent.mkdir(parents=True, exist_ok=True)
        markdown_path.write_text(report_to_markdown(report), encoding="utf-8")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
