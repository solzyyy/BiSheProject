from __future__ import annotations

import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from src.pipeline.config import PipelineConfig
from src.pipeline.runner import StepHandler, StepResult


@dataclass(frozen=True)
class BuiltinStepSpec:
    command_name: str
    default_outputs: tuple[str, ...] = ()


BUILTIN_STEP_SPECS: dict[str, BuiltinStepSpec] = {
    "extract-events": BuiltinStepSpec("extract-events", ("out/extract_chain.json",)),
    # 默认相对项目根；与 extract-events 同 run 时一般为 runs/ui/<run_id>/out/extract_relations.json
    "extract-relations": BuiltinStepSpec(
        "extract-relations", ("out/extract_relations.json",)
    ),
    # 一次跑完事件链 + 关系；CLI 与 Streamlit 单步均支持
    "extract-events-and-relations": BuiltinStepSpec(
        "extract-events-and-relations",
        ("out/extract_chain.json", "out/extract_relations.json"),
    ),
    "generate-mentions": BuiltinStepSpec("generate-mentions", ("out/mentions.json",)),
    "generate-entities": BuiltinStepSpec("generate-entities", ("out/entities.json",)),
    "generate-mentions-and-entities": BuiltinStepSpec(
        "generate-mentions-and-entities",
        ("out/mentions.json", "out/entities.json"),
    ),
    "extract-character-states": BuiltinStepSpec("extract-character-states", ("out/character_states.json",)),
    "extract-world-states": BuiltinStepSpec("extract-world-states", ("out/world_states.json",)),
    "extract-relationship-states": BuiltinStepSpec("extract-relationship-states", ("out/relationship_states.json",)),
    "init-state-baseline": BuiltinStepSpec("init-state-baseline", ("out/state_baseline.json",)),
    "extract-states-and-baseline": BuiltinStepSpec(
        "extract-states-and-baseline",
        (
            "out/world_states.json",
            "out/character_states.json",
            "out/relationship_states.json",
            "out/state_baseline.json",
        ),
    ),
    "import-neo4j": BuiltinStepSpec("import-neo4j"),
    "import-entities": BuiltinStepSpec("import-entities"),
    "import-state-changes": BuiltinStepSpec("import-state-changes"),
    "import-neo4j-all": BuiltinStepSpec("import-neo4j-all"),
    "aggregate-characters": BuiltinStepSpec("aggregate-characters", ("out/character_profiles.json",)),
    "aggregate-personas": BuiltinStepSpec("aggregate-personas", ("out/character_personas.json",)),
    "aggregate-characters-and-personas": BuiltinStepSpec(
        "aggregate-characters-and-personas",
        ("out/character_profiles.json", "out/character_personas.json"),
    ),
    "generate-canonical-branch": BuiltinStepSpec("generate-canonical-branch", ("out/canonical_branch.json",)),
    "analyze-decision-points": BuiltinStepSpec("analyze-decision-points", ("out/decision_points_analysis.json",)),
    "determine-ending-candidates": BuiltinStepSpec("determine-ending-candidates", ("out/ending_candidates.json",)),
    "generate-branch": BuiltinStepSpec("generate-branch", ("out/branches.json",)),
    "generate-all-paths": BuiltinStepSpec(
        "generate-all-paths",
        ("out/all_paths/index.json",),
    ),
    "complete-all-path-events": BuiltinStepSpec(
        "complete-all-path-events",
        ("out/all_paths_completed/index.json",),
    ),
    "generate-content": BuiltinStepSpec("generate-content", ("out/enhanced_paths",)),
    "generate-renpy-scripts": BuiltinStepSpec(
        "generate-renpy-scripts", ("wangfo/game/script.rpy",)
    ),
    "import-assets": BuiltinStepSpec("import-assets"),
    "validate-outputs": BuiltinStepSpec("validate-outputs"),
    "evaluate-solution": BuiltinStepSpec("evaluate-solution", ("out/solution_evaluation.json",)),
    "collect-metrics": BuiltinStepSpec("collect-metrics"),
}


def _param_key_to_flag(key: str) -> str:
    return f"--{key.replace('_', '-')}"


