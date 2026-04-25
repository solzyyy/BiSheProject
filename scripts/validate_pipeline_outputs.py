"""
答辩前最小验证脚本。

用途：
1. 校验最终 Ren'Py 游戏工程是否完整可展示。
2. 校验完整流水线关键产物是否存在且结构基本正确。
3. 提供统一的命令行出口，便于 README、答辩检查单和 CLI 调用。
"""

from __future__ import annotations

import argparse
import json
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable


@dataclass
class CheckResult:
    name: str
    passed: bool
    detail: str


def load_json(path: Path):
    with path.open("r", encoding="utf-8") as f:
        return json.load(f)


def require_file(path: Path, description: str) -> CheckResult:
    if not path.is_file():
        return CheckResult(description, False, f"缺少文件: {path}")
    return CheckResult(description, True, f"文件存在: {path}")


def require_dir(path: Path, description: str) -> CheckResult:
    if not path.is_dir():
        return CheckResult(description, False, f"缺少目录: {path}")
    return CheckResult(description, True, f"目录存在: {path}")


def validate_entities(entities_path: Path) -> list[CheckResult]:
    results: list[CheckResult] = [require_file(entities_path, "实体文件存在")]
    if not entities_path.is_file():
        return results

    try:
        entities = load_json(entities_path)
    except Exception as exc:  # pragma: no cover - defensive
        return results + [CheckResult("实体 JSON 可解析", False, str(exc))]

    if not isinstance(entities, list) or not entities:
        return results + [CheckResult("实体列表非空", False, "entities.json 应为非空数组")]

    canonical_names: set[str] = set()
    alias_to_owner: dict[str, str] = {}
    duplicate_aliases: list[str] = []
    missing_fields = 0

    for entity in entities:
        if not isinstance(entity, dict):
            missing_fields += 1
            continue

        canonical_name = entity.get("canonical_name")
        entity_id = entity.get("id")
        aliases = entity.get("aliases", [])

        if not canonical_name or not entity_id or not isinstance(aliases, list):
            missing_fields += 1
            continue

        canonical_names.add(canonical_name)
        for alias in aliases:
            if alias in alias_to_owner and alias_to_owner[alias] != canonical_name:
                duplicate_aliases.append(alias)
            alias_to_owner[alias] = canonical_name

    results.append(
        CheckResult(
            "实体字段完整",
            missing_fields == 0,
            "所有实体均包含 id、canonical_name、aliases"
            if missing_fields == 0
            else f"存在 {missing_fields} 条字段不完整的实体记录",
        )
    )
    results.append(
        CheckResult(
            "实体规范名唯一",
            len(canonical_names) == len(entities),
            f"规范名数量: {len(canonical_names)} / 实体数量: {len(entities)}",
        )
    )
    results.append(
        CheckResult(
            "实体别名无跨实体冲突",
            not duplicate_aliases,
            "未发现跨实体重复别名"
            if not duplicate_aliases
            else f"冲突别名: {sorted(set(duplicate_aliases))}",
        )
    )
    return results


def validate_extract_chain(extract_chain_path: Path) -> list[CheckResult]:
    results: list[CheckResult] = [require_file(extract_chain_path, "事件链文件存在")]
    if not extract_chain_path.is_file():
        return results

    try:
        data = load_json(extract_chain_path)
    except Exception as exc:
        return results + [CheckResult("事件链 JSON 可解析", False, str(exc))]

    if not isinstance(data, list) or not data:
        return results + [CheckResult("事件链非空", False, "extract_chain.json 应为非空数组")]

    missing_event_id = 0
    for item in data:
        if not isinstance(item, dict):
            missing_event_id += 1
            continue
        if not (item.get("event_id") or item.get("id")):
            missing_event_id += 1

    results.append(CheckResult("事件链非空", True, f"共 {len(data)} 条事件记录"))
    results.append(
        CheckResult(
            "事件记录包含事件 ID",
            missing_event_id == 0,
            "所有事件均有 event_id/id" if missing_event_id == 0 else f"缺失 {missing_event_id} 条",
        )
    )
    return results


def validate_json_file(path: Path, description: str, expected_types: tuple[type, ...]) -> list[CheckResult]:
    results = [require_file(path, f"{description}存在")]
    if not path.is_file():
        return results
    try:
        data = load_json(path)
    except Exception as exc:
        return results + [CheckResult(f"{description}可解析", False, str(exc))]

    results.append(
        CheckResult(
            f"{description}结构类型正确",
            isinstance(data, expected_types),
            f"类型: {type(data).__name__}",
        )
    )
    return results


