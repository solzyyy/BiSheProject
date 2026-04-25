"""
LLM 配置文件 🤖⚙️

管理不同 LLM 提供商的配置信息。
"""

import os
from typing import Dict, Any
from dotenv import load_dotenv

load_dotenv()


# LLM 配置字典（基础配置，会被环境变量覆盖）
# 注意：环境变量可以在 .env 文件中设置来覆盖这些默认值
_LLM_CONFIGS_BASE: Dict[str, Dict[str, Any]] = {
    "deepseek": {
        "provider": "deepseek",
        "base_url_default": "https://api.deepseek.com",
        "base_url_env": "DEEPSEEK_BASE_URL",
        "api_key_env": "DEEPSEEK_API_KEY",
        "model_default": "deepseek-chat",
        "model_env": "DEEPSEEK_MODEL",
        "default_temperature": 1.0,
        "default_max_tokens": 8192,  # 🔧 增加到 8192：状态变化生成需要大输出空间（prompt 可能 8000+ 字符，输出需要多个状态变化对象）
        "default_timeout": 120.0,
        "default_max_retries": 3,
        "default_max_concurrent": 10,
    },
    "gpt-4o": {
        "provider": "openai",
        "base_url_default": "https://api.openai.com/v1",
        "base_url_env": "OPENAI_API_BASE",
        "api_key_env": "OPENAI_API_KEY",
        "model_default": "gpt-4o-2024-11-20",
        "model_env": "OPENAI_MODEL",
        "default_temperature": 1.0,
        "default_max_tokens": 8192,  # 🔧 增加到 8192：与 deepseek 保持一致，确保复杂场景有足够输出空间
        "default_timeout": 120.0,
        "default_max_retries": 3,
        "default_max_concurrent": 10,
    },
    "gemini": {
        "provider": "gemini",
        "base_url_default": "https://generativelanguage.googleapis.com",
        "base_url_env": "GEMINI_BASE_URL",
        "api_key_env": "GEMINI_API_KEY",
        "model_default": "gemini-2.5-flash",
        "model_env": "GEMINI_MODEL",
        "default_temperature": 1.0,
        "default_max_tokens": 8192,
        "default_timeout": 120.0,
        "default_max_retries": 5,
        "default_max_concurrent": 10,
    },
    "claude": {
        "provider": "claude",
        "base_url_default": "https://api.anthropic.com",
        "base_url_env": "ANTHROPIC_BASE_URL",
        "api_key_env": "ANTHROPIC_API_KEY",
        "model_default": "claude-sonnet-4-5-20250929",
        "model_env": "ANTHROPIC_MODEL",
        "default_temperature": 1.0,
        "default_max_tokens": 8192,
        "default_timeout": 120.0,
        "default_max_retries": 3,
        "default_max_concurrent": 10,
    },
}


def get_llm_config(model_name: str) -> Dict[str, Any]:
    """
    获取指定模型的配置（从环境变量读取实际值）
    
    Args:
        model_name: 模型名称（如 "deepseek", "gpt-4o", "gpt-4o-turbo"）
    
    Returns:
        模型配置字典，包含从环境变量读取的实际值
    
    Raises:
        ValueError: 如果模型名称不存在
    """
    if model_name not in _LLM_CONFIGS_BASE:
        available = ", ".join(_LLM_CONFIGS_BASE.keys())
        raise ValueError(
            f"未知的模型名称: {model_name}。可用模型: {available}"
        )
    
    base_config = _LLM_CONFIGS_BASE[model_name].copy()
    
    # 构建实际配置，从环境变量读取值（如果存在），否则使用默认值
    config: Dict[str, Any] = {
        "provider": base_config.get("provider", "openai"),
        "base_url": os.getenv(
            base_config["base_url_env"],
            base_config["base_url_default"]
        ),
        "api_key": os.getenv(base_config["api_key_env"]),
        "model": os.getenv(
            base_config["model_env"],
            base_config["model_default"]
        ),
        "default_temperature": base_config["default_temperature"],
        "default_max_tokens": base_config["default_max_tokens"],
        "default_timeout": base_config["default_timeout"],
        "default_max_retries": base_config["default_max_retries"],
        "default_max_concurrent": base_config["default_max_concurrent"],
    }
    
    return config


def get_default_model() -> str:
    """
    获取默认模型名称（从环境变量读取，如果没有则返回 "deepseek"）
    
    Returns:
        默认模型名称
    """
    return os.getenv("LLM_MODEL", "deepseek")