# Typer 里显式写了与「参数名推导」不一致的长选项时，在此按子命令覆盖。
_CLI_PARAM_FLAG_OVERRIDES: dict[str, dict[str, str]] = {
    "generate-branch": {
        "fork_event_id": "--fork-event",
        "max_branches_per_point": "--max-branches",
    },
    "complete-all-path-events": {
        "input_dir": "--input",
        "output_dir": "--output",
    },
}


def params_to_cli_args(
    params: dict[str, Any], *, command_name: str | None = None
) -> list[str]:
    """
    将流水线步骤的 params 转为传给 `python -m src.cli <cmd>` 的 argv 片段。
    供 builtin handler 与 Streamlit 单步执行等复用。

    ``command_name``：与 Typer 子命令名一致时，可对个别键使用 CLI 实际长选项（如 generate-branch 的 ``--max-branches``）。
    """
    overrides = _CLI_PARAM_FLAG_OVERRIDES.get(command_name or "", {})
    args: list[str] = []
    for key, value in params.items():
        if value is None:
            continue

        flag = overrides.get(key) or _param_key_to_flag(key)
        if isinstance(value, bool):
            args.append(flag if value else f"--no-{key.replace('_', '-')}")
            continue

        if isinstance(value, (list, tuple)):
            for item in value:
                args.extend([flag, str(item)])
            continue

        args.extend([flag, str(value)])
    return args


def _default_character_output_dir(
    config: PipelineConfig, params: dict[str, Any]
) -> Path:
    """与 extract 一致：默认 ``<output_root>/out``。"""
    od = params.get("output_dir")
    if od is not None and str(od).strip():
        return Path(str(od).strip())
    return Path(config.output_root) / "out"


def _rewrite_path_for_run_out(
    val: Any,
    *,
    base: Path,
    fallback_segment: str,
) -> str:
    """
    将模板里常见的 ``out/...`` 或指向其它 run 的 ``runs/ui/.../out/...``
    改到当前流水线的 ``<output_root>/out/<fallback_segment>``，
    与 ``scripts.ui.utils.merge_step_params_for_ui`` 行为对齐。
    """
    fallback = (base / fallback_segment).as_posix()
    if val is None:
        return fallback
    if not isinstance(val, str):
        return fallback
    s = val.strip().replace("\\", "/")
    if not s:
        return fallback
    if s.startswith("out/"):
        return fallback
    base_s = base.as_posix().rstrip("/")
    if s.startswith("runs/ui/") and not s.startswith(base_s + "/"):
        return fallback
    return s


