"""
路径事件数据提取器 - 从事件中提取各种数据 📝✨

职责：
1. 从事件中提取原文片段（source_text）
2. 从事件中提取对话信息（dialogue）
3. 从事件中提取场景描述（scene_description）
4. 从事件中提取玩家选择（player_choice）
5. 从事件中提取状态变化（state_changes）

这个类封装了所有事件数据提取逻辑，避免代码重复。
"""

from typing import Dict, List, Any, Optional
from .path_data_loader import PathDataLoader


class PathEventExtractor:
    """
    路径事件数据提取器 📝
    
    从事件中提取各种数据，统一管理提取逻辑
    """
    
    def __init__(self, data_loader: Optional[PathDataLoader] = None):
        """
        初始化事件提取器
        
        Args:
            data_loader: 数据加载器（用于访问 extract_chain.json）
        """
        self.data_loader = data_loader or PathDataLoader()
    
    def extract_source_text(self, event: Dict[str, Any]) -> Optional[str]:
        """
        从事件中提取原文片段（source_text）📖
        
        策略：
        1. 如果是主线事件，从 extract_chain.json 中查找对应事件的原文
        2. 如果是分支事件，使用事件的 description 或 event_data 中的内容作为 source_text
        
        Args:
            event: 事件字典
            
        Returns:
            原文片段，如果没找到返回 None
        """
        event_id = event.get("event_id", "")
        original_event_id = event.get("original_event_id", "")
        if not event_id and not original_event_id:
            return None
        
        # 若事件自身带有重写或生成的 source_text，优先使用
        if event.get("source_text"):
            return event.get("source_text")
        
        # 🔧 关键修复：检查是否是分支事件
        is_branch_event = event.get("is_branch_event", False)
        event_type = event.get("type", "")
        
        # 如果是分支事件，使用事件的描述作为 source_text
        if is_branch_event or event_type == "branch_event":
            # 优先使用 event_data 中的内容
            event_data = event.get("event_data", {})
            if event_data:
                # 尝试从 event_data 中提取场景描述或原文
                scene = event_data.get("场景", "")
                result = event_data.get("结果", "")
                if scene or result:
                    # 组合场景和结果作为 source_text
                    parts = []
                    if scene:
                        parts.append(scene)
                    if result:
                        parts.append(result)
                    return " ".join(parts)
            
            # 如果没有 event_data，使用 description
            description = event.get("description", "")
            if description:
                return description
            
            # 如果都没有，返回 None
            return None
        
        # 主线事件：从 extract_chain.json 中查找事件
        # 注意：canonical_branch_chronological 会重排并“重编号” event_id，因此应优先用 original_event_id 查找原文。
        lookup_id = original_event_id or event_id
        extract_event = self.data_loader.get_extract_event(lookup_id)
        if extract_event:
            event_data = extract_event.get("事件", {})
            source_text = event_data.get("source_text", "")
            return source_text if source_text else None
        
        return None
    
    def extract_dialogue(self, event: Dict[str, Any]) -> List[Dict[str, Any]]:
        """
        从事件中提取对话信息 📝
        
        通过 event_id 从 extract_chain.json 中查找对应事件
        
        Args:
            event: 事件字典
            
        Returns:
            对话信息列表，格式：[{"speaker": "...", "text": "...", "tone": "..."}]
        """
        event_id = event.get("event_id", "")
        original_event_id = event.get("original_event_id", "")
        if not event_id and not original_event_id:
            return []
        
        # 若事件自身带有重写或生成的 dialogue，优先使用
        if event.get("dialogue") is not None and isinstance(event.get("dialogue"), list):
            return event.get("dialogue")
        
        # 从 extract_chain.json 中查找事件
        # 同 extract_source_text：优先用 original_event_id 对齐 extract_chain
        lookup_id = original_event_id or event_id
        extract_event = self.data_loader.get_extract_event(lookup_id)
        if not extract_event:
            return []
        
        dialogue_info = extract_event.get("对话信息", {})
        if not dialogue_info or not dialogue_info.get("has_dialogue", False):
            return []
        
        dialogue_info_data = dialogue_info.get("dialogue_info")
        if not dialogue_info_data:
            return []
        
        dialogue_content = dialogue_info_data.get("dialogue_content", [])
        dialogue = []
        
        for dialogue_item in dialogue_content:
            speaker = dialogue_item.get("speaker", "")
            content = dialogue_item.get("content", "")
            tone = dialogue_item.get("tone", "")
            
            if speaker and content:
                dialogue.append({
                    "speaker": speaker,
                    "text": content,
                    "tone": tone,
                })
        
        return dialogue
    
    def extract_scene_description(self, event: Dict[str, Any]) -> str:
        """
        从事件中提取场景描述
        
        Args:
            event: 事件字典
            
        Returns:
            场景描述文本
        """
        # 优先从 description 字段提取
        description = event.get("description", "")
        if description:
            return description
        
        # 从 event_data 中提取
        event_data = event.get("event_data", {})
        if event_data:
            # 组合多个字段作为场景描述
            parts = []
            if event_data.get("场景"):
                parts.append(event_data["场景"])
            if event_data.get("行动"):
                parts.append(event_data["行动"])
            if event_data.get("结果"):
                parts.append(event_data["结果"])
            if parts:
                return "。".join(parts)
        
        # 从 canonical_record 中提取
        canonical_record = event.get("canonical_record", {})
        if canonical_record:
            return canonical_record.get("description", "")
        
        return ""
    
    def extract_player_choice(
        self,
        event: Dict[str, Any],
        data_loader: Optional[PathDataLoader] = None,
    ) -> Optional[Dict[str, Any]]:
        """
        从事件中提取玩家选择
        
        Args:
            event: 事件字典
            data_loader: 数据加载器（用于访问 branches.json）
            
        Returns:
            玩家选择信息，格式：{"prompt": "...", "options": [...]}
        """
        decision_point = event.get("decision_point")
        canonical_choice = event.get("canonical_choice")
        
        if not decision_point or not canonical_choice:
            return None
        
        # 从 branches.json 中查找对应的分支选择信息
        loader = data_loader or self.data_loader
        # 注意：分支路径会对 event_id 做时间线重编号，但原始 fork_event_id 会保存在 original_event_id。
        # decision_point 的查表必须使用真实的 fork_event_id，否则会找不到分支选项。
        fork_event_id = event.get("original_event_id") or event.get("event_id", "")
        branch_data = loader.get_branch_data(fork_event_id, canonical_choice)
        
        if branch_data:
            return {
                "prompt": f"在 {decision_point} 做出选择",
                "options": [
                    {
                        "text": branch_data.get("description", canonical_choice),
                        "branch_choice_id": canonical_choice,
                        "jump_target": f"path_{canonical_choice}",
                    }
                ]
            }
        
        return None
    
    def extract_state_changes(self, event: Dict[str, Any]) -> Dict[str, Any]:
        """
        从事件中提取状态变化
        
        Args:
            event: 事件字典
            
        Returns:
            状态变化字典，格式：{character_id: {dimension: value}, ...}
        """
        # 优先从 selected_state_changes 提取
        selected_scs = event.get("selected_state_changes", [])
        if not selected_scs:
            # 如果没有 selected_state_changes，尝试从 state_changes 提取
            selected_scs = event.get("state_changes", [])
        
        if not selected_scs:
            return {}
        
        # 转换为简单的字典格式
        state_changes = {}
        for sc in selected_scs:
            # 类型检查：确保 sc 是字典类型
            if not isinstance(sc, dict):
                # 如果 sc 不是字典（可能是整数、字符串等），跳过
                continue
            
            target_type = sc.get("target_type", "")
            target_id = sc.get("target_id", "")
            dimension = sc.get("dimension", "")
            value = sc.get("value")
            
            if target_type == "character" and target_id and dimension:
                if target_id not in state_changes:
                    state_changes[target_id] = {}
                state_changes[target_id][dimension] = value
            elif target_type == "relationship" and target_id and dimension:
                # 关系状态使用特殊格式
                rel_key = f"relationship_{target_id}" if isinstance(target_id, str) else f"relationship_{target_id[0]}_{target_id[1]}"
                if rel_key not in state_changes:
                    state_changes[rel_key] = {}
                state_changes[rel_key][dimension] = value
        
        return state_changes
    
    def determine_event_type(self, event: Dict[str, Any]) -> str:
        """
        判断事件类型
        
        Args:
            event: 事件字典
            
        Returns:
            事件类型：decision_point | branch_event | mainline_event | ending
        """
        # 🔧 关键修复：优先检查是否是结局事件（提前结局事件可能有 decision_point 字段，但应该被标记为 ending）
        if event.get("is_ending") or event.get("ending_type") or "ending" in event.get("event_id", "").lower() or "ending" in event.get("original_event_id", "").lower():
            return "ending"
        
        # 检查是否是决策点
        if event.get("decision_point") or event.get("canonical_choice"):
            return "decision_point"
        
        # 检查是否是分支事件（有 branch_id 或不在主线中）
        if "branch" in event.get("event_id", "").lower() or event.get("is_branch_event"):
            return "branch_event"

        # 合流后的主线事件（与合流前的主线事件区分）
        if event.get("is_mainline_event_merged"):
            return "mainline_event_merged"

        # 检查是否是主线事件（在 canonical_branch.json 中，合流之前）
        if event.get("is_mainline_event") or event.get("canonical_record"):
            return "mainline_event"

        # 默认返回 branch_event（如果无法判断）
        return "branch_event"


__all__ = ["PathEventExtractor"]

