"""
核心工具模块 🛠️✨

包含可复用的基础组件：
- LLM 客户端
- 文本处理工具
- 嵌入模型客户端
- 向量数据库
- Neo4j 数据库客户端

"""

from .llm_client import AsyncLLMClient
from .text_processor import split_text_to_paragraphs
from .embedding_client import EmbeddingClient
from .vector_db import VectorDB

# Neo4j 数据库客户端
from .neo4j_client import Neo4jClient

try:
    from character.models.mention import Mention
    from character.models.state_change import (
        Condition,
        StateChange,
        CharacterStateChange,
        RelationshipStateChange,
        WorldStateChange,
    )
except ImportError:
    # 如果导入失败，设为 None（向后兼容）
    Mention = None
    Condition = None
    StateChange = None
    CharacterStateChange = None
    RelationshipStateChange = None
    WorldStateChange = None
    normalize_character_names = None
    apply_normalization_to_events = None

__all__ = [
    "AsyncLLMClient",
    "split_text_to_paragraphs",
    "EmbeddingClient",
    "VectorDB",
    "Neo4jClient",
    "Mention",
    "Condition",
    "StateChange",
    "CharacterStateChange",
    "RelationshipStateChange",
    "WorldStateChange",
    "normalize_character_names",
    "apply_normalization_to_events",
]

