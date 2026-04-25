"""
生成人工评审清单（CSV）：
1. 从现有 pipeline 产物中抽样；
2. 预填规则化“建议分”作为草案；
3. 预留人工复核列，便于答辩/论文复现。
"""

from __future__ import annotations

import argparse
import csv
import json
import random
import re
from pathlib import Path
from typing import Any


HEADER = [
    "sample_id",
    "sample_type",
    "source_file",
    "evidence",
    "excerpt",
    "scriptability_auto",
    "branch_reasonableness_auto",
    "coherence_auto",
    "character_consistency_auto",
    "feedback_strength_auto",
    "scriptability_human",
    "branch_reasonableness_human",
    "coherence_human",
    "character_consistency_human",
    "feedback_strength_human",
    "auto_note",
    "human_note",
    "reviewer",
]


def _load_json(path: Path) -> Any | None:
    if not path.is_file():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return None


def _sample_list(items: list[Any], sample_size: int, rng: random.Random) -> list[Any]:
    if sample_size <= 0:
        return []
    if len(items) <= sample_size:
        return items
    return rng.sample(items, sample_size)


def _get_event_excerpt(event: dict[str, Any]) -> str:
    for key in ("summary", "description", "event_desc", "content", "text"):
        value = event.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip().replace("\n", " ")[:80]
    return json.dumps(event, ensure_ascii=False)[:80]


def _event_auto_scores(event: dict[str, Any]) -> tuple[int, str]:
    score = 0
    note_parts: list[str] = []

    has_text = any(
        isinstance(event.get(k), str) and event.get(k).strip()
        for k in ("summary", "description", "event_desc", "content", "text")
    )
    has_chars = isinstance(event.get("characters"), list) and len(event.get("characters", [])) > 0
    has_scene = any(
        isinstance(event.get(k), str) and event.get(k).strip()
        for k in ("location", "scene", "place")
    )

    if has_text:
        score += 1
        note_parts.append("有事件文本")
    if has_chars:
        score += 1
        note_parts.append("有人物信息")
    if has_scene:
        note_parts.append("有场景信息")

    return min(score, 2), "；".join(note_parts) if note_parts else "字段信息较少，建议人工重点检查"


def _flatten_decision_points(data: Any) -> list[dict[str, Any]]:
    if data is None:
        return []
    if isinstance(data, dict):
        if isinstance(data.get("decision_points"), list):
            return [item for item in data["decision_points"] if isinstance(item, dict)]
        result: list[dict[str, Any]] = []
        for value in data.values():
            result.extend(_flatten_decision_points(value))
        return result
    if isinstance(data, list):
        result: list[dict[str, Any]] = []
        for item in data:
            if isinstance(item, dict):
                if "decision_id" in item or "point_id" in item or "options" in item:
                    result.append(item)
                else:
                    result.extend(_flatten_decision_points(item))
        return result
    return []


def _decision_auto_scores(point: dict[str, Any]) -> tuple[int, int, str]:
    options = point.get("options")
    if isinstance(options, list):
        option_count = len(options)
    else:
        option_count = 0
    if option_count >= 2:
        return 2, 1, f"选项数={option_count}（分支潜力较好）"
    if option_count == 1:
        return 1, 0, "只有 1 个选项，分支性弱"
    return 0, 0, "无有效 options 字段"


def _collect_path_samples(paths_dir: Path) -> list[dict[str, str]]:
    result: list[dict[str, str]] = []
    if not paths_dir.is_dir():
        return result

    label_pattern = re.compile(r"^\s*label\s+([A-Za-z0-9_]+)\s*:")
    for path_file in sorted(paths_dir.glob("*.rpy")):
        lines = path_file.read_text(encoding="utf-8", errors="ignore").splitlines()
        label_name = ""
        for idx, line in enumerate(lines):
            m = label_pattern.match(line)
            if m:
                label_name = m.group(1)
                snippet = " ".join(lines[idx : min(idx + 4, len(lines))]).strip()
                result.append(
                    {
                        "sample_id": f"{path_file.stem}:{label_name}",
                        "source_file": str(path_file),
                        "evidence": f"label {label_name}",
                        "excerpt": snippet[:120],
                    }
                )
                break
        if not label_name:
            snippet = " ".join(lines[:4]).strip() if lines else ""
            result.append(
                {
                    "sample_id": f"{path_file.stem}:file",
                    "source_file": str(path_file),
                    "evidence": "file_head",
                    "excerpt": snippet[:120],
                }
            )
    return result


def _path_auto_scores(excerpt: str) -> tuple[int, int, int, str]:
    text = excerpt.lower()
    has_menu = "menu:" in text
    has_jump = "jump " in text
    has_return = "return" in text
    has_scene = "scene " in text

    coherence = 2 if (has_scene and (has_jump or has_return)) else (1 if has_scene else 0)
    feedback = 2 if has_menu else (1 if has_jump else 0)
    consistency = 1 if has_scene else 0
    note = f"scene={has_scene}, menu={has_menu}, jump={has_jump}, return={has_return}"
    return coherence, consistency, feedback, note


