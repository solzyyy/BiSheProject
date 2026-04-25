"""
事件提取主程序 🚀

从文本文件中提取事件和关系，并保存为 JSON 格式。
"""

from __future__ import annotations

import json
import os
from collections.abc import Callable
from pathlib import Path
from typing import Any

from rich import print

from event_graph.extractors.event_extractor import process_text


def run_extract(
    input_path: Path,
    *,
    chunk_size: int = 800,
    chunk_overlap: int = 160,
    max_chunks: int | None = None,
    out_dir: Path | None = None,
    progress_callback: Callable[[int, int], None] | None = None,
) -> tuple[dict[str, Any], list[str]]:
    """
    从文件读取文本并抽取事件，写入 out_dir/extract_chain.json。

    progress_callback: 每完成一个分块调用一次 (已完成数, 总分块数)。
    """
    out_dir = out_dir or Path("out")
    if not input_path.exists():
        raise FileNotFoundError(str(input_path))

    text = input_path.read_text(encoding="utf-8")
    print(
        f"[cyan]extract-events 参数[/cyan] chunk_size={chunk_size}, "
        f"chunk_overlap={chunk_overlap}, max_chunks={max_chunks}"
    )
    chained, errors = process_text(
        text,
        chunk_size=chunk_size,
        chunk_overlap=chunk_overlap,
        max_chunks=max_chunks,
        progress_callback=progress_callback,
    )

    out_dir.mkdir(parents=True, exist_ok=True)
    chain_path = out_dir / "extract_chain.json"
    chain_path.write_text(
        json.dumps(chained, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    if errors:
        (out_dir / "extract_errors.log").write_text("\n".join(errors), encoding="utf-8")
        print(f"[yellow]Completed with {len(errors)} chunk errors[/yellow]")
    print(f"[green]Saved[/green] {chain_path}")
    return chained, errors


def main() -> None:
    input_path = Path(os.getenv("INPUT_TEXT_PATH", "王佛脱险记.txt"))
    chunk_size = int(os.getenv("EXTRACT_CHUNK_SIZE", "800"))
    chunk_overlap = int(os.getenv("EXTRACT_CHUNK_OVERLAP", "160"))
    max_chunks_raw = os.getenv("EXTRACT_MAX_CHUNKS", "")
    max_chunks = int(max_chunks_raw) if max_chunks_raw.strip() else None

    if not input_path.exists():
        print(f"[yellow]文件不存在: {input_path}[/yellow]")
        print("[yellow]请设置 INPUT_TEXT_PATH 环境变量或确保文件存在[/yellow]")
        return

    run_extract(
        input_path,
        chunk_size=chunk_size,
        chunk_overlap=chunk_overlap,
        max_chunks=max_chunks,
        out_dir=Path("out"),
    )


if __name__ == "__main__":
    main()