def _expand_builtin_step_params(
    config: PipelineConfig, step_name: str, params: dict[str, Any]
) -> dict[str, Any]:
    """
    为流水线内建步骤补全 CLI 路径；并去掉仅用于合并的 output_dir（避免传给 Typer 未知选项）。
    generate-mentions / generate-entities 的默认产出为 ``<output_dir>/mentions.json`` 等。
    """
    p = dict(params)
    if step_name == "generate-mentions":
        base = _default_character_output_dir(config, p)
        if not p.get("input") or not str(p.get("input", "")).strip():
            p["input"] = str(base / "extract_chain.json")
        raw_out = p.get("output") or p.get("output_path")
        if raw_out is None or (isinstance(raw_out, str) and not str(raw_out).strip()):
            p["output"] = str(base / "mentions.json")
        else:
            p["output"] = str(raw_out).strip()
        p.pop("output_path", None)
        p.pop("output_dir", None)
    elif step_name == "generate-entities":
        base = _default_character_output_dir(config, p)
        if not p.get("input") or not str(p.get("input", "")).strip():
            p["input"] = str(base / "mentions.json")
        raw_out = p.get("output") or p.get("output_path")
        if raw_out is None or (isinstance(raw_out, str) and not str(raw_out).strip()):
            p["output"] = str(base / "entities.json")
        else:
            p["output"] = str(raw_out).strip()
        p.pop("output_path", None)
        p.pop("output_dir", None)
    elif step_name == "generate-mentions-and-entities":
        base = _default_character_output_dir(config, p)
        if not p.get("input") or not str(p.get("input", "")).strip():
            p["input"] = str(base / "extract_chain.json")
        else:
            p["input"] = str(p["input"]).strip()
        if not p.get("output_dir") or not str(p.get("output_dir", "")).strip():
            p["output_dir"] = str(base)
        else:
            p["output_dir"] = str(p["output_dir"]).strip()
        p.pop("output", None)
        p.pop("output_path", None)
    elif step_name == "extract-states-and-baseline":
        base = _default_character_output_dir(config, p)
        if not p.get("output_dir") or not str(p.get("output_dir", "")).strip():
            p["output_dir"] = str(base)
        else:
            p["output_dir"] = str(p["output_dir"]).strip()
        od = Path(p["output_dir"])
        if not p.get("events") or not str(p.get("events", "")).strip():
            p["events"] = str(od / "extract_chain.json")
        if not p.get("mentions") or not str(p.get("mentions", "")).strip():
            p["mentions"] = str(od / "mentions.json")
        if not p.get("entities") or not str(p.get("entities", "")).strip():
            p["entities"] = str(od / "entities.json")
        p.pop("output", None)
        p.pop("output_path", None)
    elif step_name == "extract-world-states":
        base = _default_character_output_dir(config, p)
        if not p.get("events") or not str(p.get("events", "")).strip():
            p["events"] = str(base / "extract_chain.json")
        else:
            p["events"] = str(p["events"]).strip()
        raw_out = p.get("output") or p.get("output_path")
        if raw_out is None or (isinstance(raw_out, str) and not str(raw_out).strip()):
            p["output"] = str(base / "world_states.json")
        else:
            p["output"] = str(raw_out).strip()
        p.pop("output_path", None)
        p.pop("output_dir", None)
    elif step_name == "extract-character-states":
        base = _default_character_output_dir(config, p)
        if not p.get("events") or not str(p.get("events", "")).strip():
            p["events"] = str(base / "extract_chain.json")
        else:
            p["events"] = str(p["events"]).strip()
        if not p.get("mentions") or not str(p.get("mentions", "")).strip():
            p["mentions"] = str(base / "mentions.json")
        else:
            p["mentions"] = str(p["mentions"]).strip()
        if not p.get("entities") or not str(p.get("entities", "")).strip():
            p["entities"] = str(base / "entities.json")
        else:
            p["entities"] = str(p["entities"]).strip()
        raw_out = p.get("output") or p.get("output_path")
        if raw_out is None or (isinstance(raw_out, str) and not str(raw_out).strip()):
            p["output"] = str(base / "character_states.json")
        else:
            p["output"] = str(raw_out).strip()
        p.pop("output_path", None)
        p.pop("output_dir", None)
    elif step_name == "extract-relationship-states":
        base = _default_character_output_dir(config, p)
        if not p.get("events") or not str(p.get("events", "")).strip():
            p["events"] = str(base / "extract_chain.json")
        else:
            p["events"] = str(p["events"]).strip()
        if not p.get("mentions") or not str(p.get("mentions", "")).strip():
            p["mentions"] = str(base / "mentions.json")
        else:
            p["mentions"] = str(p["mentions"]).strip()
        if not p.get("entities") or not str(p.get("entities", "")).strip():
            p["entities"] = str(base / "entities.json")
        else:
            p["entities"] = str(p["entities"]).strip()
        raw_out = p.get("output") or p.get("output_path")
        if raw_out is None or (isinstance(raw_out, str) and not str(raw_out).strip()):
            p["output"] = str(base / "relationship_states.json")
        else:
            p["output"] = str(raw_out).strip()
        p.pop("output_path", None)
        p.pop("output_dir", None)
    elif step_name == "init-state-baseline":
        base = _default_character_output_dir(config, p)
        if not p.get("world_states") or not str(p.get("world_states", "")).strip():
            p["world_states"] = str(base / "world_states.json")
        else:
            p["world_states"] = str(p["world_states"]).strip()
        if not p.get("character_states") or not str(p.get("character_states", "")).strip():
            p["character_states"] = str(base / "character_states.json")
        else:
            p["character_states"] = str(p["character_states"]).strip()
        if not p.get("relationship_states") or not str(p.get("relationship_states", "")).strip():
            p["relationship_states"] = str(base / "relationship_states.json")
        else:
            p["relationship_states"] = str(p["relationship_states"]).strip()
        raw_out = p.get("output") or p.get("output_path")
        if raw_out is None or (isinstance(raw_out, str) and not str(raw_out).strip()):
            p["output"] = str(base / "state_baseline.json")
        else:
            p["output"] = str(raw_out).strip()
        p.pop("output_path", None)
        p.pop("output_dir", None)
    elif step_name == "import-neo4j":
        base = _default_character_output_dir(config, p)
        raw_json = p.get("json")
        if raw_json is None or (isinstance(raw_json, str) and not str(raw_json).strip()):
            # merged JSON（必须包含 relations），否则导入时不会创建事件关系边。
            p["json"] = str(base / "extract_merged.json")
        else:
            p["json"] = str(raw_json).strip()
        p.pop("output_dir", None)
    elif step_name == "import-entities":
        base = _default_character_output_dir(config, p)
        raw_entities = p.get("entities")
        if raw_entities is None or (
            isinstance(raw_entities, str) and not str(raw_entities).strip()
        ):
            p["entities"] = str(base / "entities.json")
        else:
            p["entities"] = str(raw_entities).strip()
        p.pop("output_dir", None)
    elif step_name == "import-state-changes":
        base = _default_character_output_dir(config, p)
        raw_states = p.get("states")
        if raw_states is None or (isinstance(raw_states, str) and not str(raw_states).strip()):
            p["states"] = str(base / "world_states.json")
        else:
            p["states"] = str(raw_states).strip()
        p.pop("output_dir", None)
    elif step_name == "import-neo4j-all":
        base = _default_character_output_dir(config, p)
        if not p.get("output_dir") or not str(p.get("output_dir", "")).strip():
            p["output_dir"] = str(base)
        else:
            p["output_dir"] = str(p["output_dir"]).strip()
        od = Path(p["output_dir"])
        if not p.get("json") or not str(p.get("json", "")).strip():
            # merged JSON（必须包含 relations），否则导入时不会创建事件关系边。
            p["json"] = str(od / "extract_merged.json")
        else:
            p["json"] = str(p["json"]).strip()
        if not p.get("entities") or not str(p.get("entities", "")).strip():
            p["entities"] = str(od / "entities.json")
        else:
            p["entities"] = str(p["entities"]).strip()
        if not p.get("world_states") or not str(p.get("world_states", "")).strip():
            p["world_states"] = str(od / "world_states.json")
        else:
            p["world_states"] = str(p["world_states"]).strip()
        if not p.get("character_states") or not str(
            p.get("character_states", "")
        ).strip():
            p["character_states"] = str(od / "character_states.json")
        else:
            p["character_states"] = str(p["character_states"]).strip()
        if not p.get("relationship_states") or not str(
            p.get("relationship_states", "")
        ).strip():
            p["relationship_states"] = str(od / "relationship_states.json")
        else:
            p["relationship_states"] = str(p["relationship_states"]).strip()
    elif step_name == "aggregate-characters":
        base = _default_character_output_dir(config, p)
        if not p.get("events") or not str(p.get("events", "")).strip():
            p["events"] = str(base / "extract_chain.json")
        else:
            p["events"] = str(p["events"]).strip()
        if not p.get("char_states") or not str(p.get("char_states", "")).strip():
            p["char_states"] = str(base / "character_states.json")
        else:
            p["char_states"] = str(p["char_states"]).strip()
        if not p.get("rel_states") or not str(p.get("rel_states", "")).strip():
            p["rel_states"] = str(base / "relationship_states.json")
        else:
            p["rel_states"] = str(p["rel_states"]).strip()
        if not p.get("entities") or not str(p.get("entities", "")).strip():
            p["entities"] = str(base / "entities.json")
        else:
            p["entities"] = str(p["entities"]).strip()
        if not p.get("mentions") or not str(p.get("mentions", "")).strip():
            p["mentions"] = str(base / "mentions.json")
        else:
            p["mentions"] = str(p["mentions"]).strip()
        raw_out = p.get("output") or p.get("output_path")
        if raw_out is None or (isinstance(raw_out, str) and not str(raw_out).strip()):
            p["output"] = str(base / "character_profiles.json")
        else:
            p["output"] = str(raw_out).strip()
        if p.get("use_rules"):
            p["rules"] = True
        p.pop("use_rules", None)
        p.pop("output_path", None)
        p.pop("output_dir", None)
    elif step_name == "aggregate-personas":
        base = _default_character_output_dir(config, p)
        if not p.get("profiles") or not str(p.get("profiles", "")).strip():
            p["profiles"] = str(base / "character_profiles.json")
        else:
            p["profiles"] = str(p["profiles"]).strip()
        raw_out = p.get("output") or p.get("output_path")
        if raw_out is None or (isinstance(raw_out, str) and not str(raw_out).strip()):
            p["output"] = str(base / "character_personas.json")
        else:
            p["output"] = str(raw_out).strip()
        p.pop("output_path", None)
        p.pop("output_dir", None)
    elif step_name == "aggregate-characters-and-personas":
        base = _default_character_output_dir(config, p)
        if not p.get("output_dir") or not str(p.get("output_dir", "")).strip():
            p["output_dir"] = str(base)
        else:
            p["output_dir"] = str(p["output_dir"]).strip()
        od = Path(p["output_dir"])
        if not p.get("events") or not str(p.get("events", "")).strip():
            p["events"] = str(od / "extract_chain.json")
        if not p.get("char_states") or not str(p.get("char_states", "")).strip():
            p["char_states"] = str(od / "character_states.json")
        if not p.get("rel_states") or not str(p.get("rel_states", "")).strip():
            p["rel_states"] = str(od / "relationship_states.json")
        if not p.get("entities") or not str(p.get("entities", "")).strip():
            p["entities"] = str(od / "entities.json")
        if not p.get("mentions") or not str(p.get("mentions", "")).strip():
            p["mentions"] = str(od / "mentions.json")
        if not p.get("profiles") or not str(p.get("profiles", "")).strip():
            p["profiles"] = str(od / "character_profiles.json")
        if not p.get("personas_output") or not str(p.get("personas_output", "")).strip():
            p["personas_output"] = str(od / "character_personas.json")
        if p.get("use_rules"):
            p["rules"] = True
        p.pop("use_rules", None)
        p.pop("output", None)
        p.pop("output_path", None)
    elif step_name == "generate-canonical-branch":
        base = _default_character_output_dir(config, p)
        raw_out = p.get("output") or p.get("output_path")
        if raw_out is None or (isinstance(raw_out, str) and not str(raw_out).strip()):
            # CLI flag: --output（变量名 output_path）
            p["output"] = str(base / "canonical_branch.json")
        else:
            p["output"] = str(raw_out).strip()
        p.pop("output_path", None)
        p.pop("output_dir", None)
    elif step_name == "generate-all-paths":
        base = _default_character_output_dir(config, p)
        raw_out = p.get("output") or p.get("output_path")
        if raw_out is None or (isinstance(raw_out, str) and not str(raw_out).strip()):
            p["output"] = str(base / "all_paths")
        else:
            p["output"] = str(raw_out).strip()
        p.pop("output_path", None)
        p.pop("output_dir", None)
    elif step_name == "complete-all-path-events":
        base = _default_character_output_dir(config, p)
        raw_in = p.get("input_dir") or p.get("input")
        if raw_in is None or (isinstance(raw_in, str) and not str(raw_in).strip()):
            p["input_dir"] = str(base / "all_paths")
        else:
            p["input_dir"] = str(raw_in).strip()
        raw_out = p.get("output_dir") or p.get("output")
        if raw_out is None or (isinstance(raw_out, str) and not str(raw_out).strip()):
            p["output_dir"] = str(base / "all_paths_completed")
        else:
            p["output_dir"] = str(raw_out).strip()
        p.pop("input", None)
        p.pop("output", None)
        p.pop("output_path", None)
    elif step_name == "generate-content":
        base = _default_character_output_dir(config, p)
        raw_in = p.get("input") or p.get("input_dir")
        p["input"] = _rewrite_path_for_run_out(
            raw_in, base=base, fallback_segment="all_paths_completed"
        )
        p.pop("input_dir", None)
        raw_out = p.get("output") or p.get("output_dir")
        p["output"] = _rewrite_path_for_run_out(
            raw_out, base=base, fallback_segment="enhanced_paths"
        )
        p.pop("output_dir", None)
        raw_pf = p.get("personas") or p.get("personas_file")
        p["personas"] = _rewrite_path_for_run_out(
            raw_pf, base=base, fallback_segment="character_personas.json"
        )
        p.pop("personas_file", None)
    elif step_name in ("generate-renpy-scripts", "import-assets"):
        # import-assets 末尾同样会调用 generate_content_and_scripts，路径须与 generate-renpy-scripts 一致
        base = _default_character_output_dir(config, p)
        raw_ep = p.get("enhanced_paths") or p.get("paths") or p.get("input")
        p["enhanced_paths"] = _rewrite_path_for_run_out(
            raw_ep, base=base, fallback_segment="enhanced_paths"
        )
        for alt in ("paths", "input", "input_dir"):
            p.pop(alt, None)
        raw_pf = p.get("personas")
        p["personas"] = _rewrite_path_for_run_out(
            raw_pf, base=base, fallback_segment="character_personas.json"
        )
        raw_ef = p.get("entities")
        if raw_ef is None or (isinstance(raw_ef, str) and not str(raw_ef).strip()):
            cand = base / "entities.json"
            if cand.is_file():
                p["entities"] = str(cand)
            else:
                p.pop("entities", None)
        else:
            p["entities"] = _rewrite_path_for_run_out(
                raw_ef, base=base, fallback_segment="entities.json"
            )
        p.pop("output_dir", None)
        p.pop("output_path", None)
    return p