def _make_row(
    *,
    sample_id: str,
    sample_type: str,
    source_file: str,
    evidence: str,
    excerpt: str,
    scriptability_auto: int = -1,
    branch_auto: int = -1,
    coherence_auto: int = -1,
    consistency_auto: int = -1,
    feedback_auto: int = -1,
    auto_note: str = "",
) -> dict[str, str]:
    def norm(v: int) -> str:
        return "" if v < 0 else str(v)

    return {
        "sample_id": sample_id,
        "sample_type": sample_type,
        "source_file": source_file,
        "evidence": evidence,
        "excerpt": excerpt.replace("\n", " ")[:200],
        "scriptability_auto": norm(scriptability_auto),
        "branch_reasonableness_auto": norm(branch_auto),
        "coherence_auto": norm(coherence_auto),
        "character_consistency_auto": norm(consistency_auto),
        "feedback_strength_auto": norm(feedback_auto),
        "scriptability_human": "",
        "branch_reasonableness_human": "",
        "coherence_human": "",
        "character_consistency_human": "",
        "feedback_strength_human": "",
        "auto_note": auto_note,
        "human_note": "",
        "reviewer": "",
    }


def build_manual_review_checklist(
    *,
    project_root: Path,
    output_csv: Path,
    event_sample_size: int = 20,
    decision_sample_size: int = 20,
    path_sample_size: int = 10,
    random_seed: int = 42,
) -> dict[str, int]:
    rng = random.Random(random_seed)
    out_dir = project_root / "out"
    paths_dir = project_root / "wangfo" / "game" / "paths"

    rows: list[dict[str, str]] = []

    extract_chain = _load_json(out_dir / "extract_chain.json")
    events = extract_chain if isinstance(extract_chain, list) else []
    event_items = [e for e in events if isinstance(e, dict)]
    for event in _sample_list(event_items, event_sample_size, rng):
        event_id = str(event.get("event_id") or event.get("id") or "event_unknown")
        score, note = _event_auto_scores(event)
        rows.append(
            _make_row(
                sample_id=event_id,
                sample_type="event",
                source_file=str(out_dir / "extract_chain.json"),
                evidence=f"event_id={event_id}",
                excerpt=_get_event_excerpt(event),
                scriptability_auto=score,
                auto_note=note,
            )
        )

    decision_data = _load_json(out_dir / "decision_points_analysis.json")
    decision_points = _flatten_decision_points(decision_data)
    for idx, point in enumerate(_sample_list(decision_points, decision_sample_size, rng), start=1):
        decision_id = str(point.get("decision_id") or point.get("point_id") or f"decision_{idx}")
        branch_score, feedback_score, note = _decision_auto_scores(point)
        excerpt = str(point.get("description") or point.get("summary") or json.dumps(point, ensure_ascii=False)[:80])
        rows.append(
            _make_row(
                sample_id=decision_id,
                sample_type="decision",
                source_file=str(out_dir / "decision_points_analysis.json"),
                evidence=f"decision_id={decision_id}",
                excerpt=excerpt,
                branch_auto=branch_score,
                feedback_auto=feedback_score,
                auto_note=note,
            )
        )

    path_samples = _collect_path_samples(paths_dir)
    for sample in _sample_list(path_samples, path_sample_size, rng):
        coherence, consistency, feedback, note = _path_auto_scores(sample["excerpt"])
        rows.append(
            _make_row(
                sample_id=sample["sample_id"],
                sample_type="path",
                source_file=sample["source_file"],
                evidence=sample["evidence"],
                excerpt=sample["excerpt"],
                coherence_auto=coherence,
                consistency_auto=consistency,
                feedback_auto=feedback,
                auto_note=note,
            )
        )

    output_csv.parent.mkdir(parents=True, exist_ok=True)
    with output_csv.open("w", encoding="utf-8-sig", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=HEADER)
        writer.writeheader()
        writer.writerows(rows)

    event_rows = sum(1 for row in rows if row["sample_type"] == "event")
    decision_rows = sum(1 for row in rows if row["sample_type"] == "decision")
    path_rows = sum(1 for row in rows if row["sample_type"] == "path")

    return {
        "event_rows": event_rows,
        "decision_rows": decision_rows,
        "path_rows": path_rows,
        "total_rows": len(rows),
    }


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="生成人工评审抽样清单（含建议分）")
    parser.add_argument(
        "--project-root",
        default=str(Path(__file__).resolve().parent.parent.parent),
        help="项目根目录",
    )
    parser.add_argument(
        "--output",
        default="docs/manual_review_template.csv",
        help="输出 CSV 路径（相对项目根）",
    )
    parser.add_argument("--event-sample-size", type=int, default=20, help="事件样本数")
    parser.add_argument("--decision-sample-size", type=int, default=20, help="决策点样本数")
    parser.add_argument("--path-sample-size", type=int, default=10, help="路径脚本样本数")
    parser.add_argument("--seed", type=int, default=42, help="随机种子（保证可复现）")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    project_root = Path(args.project_root).resolve()
    output_csv = project_root / args.output
    stats = build_manual_review_checklist(
        project_root=project_root,
        output_csv=output_csv,
        event_sample_size=args.event_sample_size,
        decision_sample_size=args.decision_sample_size,
        path_sample_size=args.path_sample_size,
        random_seed=args.seed,
    )
    print(
        f"清单已生成: {output_csv}\n"
        f"event={stats['event_rows']}, decision={stats['decision_rows']}, "
        f"path={stats['path_rows']}, total={stats['total_rows']}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
