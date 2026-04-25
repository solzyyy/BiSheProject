"""
Canonical Branch 生成器 - 生成主线（世界真相记录）📜✨

主线 = 世界在"无人干预"下真实发生过的历史
支线 = 在某个历史瞬间，玩家没有照着它发生

输出格式：
{
  "event_id": "E12",
  "description": "王佛在黎明时分渡河",
  "decision_point": "cross_river",  # 可选：这里是「可选择节点」
  "canonical_choice": "cross_now",  # 可选：世界真实走了哪条
  "pre_condition": {...},  # 前提条件
  "state_changes": [...],  # 所有可能的状态变化
  "selected_state_changes": [0, 2, 5]  # 主线应该应用的状态变化索引
}

StateChanges 分类：
1. 世界事实型（world_fact）：必须在主线中 apply ✅
2. 人物内在状态（potential）：只记录，不立即 apply ⚠️
3. 人物关系状态（conditional）：冻结为「条件型变化」❄️
"""

import json
import asyncio
from typing import Dict, List, Any, Optional, Tuple
from pathlib import Path

from core.llm_client import AsyncLLMClient
from state_manager.state_applier import StateApplier
from state_manager.state_snapshot import StateSnapshot
from state_manager.models import UpdatedState, PersonaUpdate


class CanonicalBranchGenerator:
    """
    Canonical Branch 生成器 - 生成主线（世界真相记录）📜
    
    从第一个事件开始，逐步应用状态变化，生成主线记录。
    主线是"无人干预"下真实发生过的历史，不是玩家正在玩的游戏。
    
    特点：
    - 只 apply 世界事实型状态变化
    - 人物内在状态和关系状态标记为 potential/conditional
    - 识别 decision_point 和 canonical_choice
    - 生成结构化的主线记录
    """
    
    def __init__(
        self,
        llm_client: Optional[AsyncLLMClient] = None,
        state_applier: Optional[StateApplier] = None,
        narrative_llm_model: str = "deepseek",
        progress_file: Optional[Path | str] = None,
    ):
        """
        初始化生成器
        
        Args:
            llm_client: LLM 客户端（用于生成文游内容，如果为 None 会创建默认客户端）
            state_applier: 状态应用器（如果为 None 会创建新实例）
            narrative_llm_model: 用于生成文游内容的 LLM 模型（默认：deepseek）
            progress_file: 进度文件路径；用于控制 resume 的“作用域”（建议与 output_dir 同目录）
        """
        # 用于生成文游内容的 LLM 客户端
        # 设置 temperature=0.3 和 max_tokens=2000 用于主线记录生成
        if llm_client is None:
            self.narrative_llm = AsyncLLMClient(
                model_name=narrative_llm_model,
                temperature=0.3,
                max_tokens=2000,
            )
        else:
            self.narrative_llm = llm_client
        
        # 状态应用器（用于应用状态变化和管理上下文）
        self.state_applier = state_applier or StateApplier()
        
        # 当前状态（用于跟踪状态变化）
        self.current_states: Optional[UpdatedState] = None
        
        # 进度文件路径
        # 默认仍落在 out/ 下；由 CLI/脚本在运行时可传入“与 output_path 同目录”的 progress_file，避免不同运行串进度
        self.progress_file = Path(progress_file) if progress_file is not None else Path("out/canonical_branch_progress.json")
    
    async def get_first_event_id(self) -> Optional[str]:
        """
        从 Neo4j 获取第一个事件ID 🎯
        
        使用 Event 节点的 id 唯一约束优化 ORDER BY 查询性能 🚀
        
        Returns:
            第一个事件ID，如果不存在则返回 None
        """
        # 优化查询：直接使用 is_start 字段（已扁平化存储）
        # 注意：Event 节点的 id 字段有唯一约束，可以作为索引用于排序
        query = """
        MATCH (e:Event)
        WHERE e.is_start = true
        RETURN e.id as id
        ORDER BY e.id
        LIMIT 1
        """
        
        with self.state_applier.neo4j_client._driver.session() as session:
            result = session.run(query)
            record = result.single()
            if record:
                return record["id"]
        
        # 如果没有找到起始事件，返回第一个事件
        # 使用 Event 节点的 id 唯一约束优化排序
        query = """
        MATCH (e:Event)
        RETURN e.id as id
        ORDER BY e.id
        LIMIT 1
        """
        
        with self.state_applier.neo4j_client._driver.session() as session:
            result = session.run(query)
            record = result.single()
            if record:
                return record["id"]
        
        return None
    
    async def get_all_event_ids(self) -> List[str]:
        """
        从 Neo4j 获取所有事件ID（按顺序）📚
        
        Returns:
            事件ID列表
        """
        return await self.state_applier._get_all_event_ids()
    
    def _classify_state_changes(
        self,
        state_changes: List[Dict[str, Any]]
    ) -> Dict[str, List[Dict[str, Any]]]:
        """
        分类状态变化 🧩
        
        将 state_changes 分为三类，同时保留原始 StateChange 格式，以便支线可以直接应用。
        
        Args:
            state_changes: 状态变化列表（标准 StateChange 格式）
            
        Returns:
            分类后的状态变化字典：{
                "world_fact": [...],
                "potential": [...],
                "conditional": [...]
            }
            每个状态变化都保留原始 StateChange 格式，并添加元数据字段（以 _ 开头）用于展示和选择。
        """
        classified = {
            "world_fact": [],
            "potential": [],
            "conditional": []
        }
        
        for sc in state_changes:
            target_type = sc.get("target_type")
            
            # 保留原始 StateChange 格式（用于 apply）
            base_state_change = {
                "source_event": sc.get("source_event"),
                "target_type": sc.get("target_type"),
                "target_id": sc.get("target_id"),
                "dimension": sc.get("dimension"),
                "change_type": sc.get("change_type", "set"),
                "value": sc.get("value"),
                "condition": sc.get("condition"),
                "is_obstacle": sc.get("is_obstacle", False),
                "logic_impact": sc.get("logic_impact", ""),
                "plot_sensitive": sc.get("plot_sensitive"),
            }
            
            # 添加元数据字段（用于展示和选择，以 _ 开头）
            metadata = {
                "_target_path": self._build_target_path(sc),
                "_classification": None,  # 将在下面设置
            }
            
            if target_type == "world":
                # 世界事实型：物品获得/消失、世界状态改变、客观事实发生
                metadata["_classification"] = "world_fact"
                classified["world_fact"].append({
                    **base_state_change,
                    **metadata,
                })
            elif target_type == "character":
                # 人物内在状态：情绪变化、价值观转变、信念、决心、动机
                metadata["_classification"] = "potential"
                metadata["_triggered_by"] = sc.get("source_event")
                classified["potential"].append({
                    **base_state_change,
                    **metadata,
                })
            elif target_type == "relationship":
                # 人物关系状态：关系变化几乎永远依赖选择
                metadata["_classification"] = "conditional"
                metadata["_decision_point"] = sc.get("source_event")  # 暂时用 source_event，后续可以优化
                metadata["_choice"] = "canonical"  # 主线选择
                classified["conditional"].append({
                    **base_state_change,
                    **metadata,
                })
        
        return classified
    
    def _build_target_path(self, state_change: Dict[str, Any]) -> List[str]:
        """
        构建目标路径 🎯
        
        将 state_change 转换为路径格式，例如：
        - world: ["world", "boat", "usable"]
        - character: ["characters", "wangfo", "emotion"]
        - relationship: ["relationships", "wangfo", "ferryman", "trust"]
        
        Args:
            state_change: 状态变化字典
            
        Returns:
            路径列表
        """
        target_type = state_change.get("target_type")
        target_id = state_change.get("target_id")
        dimension = state_change.get("dimension")
        
        if target_type == "world":
            # 世界状态：["world", target_id, dimension]
            return ["world", str(target_id), dimension]
        elif target_type == "character":
            # 人物状态：["characters", character_id, dimension]
            return ["characters", str(target_id), dimension]
        elif target_type == "relationship":
            # 关系状态：["relationships", char_a, char_b, dimension]
            if isinstance(target_id, list) and len(target_id) >= 2:
                char_a, char_b = target_id[0], target_id[1]
                # 确保顺序一致
                if char_a > char_b:
                    char_a, char_b = char_b, char_a
                return ["relationships", char_a, char_b, dimension]
            else:
                # 如果是字符串，假设格式是 "char_a|char_b"
                if isinstance(target_id, str) and "|" in target_id:
                    char_a, char_b = target_id.split("|", 1)
                    return ["relationships", char_a, char_b, dimension]
                return ["relationships", str(target_id), dimension]
        
        return []
    
    async def _build_canonical_context(
        self,
        event_id: str,
        event_info: Dict[str, Any],
        snapshot: StateSnapshot,
    ) -> str:
        """
        构建主线生成的上下文 📚
        
        主线专用：不使用文游内容摘要（因为主线不生成文游内容），
        只使用事件信息和状态快照。
        
        Args:
            event_id: 事件ID
            event_info: 事件信息
            snapshot: 当前状态快照
            
        Returns:
            格式化的上下文文本
        """
        context_parts = []
        
        # 1. 事件信息
        context_parts.append("=== 事件信息 ===")
        context_parts.append(f"事件ID: {event_info.get('id')}")
        context_parts.append(f"人物: {', '.join(event_info.get('人物', []))}")
        context_parts.append(f"行动: {event_info.get('行动', '')}")
        context_parts.append(f"目标: {event_info.get('目标', '')}")
        context_parts.append(f"结果: {event_info.get('结果', '')}")
        context_parts.append(f"结果影响: {event_info.get('结果影响', '')}")
        context_parts.append(f"场景: {event_info.get('场景', '')}")
        context_parts.append(f"时间: {event_info.get('时间', '')}")
        context_parts.append("")
        
        # 2. 状态快照
        context_parts.append(snapshot.to_text_summary())
        context_parts.append("")
        
        # 注意：主线不使用文游内容摘要，因为：
        # - 主线生成的是结构化记录，不是文游内容
        # - 摘要应该基于文游内容生成
        # - 如果后续需要，可以将已处理的主线记录作为上下文
        
        return "\n".join(context_parts)
    
    def _extract_standard_state_change(self, classified_sc: Dict[str, Any]) -> Dict[str, Any]:
        """
        从分类后的状态变化中提取标准 StateChange 格式 🎯
        
        用于支线应用状态变化时，确保格式与 StateChange 模型一致。
        
        Args:
            classified_sc: 分类后的状态变化（包含元数据字段）
            
        Returns:
            标准 StateChange 格式的字典（移除元数据字段，以 _ 开头）
        """
        # 提取原始 StateChange 格式（移除元数据字段）
        standard_sc = {
            "source_event": classified_sc.get("source_event"),
            "target_type": classified_sc.get("target_type"),
            "target_id": classified_sc.get("target_id"),
            "dimension": classified_sc.get("dimension"),
            "change_type": classified_sc.get("change_type", "set"),
            "value": classified_sc.get("value"),
            "condition": classified_sc.get("condition"),
            "is_obstacle": classified_sc.get("is_obstacle", False),
            "logic_impact": classified_sc.get("logic_impact", ""),
        }
        # 可选字段
        if "plot_sensitive" in classified_sc:
            standard_sc["plot_sensitive"] = classified_sc.get("plot_sensitive")
        
        return standard_sc
    
    async def generate_canonical_record(
        self,
        event_id: str,
        event_info: Dict[str, Any],
        state_changes: List[Dict[str, Any]],
        snapshot: StateSnapshot,
    ) -> Dict[str, Any]:
        """
        生成主线记录 📜
        
        让 LLM 从所有状态变化中选择哪些应该在主线中 apply。
        
        Args:
            event_id: 事件ID
            event_info: 事件信息
            state_changes: 状态变化列表
            snapshot: 当前状态快照
            
        Returns:
            主线记录字典，包含：
            - description: 事件描述
            - decision_point: 可选择节点标识符（可选）
            - canonical_choice: 世界真实走的选择（可选）
            - pre_condition: 前提条件（可选）
            - selected_state_changes: LLM 选择的状态变化索引列表
            - state_changes: 所有状态变化（标准 StateChange 格式 + 元数据字段）
              格式：每个状态变化都包含标准 StateChange 字段（source_event, target_type, target_id, 
              dimension, change_type, value, condition, is_obstacle, logic_impact, plot_sensitive）
              以及元数据字段（以 _ 开头，如 _classification, _target_path 等）
              支线可以直接使用这些 state_changes，只需移除元数据字段即可
        """
        # 分类状态变化
        classified = self._classify_state_changes(state_changes)
        
        # 构建上下文（主线专用：使用已处理的主线记录作为上下文，而不是文游内容摘要）
        context = await self._build_canonical_context(
            event_id=event_id,
            event_info=event_info,
            snapshot=snapshot,
        )
        
        # 构建提示词（让 LLM 选择哪些状态变化应该在主线中 apply）
        prompt = self._build_canonical_record_prompt(
            event_info=event_info,
            classified_changes=classified,
            context=context,
        )
        
        # 调用 LLM 生成主线记录
        # 注意：temperature 和 max_tokens 需要在初始化时设置，invoke() 只接受 prompt 和 return_json
        response = await self.narrative_llm.invoke(
            prompt,
            return_json=True,
        )
        
        # 解析响应
        if isinstance(response, str):
            import json
            response = json.loads(response)
        
        # 确保包含所有必要字段
        record = {
            "event_id": event_id,
            "description": response.get("description", event_info.get("行动", "")),
            "state_changes": classified["world_fact"] + classified["potential"] + classified["conditional"],
        }
        
        # 可选字段
        if "decision_point" in response:
            record["decision_point"] = response["decision_point"]
        if "canonical_choice" in response:
            record["canonical_choice"] = response["canonical_choice"]
        if "pre_condition" in response:
            record["pre_condition"] = response["pre_condition"]
        if "selected_state_changes" in response:
            record["selected_state_changes"] = response["selected_state_changes"]
        
        return record
    
    def _build_canonical_record_prompt(
        self,
        event_info: Dict[str, Any],
        classified_changes: Dict[str, List[Dict[str, Any]]],
        context: str,
    ) -> str:
        """
        构建主线记录生成提示词 📝
        
        让 LLM 从所有状态变化中选择哪些应该在主线中 apply。
        使用 plot_sensitive 作为可选择节点的参考。
        
        Args:
            event_info: 事件信息
            classified_changes: 分类后的状态变化
            context: 完整上下文
            
        Returns:
            提示词字符串
        """
        # 合并所有状态变化，并添加索引
        all_changes = []
        all_changes.extend(classified_changes['world_fact'])
        all_changes.extend(classified_changes['potential'])
        all_changes.extend(classified_changes['conditional'])
        
        # 为每个状态变化添加索引
        for i, change in enumerate(all_changes):
            change['_index'] = i
        
        # 分析 decision_point 候选（规范化判断）
        decision_analysis = self._analyze_decision_point_candidates(classified_changes)
        
        prompt = f"""你是一位主线记录生成助手。主线 = 世界在"无人干预"下真实发生过的历史。

=== 事件信息 ===
事件ID: {event_info.get('id')}
人物: {', '.join(event_info.get('人物', []))}
行动: {event_info.get('行动', '')}
目标: {event_info.get('目标', '')}
结果: {event_info.get('结果', '')}
前提条件: {event_info.get('前提条件', '')}
结果影响: {event_info.get('结果影响', '')}
场景: {event_info.get('场景', '')}
时间: {event_info.get('时间', '')}

=== 所有状态变化（请选择哪些应该在主线中 apply） ===
{self._format_state_changes_with_index(all_changes)}

注意：
- plot_sensitive（剧情敏感度）: 0.0-1.0，值越高表示对剧情越重要
  - 0.7-1.0: 高敏感度，能限制行动或改变选择，通常是可选择节点
  - 0.3-0.6: 中敏感度，对剧情有影响
  - 0.0-0.2: 低敏感度，仅背景设定
- 世界事实型（world_fact）: 客观事实，通常应该在主线中 apply
- 人物内在状态（potential）: 可能后果，需要判断是否应该在主线中 apply
- 人物关系状态（conditional）: 条件型变化，通常依赖选择，需要判断是否应该在主线中 apply

=== Decision Point 分析（规范化判断） ===
{self._format_decision_analysis(decision_analysis)}

判断规则：
1. 如果存在 conditional 类型的状态变化（关系状态），通常表示选择节点（置信度：高）
2. 如果存在多个（>=2）高敏感度（>=0.7）的 potential 类型变化，可能是选择节点（置信度：中）
3. 如果存在多个（>=3）高敏感度（>=0.7）的状态变化，可能是选择节点（置信度：中低）
4. 如果只有1-2个高敏感度变化且没有关系变化，可能不是选择节点（置信度：低）

注意：这个分析仅供参考，最终判断需要结合事件内容和剧情逻辑。

=== 当前世界状态（上一事件之后的状态） ===
{context}

重要提示：
- 这是处理完之前所有事件后的累积状态
- 人物状态、关系状态、世界状态都是基于之前事件的更新结果
- 请基于这个累积状态来判断应该选择哪些状态变化

=== 生成要求 ===
请生成一个 JSON 对象，包含以下字段：

1. **description** (必需): 简洁描述这个事件（1-2句话）

2. **decision_point** (可选): 如果这是一个"可选择节点"（即存在多个可能的选择），请提供一个标识符，例如 "cross_river"、"choose_path" 等。
   - 参考上面的 Decision Point 分析结果
   - 如果分析结果显示 has_decision_point=true 且 confidence>=0.6，通常应该设置 decision_point
   - 如果分析结果显示 has_decision_point=false 或 confidence<0.6，通常不应该设置 decision_point
   - 最终判断需要结合事件内容和剧情逻辑，不要盲目依赖分析结果
   - 如果这不是选择节点，则省略此字段

3. **canonical_choice** (可选): 如果存在 decision_point，请指定"世界真实走了哪条路"的选择标识符，例如 "cross_now"、"take_path_a" 等。

4. **pre_condition** (可选): 前提条件，格式为 {{"path": [...], "op": "ge"|"le"|"eq"|"ne"|"contains", "value": ...}}，例如 {{"path": ["inventory", "gold"], "op": "ge", "value": 10}}。如果没有前提条件，则省略此字段。

5. **selected_state_changes** (必需): 应该在主线中 apply 的状态变化索引列表（数组），例如 [0, 2, 5]
   - 选择原则：
     * 世界事实型（world_fact）: 通常应该选择，除非是明显的条件型变化
     * 人物内在状态（potential）: 根据事件结果和剧情逻辑判断，是否"世界真实发生了这个变化"
     * 人物关系状态（conditional）: 根据事件结果和剧情逻辑判断，是否"世界真实发生了这个关系变化"
   - 只选择"世界真实发生"的变化，不要选择"可能发生"或"条件发生"的变化

输出格式（JSON）:
{{
  "description": "...",
  "decision_point": "...",  // 可选
  "canonical_choice": "...",  // 可选
  "pre_condition": {{...}},  // 可选
  "selected_state_changes": [0, 2, 5]  // 必需：主线应该应用的状态变化索引列表
}}

请只输出 JSON，不要输出其他内容。
"""
        return prompt
    
    def _analyze_decision_point_candidates(
        self,
        classified_changes: Dict[str, List[Dict[str, Any]]],
    ) -> Dict[str, Any]:
        """
        分析状态变化，判断是否存在 decision_point 候选 🎯
        
        规范化的判断逻辑：
        1. 如果存在 conditional 类型的状态变化（关系状态），通常表示选择节点
        2. 如果存在多个高 plot_sensitive（>= 0.7）的状态变化，可能是选择节点
        3. 如果存在 potential 类型且 plot_sensitive >= 0.7 的状态变化，可能是选择节点
        4. 综合考虑：conditional > potential (高敏感度) > world_fact (高敏感度)
        
        Args:
            classified_changes: 分类后的状态变化
            
        Returns:
            分析结果字典：
            {
                "has_decision_point": bool,
                "confidence": float,  # 0.0-1.0，判断的置信度
                "reasoning": str,  # 判断理由
                "candidate_changes": List[Dict],  # 候选的状态变化
            }
        """
        all_changes = []
        all_changes.extend(classified_changes.get('world_fact', []))
        all_changes.extend(classified_changes.get('potential', []))
        all_changes.extend(classified_changes.get('conditional', []))
        
        # 1. 检查是否有 conditional 类型（关系状态变化通常表示选择）
        conditional_changes = classified_changes.get('conditional', [])
        has_conditional = len(conditional_changes) > 0
        
        # 2. 检查高敏感度的状态变化（plot_sensitive >= 0.7）
        high_sensitive_changes = [
            sc for sc in all_changes
            if sc.get('plot_sensitive', 0) >= 0.7
        ]
        
        # 3. 检查 potential 类型的高敏感度变化
        high_sensitive_potential = [
            sc for sc in classified_changes.get('potential', [])
            if sc.get('plot_sensitive', 0) >= 0.7
        ]
        
        # 4. 判断逻辑
        has_decision_point = False
        confidence = 0.0
        reasoning_parts = []
        candidate_changes = []
        
        if has_conditional:
            # 如果有关系状态变化，很可能是选择节点
            has_decision_point = True
            confidence = 0.9
            reasoning_parts.append(f"存在 {len(conditional_changes)} 个关系状态变化（conditional），通常表示选择节点")
            candidate_changes.extend(conditional_changes)
        elif len(high_sensitive_potential) >= 2:
            # 如果有多个高敏感度的人物内在状态变化，可能是选择节点
            has_decision_point = True
            confidence = 0.7
            reasoning_parts.append(f"存在 {len(high_sensitive_potential)} 个高敏感度（>=0.7）的人物内在状态变化，可能表示选择节点")
            candidate_changes.extend(high_sensitive_potential)
        elif len(high_sensitive_changes) >= 3:
            # 如果有多个高敏感度的状态变化（包括世界状态），可能是选择节点
            has_decision_point = True
            confidence = 0.6
            reasoning_parts.append(f"存在 {len(high_sensitive_changes)} 个高敏感度（>=0.7）的状态变化，可能表示选择节点")
            candidate_changes.extend(high_sensitive_changes[:3])  # 只取前3个
        elif len(high_sensitive_changes) >= 1 and len(conditional_changes) == 0:
            # 如果只有1-2个高敏感度变化，且没有关系变化，可能不是选择节点
            has_decision_point = False
            confidence = 0.3
            reasoning_parts.append(f"存在 {len(high_sensitive_changes)} 个高敏感度变化，但没有关系状态变化，可能不是选择节点")
        
        if not has_decision_point:
            reasoning_parts.append("未发现明显的选择节点特征")
        
        return {
            "has_decision_point": has_decision_point,
            "confidence": confidence,
            "reasoning": "；".join(reasoning_parts),
            "candidate_changes": candidate_changes,
        }
    
    def _format_state_changes_with_index(self, changes: List[Dict[str, Any]]) -> str:
        """格式化状态变化列表为字符串（包含索引和详细信息）"""
        if not changes:
            return "  无"
        
        lines = []
        for sc in changes:
            index = sc.get('_index', '?')
            classification = sc.get('_classification', 'unknown')
            target_path = sc.get('_target_path', [])
            value = sc.get('value', '')
            change_type = sc.get('change_type', 'set')
            plot_sensitive = sc.get('plot_sensitive')
            logic_impact = sc.get('logic_impact', '')
            target_type = sc.get('target_type', '')
            dimension = sc.get('dimension', '')
            
            line = f"  [{index}] {classification} ({target_type}): {target_path} -> {value} (操作: {change_type}, 维度: {dimension})"
            if plot_sensitive is not None:
                line += f" [剧情敏感度: {plot_sensitive:.2f}]"
            if logic_impact:
                line += f"\n      逻辑影响: {logic_impact}"
            lines.append(line)
        
        return "\n".join(lines)
    
    def _format_decision_analysis(self, analysis: Dict[str, Any]) -> str:
        """
        格式化 decision_point 分析结果为字符串 📊
        
        Args:
            analysis: 分析结果字典
            
        Returns:
            格式化的字符串
        """
        lines = []
        lines.append(f"是否存在 Decision Point: {analysis['has_decision_point']}")
        lines.append(f"置信度: {analysis['confidence']:.2f}")
        lines.append(f"判断理由: {analysis['reasoning']}")
        
        candidate_changes = analysis.get('candidate_changes', [])
        if candidate_changes:
            lines.append(f"\n候选状态变化（共 {len(candidate_changes)} 个）:")
            for sc in candidate_changes[:5]:  # 最多显示5个
                index = sc.get('_index', '?')
                classification = sc.get('_classification', 'unknown')
                plot_sensitive = sc.get('plot_sensitive', 0)
                target_path = sc.get('_target_path', [])
                lines.append(f"  [{index}] {classification}: {target_path} (敏感度: {plot_sensitive:.2f})")
        else:
            lines.append("\n无候选状态变化")
        
        return "\n".join(lines)
    
    def _format_state_changes(self, changes: List[Dict[str, Any]]) -> str:
        """格式化状态变化列表为字符串"""
        if not changes:
            return "  无"
        
        lines = []
        for sc in changes:
            target_path = sc.get('_target_path', sc.get('target_path', []))
            value = sc.get('value', '')
            change_type = sc.get('change_type', sc.get('op', 'set'))
            lines.append(f"  - {target_path} -> {value} (操作: {change_type})")
        return "\n".join(lines)
    
    async def process_event(
        self,
        event_id: str,
    ) -> Dict[str, Any]:
        """
        处理单个事件：让 LLM 选择状态变化并生成主线记录 🎯
        
        Args:
            event_id: 事件ID
            
        Returns:
            处理结果：{
                "event_id": "...",
                "description": "...",
                "decision_point": "...",  // 可选
                "canonical_choice": "...",  // 可选
                "pre_condition": {...},  // 可选
                "state_changes": [...],  // 所有状态变化（标准格式 + 元数据）
                "selected_state_changes": [...],  // LLM 选择的状态变化索引
                "updated_states": UpdatedState,  // 只包含 LLM 选中的变化
                "snapshot": StateSnapshot,
            }
        """
        print(f"处理事件: {event_id}")
        
        # 1. 获取事件信息和所有状态变化
        print(f"  获取事件信息和状态变化...")
        event_info = await self.state_applier.get_event_info(event_id)
        if event_info is None:
            raise ValueError(f"事件 {event_id} 不存在")
        
        all_state_changes = await self.state_applier.get_event_state_changes(event_id)
        
        # 2. 分类状态变化
        print(f"  分类状态变化...")
        classified = self._classify_state_changes(all_state_changes)
        
        print(f"    世界事实型: {len(classified['world_fact'])} 个")
        print(f"    人物内在状态: {len(classified['potential'])} 个")
        print(f"    人物关系状态: {len(classified['conditional'])} 个")
        
        # 3. 生成主线记录（让 LLM 选择哪些状态变化应该在主线中 apply）
        print(f"  生成主线记录（LLM 选择状态变化）...")
        
        # 先创建临时快照（用于生成主线记录）
        temp_snapshot = self.state_applier._create_state_snapshot(self.current_states, current_event_id=event_id)
        
        canonical_record = await self.generate_canonical_record(
            event_id=event_id,
            event_info=event_info,
            state_changes=all_state_changes,
            snapshot=temp_snapshot,
        )
        
        # 4. 根据 LLM 的选择，apply 选中的状态变化
        updated_states = self.current_states
        snapshot = None
        
        selected_indices = canonical_record.get("selected_state_changes", [])
        if selected_indices:
            print(f"  LLM 选择了 {len(selected_indices)} 个状态变化需要在主线中 apply")
            
            # 合并所有状态变化（按索引顺序）
            all_changes_list = classified["world_fact"] + classified["potential"] + classified["conditional"]
            
            # 根据索引获取选中的状态变化
            # 注意：分类后的状态变化已经保留了原始 StateChange 格式，可以直接使用
            selected_state_changes = []
            for idx in selected_indices:
                if 0 <= idx < len(all_changes_list):
                    sc = all_changes_list[idx]
                    # 提取标准 StateChange 格式（移除元数据字段）
                    state_change = self._extract_standard_state_change(sc)
                    selected_state_changes.append(state_change)
            
            if selected_state_changes:
                print(f"  应用 LLM 选中的状态变化...")
                # 使用 StateApplier 应用已选中的变化
                # 注意：StateApplier 不知道完整的 state_changes 池子，只知道被选中的
                updated_states, _, snapshot = await self.state_applier.apply_state_change(
                    event_id=event_id,
                    selected_state_changes=selected_state_changes,
                    current_states=self.current_states,
                    event_info=event_info,
                )
                
                # 更新当前状态
                self.current_states = updated_states
            else:
                snapshot = self.state_applier._create_state_snapshot(self.current_states, current_event_id=event_id)
        else:
            print(f"  LLM 没有选择任何状态变化需要在主线中 apply")
            snapshot = self.state_applier._create_state_snapshot(self.current_states, current_event_id=event_id)
        
        # 打印生成的主线记录，方便查看效果
        print(f"\n{'='*70}")
        print(f"主线记录 {event_id}:")
        print(f"{'='*70}")
        import json
        print(json.dumps(canonical_record, ensure_ascii=False, indent=2))
        print(f"{'='*70}\n")
        
        result = {
            "event_id": event_id,
            "description": canonical_record.get("description", ""),
            "decision_point": canonical_record.get("decision_point"),
            "canonical_choice": canonical_record.get("canonical_choice"),
            "pre_condition": canonical_record.get("pre_condition"),
            "state_changes": canonical_record.get("state_changes", []),
            "selected_state_changes": canonical_record.get("selected_state_changes", []),
            "updated_states": updated_states.model_dump() if hasattr(updated_states, "model_dump") else updated_states.dict(),
            # 使用 LLM 侧格式的精简快照用于存储（StateSnapshot 没有 to_dict() 方法）
            "snapshot": snapshot.to_dict_for_llm() if snapshot else None,
        }
        
        return result
    
    
    async def generate_canonical_branch(
        self,
        start_event_id: Optional[str] = None,
        max_events: Optional[int] = None,
        resume: bool = True,
        save_progress_interval: int = 1,
    ) -> Dict[str, Any]:
        """
        生成主线（世界真相记录）📜
        
        Args:
            start_event_id: 起始事件ID（如果为 None，会自动查找第一个事件）
            max_events: 最大处理事件数（如果为 None，处理所有事件）
            resume: 是否从上次进度恢复（默认：True）
            save_progress_interval: 每处理几个事件保存一次进度（默认：1，即每个事件都保存）
            
        Returns:
            生成结果：{
                "start_event_id": "...",
                "processed_events": [
                    {
                        "event_id": "...",
                        "description": "...",
                        "decision_point": "...",  // 可选
                        "canonical_choice": "...",  // 可选
                        "pre_condition": {...},  // 可选
                        "state_changes": [...],
                        ...
                    },
                    ...
                ],
                "final_snapshot": StateSnapshot,
                "total_events": int
            }
        """
        print("=" * 70)
        print("🌟 Canonical Branch 生成器")
        print("=" * 70)
        print()
        
        # 尝试从进度文件恢复
        processed_events = []
        current_index = 0
        event_ids_to_process = []
        
        if resume:
            print("🔍 检查是否有保存的进度...")
            progress_data = self.load_progress()
            if progress_data:
                print(f"✅ 找到保存的进度，从第 {progress_data['current_index'] + 1} 个事件继续")
                processed_events = progress_data.get("processed_events", [])
                event_ids_to_process = progress_data.get("event_ids_to_process", [])
                current_index = progress_data.get("current_index", 0)
                start_event_id = progress_data.get("start_event_id", start_event_id)
                print(f"  已处理: {len(processed_events)} 个事件")
                print(f"  剩余: {len(event_ids_to_process) - current_index} 个事件")
                print()
            else:
                print("ℹ️  没有找到保存的进度，从头开始")
                print()
        
        # 如果没有从进度恢复，重新初始化
        if not event_ids_to_process:
            # 1. 确定起始事件
            if start_event_id is None:
                print("🔍 查找第一个事件...")
                start_event_id = await self.get_first_event_id()
                if start_event_id is None:
                    raise ValueError("❌ 没有找到起始事件！")
            
            print(f"✅ 起始事件: {start_event_id}")
            print()
            
            # 2. 获取所有事件ID（从起始事件开始）
            all_event_ids = await self.get_all_event_ids()
            
            # 找到起始事件的位置
            try:
                start_index = all_event_ids.index(start_event_id)
                event_ids_to_process = all_event_ids[start_index:]
            except ValueError:
                print(f"⚠️  起始事件 {start_event_id} 不在事件列表中，使用所有事件")
                event_ids_to_process = all_event_ids
            
            # 限制处理数量
            if max_events:
                event_ids_to_process = event_ids_to_process[:max_events]
            
            # 3. 初始化当前状态（从基线）
            print("📊 初始化状态（从基线）...")
            self.current_states = self.state_applier._initialize_states_from_baseline()
            print()
        
        print(f"📚 将处理 {len(event_ids_to_process)} 个事件（从第 {current_index + 1} 个开始）")
        print()
        
        # 4. 逐个处理事件（从 current_index 开始）
        final_snapshot = None
        
        for i in range(current_index, len(event_ids_to_process)):
            event_id = event_ids_to_process[i]
            event_number = i + 1
            
            print(f"[{event_number}/{len(event_ids_to_process)}] 处理事件: {event_id}")
            
            try:
                result = await self.process_event(event_id)
                processed_events.append(result)
                final_snapshot = result["snapshot"]
                print(f"  ✅ 完成")
                
                # 每处理 save_progress_interval 个事件保存一次进度
                if (event_number) % save_progress_interval == 0:
                    self.save_progress(
                        start_event_id=start_event_id,
                        processed_events=processed_events,
                        event_ids_to_process=event_ids_to_process,
                        current_index=event_number,  # 下一个要处理的事件索引
                    )
            except Exception as e:
                print(f"  ❌ 处理失败: {e}")
                import traceback
                traceback.print_exc()
                # 即使失败也保存进度，方便下次继续
                self.save_progress(
                    start_event_id=start_event_id,
                    processed_events=processed_events,
                    event_ids_to_process=event_ids_to_process,
                    current_index=event_number,
                )
            
            print()
        
        # 5. 删除进度文件（处理完成）
        if self.progress_file.exists():
            self.progress_file.unlink()
            print(f"✅ 处理完成，已删除进度文件")
        print()
        
        # 7. 返回结果
        return {
            "start_event_id": start_event_id,
            "processed_events": processed_events,
            "final_snapshot": final_snapshot,  # 已经是字典格式（从 process_event 返回）
            "total_events": len(processed_events),
        }
    
    def save_progress(
        self,
        start_event_id: str,
        processed_events: List[Dict[str, Any]],
        event_ids_to_process: List[str],
        current_index: int,
    ) -> None:
        """
        保存处理进度 💾
        
        Args:
            start_event_id: 起始事件ID
            processed_events: 已处理的事件列表
            event_ids_to_process: 待处理的事件ID列表
            current_index: 当前处理到第几个事件（从0开始）
        """
        progress_data = {
            "start_event_id": start_event_id,
            "processed_events": processed_events,
            "event_ids_to_process": event_ids_to_process,
            "current_index": current_index,
            "current_states": self.current_states.model_dump() if hasattr(self.current_states, "model_dump") else (self.current_states.dict() if self.current_states else None),
        }
        
        self.progress_file.parent.mkdir(parents=True, exist_ok=True)
        with open(self.progress_file, 'w', encoding='utf-8') as f:
            json.dump(progress_data, f, ensure_ascii=False, indent=2)
        
        print(f"  💾 进度已保存到: {self.progress_file}")
    
    def load_progress(self) -> Optional[Dict[str, Any]]:
        """
        加载处理进度 📂
        
        Returns:
            进度数据，如果文件不存在则返回 None
        """
        if not self.progress_file.exists():
            return None
        
        try:
            with open(self.progress_file, 'r', encoding='utf-8') as f:
                progress_data = json.load(f)
            
            # 恢复当前状态
            if progress_data.get("current_states"):
                from state_manager.models import UpdatedState
                self.current_states = UpdatedState(**progress_data["current_states"])
            
            return progress_data
        except Exception as e:
            print(f"⚠️  加载进度失败: {e}")
            return None
    
    async def generate_for_first_event(self) -> Dict[str, Any]:
        """
        为第一个事件生成分支内容 🌟
        
        这是一个便捷方法，只处理第一个事件。
        
        Returns:
            生成结果
        """
        return await self.generate_canonical_branch(max_events=1)
    
    def close(self):
        """关闭资源"""
        if self.state_applier:
            self.state_applier.close()


__all__ = ["CanonicalBranchGenerator"]