def validate_json_dir(path: Path, description: str) -> list[CheckResult]:
    results = [require_dir(path, f"{description}存在")]
    if not path.is_dir():
        return results
    json_files = sorted(path.glob("*.json"))
    results.append(
        CheckResult(
            f"{description}包含 JSON",
            bool(json_files),
            f"JSON 文件数量: {len(json_files)}",
        )
    )
    parse_errors: list[str] = []
    for json_file in json_files:
        try:
            load_json(json_file)
        except Exception as exc:
            parse_errors.append(f"{json_file.name}: {exc}")
    results.append(
        CheckResult(
            f"{description}中的 JSON 可解析",
            not parse_errors,
            "全部可解析" if not parse_errors else "; ".join(parse_errors[:5]),
        )
    )
    return results


def validate_game_dir(project_root: Path) -> list[CheckResult]:
    game_dir = project_root / "wangfo" / "game"
    paths_dir = game_dir / "paths"
    endings_dir = game_dir / "endings"
    script_path = game_dir / "script.rpy"
    characters_path = game_dir / "characters.rpy"

    results: list[CheckResult] = [
        require_dir(game_dir, "Ren'Py 游戏目录存在"),
        require_file(script_path, "Ren'Py 主脚本存在"),
        require_file(characters_path, "角色定义脚本存在"),
        require_dir(paths_dir, "路径脚本目录存在"),
        require_dir(endings_dir, "结局脚本目录存在"),
    ]

    if script_path.is_file():
        content = script_path.read_text(encoding="utf-8")
        results.append(
            CheckResult(
                "主脚本包含 start 入口",
                "label start:" in content,
                "已检测到 label start",
            )
        )
        results.append(
            CheckResult(
                "主脚本跳转主线路径",
                "jump canonical_path" in content,
                "已检测到 jump canonical_path",
            )
        )

    if paths_dir.is_dir():
        path_files = sorted(paths_dir.glob("*.rpy"))
        results.append(CheckResult("路径脚本数量充足", len(path_files) >= 1, f"路径脚本: {len(path_files)}"))

    if endings_dir.is_dir():
        ending_files = sorted(endings_dir.glob("*.rpy"))
        results.append(CheckResult("结局脚本数量充足", len(ending_files) >= 1, f"结局脚本: {len(ending_files)}"))

    if characters_path.is_file():
        define_count = sum(1 for line in characters_path.read_text(encoding="utf-8").splitlines() if line.startswith("define "))
        results.append(CheckResult("角色定义已生成", define_count >= 1, f"角色定义数: {define_count}"))

    return results


def validate_pipeline(project_root: Path) -> list[CheckResult]:
    out_dir = project_root / "out"
    results: list[CheckResult] = [require_dir(out_dir, "out 目录存在")]
    if not out_dir.is_dir():
        return results

    results.extend(validate_extract_chain(out_dir / "extract_chain.json"))
    results.extend(validate_entities(out_dir / "entities.json"))
    results.extend(validate_json_file(out_dir / "state_baseline.json", "状态基线文件", (dict, list)))
    results.extend(validate_json_file(out_dir / "canonical_branch.json", "主线文件", (dict, list)))
    results.extend(validate_json_file(out_dir / "decision_points_analysis.json", "决策点分析文件", (dict, list)))
    results.extend(validate_json_file(out_dir / "branches.json", "分支文件", (list, dict)))
    results.extend(validate_json_dir(out_dir / "all_paths_completed", "补全路径目录"))
    results.extend(validate_json_dir(out_dir / "enhanced_paths", "增强路径目录"))
    return results


def print_results(results: Iterable[CheckResult]) -> int:
    failures = 0
    for result in results:
        status = "PASS" if result.passed else "FAIL"
        print(f"[{status}] {result.name}: {result.detail}")
        if not result.passed:
            failures += 1
    return failures


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="验证答辩演示所需的关键产物")
    parser.add_argument(
        "--mode",
        choices=("game", "pipeline", "all"),
        default="all",
        help="验证范围：仅游戏工程、仅流水线产物、或全部",
    )
    parser.add_argument(
        "--project-root",
        default=str(Path(__file__).resolve().parent.parent),
        help="项目根目录",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    project_root = Path(args.project_root).resolve()

    all_results: list[CheckResult] = []
    if args.mode in {"game", "all"}:
        all_results.extend(validate_game_dir(project_root))
    if args.mode in {"pipeline", "all"}:
        all_results.extend(validate_pipeline(project_root))

    failures = print_results(all_results)
    if failures:
        print(f"\n验证完成：存在 {failures} 项未通过。")
        return 1

    print("\n验证完成：全部通过。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
