"""
收集毕设答辩和论文所需的静态统计信息。

该脚本不依赖 LLM 或 Neo4j，可直接基于仓库当前状态收集：
- 原始文本规模
- Python 工程规模
- Ren'Py 游戏规模
- 路径、结局、角色、背景资源数量
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path


def count_lines(path: Path) -> int:
    return len(path.read_text(encoding="utf-8", errors="ignore").splitlines())


def count_chars(path: Path) -> int:
    text = path.read_text(encoding="utf-8", errors="ignore")
    return len(text.replace("\n", "").replace("\r", ""))


def collect_metrics(project_root: Path) -> dict:
    excluded_dirs = {"venv", ".venv", "__pycache__", ".git", ".cursor", "node_modules"}
    story_path = project_root / "王佛脱险记.txt"
    python_files = sorted(
        path
        for path in project_root.rglob("*.py")
        if not any(part in excluded_dirs for part in path.parts)
    )
    renpy_files = sorted((project_root / "wangfo" / "game").rglob("*.rpy"))
    path_files = sorted((project_root / "wangfo" / "game" / "paths").glob("*.rpy"))
    ending_files = sorted((project_root / "wangfo" / "game" / "endings").glob("*.rpy"))
    characters_path = project_root / "wangfo" / "game" / "characters.rpy"
    images_doubao_path = project_root / "wangfo" / "game" / "images_doubao.rpy"
    canonical_path = project_root / "wangfo" / "game" / "paths" / "canonical_path.rpy"

    metrics = {
        "text": {
            "source_file": str(story_path.relative_to(project_root)),
            "line_count": count_lines(story_path) if story_path.exists() else None,
            "char_count": count_chars(story_path) if story_path.exists() else None,
        },
        "codebase": {
            "python_file_count": len(python_files),
            "python_line_count": sum(count_lines(path) for path in python_files),
        },
        "game": {
            "renpy_file_count": len(renpy_files),
            "renpy_line_count": sum(count_lines(path) for path in renpy_files),
            "path_script_count": len(path_files),
            "ending_script_count": len(ending_files),
            "character_definition_count": 0,
            "background_image_count": 0,
            "canonical_path_line_count": count_lines(canonical_path) if canonical_path.exists() else None,
            "canonical_event_count": 0,
        },
        "paths": {
            "path_files": [path.name for path in path_files],
            "ending_files": [path.name for path in ending_files],
        },
    }

    if characters_path.exists():
        metrics["game"]["character_definition_count"] = sum(
            1 for line in characters_path.read_text(encoding="utf-8").splitlines() if line.startswith("define ")
        )

    if images_doubao_path.exists():
        metrics["game"]["background_image_count"] = sum(
            1 for line in images_doubao_path.read_text(encoding="utf-8").splitlines() if line.startswith("image bg_")
        )

    if canonical_path.exists():
        metrics["game"]["canonical_event_count"] = sum(
            1 for line in canonical_path.read_text(encoding="utf-8").splitlines() if line.startswith("    # 事件 E")
        )

    out_dir = project_root / "out"
    if out_dir.is_dir():
        optional_outputs = {
            "extract_chain_exists": (out_dir / "extract_chain.json").exists(),
            "entities_exists": (out_dir / "entities.json").exists(),
            "canonical_branch_exists": (out_dir / "canonical_branch.json").exists(),
            "branches_exists": (out_dir / "branches.json").exists(),
            "all_paths_completed_exists": (out_dir / "all_paths_completed").exists(),
            "enhanced_paths_exists": (out_dir / "enhanced_paths").exists(),
        }
        metrics["pipeline_outputs"] = optional_outputs

    return metrics


def metrics_to_markdown(metrics: dict) -> str:
    text = metrics["text"]
    codebase = metrics["codebase"]
    game = metrics["game"]

    return "\n".join(
        [
            "# 答辩实验结果",
            "",
            "> 本文件由 `scripts/collect_project_metrics.py` 的统计口径整理而成，用于 README、答辩材料和论文第 5 章统一引用。",
            "",
            "## 一、当前工程统计",
            "",
            "| 指标 | 数值 |",
            "| --- | --- |",
            f"| 原始文本路径 | `{text['source_file']}` |",
            f"| 原始文本行数 | {text['line_count']} |",
            f"| 原始文本字数（去换行） | {text['char_count']} |",
            f"| Python 文件数 | {codebase['python_file_count']} |",
            f"| Python 代码总行数 | {codebase['python_line_count']} |",
            f"| Ren'Py 脚本文件数 | {game['renpy_file_count']} |",
            f"| Ren'Py 脚本总行数 | {game['renpy_line_count']} |",
            f"| 主线路径事件数 | {game['canonical_event_count']} |",
            f"| 路径脚本数 | {game['path_script_count']} |",
            f"| 结局脚本数 | {game['ending_script_count']} |",
            f"| 角色定义数 | {game['character_definition_count']} |",
            f"| 场景背景图数 | {game['background_image_count']} |",
            f"| `canonical_path.rpy` 行数 | {game['canonical_path_line_count']} |",
            "",
            "## 二、当前可展示的最终游戏产物",
            "",
            f"- 路径脚本：{', '.join(metrics['paths']['path_files'])}",
            f"- 结局脚本：{', '.join(metrics['paths']['ending_files'])}",
            "",
            "## 三、使用建议",
            "",
            "- 论文中涉及“工程规模”与“最终游戏规模”的描述，优先引用本文件中的统计值。",
            "- 若重新生成了 `wangfo/game/` 或 `out/` 产物，请重新运行统计脚本并同步更新论文与 README。",
        ]
    ) + "\n"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="收集项目静态统计信息")
    parser.add_argument(
        "--project-root",
        default=str(Path(__file__).resolve().parent.parent),
        help="项目根目录",
    )
    parser.add_argument(
        "--write-json",
        default="",
        help="可选：将统计结果写入 JSON 文件",
    )
    parser.add_argument(
        "--write-markdown",
        default="",
        help="可选：将统计结果写入 Markdown 文件",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    project_root = Path(args.project_root).resolve()
    metrics = collect_metrics(project_root)

    print(json.dumps(metrics, ensure_ascii=False, indent=2))

    if args.write_json:
        json_path = Path(args.write_json)
        json_path.parent.mkdir(parents=True, exist_ok=True)
        json_path.write_text(json.dumps(metrics, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    if args.write_markdown:
        markdown_path = Path(args.write_markdown)
        markdown_path.parent.mkdir(parents=True, exist_ok=True)
        markdown_path.write_text(metrics_to_markdown(metrics), encoding="utf-8")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