def _resolve_output_paths(spec: BuiltinStepSpec, params: dict[str, Any]) -> list[str]:
    if spec.command_name in {
        "import-neo4j",
        "import-entities",
        "import-state-changes",
        "import-neo4j-all",
    }:
        # 导入类步骤不产出本地文件，不参与「可复用产物」判断。
        return []

    output_path = params.get("output") or params.get("output_path")
    output_dir = params.get("output_dir")

    if spec.command_name == "generate-all-paths":
        paths: list[str] = []
        if output_path and str(output_path).strip():
            pth = Path(str(output_path).strip())
            paths.append(
                str(pth / "index.json") if pth.suffix.lower() != ".json" else str(pth)
            )
        else:
            paths.append("out/all_paths/index.json")
        return paths

    if spec.command_name == "complete-all-path-events":
        co = params.get("output_dir") or params.get("output")
        if co is not None and str(co).strip():
            return [str(Path(str(co).strip()) / "index.json")]
        return ["out/all_paths_completed/index.json"]

    if output_path:
        return [str(output_path)]
    if output_dir:
        od = str(output_dir).strip()
        if spec.command_name == "generate-mentions":
            return [str(Path(od) / "mentions.json")]
        if spec.command_name == "generate-entities":
            return [str(Path(od) / "entities.json")]
        if spec.command_name == "generate-mentions-and-entities":
            return [
                str(Path(od) / "mentions.json"),
                str(Path(od) / "entities.json"),
            ]
        if spec.command_name == "extract-states-and-baseline":
            b = Path(od)
            return [
                str(b / "world_states.json"),
                str(b / "character_states.json"),
                str(b / "relationship_states.json"),
                str(b / "state_baseline.json"),
            ]
        if spec.command_name == "aggregate-characters-and-personas":
            b = Path(od)
            return [str(b / "character_profiles.json"), str(b / "character_personas.json")]
        return [str(output_dir)]
    return list(spec.default_outputs)


