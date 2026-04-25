"""
从 src.cli 的 Typer 应用反射子命令、参数与源码，供 Streamlit「CLI 参数目录」页使用。
"""
from __future__ import annotations

import inspect
import sys
from pathlib import Path
from typing import Any


def _ensure_project_root_on_path(project_root: Path) -> None:
    s = str(project_root.resolve())
    if s not in sys.path:
        sys.path.insert(0, s)


def _format_default(value: Any) -> str:
    if value is None:
        return "None"
    if value is inspect.Parameter.empty:
        return "—"
    if callable(value):
        return "(callable)"
    return repr(value)


def build_cli_catalog(project_root: Path) -> list[dict[str, Any]]:
    """
    返回按命令名排序的列表，每项包含：
    command, description, params[], source_file, source_start_line, source_code, callback_name
    """
    _ensure_project_root_on_path(project_root)
    from typer.main import get_command

    from src.cli import app

    group = get_command(app)
    catalog: list[dict[str, Any]] = []

    for name in sorted(group.commands.keys()):
        cmd = group.commands[name]
        callback = cmd.callback
        params_info: list[dict[str, Any]] = []

        for p in getattr(cmd, "params", []):
            if getattr(p, "hidden", False):
                continue
            opts = list(getattr(p, "opts", []) or [])
            # pipeline JSON 里用参数名 snake_case；builtin_steps 转成 --snake-case
            # 若 opts 与「参数名转 kebab」不一致，单独标出
            inferred_flag = f"--{p.name.replace('_', '-')}"
            primary_cli = opts[0] if opts else inferred_flag
            flag_mismatch = bool(opts) and primary_cli != inferred_flag and inferred_flag not in opts

            params_info.append(
                {
                    "name": p.name,
                    "opts": opts,
                    "primary_cli": primary_cli,
                    "inferred_pipeline_flag": inferred_flag,
                    "flag_mismatch": flag_mismatch,
                    "help": (getattr(p, "help", None) or "").strip(),
                    "default_str": _format_default(getattr(p, "default", None)),
                    "required": bool(getattr(p, "required", False)),
                }
            )

        try:
            src_lines, start_line = inspect.getsourcelines(callback)
            source_code = "".join(src_lines)
            source_file = inspect.getsourcefile(callback) or ""
        except (OSError, TypeError):
            source_code = ""
            start_line = 0
            source_file = ""

        desc = (getattr(cmd, "help", None) or "").strip()
        if not desc and callback.__doc__:
            desc = callback.__doc__.strip().split("\n")[0].strip()

        catalog.append(
            {
                "command": name,
                "callback_name": getattr(callback, "__name__", ""),
                "description": desc,
                "params": params_info,
                "source_file": str(Path(source_file).resolve()).replace("\\", "/")
                if source_file
                else "",
                "source_start_line": start_line,
                "source_code": source_code,
            }
        )

    return catalog


def get_pipeline_step_names(project_root: Path) -> frozenset[str]:
    """builtin_steps 中注册的流水线步骤名（与 configs steps 键一致）。"""
    _ensure_project_root_on_path(project_root)
    from src.pipeline.builtin_steps import BUILTIN_STEP_SPECS

    return frozenset(BUILTIN_STEP_SPECS.keys())
