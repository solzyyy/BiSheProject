"""
状态变化生成器 - 统一的状态变化生成逻辑 🎯✨

功能：
1. 为分叉点生成状态变化（基于分支选择）- `generate_for_branch_choice()`
2. 为分支新生成的事件生成状态变化（基于分支事件内容）- `generate_for_branch_new_event()`

注意：
- `generate_for_branch_event()` 已过时，不再使用
- 现在统一使用 `generate_for_branch_new_event()` 为分支事件生成状态变化

这个类封装了所有状态变化生成的通用逻辑，避免代码重复。
"""

import json
import os
from datetime import datetime
from typing import Dict, List, Any, Optional
from pathlib import Path

from core.llm_client import AsyncLLMClient
from state_manager.state_applier import StateApplier
from state_manager.models import UpdatedState


class StateChangeGenerator:
    """
    状态变化生成器 - 统一的状态变化生成逻辑 🎯
    
    核心功能：
    - 为分叉点生成状态变化（`generate_for_branch_choice()`）
    - 为分支新生成的事件生成状态变化（`generate_for_branch_new_event()`）
    - 提供统一的接口和可配置的提示词
    - 确保生成的状态变化符合逻辑和格式要求
    
    注意：
    - 只负责生成状态变化列表，不负责应用状态变化
    - 状态变化的应用由 StateApplier 负责
    - `generate_for_branch_event()` 已过时，不再使用
    """
    
    def __init__(
        self,
        llm_client: Optional[AsyncLLMClient] = None,
        state_applier: Optional[StateApplier] = None,
        canonical_branch_path: str = "out/canonical_branch.json",
        decision_analysis_path: str = "out/decision_points_analysis.json",
    ):
        """
        初始化状态变化生成器
        
        Args:
            llm_client: LLM 客户端
            state_applier: 状态应用器
            canonical_branch_path: 主线记录文件路径
            decision_analysis_path: 决策点分析文件路径
        """
        self.llm_client = llm_client or AsyncLLMClient.create_default()
        self.state_applier = state_applier or StateApplier()
        self.canonical_branch_path = Path(canonical_branch_path)
        self.decision_analysis_path = Path(decision_analysis_path)
        
        # 加载关键事件信息（如果存在）
        self.critical_events: Optional[set[str]] = None
        self._load_critical_events()
    
    def _load_critical_events(self) -> None:
        """加载关键事件信息（从决策点分析文件）"""
        if not self.decision_analysis_path.exists():
            return
        
        try:
            with open(self.decision_analysis_path, "r", encoding="utf-8") as f:
                analysis = json.load(f)
            critical_events = analysis.get("critical_events", [])
            self.critical_events = set(critical_events) if critical_events else None
        except Exception:
            self.critical_events = None
    
    async def generate_for_branch_choice(
        self,
        fork_event_id: str,
        event_info: Dict[str, Any],
        branch_choice: Dict[str, Any],
        base_snapshot: Optional[Dict[str, Any]],
        original_state_changes: List[Dict[str, Any]],
        canonical_selected_state_changes: List[Dict[str, Any]],
        canonical_choice: Optional[str] = None,
    ) -> List[Dict[str, Any]]:
        """
        为分叉点的分支选择生成状态变化 🎯
        
        这是分叉点的状态变化，基于分支选择生成，与主线选择形成对比。
        
        Args:
            fork_event_id: 分叉事件ID
            event_info: 事件信息
            branch_choice: 分支选择（包含 choice_id, description, reasoning）
            base_snapshot: 基础状态快照（分叉点之前的状态）
            original_state_changes: 原事件的所有可能状态变化（参考）
            canonical_selected_state_changes: 主线选择的状态变化（对比参考）
            canonical_choice: 主线选择（可选，用于对比）
            
        Returns:
            生成的状态变化列表
        """
        # 格式化主线选择的状态变化
        canonical_sc_text = ""
        if canonical_selected_state_changes:
            canonical_sc_text = f"""
主线选择（{canonical_choice}）对应的状态变化：
{json.dumps(canonical_selected_state_changes, ensure_ascii=False, indent=2)}
"""
        
        # 格式化原事件的状态变化（作为参考）
        original_sc_text = ""
        if original_state_changes:
            limited_sc = original_state_changes 
            original_sc_text = f"""
原事件的所有可能状态变化（参考，了解状态变化类型和范围）：
{json.dumps(limited_sc, ensure_ascii=False, indent=2)}
"""
        
        canonical_choice_text = ""
        if canonical_choice:
            canonical_choice_text = f"\n- 主线选择: {canonical_choice}"
        
        # 获取精简的静态人设和状态快照
        personas_text = self._get_relevant_personas_text(event_info, base_snapshot)
        snapshot_text = self._get_simplified_snapshot_text(base_snapshot)
        
        from state_manager.dimensions import get_dimension_list_for_prompt, WORLD_STATE_GUIDANCE

        prompt = f"""你是一个支线状态变化生成助手，根据分支选择生成对应的状态变化。

**核心理念**：
- 状态变化 = 赋值：把某个维度设为新的描述性文本
- 只使用下方列出的合法维度，不要发明新维度
- 分支选择的状态变化应与主线选择形成对比

**合法维度列表（只能从中选择）**：
{get_dimension_list_for_prompt()}

{WORLD_STATE_GUIDANCE}

**事件信息**：
- 事件ID: {fork_event_id}
- 事件描述: {event_info.get('行动', '')}
- 分支选择: {branch_choice.get('choice_id')} - {branch_choice.get('description', '')}
- 选择理由: {branch_choice.get('reasoning', '')}{canonical_choice_text}
{personas_text}
{snapshot_text}

**参考信息**（用于理解状态变化的类型和范围）：
{canonical_sc_text}
{original_sc_text}

**任务**：根据分支选择生成状态变化，优先生成人物和关系的变化。

**输出格式（JSON）**：
{{
    "state_changes": [
        {{
            "target_type": "character|relationship|world",
            "target_id": "目标ID",
            "dimension": "从上方合法维度中选择",
            "value": "描述性文本"
        }}
    ],
    "reasoning": "2-3句话解释"
}}
"""
        
        return await self._invoke_llm_and_validate(prompt, fork_event_id)
    
    
    async def generate_for_branch_new_event(
        self,
        event_id: str,
        event_description: str,
        branch_choice: Dict[str, Any],
        current_states: Optional[UpdatedState],
        previous_event: Optional[Dict[str, Any]] = None,
    ) -> List[Dict[str, Any]]:
        """
        为分支新生成的事件生成状态变化 🌿✨
        
        这是分支事件的状态变化，基于分支事件内容生成。
        
        **使用场景**：
        - 在 `path_generation_functions.py` 中：为通过 LangGraph 生成的分支事件生成状态变化
        - 在 `branch_event_generator.py` 中：为手动生成的分支事件生成状态变化
        
        **流程**：
        1. LLM 生成分支事件内容（event_data, description）
        2. 调用此方法生成对应的状态变化
        3. 状态变化会被添加到 branch_event 中
        4. 后续通过 StateApplier 应用状态变化到当前状态
        
        Args:
            event_id: 分支事件ID（格式：{fork_event_id}_branch_{choice_id}_{sequence_number}）
            event_description: 分支事件描述（1-2句话，描述事件的核心行动和结果）
            branch_choice: 分支选择（包含 choice_id, description, reasoning，用于上下文）
            current_states: 分支的当前状态（累积的状态，包含之前所有分支事件的状态变化）
            previous_event: 上一个分支事件（可选，用于上下文，包含 event_id 和 description）
            
        Returns:
            生成的状态变化列表，每个状态变化包含：
            - source_event: 事件ID
            - target_type: character | relationship | world
            - target_id: 目标ID
            - dimension: 维度名称
            - change_type: set | add | modify
            - value: 变化值
            - plot_sensitive: 0.0-1.0（剧情敏感度）
            - condition: 条件（可选）
            - is_obstacle: 是否是障碍（可选）
        """
        # 格式化当前状态（用于 LLM 理解当前分支的累积状态）
        states_text = ""
        if current_states:
            # 使用 model_dump() 或 dict() 方法获取状态字典
            if hasattr(current_states, "model_dump"):
                states_dict = current_states.model_dump()
            elif hasattr(current_states, "dict"):
                states_dict = current_states.dict()
            else:
                states_dict = current_states
            
            states_text = f"""
当前分支状态（累积的状态，包含之前所有分支事件的状态变化）：
{json.dumps(states_dict, ensure_ascii=False, indent=2)}
"""
        
        # 格式化上一个事件（用于 LLM 理解事件序列的连续性）
        previous_event_text = ""
        if previous_event:
            prev_desc = previous_event.get("description", "")
            prev_id = previous_event.get("event_id", "")
            previous_event_text = f"""
上一个分支事件（用于理解事件序列的连续性）：
- 事件ID: {prev_id}
- 事件描述: {prev_desc}
"""
        
        # 格式化分支选择信息（用于 LLM 理解分支的起点和逻辑）
        branch_choice_text = f"""
分支选择（分支的起点和逻辑）：
- 选择ID: {branch_choice.get('choice_id', 'unknown')}
- 选择描述: {branch_choice.get('description', '')}
- 选择理由: {branch_choice.get('reasoning', '')}
"""
        
        from state_manager.dimensions import get_dimension_list_for_prompt, WORLD_STATE_GUIDANCE

        prompt = f"""你是一个分支事件状态变化生成助手，负责为分支新生成的事件生成对应的状态变化。

**核心理念**：
- 状态变化 = 赋值：把某个维度设为新的描述性文本
- 只使用下方列出的合法维度，不要发明新维度
- 优先生成人物和关系的状态变化，世界状态变化要极度克制

**合法维度列表（只能从中选择）**：
{get_dimension_list_for_prompt()}

{WORLD_STATE_GUIDANCE}

**当前分支信息**：
{branch_choice_text}
{previous_event_text}
{states_text}

**当前事件**：
- 事件ID: {event_id}
- 事件描述: {event_description}

**任务**：
根据事件内容和当前状态，生成状态变化。每条状态变化就是一句赋值：把[谁]的[哪个维度]设成[什么值]。

**输出格式（JSON）**：
{{
    "state_changes": [
        {{
            "target_type": "character|relationship|world",
            "target_id": "目标ID（人物用角色ID如C011，关系用[C011, C013]，世界用英文标识如painting_tools）",
            "dimension": "从上方合法维度中选择",
            "value": "描述性文本（如'从追求艺术转向保护师傅'）"
        }}
    ],
    "reasoning": "2-3句话，解释状态变化的逻辑"
}}
"""
        
        return await self._invoke_llm_and_validate(prompt, event_id)
    
    async def generate_for_mainline_event_after_merge(
        self,
        event_id: str,
        event_info: Dict[str, Any],
        current_states: Optional[UpdatedState],
        canonical_state_changes: List[Dict[str, Any]],
        previous_event: Optional[Dict[str, Any]] = None,
        delta_since_fork_text: Optional[str] = None,
        target_ending: Optional[Dict[str, Any]] = None,
    ) -> List[Dict[str, Any]]:
        """
        为合流后的主线事件生成状态变化 🔄✨
        
        这是合流后主线事件的状态变化，基于分支路径后的新世界状态重新生成。
        若提供 target_ending，生成的状态变化应使剧情自然导向该结局。
        
        **使用场景**：
        - 在 `process_mainline_after_merge` 中：先确定结局，再生成事件，使事件导向该结局
        
        Args:
            event_id: 主线事件ID（例如：E15, E16）
            event_info: 事件信息（从 Neo4j 获取）
            current_states: 分支路径后的当前状态
            canonical_state_changes: 主线记录中的状态变化（作为参考）
            previous_event: 上一个主线事件（可选）
            delta_since_fork_text: 分支以来（从分叉点到当前）的净变化摘要（可选）。用于约束合流后主线的状态变化不能“回滚/否认”分支后果。
            target_ending: 已确定的目标结局（可选），状态变化应使剧情导向该结局
            
        Returns:
            生成的状态变化列表，每个状态变化包含：
            - target_type: character | relationship | world
            - target_id: 目标ID
            - dimension: 维度名称
            - value: 变化值（描述性文本）
        """
        from state_manager.dimensions import get_dimension_list_for_prompt, WORLD_STATE_GUIDANCE

        # ── 只提取与 canonical changes 相关的实体状态（减少 token）──
        states_text = ""
        if current_states and canonical_state_changes:
            relevant = self._extract_relevant_states(current_states, canonical_state_changes)
            states_text = f"""
当前状态（仅列出本事件涉及的实体）：
{json.dumps(relevant, ensure_ascii=False, indent=2)}
"""
        
        previous_event_text = ""
        if previous_event:
            prev_desc = previous_event.get("description", "")
            prev_id = previous_event.get("event_id", "")
            previous_event_text = f"""
上一个事件：{prev_id} — {prev_desc}
"""
        
        # ── 精简 canonical changes：只保留 4 个核心字段 ──
        canonical_changes_text = ""
        if canonical_state_changes:
            simplified = self._simplify_canonical_changes(canonical_state_changes)
            canonical_changes_text = f"""
主线记录中的状态变化（参考，需根据新状态调整）：
{json.dumps(simplified, ensure_ascii=False, indent=2)}
"""
        
        event_info_text = f"""
事件信息：
- 事件ID: {event_id}
- 描述: {event_info.get('描述', '')}
- 场景: {event_info.get('场景', '')}
- 人物: {', '.join(event_info.get('人物', []))}
"""
        
        prompt = f"""你是一个主线事件状态变化生成助手，为合流后的主线事件生成状态变化。

**核心理念**：
- 状态变化 = 赋值：把某个维度设为新的描述性文本
- 参考但不照搬主线记录，根据分支路径后的新状态调整
- 保持主线事件的本质影响

**合法维度列表（只能从中选择）**：
{get_dimension_list_for_prompt()}

{WORLD_STATE_GUIDANCE}

"""
        if delta_since_fork_text and str(delta_since_fork_text).strip():
            prompt += f"""**分支以来的净变化摘要（delta_since_fork）**：
以下变化已在分支中发生并累积到当前状态。你生成的状态变化必须尊重它们：
- 不得否认/回滚这些变化；
- 若需要覆盖同一维度的旧值，必须在 reasoning 中说明“为何演化/加深/转向”。

{str(delta_since_fork_text).strip()}

"""
        prompt += f"""
{states_text}
{previous_event_text}
**主线事件信息**：
{event_info_text}
{canonical_changes_text}
"""
        if target_ending:
            prompt += f"""
**目标结局**（状态变化应导向此结局）：
- {target_ending.get('name', '未知')}: {target_ending.get('description', '')[:200]}
"""
        prompt += f"""
**任务**：参考主线状态变化，基于分支路径后的新状态重新生成。

**输出格式（JSON）**：
{{
    "state_changes": [
        {{
            "target_type": "character|relationship|world",
            "target_id": "目标ID",
            "dimension": "从上方合法维度中选择",
            "value": "描述性文本"
        }}
    ],
    "reasoning": "2-3句话解释"
}}
"""
        
        return await self._invoke_llm_and_validate(prompt, event_id)
    
    # ── helpers ─────────────────────────────────────────────

    @staticmethod
    def _extract_relevant_states(
        current_states: "UpdatedState",
        canonical_state_changes: List[Dict[str, Any]],
    ) -> Dict[str, Any]:
        """从 UpdatedState 中只提取 canonical_state_changes 涉及的实体。"""
        relevant: Dict[str, Any] = {
            "character_states": {},
            "relationship_states": {},
            "world_states": {},
        }
        for sc in canonical_state_changes:
            tt = sc.get("target_type")
            tid = sc.get("target_id")
            if tt == "character" and isinstance(tid, str):
                if tid in current_states.character_states:
                    relevant["character_states"][tid] = current_states.character_states[tid]
            elif tt == "relationship":
                rel_key = (
                    f"{tid[0]}|{tid[1]}"
                    if isinstance(tid, list) and len(tid) >= 2
                    else tid
                )
                if rel_key in current_states.relationship_states:
                    relevant["relationship_states"][rel_key] = current_states.relationship_states[rel_key]
            elif tt == "world" and isinstance(tid, str):
                if tid in current_states.world_states:
                    relevant["world_states"][tid] = current_states.world_states[tid]
        return relevant

    @staticmethod
    def _simplify_canonical_changes(
        canonical_state_changes: List[Dict[str, Any]],
    ) -> List[Dict[str, Any]]:
        """精简 canonical changes，只保留 4 个核心字段给 prompt。"""
        keep = {"target_type", "target_id", "dimension", "value"}
        return [{k: v for k, v in sc.items() if k in keep} for sc in canonical_state_changes]

    # ── LLM invocation ───────────────────────────────────

    async def _invoke_llm_and_validate(
        self,
        prompt: str,
        event_id: str,
    ) -> List[Dict[str, Any]]:
        """
        调用 LLM 生成状态变化并验证格式 🔍
        
        Args:
            prompt: 提示词
            event_id: 事件ID（用于设置 source_event）
            
        Returns:
            验证后的状态变化列表
        """
        def _debug_dump(stage: str, payload: Dict[str, Any]) -> None:
            """
            调试：把提示词/输入输出落盘，便于排查“黑盒”问题。
            启用方式：设置环境变量 DEBUG_STATE_PROMPTS=1。
            """
            if os.environ.get("DEBUG_STATE_PROMPTS", "").strip() not in ("1", "true", "True", "yes", "YES"):
                return
            try:
                project_root = Path(__file__).resolve().parent.parent.parent  # .../src
                out_dir = project_root / "debug_prompts" / "state_change_generator"
                out_dir.mkdir(parents=True, exist_ok=True)
                ts = datetime.now().strftime("%Y%m%d_%H%M%S_%f")
                safe_eid = "".join(c if (c.isalnum() or c in ("_", "-", ".")) else "_" for c in (event_id or "unknown"))
                fp = out_dir / f"{ts}_{safe_eid}_{stage}.json"
                fp.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
            except Exception:
                return

        _debug_dump("prompt", {"event_id": event_id, "prompt": prompt})

        # 调用 LLM 生成状态变化
        response = await self.llm_client.invoke(prompt, return_json=True)
        _debug_dump("llm_raw_response", {"event_id": event_id, "response": response})
        
        # 解析响应
        if isinstance(response, str):
            response = json.loads(response)
        
        state_changes = response.get("state_changes", [])
        _debug_dump("parsed_state_changes", {"event_id": event_id, "state_changes": state_changes})
        
        for sc in state_changes:
            if "source_event" not in sc:
                sc["source_event"] = event_id
        
        return state_changes
    
    def _get_relevant_personas_text(
        self,
        event_info: Dict[str, Any],
        base_snapshot: Optional[Dict[str, Any]],
    ) -> str:
        """
        获取精简的静态人设文本（只包含事件中涉及的人物）👥
        
        Args:
            event_info: 事件信息
            base_snapshot: 基础快照（用于提取涉及的人物）
            
        Returns:
            静态人设文本（如果没有人设则返回空字符串）
        """
        if not hasattr(self.state_applier, "personas") or not self.state_applier.personas:
            return ""
        
        # 从事件信息和快照中提取涉及的人物ID
        character_ids = set()
        
        # 从事件信息中提取人物（如果有"人物"字段）
        if "人物" in event_info:
            characters = event_info["人物"]
            if isinstance(characters, list):
                character_ids.update(characters)
            elif isinstance(characters, str):
                character_ids.add(characters)
        
        # 从快照中提取人物（如果有 character_states）
        if base_snapshot and "character_states" in base_snapshot:
            character_ids.update(base_snapshot["character_states"].keys())
        
        # 如果没有人设信息，返回空字符串
        if not character_ids:
            return ""
        
        # 只获取涉及人物的精简人设（只包含关键信息）
        personas_list = []
        for char_id in list(character_ids)[:5]:  # 最多显示5个人物
            if char_id in self.state_applier.personas:
                persona = self.state_applier.personas[char_id]
                personas_list.append({
                    "id": persona.id,
                    "name": persona.name,
                    "role": persona.role,
                    "disposition": persona.disposition,
                })
        
        if not personas_list:
            return ""
        
        return f"""
相关人物静态人设（参考）：
{json.dumps(personas_list, ensure_ascii=False, indent=2)}
"""
    
    def _get_simplified_snapshot_text(
        self,
        base_snapshot: Optional[Dict[str, Any]],
    ) -> str:
        """
        获取精简的状态快照文本（只包含人物和关系状态，去掉世界/物品状态）📸
        
        Args:
            base_snapshot: 基础快照字典
            
        Returns:
            精简的状态快照文本（如果没有快照则返回空字符串）
        """
        if not base_snapshot:
            return ""
        
        simplified = {}
        
        # 只保留人物状态
        if "character_states" in base_snapshot:
            # 只保留关键维度，避免过多信息
            character_states = {}
            for char_id, states in base_snapshot["character_states"].items():
                if isinstance(states, dict):
                    # 只保留前5个关键维度
                    limited_states = dict(list(states.items())[:5])
                    if limited_states:
                        character_states[char_id] = limited_states
            if character_states:
                simplified["character_states"] = character_states
        
        # 只保留关系状态
        if "relationship_states" in base_snapshot:
            # 只保留前5个关系
            relationship_states = base_snapshot["relationship_states"]
            if isinstance(relationship_states, dict):
                limited_relationships = dict(list(relationship_states.items())[:5])
                if limited_relationships:
                    simplified["relationship_states"] = limited_relationships
        
        if not simplified:
            return ""
        
        return f"""
当前状态（精简，只包含人物和关系状态）：
{json.dumps(simplified, ensure_ascii=False, indent=2)}
"""


__all__ = ["StateChangeGenerator"]

