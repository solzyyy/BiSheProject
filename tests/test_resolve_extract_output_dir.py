"""resolve_extract_output_dir_for_run：仅按 run_id → runs/ui/<run_id>/out。"""

from __future__ import annotations

from pathlib import Path

from scripts.ui.utils import resolve_extract_output_dir_for_run


def test_session_run_id() -> None:
    d = resolve_extract_output_dir_for_run(
        template_data={"run_id": "from_template"},
        session_ui_run_id="my_run",
        session_selected_input_path=None,
    )
    assert d == "runs/ui/my_run/out"


def test_derive_from_selected_path(tmp_path: Path) -> None:
    p = tmp_path / "novel.txt"
    p.write_text("x", encoding="utf-8")
    d = resolve_extract_output_dir_for_run(
        template_data={"run_id": "tpl"},
        session_ui_run_id="",
        session_selected_input_path=str(p),
    )
    assert d.startswith("runs/ui/")
    assert d.endswith("/out")


def test_template_run_id_fallback() -> None:
    d = resolve_extract_output_dir_for_run(
        template_data={"run_id": "demo_wangfo"},
        session_ui_run_id="",
        session_selected_input_path=None,
    )
    assert d == "runs/ui/demo_wangfo/out"


def test_fallback_default() -> None:
    d = resolve_extract_output_dir_for_run(
        template_data={},
        session_ui_run_id="",
        session_selected_input_path=None,
    )
    assert d == "runs/ui/default/out"
