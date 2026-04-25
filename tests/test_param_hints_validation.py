"""param_hints：extract-events chunk_size / chunk_overlap 耦合校验。"""

from __future__ import annotations

import pytest

from scripts.ui.param_hints import (
    parse_int_from_preset_label,
    validate_extract_events_chunk_pair,
)


def test_parse_int_from_preset_label() -> None:
    assert parse_int_from_preset_label("160 (默认)") == 160
    assert parse_int_from_preset_label("200 · 大重叠") == 200
    assert parse_int_from_preset_label("5 (试 5 块)") == 5


@pytest.mark.parametrize(
    "params, expect_err, expect_warn_substr",
    [
        ({}, None, None),
        ({"chunk_size": 800}, None, None),
        ({"chunk_overlap": 160}, None, None),
        ({"chunk_size": 800, "chunk_overlap": 160}, None, None),
        ({"chunk_size": 400, "chunk_overlap": 200}, None, "一半及以上"),
        ({"chunk_size": 800, "chunk_overlap": 400}, None, "一半及以上"),
        ({"chunk_size": 400, "chunk_overlap": 240}, None, "一半及以上"),
        ({"chunk_size": 800, "chunk_overlap": 800}, "必须小于 chunk_size", None),
        ({"chunk_size": 800, "chunk_overlap": 900}, "必须小于 chunk_size", None),
        ({"chunk_size": 0, "chunk_overlap": 10}, "必须为正整数", None),
        ({"chunk_size": 100, "chunk_overlap": -1}, "不能为负数", None),
    ],
)
def test_validate_extract_events_chunk_pair(
    params: dict,
    expect_err: str | None,
    expect_warn_substr: str | None,
) -> None:
    err, warn = validate_extract_events_chunk_pair(params)
    if expect_err is None:
        assert err is None
    else:
        assert err is not None
        assert expect_err in err
    if expect_warn_substr is None:
        assert warn is None
    else:
        assert warn is not None
        assert expect_warn_substr in warn
