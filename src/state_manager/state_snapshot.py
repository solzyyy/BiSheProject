"""
状态快照模型 - 存储当前状态的完整快照 📸✨

用于传递给 LLM 生成分支内容，包含：
1. 人物当前状态
2. 人物关系当前状态
3. 世界状态（分三层）
"""

from typing import Dict, List, Any, Optional
from pydantic import BaseModel, Field

from character.models.world_state import WorldState


class StateSnapshot(BaseModel):
    """
    状态快照 - 当前世界的完整状态 📸
    
    用于传递给 LLM，让 LLM 了解当前状态，生成符合逻辑的分支内容。
    """
    
    # 人物状态：{character_id: {dimension: value, ...}}
    character_states: Dict[str, Dict[str, Any]] = Field(
        default_factory=dict,
        description="所有人物当前的内在状态"
    )
    
    # 关系状态：{(char_a, char_b): {dimension: value, ...}}
    # 注意：这里使用字符串键 "char_a|char_b" 而不是元组，因为 JSON 不支持元组
    relationship_states: Dict[str, Dict[str, Any]] = Field(
        default_factory=dict,
        description="所有人物关系的当前状态，键格式为 'char_a|char_b'"
    )
    
    # 世界状态（分三层）
    world_state: WorldState = Field(
        default_factory=WorldState,
        description="世界状态，分三层：factual_layer, logical_layer, narrative_layer"
    )
    
    def to_dict_for_llm(self) -> Dict[str, Any]:
        """
        转换为适合传递给 LLM 的字典格式 📝
        
        Returns:
            格式化的字典，便于 LLM 理解
        """
        # 格式化人物状态
        formatted_characters = {}
        for char_id, state in self.character_states.items():
            formatted_characters[char_id] = {
                "状态维度": state,
                "说明": f"人物 {char_id} 的当前内在状态"
            }
        
        # 格式化关系状态
        formatted_relationships = {}
        for rel_key, state in self.relationship_states.items():
            char_a, char_b = rel_key.split("|")
            formatted_relationships[rel_key] = {
                "关系双方": [char_a, char_b],
                "关系状态": state,
                "说明": f"人物 {char_a} 与 {char_b} 之间的当前关系状态"
            }
        
        # 格式化世界状态
        formatted_world = {
            "客观事实层": [
                {
                    "target_id": sc.target_id,
                    "dimension": sc.dimension,
                    "value": sc.value,
                    "logic_impact": sc.logic_impact,
                    "说明": "世界客观事实，用于世界一致性约束"
                }
                for sc in self.world_state.factual_layer
            ],
            "逻辑阻碍层": [
                {
                    "target_id": sc.target_id,
                    "dimension": sc.dimension,
                    "value": sc.value,
                    "is_obstacle": sc.is_obstacle,
                    "logic_impact": sc.logic_impact,
                    "说明": "会影响行动可否的状态，分支引擎会检查这些状态"
                }
                for sc in self.world_state.logical_layer
            ],
            "叙事氛围层": [
                {
                    "target_id": sc.target_id,
                    "dimension": sc.dimension,
                    "value": sc.value,
                    "logic_impact": sc.logic_impact,
                    "说明": "值得被写出来的世界变化，影响氛围与人物反应"
                }
                for sc in self.world_state.narrative_layer
            ]
        }
        
        return {
            "人物状态": formatted_characters,
            "关系状态": formatted_relationships,
            "世界状态": formatted_world
        }
    
    def to_text_summary(self) -> str:
        """
        转换为文本摘要，用于提示词 📄
        
        Returns:
            格式化的文本摘要
        """
        lines = []
        lines.append("=== 当前状态快照 ===\n")
        
        # 人物状态
        if self.character_states:
            lines.append("【人物状态】")
            for char_id, state in self.character_states.items():
                state_str = "、".join([f"{k}: {v}" for k, v in state.items()])
                lines.append(f"  {char_id}: {state_str}")
            lines.append("")
        
        # 关系状态
        if self.relationship_states:
            lines.append("【关系状态】")
            for rel_key, state in self.relationship_states.items():
                char_a, char_b = rel_key.split("|")
                state_str = "、".join([f"{k}: {v}" for k, v in state.items()])
                lines.append(f"  {char_a} ↔ {char_b}: {state_str}")
            lines.append("")
        
        # 世界状态
        if (self.world_state.factual_layer or 
            self.world_state.logical_layer or 
            self.world_state.narrative_layer):
            lines.append("【世界状态】")
            
            if self.world_state.factual_layer:
                lines.append("  客观事实层：")
                for sc in self.world_state.factual_layer:
                    lines.append(f"    - {sc.dimension}: {sc.value}")
            
            if self.world_state.logical_layer:
                lines.append("  逻辑阻碍层：")
                for sc in self.world_state.logical_layer:
                    obstacle_mark = "🚫" if sc.is_obstacle else ""
                    lines.append(f"    - {obstacle_mark} {sc.dimension}: {sc.value} ({sc.logic_impact})")
            
            if self.world_state.narrative_layer:
                lines.append("  叙事氛围层：")
                for sc in self.world_state.narrative_layer:
                    lines.append(f"    - {sc.dimension}: {sc.value}")
        
        return "\n".join(lines)


__all__ = ["StateSnapshot"]























