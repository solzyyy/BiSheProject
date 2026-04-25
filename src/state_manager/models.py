"""
状态管理模块的数据模型 📋✨
"""

from typing import Dict, Any, Optional, List
from pydantic import BaseModel, Field


class UpdatedState(BaseModel):
    """
    更新后的状态变量 📊
    
    包含三类状态的更新结果：
    - 人物内在状态
    - 人物关系状态
    - 世界/物品状态
    """
    
    character_states: Dict[str, Dict[str, Any]] = Field(
        default_factory=dict,
        description="更新后的人物内在状态，格式：{character_id: {dimension: value, ...}}"
    )
    
    relationship_states: Dict[str, Dict[str, Any]] = Field(
        default_factory=dict,
        description="更新后的人物关系状态，格式：{'char_a|char_b': {dimension: value, ...}}"
    )
    
    world_states: Dict[str, Any] = Field(
        default_factory=dict,
        description="更新后的世界/物品状态，格式：{target_id: {dimension: value, ...}}"
    )


class PersonaUpdate(BaseModel):
    """
    静态人设更新建议 🎭
    
    LLM 判断在剧情敏感事件后是否需要更新静态人设。
    """
    
    character_id: str = Field(..., description="人物ID")
    
    should_update: bool = Field(..., description="是否需要更新静态人设")
    
    update_reason: Optional[str] = Field(
        default=None,
        description="更新原因（如果 should_update=True）"
    )
    
    updated_fields: Optional[Dict[str, str]] = Field(
        default=None,
        description="需要更新的字段及其新值（如果 should_update=True），格式：{field: new_value}"
    )


class GeneratedContent(BaseModel):
    """
    已生成的内容记录 📝
    
    用于向量化和摘要管理。
    """
    
    content_id: str = Field(..., description="内容ID（唯一标识）")
    
    event_id: str = Field(..., description="关联的事件ID")
    
    content_type: str = Field(..., description="内容类型：'event_description', 'state_update', 'persona_update' 等")
    
    content_text: str = Field(..., description="内容文本")
    
    metadata: Dict[str, Any] = Field(
        default_factory=dict,
        description="元数据（如时间戳、状态快照等）"
    )


__all__ = [
    "UpdatedState",
    "PersonaUpdate",
    "GeneratedContent",
]

