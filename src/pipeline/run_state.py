from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from src.pipeline.config import PipelineConfig


def _now_iso() -> str:
    return datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds")


def _parse_iso(value: str | None) -> datetime | None:
    if not value:
        return None
    return datetime.fromisoformat(value)


def _duration_seconds(started_at: str | None, finished_at: str | None) -> float | None:
    started = _parse_iso(started_at)
    finished = _parse_iso(finished_at)
    if not started or not finished:
        return None
    return round((finished - started).total_seconds(), 3)


def create_run_state(config: PipelineConfig, metadata: dict[str, Any] | None = None) -> dict[str, Any]:
    steps: dict[str, Any] = {}
    for step_name in config.steps:
        steps[step_name] = {
            "status": "pending",
            "started_at": None,
            "finished_at": None,
            "duration_sec": None,
            "output_paths": [],
            "validation_status": None,
            "error": None,
        }

    return {
        "run_id": config.run_id,
        "input_text": config.input_text,
        "output_root": config.output_root,
        "status": "pending",
        "current_step": None,
        "started_at": _now_iso(),
        "finished_at": None,
        "metadata": metadata or {},
        "steps": steps,
    }


def save_run_state(state: dict[str, Any]) -> Path:
    output_root = Path(state["output_root"])
    output_root.mkdir(parents=True, exist_ok=True)
    state_path = output_root / "state.json"
    state_path.write_text(json.dumps(state, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return state_path


def load_run_state(path: str | Path) -> dict[str, Any]:
    state_path = Path(path)
    return json.loads(state_path.read_text(encoding="utf-8"))


def mark_step_started(state: dict[str, Any], step_name: str) -> None:
    step = state["steps"][step_name]
    step["status"] = "running"
    step["started_at"] = _now_iso()
    step["error"] = None
    state["status"] = "running"
    state["current_step"] = step_name


def mark_step_succeeded(
    state: dict[str, Any],
    step_name: str,
    output_paths: list[str] | None = None,
    validation_status: str | None = None,
) -> None:
    step = state["steps"][step_name]
    finished_at = _now_iso()
    step["status"] = "passed"
    step["finished_at"] = finished_at
    step["duration_sec"] = _duration_seconds(step.get("started_at"), finished_at)
    step["output_paths"] = output_paths or []
    step["validation_status"] = validation_status
    step["error"] = None
    state["current_step"] = None


def mark_step_failed(state: dict[str, Any], step_name: str, error: str) -> None:
    step = state["steps"][step_name]
    finished_at = _now_iso()
    step["status"] = "failed"
    step["finished_at"] = finished_at
    step["duration_sec"] = _duration_seconds(step.get("started_at"), finished_at)
    step["error"] = error
    state["status"] = "failed"
    state["current_step"] = step_name


def mark_step_skipped(state: dict[str, Any], step_name: str, reason: str | None = None) -> None:
    step = state["steps"][step_name]
    step["status"] = "skipped"
    step["started_at"] = None
    step["finished_at"] = _now_iso()
    step["duration_sec"] = None
    step["output_paths"] = []
    step["validation_status"] = None
    step["error"] = reason


def mark_run_finished(state: dict[str, Any], status: str = "passed") -> None:
    finished_at = _now_iso()
    state["status"] = status
    state["current_step"] = None
    state["finished_at"] = finished_at


def record_event(path: str | Path, payload: dict[str, Any]) -> Path:
    events_path = Path(path)
    events_path.parent.mkdir(parents=True, exist_ok=True)
    line = json.dumps({"ts": _now_iso(), **payload}, ensure_ascii=False)
    with events_path.open("a", encoding="utf-8") as f:
        f.write(line + "\n")
    return events_path
