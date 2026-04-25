from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


SENSITIVE_KEYS = {
    "api_key",
    "gemini_api_key",
    "deepseek_api_key",
    "openai_api_key",
    "neo4j_password",
    "password",
    "token",
    "secret",
}


class PipelineConfigError(ValueError):
    """Raised when a pipeline config file is invalid."""


@dataclass
class StepConfig:
    enabled: bool = True
    params: dict[str, Any] = field(default_factory=dict)


@dataclass
class PipelineConfig:
    run_id: str
    input_text: str
    output_root: str
    validate_after_each_step: bool
    resume_from: str | None
    default_step_params: dict[str, Any]
    steps: dict[str, StepConfig]


def _contains_sensitive_keys(value: Any) -> bool:
    if isinstance(value, dict):
        for key, nested in value.items():
            if key.lower() in SENSITIVE_KEYS or _contains_sensitive_keys(nested):
                return True
    elif isinstance(value, list):
        return any(_contains_sensitive_keys(item) for item in value)
    return False


def load_pipeline_config(path: str | Path) -> PipelineConfig:
    config_path = Path(path)
    data = json.loads(config_path.read_text(encoding="utf-8"))

    if _contains_sensitive_keys(data):
        raise PipelineConfigError("配置文件中不应包含 API Key、密码或其他敏感信息，请改用 .env。")

    run_id = data.get("run_id")
    input_text = data.get("input_text")
    if not run_id or not input_text:
        raise PipelineConfigError("配置文件必须包含 run_id 和 input_text。")

    raw_steps = data.get("steps")
    if not isinstance(raw_steps, dict) or not raw_steps:
        raise PipelineConfigError("配置文件必须包含非空的 steps 配置。")

    default_step_params = data.get("default_step_params") or {}
    if not isinstance(default_step_params, dict):
        raise PipelineConfigError("default_step_params 必须为对象。")

    steps: dict[str, StepConfig] = {}
    for step_name, raw_step in raw_steps.items():
        if raw_step is None:
            raw_step = {}
        if not isinstance(raw_step, dict):
            raise PipelineConfigError(f"步骤 {step_name} 的配置必须为对象。")

        enabled = raw_step.get("enabled", True)
        raw_params = raw_step.get("params") or {}
        if not isinstance(raw_params, dict):
            raise PipelineConfigError(f"步骤 {step_name} 的 params 必须为对象。")

        merged_params = dict(default_step_params)
        merged_params.update(raw_params)
        steps[step_name] = StepConfig(enabled=bool(enabled), params=merged_params)

    return PipelineConfig(
        run_id=run_id,
        input_text=input_text,
        output_root=data.get("output_root") or f"runs/{run_id}",
        validate_after_each_step=bool(data.get("validate_after_each_step", False)),
        resume_from=data.get("resume_from"),
        default_step_params=default_step_params,
        steps=steps,
    )