def list_expected_artifact_paths_relative(
    step_name: str, params: dict[str, Any]
) -> list[str]:
    """
    单步执行完成后，用于判定「产出物是否已存在、可否复用」的相对路径（相对项目根）。

    与 BUILTIN_STEP_SPECS.default_outputs 及 params 里的 output / output_path / output_dir 一致；
    extract-events：``<output_dir>/extract_chain.json``；
    extract-relations：优先 ``output`` / ``output_path``，否则 ``<output_dir>/extract_relations.json``。
    无内置产出或未注册的步骤返回空列表。
    """
    spec = BUILTIN_STEP_SPECS.get(step_name)
    if spec is None:
        return []

    if step_name == "extract-events":
        od = params.get("output_dir")
        if od is not None and str(od).strip():
            return [str(Path(str(od).strip()) / "extract_chain.json")]
        return [str(p) for p in spec.default_outputs]

    if step_name == "extract-events-and-relations":
        od = params.get("output_dir")
        if od is not None and str(od).strip():
            base = Path(str(od).strip())
            return [
                str(base / "extract_chain.json"),
                str(base / "extract_relations.json"),
            ]
        return ["out/extract_chain.json", "out/extract_relations.json"]

    if step_name == "extract-relations":
        op = params.get("output") or params.get("output_path")
        if op is not None and str(op).strip():
            return [str(Path(str(op).strip()))]
        od = params.get("output_dir")
        if od is not None and str(od).strip():
            return [str(Path(str(od).strip()) / "extract_relations.json")]
        return [str(p) for p in spec.default_outputs]

    if step_name == "generate-mentions":
        op = params.get("output") or params.get("output_path")
        if op is not None and str(op).strip():
            return [str(Path(str(op).strip()))]
        od = params.get("output_dir")
        if od is not None and str(od).strip():
            return [str(Path(str(od).strip()) / "mentions.json")]
        return [str(p) for p in spec.default_outputs]

    if step_name == "generate-entities":
        op = params.get("output") or params.get("output_path")
        if op is not None and str(op).strip():
            return [str(Path(str(op).strip()))]
        od = params.get("output_dir")
        if od is not None and str(od).strip():
            return [str(Path(str(od).strip()) / "entities.json")]
        return [str(p) for p in spec.default_outputs]

    if step_name == "generate-mentions-and-entities":
        od = params.get("output_dir")
        if od is not None and str(od).strip():
            base = Path(str(od).strip())
            return [
                str(base / "mentions.json"),
                str(base / "entities.json"),
            ]
        return [str(p) for p in spec.default_outputs]

    if step_name == "extract-states-and-baseline":
        od = params.get("output_dir")
        if od is not None and str(od).strip():
            base = Path(str(od).strip())
            return [
                str(base / "world_states.json"),
                str(base / "character_states.json"),
                str(base / "relationship_states.json"),
                str(base / "state_baseline.json"),
            ]
        return [str(p) for p in spec.default_outputs]

    if step_name == "extract-world-states":
        op = params.get("output") or params.get("output_path")
        if op is not None and str(op).strip():
            return [str(Path(str(op).strip()))]
        od = params.get("output_dir")
        if od is not None and str(od).strip():
            return [str(Path(str(od).strip()) / "world_states.json")]
        return [str(p) for p in spec.default_outputs]

    if step_name == "extract-character-states":
        op = params.get("output") or params.get("output_path")
        if op is not None and str(op).strip():
            return [str(Path(str(op).strip()))]
        od = params.get("output_dir")
        if od is not None and str(od).strip():
            return [str(Path(str(od).strip()) / "character_states.json")]
        return [str(p) for p in spec.default_outputs]

    if step_name == "extract-relationship-states":
        op = params.get("output") or params.get("output_path")
        if op is not None and str(op).strip():
            return [str(Path(str(op).strip()))]
        od = params.get("output_dir")
        if od is not None and str(od).strip():
            return [str(Path(str(od).strip()) / "relationship_states.json")]
        return [str(p) for p in spec.default_outputs]

    if step_name == "init-state-baseline":
        op = params.get("output") or params.get("output_path")
        if op is not None and str(op).strip():
            return [str(Path(str(op).strip()))]
        od = params.get("output_dir")
        if od is not None and str(od).strip():
            return [str(Path(str(od).strip()) / "state_baseline.json")]
        return [str(p) for p in spec.default_outputs]

    if step_name == "aggregate-characters":
        op = params.get("output") or params.get("output_path")
        if op is not None and str(op).strip():
            return [str(Path(str(op).strip()))]
        od = params.get("output_dir")
        if od is not None and str(od).strip():
            return [str(Path(str(od).strip()) / "character_profiles.json")]
        return [str(p) for p in spec.default_outputs]

    if step_name == "aggregate-personas":
        op = params.get("output") or params.get("output_path")
        if op is not None and str(op).strip():
            return [str(Path(str(op).strip()))]
        od = params.get("output_dir")
        if od is not None and str(od).strip():
            return [str(Path(str(od).strip()) / "character_personas.json")]
        return [str(p) for p in spec.default_outputs]

    if step_name == "aggregate-characters-and-personas":
        od = params.get("output_dir")
        if od is not None and str(od).strip():
            base = Path(str(od).strip())
            return [
                str(base / "character_profiles.json"),
                str(base / "character_personas.json"),
            ]
        return [str(p) for p in spec.default_outputs]

    if step_name == "analyze-decision-points":
        op = params.get("output") or params.get("output_path")
        if op is not None and str(op).strip():
            return [str(Path(str(op).strip()))]
        return [str(p) for p in spec.default_outputs]

    if step_name == "complete-all-path-events":
        od = params.get("output_dir") or params.get("output")
        if od is not None and str(od).strip():
            return [str(Path(str(od).strip()) / "index.json")]
        return [str(p) for p in spec.default_outputs]

    if step_name == "generate-content":
        # generate-content 的产物是一个目录（enhanced_paths）。
        # Streamlit/UI 会把 output 指到 runs/ui/<run_id>/out/enhanced_paths，
        # 因此复用判断必须跟随 params，而不能死用默认 out/enhanced_paths。
        od = params.get("output_dir") or params.get("output")
        if od is not None and str(od).strip():
            return [str(Path(str(od).strip()))]
        return [str(p) for p in spec.default_outputs]

    return _resolve_output_paths(spec, params)


