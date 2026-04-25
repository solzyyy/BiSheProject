"""
路径输出格式化器 - 格式化路径输出为 story_content 格式 📝✨

职责：
1. 格式化路径输出（适配 story_content_format_spec.md）
2. 格式化单个事件
3. 格式化结局
4. 格式化状态快照

这个类封装了所有输出格式化逻辑，避免代码重复。
"""

import re
from typing import Dict, List, Any, Optional
from state_manager.models import UpdatedState
from .path_event_extractor import PathEventExtractor


class StoryContentFormatter:
    """
    故事内容格式化器 📝
    
    格式化路径输出为 story_content_format_spec.md 兼容格式
    """
    
    def __init__(self, event_extractor: Optional[PathEventExtractor] = None):
        """
        初始化格式化器
        
        Args:
            event_extractor: 事件提取器（用于提取事件数据）
        """
        self.event_extractor = event_extractor or PathEventExtractor()
    
    def format_path(
        self,
        path_id: str,
        path_combination: List[Dict[str, Any]],
        events: List[Dict[str, Any]],
        ending: Optional[Dict[str, Any]],
        is_canonical: bool,
        final_states: Optional[UpdatedState] = None,
    ) -> Dict[str, Any]:
        """
        格式化路径输出为 story_content 格式 📝
        
        输出便于转换为 Ren'Py 脚本的中间格式，但不是直接输出 Ren'Py 格式。
        
        Args:
            path_id: 路径ID
            path_combination: 路径组合
            events: 事件列表（内部使用原始 event_id，例如主线 E14、E20，分支 E15、E16）
            ending: 结局信息
            is_canonical: 是否是主线路径
            final_states: 最终状态
            
        Returns:
            格式化后的路径数据
        """
        # 生成 Ren'Py label（用于后续转换）
        renpy_label = self._generate_renpy_label(path_id)
        
        # 格式化事件
        # 这里做一层「输出用重排」，但保留你说的直觉：
        # - **主线路径 (is_canonical=True)**：完全保留原始 event_id，不做改动
        # - **分支路径 (is_canonical=False)**：
        #   - 分叉前：显示 event_id 与 chronological 主线一致（E1、E2…，依据 event_id 而非 original_event_id）
        #   - 分叉后：从 E(fork+1) 起连续编号；非 E{n} 的 id（如提前结局）保持原样
        #   - original_event_id 仍用于与 extract_chain 等对齐
        if is_canonical:
            # 主线路径：不改 event_id，只增加 original_event_id=event_id（保持结构统一）
            formatted_events: List[Dict[str, Any]] = []
            for event in events:
                formatted_event = self.format_event(event)
                # timeline_event_id：稳定的“时间线锚点 id”（用于后续按 fork_event_id 定位插入点）
                formatted_event["timeline_event_id"] = (formatted_event.get("event_id") or "").strip()
                # 若上游已提供 original_event_id（例如 canonical_branch_chronological 的重编号结果），要保留它；
                # 否则回退为当前 event_id。
                original_id = (
                    (event.get("original_event_id") or "").strip()
                    if isinstance(event, dict)
                    else ""
                )
                formatted_event["original_event_id"] = original_id or (formatted_event.get("event_id", "") or "")
                # event_id 保持不变
                formatted_events.append(formatted_event)
        else:
            # 分支路径：分叉前的主线事件保留原始 event_id（E1, E2, E3...），分叉后的事件从 E(fork+1) 起连续编号
            fork_number: Optional[int] = None
            for choice in path_combination:
                if not choice.get("is_canonical", False):
                    fork_event_id = choice.get("fork_event_id", "")
                    if isinstance(fork_event_id, str):
                        m = re.match(r"E(\d+)", fork_event_id)
                        if m:
                            fork_number = int(m.group(1))
                    break
            
            if fork_number is None:
                fork_number = 0
            post_fork_counter = 0  # 分叉后事件的连续编号

            formatted_events = []
            for event in events:
                formatted_event = self.format_event(event)
                # 时间线编号：与 canonical_branch_chronological 一致，用 event_id（E1、E2…），
                # 不要用 original_event_id（extract_chain 旧编号）参与「分叉前」判断或作为显示 id，否则会少 E1、整体错位。
                chrono_id = (
                    (formatted_event.get("event_id") or event.get("event_id") or "").strip()
                    if isinstance(event, dict)
                    else (formatted_event.get("event_id") or "").strip()
                )
                # timeline_event_id：稳定的“时间线锚点 id”（不随分叉后连续编号而改变）
                formatted_event["timeline_event_id"] = chrono_id
                chrono_n: Optional[int] = None
                m_ch = re.fullmatch(r"E(\d+)", chrono_id, re.I)
                if m_ch:
                    chrono_n = int(m_ch.group(1))
                # extract / 上游对齐：优先 original_event_id，否则回退时间线 id
                extract_align_id = (
                    (event.get("original_event_id") or "").strip()
                    if isinstance(event, dict)
                    else ""
                ) or chrono_id
                # 分叉前（含分叉点）且为严格主线序号 E{n}：显示 id 保持时间线编号，与主线路径一致
                if chrono_n is not None and fork_number > 0 and chrono_n <= fork_number:
                    timeline_id = chrono_id
                elif chrono_n is not None and fork_number > 0:
                    post_fork_counter += 1
                    timeline_id = f"E{fork_number + post_fork_counter}"
                else:
                    # 提前结局、branch_ 后缀 id 等非整段 E+数字：保持上游 event_id（勿用 re.match 误解析出 E3）
                    timeline_id = chrono_id
                formatted_event["original_event_id"] = extract_align_id
                formatted_event["event_id"] = timeline_id
                formatted_events.append(formatted_event)
        
        # 格式化结局（最后一个是 ending 事件由流程/提示词保证，此处不再追加）
        formatted_ending = None
        if ending:
            formatted_ending = self.format_ending(ending, final_states)

        return {
            "path_id": path_id,
            "renpy_label": renpy_label,
            "events": formatted_events,
            "ending": formatted_ending,
            "metadata": {
                "path_combination": path_combination,
                "total_events": len(formatted_events),
                "branch_events_count": sum(1 for e in formatted_events if e.get("type") == "branch_event"),
                "mainline_events_count": sum(1 for e in formatted_events if e.get("type") == "mainline_event"),
                "mainline_events_merged_count": sum(1 for e in formatted_events if e.get("type") == "mainline_event_merged"),
                "ending_type": self._determine_ending_type(ending) if ending else None,
            }
        }
    
    def format_event(self, event: Dict[str, Any]) -> Dict[str, Any]:
        """
        格式化单个事件为 story_content 格式
        
        Args:
            event: 事件字典
            
        Returns:
            格式化后的事件数据
        """
        event_id = event.get("event_id", "")
        # 🔧 关键修复：在格式化之前，确保 is_ending 和 ending_type 字段被保留
        # 这样 determine_event_type 才能正确识别提前结局事件
        is_ending = event.get("is_ending", False)
        ending_type = event.get("ending_type")
        ending_reason = event.get("ending_reason")
        
        event_type = self.event_extractor.determine_event_type(event)
        
        # 从事件中提取场景描述（使用现有的数据）
        scene_description = self.event_extractor.extract_scene_description(event)
        
        # 原文片段（从 extract_chain.json 中提取，用于参考和保持文学风格）✨
        source_text = self.event_extractor.extract_source_text(event)
        
        # 对话信息（从 extract_chain.json 中提取）✨
        dialogue = self.event_extractor.extract_dialogue(event)
        
        # 玩家选择（如果是决策点）
        player_choice = None
        if event_type == "decision_point":
            player_choice = self.event_extractor.extract_player_choice(event)
        
        # 状态变化（从 event.get("state_changes") 或 event.get("selected_state_changes")）
        state_changes = self.event_extractor.extract_state_changes(event)
        
        # 状态快照（从 event.get("snapshot")，使用简化版本）
        snapshot = event.get("snapshot")
        formatted_snapshot = self.format_snapshot(snapshot, include_full=False)
        
        formatted = {
            "event_id": event_id,
            "type": event_type,  # decision_point | branch_event | mainline_event | ending
            "scene_description": scene_description,  # 100-500字，重要场景可达800字
            "source_text": source_text,  # 原文片段，用于参考和保持文学风格 ✨
            "dialogue": dialogue,  # 从 extract_chain.json 提取的对话信息
            "player_choice": player_choice,  # 适配 Ren'Py menu
            "state_changes": state_changes,  # 适配 Ren'Py $ variable = value
            "snapshot": formatted_snapshot,  # 状态快照（简化版本）
        }
        
        # 🔧 关键修复：如果是提前结局事件，保留 ending_type 和 ending_reason 字段
        if is_ending or ending_type:
            formatted["is_ending"] = True
            if ending_type:
                formatted["ending_type"] = ending_type
            if ending_reason:
                formatted["ending_reason"] = ending_reason
        
        return formatted
    
    def format_snapshot(
        self,
        snapshot: Optional[Dict[str, Any]],
        include_full: bool = False,  # 默认不包含完整 snapshot
    ) -> Optional[Dict[str, Any]]:
        """
        格式化 snapshot 用于输出（可选包含，简化版本）📸
        
        Args:
            snapshot: 状态快照（StateSnapshot.to_dict_for_llm() 的结果）
            include_full: 是否包含完整 snapshot（默认 False，只包含引用）
        
        Returns:
            格式化的 snapshot，如果 include_full=False 则只包含 event_id 引用
        """
        if not snapshot:
            return None
        
        if include_full:
            # 包含完整 snapshot（用于调试或详细分析）
            return snapshot
        else:
            # 只包含引用（节省空间）
            # 完整数据可以通过 event_id 从 canonical_branch_chronological.json（或 canonical_branch.json）中查找
            return {
                "snapshot_ref": "通过 event_id 从 canonical_branch_chronological.json（或 canonical_branch.json）查找完整 snapshot",
            }
    
    def build_ending_event(self, ending: Dict[str, Any]) -> Dict[str, Any]:
        """
        构建「结局」作为 events 列表中的最后一个事件（供流程在组装路径时调用）。
        流程/提示词要求每条路径必须以结局结束，最后一个事件 type 为 "ending"。
        """
        ending_id = ending.get("id", "")
        event_id = f"ending_{ending_id}" if ending_id else "ending"
        return {
            "event_id": event_id,
            "type": "ending",
            "is_ending": True,
            "description": ending.get("description", ""),
            "scene_description": ending.get("description", ""),
        }

    def format_ending(
        self,
        ending: Dict[str, Any],
        final_states: Optional[UpdatedState] = None,
    ) -> Dict[str, Any]:
        """
        格式化结局为 story_content 格式

        Args:
            ending: 结局信息
            final_states: 最终状态

        Returns:
            格式化后的结局数据
        """
        ending_id = ending.get("id", "")
        ending_name = ending.get("name", "")
        
        # 生成 Ren'Py label
        renpy_label = f"ending_{ending_id}"
        
        # 场景描述（300-600字，重要结局可达1000字）
        scene_description = ending.get("description", "")
        
        # 对话信息（暂时为空，后续可以扩展）
        dialogue = []
        
        # 情感基调
        emotional_tone = self._extract_emotional_tone(ending)
        
        # 主题标签
        themes = ending.get("tags", [])
        
        return {
            "id": ending_id,
            "name": ending_name,
            "renpy_label": renpy_label,
            "scene_description": scene_description,  # 300-600字，重要结局可达1000字
            "dialogue": dialogue,  # 暂时为空，后续填充
            "emotional_tone": emotional_tone,
            "themes": themes,
            "state_snapshot": final_states.model_dump() if final_states and hasattr(final_states, "model_dump") else None,
        }
    
    def _generate_renpy_label(self, path_id: str) -> str:
        """
        生成 Ren'Py label（将 path_id 转换为合法的 label 格式）
        
        Args:
            path_id: 路径ID
            
        Returns:
            Ren'Py label
        """
        # 将 path_id 转换为小写，替换特殊字符为下划线
        label = re.sub(r'[^a-z0-9_]', '_', path_id.lower())
        # 确保以字母开头
        if label and not label[0].isalpha():
            label = "path_" + label
        return label
    
    def _determine_ending_type(self, ending: Optional[Dict[str, Any]]) -> Optional[str]:
        """
        判断结局类型
        
        Args:
            ending: 结局信息
            
        Returns:
            结局类型：bad_ending | good_ending | neutral_ending
        """
        if not ending:
            return None
        
        # 从 tags 中判断
        tags = ending.get("tags", [])
        if any("bad" in tag.lower() or "悲剧" in tag or "破裂" in tag for tag in tags):
            return "bad_ending"
        elif any("good" in tag.lower() or "好" in tag or "成功" in tag for tag in tags):
            return "good_ending"
        else:
            return "neutral_ending"
    
    def _extract_emotional_tone(self, ending: Dict[str, Any]) -> str:
        """
        提取情感基调
        
        Args:
            ending: 结局信息
            
        Returns:
            情感基调：悲痛 | 喜悦 | 平静 | 中性
        """
        tags = ending.get("tags", [])
        description = ending.get("description", "")
        
        # 从 tags 中提取情感相关标签
        emotional_tags = [tag for tag in tags if any(word in tag for word in ["悲痛", "绝望", "平静", "喜悦", "焦虑", "愤怒"])]
        if emotional_tags:
            return emotional_tags[0]
        
        # 从 description 中推断
        if any(word in description for word in ["悲痛", "绝望", "破碎"]):
            return "悲痛"
        elif any(word in description for word in ["喜悦", "满足", "成功"]):
            return "喜悦"
        elif any(word in description for word in ["平静", "安宁", "和谐"]):
            return "平静"
        else:
            return "中性"


__all__ = ["StoryContentFormatter"]

