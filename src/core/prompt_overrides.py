"""
流水线提示词覆盖：子进程或进程内执行前设置环境变量
``PIPELINE_PROMPT_FILE_<SLOT>`` 为 **绝对路径**，指向 UTF-8 文本文件；
各加载点优先读该文件，否则读仓库内默认 ``prompts/*.txt``。
"""

from __future__ import annotations

import os
from contextlib import contextmanager
from pathlib import Path

ENV_PREFIX = "PIPELINE_PROMPT_FILE_"

# 与 UI / 子进程约定一致的槽位名（全大写 + 下划线）
SLOT_EXTRACT_EVENTS = "EXTRACT_EVENTS"
SLOT_RELATION_EXTRACT = "RELATION_EXTRACT"
SLOT_MENTION_EXTRACT = "MENTION_EXTRACT"
SLOT_WORLD_STATE_EXTRACT = "WORLD_STATE_EXTRACT"
SLOT_CHARACTER_STATE_EXTRACT = "CHARACTER_STATE_EXTRACT"
SLOT_RELATIONSHIP_STATE_EXTRACT = "RELATIONSHIP_STATE_EXTRACT"
SLOT_CHARACTER_INIT = "CHARACTER_INIT"
SLOT_STORY_SYSTEM = "STORY_SYSTEM"


def prompt_env_key(slot: str) -> str:
    return f"{ENV_PREFIX}{slot}"


def read_prompt_with_override(default_path: Path, slot: str) -> str:
    """若环境变量指向已存在文件则读之，否则读 ``default_path``（不存在则返回空串）。"""
    key = prompt_env_key(slot)
    override = os.environ.get(key, "").strip()
    if override:
        p = Path(override)
        if p.is_file():
            return p.read_text(encoding="utf-8")
    if default_path.is_file():
        return default_path.read_text(encoding="utf-8")
    return ""


@contextmanager
def use_prompt_path_overrides(slot_to_path: dict[str, str] | None):
    """临时设置 ``PIPELINE_PROMPT_FILE_*``，退出后恢复。"""
    if not slot_to_path:
        yield
        return
    saved: dict[str, str | None] = {}
    try:
        for slot, path in slot_to_path.items():
            k = prompt_env_key(slot)
            saved[k] = os.environ.get(k)
            os.environ[k] = path
        yield
    finally:
        for k, old in saved.items():
            if old is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = old