def resolve_step_artifact_paths(
    project_root: Path, step_name: str, params: dict[str, Any]
) -> list[Path]:
    """将 list_expected_artifact_paths_relative 解析为项目根下的绝对路径。"""
    rels = list_expected_artifact_paths_relative(step_name, params)
    out: list[Path] = []
    for rel in rels:
        p = Path(rel)
        out.append(p if p.is_absolute() else (project_root / p))
    return out


def artifacts_all_present_for_reuse(
    project_root: Path, step_name: str, params: dict[str, Any]
) -> tuple[bool, list[Path]]:
    """
    若本步在 builtin 中有可识别的产出路径，且 **全部** 已存在（文件或目录），
    则返回 (True, paths)；否则 (False, paths) 或 paths 为空表示无法做复用判断。
    """
    paths = resolve_step_artifact_paths(project_root, step_name, params)
    if not paths:
        return False, []
    ok = all(p.exists() for p in paths)
    return ok, paths


def make_builtin_step_handler(spec: BuiltinStepSpec, project_root: Path) -> StepHandler:
    def handler(config: PipelineConfig, step_name: str, _state: dict[str, Any]) -> StepResult:
        step_config = config.steps[step_name]
        expanded = _expand_builtin_step_params(config, step_name, step_config.params)
        command = [
            sys.executable,
            "-m",
            "src.cli",
            spec.command_name,
            *params_to_cli_args(expanded, command_name=spec.command_name),
        ]
        subprocess.run(command, cwd=str(project_root), check=True)
        return StepResult(output_paths=_resolve_output_paths(spec, expanded))

    return handler


def build_builtin_step_handlers(project_root: str | Path | None = None) -> dict[str, StepHandler]:
    resolved_root = Path(project_root) if project_root is not None else Path(__file__).resolve().parents[2]
    return {
        step_name: make_builtin_step_handler(spec, resolved_root)
        for step_name, spec in BUILTIN_STEP_SPECS.items()
    }
