"""
Streamlit / 流水线 UI：各步骤与 ``prompts/*.txt`` 槽位对应关系，以及「仅源码拼接」提示说明。

槽位名与 ``src.core.prompt_overrides`` 中常量一致；环境变量为
``PIPELINE_PROMPT_FILE_<SLOT>``（值为覆盖文件的绝对路径）。
"""

from __future__ import annotations

from dataclasses import dataclass

from src.core.prompt_overrides import (
    SLOT_CHARACTER_INIT,
    SLOT_CHARACTER_STATE_EXTRACT,
    SLOT_EXTRACT_EVENTS,
    SLOT_MENTION_EXTRACT,
    SLOT_RELATIONSHIP_STATE_EXTRACT,
    SLOT_RELATION_EXTRACT,
    SLOT_STORY_SYSTEM,
    SLOT_WORLD_STATE_EXTRACT,
)


@dataclass(frozen=True)
class PromptSlotMeta:
    slot_id: str
    rel_path: str
    label: str


# 仓库内所有「文件型」提示词槽位
PROMPT_SLOTS: dict[str, PromptSlotMeta] = {
    SLOT_EXTRACT_EVENTS: PromptSlotMeta(
        SLOT_EXTRACT_EVENTS, "prompts/extract_prompt.txt", "事件抽取（extract-events）"
    ),
    SLOT_RELATION_EXTRACT: PromptSlotMeta(
        SLOT_RELATION_EXTRACT, "prompts/relation_extract_prompt.txt", "关系抽取（extract-relations）"
    ),
    SLOT_MENTION_EXTRACT: PromptSlotMeta(
        SLOT_MENTION_EXTRACT, "prompts/mention_extract_prompt.txt", "Mention 抽取（generate-mentions）"
    ),
    SLOT_WORLD_STATE_EXTRACT: PromptSlotMeta(
        SLOT_WORLD_STATE_EXTRACT,
        "prompts/world_state_extract_prompt.txt",
        "世界/物品状态抽取",
    ),
    SLOT_CHARACTER_STATE_EXTRACT: PromptSlotMeta(
        SLOT_CHARACTER_STATE_EXTRACT,
        "prompts/character_state_extract_prompt.txt",
        "人物内在状态抽取",
    ),
    SLOT_RELATIONSHIP_STATE_EXTRACT: PromptSlotMeta(
        SLOT_RELATIONSHIP_STATE_EXTRACT,
        "prompts/relationship_state_extract_prompt.txt",
        "人物关系状态抽取",
    ),
    SLOT_CHARACTER_INIT: PromptSlotMeta(
        SLOT_CHARACTER_INIT, "prompts/character_init_prompt.txt", "人物节点初始化（init-characters）"
    ),
    SLOT_STORY_SYSTEM: PromptSlotMeta(
        SLOT_STORY_SYSTEM,
        "prompts/story_generation_system_prompt.txt",
        "叙事/去 AI 味系统预设（内容增强、路径生成等）",
    ),
}

ALL_FILE_SLOT_IDS: tuple[str, ...] = tuple(PROMPT_SLOTS.keys())

# 单步名 -> 可编辑的提示词槽（仅含文件型）
STEP_FILE_PROMPT_SLOTS: dict[str, tuple[str, ...]] = {
    "extract-events": (SLOT_EXTRACT_EVENTS,),
    "extract-relations": (SLOT_RELATION_EXTRACT,),
    "extract-events-and-relations": (SLOT_EXTRACT_EVENTS, SLOT_RELATION_EXTRACT),
    "generate-mentions": (SLOT_MENTION_EXTRACT,),
    "generate-mentions-and-entities": (SLOT_MENTION_EXTRACT,),
    "extract-world-states": (SLOT_WORLD_STATE_EXTRACT,),
    "extract-character-states": (SLOT_CHARACTER_STATE_EXTRACT,),
    "extract-relationship-states": (SLOT_RELATIONSHIP_STATE_EXTRACT,),
    "extract-states-and-baseline": (
        SLOT_WORLD_STATE_EXTRACT,
        SLOT_CHARACTER_STATE_EXTRACT,
        SLOT_RELATIONSHIP_STATE_EXTRACT,
    ),
    "init-characters": (SLOT_CHARACTER_INIT,),
    "generate-content": (SLOT_STORY_SYSTEM,),
    "generate-renpy-scripts": (SLOT_STORY_SYSTEM,),
    "generate-all-paths": (SLOT_STORY_SYSTEM,),
}

# 无独立 txt、提示词在源码中拼接的步骤（只读说明，避免误以为漏做）
STEP_INLINE_PROMPT_NOTES: dict[str, str] = {
    "generate-entities": (
        "别名确认等提示词在源码中动态拼接："
        "`src/character/normalizers/alias_mapper.py`（`build_llm_prompt`）。"
    ),
    "aggregate-characters": (
        "行动词抽取等提示词在源码中拼接："
        "`src/character/aggregators/character_aggregator.py`。"
    ),
    "aggregate-personas": (
        "静态人设多段提示在源码中拼接："
        "`src/character/aggregators/persona_aggregator.py`。"
    ),
    "aggregate-characters-and-personas": (
        "人物画像与人设两步的 LLM 提示均在源码中拼接："
        "`character_aggregator.py`、`persona_aggregator.py`。"
    ),
    "generate-canonical-branch": (
        "主线记录提示在源码中拼接："
        "`src/branch/generation/canonical_branch.py`（`_build_canonical_record_prompt`）。"
    ),
    "analyze-decision-points": (
        "决策点分析提示在源码中拼接："
        "`src/branch/analysis/decision_point_analyzer.py`。"
    ),
    "generate-branch": (
        "支线生成相关提示在源码中拼接："
        "`src/branch/generation/branch_generator.py` 等。"
    ),
    "determine-ending-candidates": (
        "结局候选分析提示在源码中拼接："
        "`src/branch/analysis/ending_candidate_determiner.py`。"
    ),
    "apply-states": (
        "状态应用使用内置 system 文案 + 动态 user prompt："
        "`src/state_manager/state_applier.py`。"
    ),
    "import-entities": "通常不涉及 LLM 提示词。",
    "import-state-changes": "通常不涉及 LLM 提示词。",
    "import-neo4j": "不涉及 LLM 提示词。",
    "init-indexes": "不涉及 LLM 提示词。",
    "init-state-baseline": "规则聚合生成 baseline，无独立提示词 txt。",
    "validate-outputs": "不涉及 LLM。",
    "evaluate-solution": "不涉及提示词文件（或见对应脚本）。",
    "collect-metrics": "不涉及 LLM。",
}

# 既有文件槽又有大量内置拼接时，附加说明
STEP_EXTRA_PROMPT_NOTES: dict[str, str] = {
    "generate-all-paths": (
        "除上方「叙事系统预设」txt 外，路径内事件生成还有大量 function calling / "
        "内置字符串提示，见 `src/branch/generation/path_generation_functions.py`。"
        "事件补全为独立步骤 `complete-all-path-events`（无独立 txt，见 `scripts/complete_all_paths_events.py`）。"
    ),
}
