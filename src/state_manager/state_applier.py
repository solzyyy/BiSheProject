"""
状态应用器 - 使用 LLM function calling 应用状态变化 🎯✨

通过扫描事件知识图谱中与事件节点相连的 StateChange 节点，
利用 LLM 的 function calling 功能自定义更新逻辑。
"""

import json
import asyncio
import os
import sys
from datetime import datetime
from typing import Dict, List, Any, Optional
from pathlib import Path

# 添加 src 目录到路径
src_dir = Path(__file__).parent.parent.parent
if str(src_dir) not in sys.path:
    sys.path.insert(0, str(src_dir))

from core.neo4j_client import Neo4jClient
from character.models.state_change import StateChange
from character.models.character_persona import CharacterPersona
from character.models.world_state import WorldState, build_world_state_from_states
from core.llm_client import AsyncLLMClient
from state_manager.models import UpdatedState, PersonaUpdate

from state_manager.state_snapshot import StateSnapshot


class StateApplier:
    """
    状态应用器 - 使用 LLM function calling 应用状态变化 🌊
    
    设计理念：
    - 扫描 Neo4j 中与事件相连的 StateChange 节点
    - 使用 LLM function calling 自定义更新逻辑
    - 结合静态人设，判断是否需要更新人设
    - 支持并发处理和重试机制
    
    核心功能：
    - Event + StateChange 的结构处理
    - 状态更新（人物、关系、世界）
    - 状态快照生成
    
    注意：摘要、大总结、文游文本等是"派生品"，不是"必需品"，已移除。
    """
    
    def __init__(
        self,
        llm_client: Optional[AsyncLLMClient] = None,
        neo4j_client: Optional[Neo4jClient] = None,
        baseline_path: Optional[str] = None,
        personas_path: Optional[str] = None,
        entities_path: Optional[str] = None,
        max_concurrent: int = 5,
    ):
        """
        初始化状态应用器
        
        Args:
            llm_client: LLM 客户端（如果为 None，会创建默认客户端）
            neo4j_client: Neo4j 客户端（如果为 None，会创建新客户端）
            baseline_path: 状态基线文件路径（默认：out/state_baseline.json）
            personas_path: 静态人设文件路径（默认：out/character_personas.json）
            entities_path: 实体映射文件路径（默认：out/entities.json）
            max_concurrent: 最大并发数
        """
        # 创建 deepseek 客户端用于 function calling
        self.llm_client = llm_client or AsyncLLMClient.create_default("deepseek")
        self.neo4j_client = neo4j_client or Neo4jClient()
        self.max_concurrent = max_concurrent
        self.semaphore = asyncio.Semaphore(max_concurrent)
        
        # 确保 StateChange 索引已创建（用于优化查询性能）🚀
        try:
            self.neo4j_client.init_state_change_indexes()
        except Exception as e:
            print(f"[yellow]初始化 StateChange 索引时出错（可能已存在）: {e}[/yellow]")
        
        # 加载状态基线
        if baseline_path is None:
            baseline_path = "out/state_baseline.json"
        self.baseline = self._load_baseline(baseline_path)
        
        # 加载静态人设
        if personas_path is None:
            personas_path = "out/character_personas.json"
        self.personas = self._load_personas(personas_path)
        
        # 加载实体映射（用于名称到ID的映射）
        if entities_path is None:
            entities_path = "out/entities.json"
        self.entities_map = self._load_entities_map(entities_path)
    
    def _load_baseline(self, path: str) -> Dict[str, Any]:
        """加载状态基线"""
        baseline_file = Path(path)
        if not baseline_file.exists():
            raise FileNotFoundError(f"状态基线文件不存在: {path}")
        
        with open(baseline_file, "r", encoding="utf-8") as f:
            data = json.load(f)
        
        return data.get("baseline", {})
    
    def _load_personas(self, path: str) -> Dict[str, CharacterPersona]:
        """加载静态人设"""
        personas_file = Path(path)
        if not personas_file.exists():
            print(f"⚠️  静态人设文件不存在: {path}，将使用空字典")
            return {}
        
        with open(personas_file, "r", encoding="utf-8") as f:
            data = json.load(f)
        
        personas_dict = {}
        for persona_data in data.get("personas", []):
            persona = CharacterPersona(**persona_data)
            personas_dict[persona.id] = persona
        
        return personas_dict
    
    def _load_entities_map(self, path: str) -> Dict[str, str]:
        """
        加载实体映射（名称 -> ID）🆔
        
        Args:
            path: 实体文件路径
            
        Returns:
            名称到ID的映射字典：{canonical_name: id, alias: id, ...}
        """
        entities_file = Path(path)
        if not entities_file.exists():
            print(f"⚠️  实体映射文件不存在: {path}，将使用空字典")
            return {}
        
        with open(entities_file, "r", encoding="utf-8") as f:
            data = json.load(f)
        
        entities_map = {}
        for entity in data.get("entities", []):
            entity_id = entity.get("id")
            canonical_name = entity.get("canonical_name", "")
            aliases = entity.get("aliases", [])
            
            # 映射 canonical_name -> id
            if canonical_name:
                entities_map[canonical_name] = entity_id
            
            # 映射所有别名 -> id
            for alias in aliases:
                if alias:
                    entities_map[alias] = entity_id
        
        return entities_map
    
    async def get_event_state_changes(self, event_id: str) -> List[Dict[str, Any]]:
        """
        从 Neo4j 获取指定事件触发的所有状态变化
        
        使用 state_source_target_dimension 复合索引优化查询和排序性能 🚀
        
        Args:
            event_id: 事件ID
            
        Returns:
            状态变化列表（字典格式）
        """
        # 优化查询：使用 state_source_target_dimension 复合索引
        # 这个索引包含 (source_event, target_type, dimension)
        # 可以同时用于 WHERE 过滤和 ORDER BY 排序，性能最优
        query = """
        MATCH (s:StateChange)
        WHERE s.source_event = $event_id
        RETURN s.source_event as source_event,
               s.target_type as target_type,
               s.target_id as target_id,
               s.dimension as dimension,
               s.change_type as change_type,
               s.value as value,
               s.is_obstacle as is_obstacle,
               s.logic_impact as logic_impact,
               s.plot_sensitive as plot_sensitive
        ORDER BY s.target_type, s.dimension
        """
        
        with self.neo4j_client._driver.session() as session:
            result = session.run(query, event_id=event_id)
            state_changes = []
            for record in result:
                # 尝试获取 condition（可能不存在）
                condition = None
                try:
                    condition = record.get("condition")
                    if condition and isinstance(condition, str):
                        try:
                            condition = json.loads(condition)
                        except json.JSONDecodeError:
                            condition = None
                except KeyError:
                    # condition 属性不存在，使用 None
                    condition = None
                
                # 解析 target_id（如果是 JSON 字符串）
                target_id = record.get("target_id")
                if target_id and isinstance(target_id, str):
                    try:
                        target_id = json.loads(target_id)
                    except json.JSONDecodeError:
                        pass
                
                state_changes.append({
                    "source_event": record.get("source_event"),
                    "target_type": record.get("target_type"),
                    "target_id": target_id,
                    "dimension": record.get("dimension"),
                    "change_type": record.get("change_type"),
                    "value": record.get("value"),
                    "condition": condition,
                    "is_obstacle": record.get("is_obstacle", False),
                    "logic_impact": record.get("logic_impact"),
                    "plot_sensitive": record.get("plot_sensitive"),
                })
            
            return state_changes
    
    async def get_event_info(self, event_id: str) -> Optional[Dict[str, Any]]:
        """
        从 Neo4j 获取事件信息
        
        Args:
            event_id: 事件ID
            
        Returns:
            事件信息字典，如果不存在则返回 None
        """
        query = """
        MATCH (e:Event {id: $event_id})
        RETURN e.id as id,
               e.人物 as 人物,
               e.行动 as 行动,
               e.目标 as 目标,
               e.结果 as 结果,
               e.情绪 as 情绪,
               e.时间 as 时间,
               e.场景 as 场景,
               e.前提条件 as 前提条件,
               e.结果影响 as 结果影响,
               e.source_text as source_text,
               e.emotion_value as emotion_value,
               e.conflict_value as conflict_value,
               e.impact_value as impact_value
        """
        
        with self.neo4j_client._driver.session() as session:
            result = session.run(query, event_id=event_id)
            record = result.single()
            
            if record is None:
                return None
            
            return dict(record)
    
    def _get_character_id_by_name(self, char_name: str) -> Optional[str]:
        """
        通过人物名称查找人物ID 🆔
        
        从 entities.json 加载的映射中查找，支持 canonical_name 和 aliases。
        
        Args:
            char_name: 人物名称
            
        Returns:
            人物ID，如果找不到则返回 None
        """
        return self.entities_map.get(char_name)
    
    def _get_function_definitions(self) -> List[Dict[str, Any]]:
        """
        定义 LLM function calling 的函数列表 🛠️
        
        Returns:
            函数定义列表
        """
        return [
            {
                "type": "function",
                "function": {
                    "name": "update_character_state",
                    "description": "更新人物内在状态。根据事件内容和当前状态，更新指定人物的某个维度状态。",
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "character_id": {
                                "type": "string",
                                "description": "人物ID"
                            },
                            "dimension": {
                                "type": "string",
                                "description": "状态维度（如：情绪状态、动机、世界观等）"
                            },
                            "value": {
                                "type": "string",
                                "description": "新的状态值"
                            },
                            "reason": {
                                "type": "string",
                                "description": "更新原因（简要说明为什么这样更新）"
                            }
                        },
                        "required": ["character_id", "dimension", "value", "reason"]
                    }
                }
            },
            {
                "type": "function",
                "function": {
                    "name": "update_relationship_state",
                    "description": "更新人物关系状态。根据事件内容，更新两个人物之间的某个关系维度。",
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "character_a_id": {
                                "type": "string",
                                "description": "第一个人物ID"
                            },
                            "character_b_id": {
                                "type": "string",
                                "description": "第二个人物ID"
                            },
                            "dimension": {
                                "type": "string",
                                "description": "关系维度（如：情感状态、权力动态、连接强度等）"
                            },
                            "value": {
                                "type": "string",
                                "description": "新的关系状态值"
                            },
                            "reason": {
                                "type": "string",
                                "description": "更新原因"
                            }
                        },
                        "required": ["character_a_id", "character_b_id", "dimension", "value", "reason"]
                    }
                }
            },
            {
                "type": "function",
                "function": {
                    "name": "update_world_state",
                    "description": "更新世界/物品状态。根据事件内容，更新世界或物品的某个维度状态。",
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "target_id": {
                                "type": "string",
                                "description": "目标ID（世界变量标识符或物品ID）"
                            },
                            "dimension": {
                                "type": "string",
                                "description": "状态维度（如：场景状态、环境状态、物品存在等）"
                            },
                            "value": {
                                "type": "string",
                                "description": "新的状态值"
                            },
                            "reason": {
                                "type": "string",
                                "description": "更新原因"
                            }
                        },
                        "required": ["target_id", "dimension", "value", "reason"]
                    }
                }
            },
            {
                "type": "function",
                "function": {
                    "name": "update_persona",
                    "description": "判断并更新静态人设。在剧情比较敏感的事件发生后，判断是否需要更新人物的静态人设（如 role、social_position、disposition、worldview 等）。",
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "character_id": {
                                "type": "string",
                                "description": "人物ID"
                            },
                            "should_update": {
                                "type": "boolean",
                                "description": "是否需要更新静态人设"
                            },
                            "update_reason": {
                                "type": "string",
                                "description": "更新原因（如果 should_update=True）"
                            },
                            "updated_fields": {
                                "type": "object",
                                "description": "需要更新的字段及其新值（如果 should_update=True），格式：{field: new_value}，可选字段：role, social_position, disposition, worldview",
                                "properties": {
                                    "role": {"type": "string"},
                                    "social_position": {"type": "string"},
                                    "disposition": {"type": "string"},
                                    "worldview": {"type": "string"}
                                }
                            }
                        },
                        "required": ["character_id", "should_update"]
                    }
                }
            }
        ]
    
    async def apply_state_change(
        self,
        event_id: str,
        selected_state_changes: List[Dict[str, Any]],
        current_states: Optional[UpdatedState] = None,
        event_info: Optional[Dict[str, Any]] = None,
    ) -> tuple[UpdatedState, List[PersonaUpdate], "StateSnapshot"]:
        """
        应用已选中的状态变化 🎯
        
        重要：这个方法只接受"已经被选中的 state_changes"，不看到完整池子。
        职责分离：
        - StateChangePool：从事件中提取，全部存在，不 apply
        - Choice / Canonical Selector：决定哪一包 state_change 生效（外部调用者负责）
        - StateApplier（这里）：只处理已选中的变化
        
        Args:
            event_id: 事件ID
            selected_state_changes: 已选中的状态变化列表（只包含需要 apply 的变化）
            current_states: 当前状态（如果为 None，会从基线初始化）
            event_info: 事件信息（如果为 None，会从 Neo4j 获取）
            
        Returns:
            (更新后的状态, 人设更新建议列表, 状态快照)
        """
        if self.llm_client.use_stub:
            # Stub 模式：返回空结果
            snapshot = self._create_state_snapshot(UpdatedState(), current_event_id=event_id)
            return UpdatedState(), [], snapshot
        
        # 获取事件信息（如果没有提供）
        if event_info is None:
            event_info = await self.get_event_info(event_id)
            if event_info is None:
                print(f"⚠️  事件 {event_id} 不存在")
                snapshot = self._create_state_snapshot(current_states or UpdatedState(), current_event_id=event_id)
                return UpdatedState(), [], snapshot
        
        # 如果没有选中的状态变化，直接返回
        if not selected_state_changes:
            print(f"ℹ️  事件 {event_id} 没有需要应用的状态变化")
            snapshot = self._create_state_snapshot(current_states or UpdatedState(), current_event_id=event_id)
            return current_states or UpdatedState(), [], snapshot
        
        # 初始化当前状态
        if current_states is None:
            current_states = self._initialize_states_from_baseline()
        
        def _debug_dump(stage: str, payload: Dict[str, Any]) -> None:
            """
            调试：把 apply 阶段的提示词/工具调用/结果落盘，便于排查“黑盒”。
            启用方式：设置环境变量 DEBUG_STATE_PROMPTS=1。
            """
            if os.environ.get("DEBUG_STATE_PROMPTS", "").strip() not in ("1", "true", "True", "yes", "YES"):
                return
            try:
                project_root = Path(__file__).resolve().parent.parent  # .../src
                out_dir = project_root / "debug_prompts" / "state_applier"
                out_dir.mkdir(parents=True, exist_ok=True)
                ts = datetime.now().strftime("%Y%m%d_%H%M%S_%f")
                safe_eid = "".join(c if (c.isalnum() or c in ("_", "-", ".")) else "_" for c in (event_id or "unknown"))
                fp = out_dir / f"{ts}_{safe_eid}_{stage}.json"
                fp.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
            except Exception:
                return

        _debug_dump(
            "inputs",
            {
                "event_id": event_id,
                "event_info": event_info,
                "selected_state_changes": selected_state_changes,
            },
        )

        # 构建提示词（只包含已选中的状态变化）
        prompt = await self._build_prompt(event_info, selected_state_changes, current_states)
        _debug_dump("prompt", {"event_id": event_id, "prompt": prompt})
        
        # 调用 LLM with function calling
        async with self.semaphore:
            response = await self.llm_client.client.chat.completions.create(
                model=self.llm_client.model,
                messages=[
                    {"role": "system", "content": "你是一个状态管理助手，负责根据事件内容和已选中的状态变化更新游戏状态。这些状态变化已经被外部选择器确定需要应用，你只需要根据事件内容判断如何正确应用这些变化。请使用提供的函数来更新状态。"},
                    {"role": "user", "content": prompt}
                ],
                tools=self._get_function_definitions(),
                tool_choice="auto",
                temperature=0.3,
            )
        
        # 处理 LLM 响应
        updated_states = UpdatedState(
            character_states=current_states.character_states.copy(),
            relationship_states=current_states.relationship_states.copy(),
            world_states=current_states.world_states.copy(),
        )
        persona_updates = []
        
        message = response.choices[0].message
        try:
            tool_calls_dump = []
            if getattr(message, "tool_calls", None):
                for tc in message.tool_calls:
                    tool_calls_dump.append(
                        {"name": tc.function.name, "arguments": tc.function.arguments}
                    )
            _debug_dump("llm_tool_calls", {"event_id": event_id, "tool_calls": tool_calls_dump})
        except Exception:
            pass

        if message.tool_calls:
            for tool_call in message.tool_calls:
                function_name = tool_call.function.name
                function_args = json.loads(tool_call.function.arguments)
                
                if function_name == "update_character_state":
                    char_id = function_args["character_id"]
                    dimension = function_args["dimension"]
                    value = function_args["value"]
                    
                    if char_id not in updated_states.character_states:
                        updated_states.character_states[char_id] = {}
                    updated_states.character_states[char_id][dimension] = value
                    
                elif function_name == "update_relationship_state":
                    char_a = function_args["character_a_id"]
                    char_b = function_args["character_b_id"]
                    dimension = function_args["dimension"]
                    value = function_args["value"]
                    
                    rel_key = f"{char_a}|{char_b}"
                    if rel_key not in updated_states.relationship_states:
                        updated_states.relationship_states[rel_key] = {}
                    updated_states.relationship_states[rel_key][dimension] = value
                    
                elif function_name == "update_world_state":
                    target_id = function_args["target_id"]
                    dimension = function_args["dimension"]
                    value = function_args["value"]
                    
                    if target_id not in updated_states.world_states:
                        updated_states.world_states[target_id] = {}
                    updated_states.world_states[target_id][dimension] = value
                    
                elif function_name == "update_persona":
                    persona_update = PersonaUpdate(
                        character_id=function_args["character_id"],
                        should_update=function_args["should_update"],
                        update_reason=function_args.get("update_reason"),
                        updated_fields=function_args.get("updated_fields"),
                    )
                    persona_updates.append(persona_update)
        
        # 创建状态快照（供后续剧情生成使用）
        snapshot = self._create_state_snapshot(updated_states, current_event_id=event_id)
        _debug_dump(
            "outputs",
            {
                "event_id": event_id,
                "updated_states": updated_states.model_dump() if hasattr(updated_states, "model_dump") else updated_states.dict(),
                "snapshot": snapshot.to_dict_for_llm() if snapshot else None,
            },
        )
        
        return updated_states, persona_updates, snapshot
    
    def apply_state_changes_directly(
        self,
        selected_state_changes: List[Dict[str, Any]],
        current_states: UpdatedState,
        event_id: Optional[str] = None,
    ) -> tuple[UpdatedState, "StateSnapshot"]:
        """
        直接应用状态变化（纯赋值，不需要 LLM）⚡

        每条状态变化只需要 target_type / target_id / dimension / value 四个字段，
        统一按赋值处理，不再区分 change_type。
        
        Args:
            selected_state_changes: 已选中的状态变化列表
            current_states: 当前状态（可以是 UpdatedState 对象或字典）
            event_id: 事件ID（用于创建快照）
            
        Returns:
            (更新后的状态, 状态快照)
        """
        if isinstance(current_states, dict):
            try:
                current_states = UpdatedState(**current_states)
            except Exception:
                current_states = UpdatedState()
        
        if not selected_state_changes:
            snapshot = self._create_state_snapshot(current_states or UpdatedState(), current_event_id=event_id)
            return current_states or UpdatedState(), snapshot
        
        if current_states is None:
            current_states = self._initialize_states_from_baseline()
        
        updated_states = UpdatedState(
            character_states=current_states.character_states.copy(),
            relationship_states=current_states.relationship_states.copy(),
            world_states=current_states.world_states.copy(),
        )
        
        for sc in selected_state_changes:
            target_type = sc.get("target_type")
            target_id = sc.get("target_id")
            dimension = sc.get("dimension")
            value = sc.get("value")
            
            if not target_type or not dimension:
                continue
            
            if target_type == "character":
                if target_id not in updated_states.character_states:
                    updated_states.character_states[target_id] = {}
                updated_states.character_states[target_id][dimension] = value
                    
            elif target_type == "relationship":
                if isinstance(target_id, list) and len(target_id) >= 2:
                    rel_key = f"{target_id[0]}|{target_id[1]}"
                elif isinstance(target_id, str):
                    rel_key = target_id
                else:
                    continue
                if rel_key not in updated_states.relationship_states:
                    updated_states.relationship_states[rel_key] = {}
                updated_states.relationship_states[rel_key][dimension] = value
                    
            elif target_type == "world":
                if target_id not in updated_states.world_states:
                    updated_states.world_states[target_id] = {}
                updated_states.world_states[target_id][dimension] = value
        
        snapshot = self._create_state_snapshot(updated_states, current_event_id=event_id)
        return updated_states, snapshot
    
    def _initialize_states_from_baseline(self) -> UpdatedState:
        """从基线初始化状态"""
        states = UpdatedState()
        
        # 初始化人物状态
        character_baseline = self.baseline.get("character_baseline", {})
        # 基线中的值是 None，这里不初始化，等待第一次更新
        
        # 初始化关系状态
        relationship_baseline = self.baseline.get("relationship_baseline", {})
        # 同样不初始化
        
        # 初始化世界状态
        world_baseline = self.baseline.get("world_baseline", {})
        # 同样不初始化
        
        return states
    
    
    
    async def _build_prompt(
        self,
        event_info: Dict[str, Any],
        state_changes: List[Dict[str, Any]],
        current_states: UpdatedState,
    ) -> str:
        """
        构建状态更新的提示词 📝
        
        只包含事件信息和状态变化，不包含文游内容上下文。
        
        Args:
            current_states: 当前状态（可以是 UpdatedState 对象或字典）
        """
        # 🔧 修复：处理 current_states 可能是字典的情况
        if isinstance(current_states, dict):
            try:
                current_states = UpdatedState(**current_states)
            except Exception as e:
                # 如果转换失败，创建一个空的 UpdatedState
                import logging
                logger = logging.getLogger(__name__)
                logger.warning(f"⚠️  无法将 current_states 字典转换为 UpdatedState 对象: {e}，使用空状态")
                current_states = UpdatedState()
        
        lines = []
        
        lines.append("=== 事件信息 ===")
        lines.append(f"事件ID: {event_info.get('id')}")
        lines.append(f"人物: {', '.join(event_info.get('人物', []))}")
        lines.append(f"行动: {event_info.get('行动', '')}")
        lines.append(f"目标: {event_info.get('目标', '')}")
        lines.append(f"结果: {event_info.get('结果', '')}")
        lines.append(f"结果影响: {event_info.get('结果影响', '')}")
        lines.append(f"情绪: {event_info.get('情绪', '')}")
        lines.append(f"场景: {event_info.get('场景', '')}")
        lines.append("")
        
        lines.append("=== 需要应用的状态变化（已选中） ===")
        lines.append("注意：这些状态变化已经被外部选择器确定需要应用，你只需要根据事件内容判断如何正确应用。")
        for sc in state_changes:
            lines.append(f"- {sc['target_type']}: {sc['target_id']} 的 {sc['dimension']} -> {sc['value']}")
            if sc.get('logic_impact'):
                lines.append(f"  逻辑影响: {sc['logic_impact']}")
        lines.append("")
        
        # 添加相关人物的静态人设（通过ID获取）
        event_characters = event_info.get('人物', [])
        if event_characters:
            lines.append("=== 相关人物的静态人设 ===")
            for char_name in event_characters:
                # 通过名称查找人物ID
                char_id = self._get_character_id_by_name(char_name)
                if char_id:
                    # 通过ID获取人设
                    persona = self.personas.get(char_id)
                    if persona:
                        lines.append(f"{persona.name} ({persona.id}):")
                        lines.append(f"  role: {persona.role}")
                        lines.append(f"  social_position: {persona.social_position}")
                        lines.append(f"  disposition: {persona.disposition}")
                        lines.append(f"  worldview: {persona.worldview}")
                    else:
                        lines.append(f"  ⚠️  未找到人物 {char_name} (ID: {char_id}) 的静态人设")
                else:
                    lines.append(f"  ⚠️  未找到人物 {char_name} 的ID")
            lines.append("")
        
        lines.append("=== 当前状态（仅显示相关状态，这是处理完之前所有事件后的累积状态） ===")
        lines.append("注意：这些状态值是基于之前所有事件的更新结果，不是初始基线状态。")
        lines.append("")
        
        # 收集需要显示的状态（只显示与事件和状态变化相关的）
        relevant_char_ids = set(event_info.get('人物', []))
        relevant_rel_keys = set()
        relevant_world_ids = set()
        
        # 从状态变化中提取相关的目标
        for sc in state_changes:
            target_type = sc.get('target_type')
            target_id = sc.get('target_id')
            
            if target_type == 'character':
                if isinstance(target_id, str):
                    relevant_char_ids.add(target_id)
                elif isinstance(target_id, list):
                    relevant_char_ids.update(target_id)
            elif target_type == 'relationship':
                if isinstance(target_id, list) and len(target_id) >= 2:
                    # 关系格式：["C008", "C010"] -> "C008|C010"
                    rel_key = f"{target_id[0]}|{target_id[1]}"
                    relevant_rel_keys.add(rel_key)
                elif isinstance(target_id, str):
                    # 如果已经是格式化的关系键
                    relevant_rel_keys.add(target_id)
            elif target_type == 'world':
                if isinstance(target_id, str):
                    relevant_world_ids.add(target_id)
                elif isinstance(target_id, list):
                    relevant_world_ids.update(target_id)
        
        # 人物状态（只显示相关的）
        relevant_char_states = {
            char_id: state 
            for char_id, state in current_states.character_states.items()
            if char_id in relevant_char_ids
        }
        if relevant_char_states:
            lines.append("人物状态:")
            for char_id, state in relevant_char_states.items():
                state_str = "、".join([f"{k}: {v}" for k, v in state.items()])
                lines.append(f"  {char_id}: {state_str}")
        else:
            lines.append("人物状态: 无相关状态")
        lines.append("")
        
        # 关系状态（只显示相关的）
        relevant_rel_states = {
            rel_key: state
            for rel_key, state in current_states.relationship_states.items()
            if rel_key in relevant_rel_keys
        }
        if relevant_rel_states:
            lines.append("关系状态:")
            for rel_key, state in relevant_rel_states.items():
                state_str = "、".join([f"{k}: {v}" for k, v in state.items()])
                lines.append(f"  {rel_key}: {state_str}")
        else:
            lines.append("关系状态: 无相关状态")
        lines.append("")
        
        # 世界状态（只显示相关的）
        relevant_world_states = {
            target_id: state
            for target_id, state in current_states.world_states.items()
            if target_id in relevant_world_ids
        }
        if relevant_world_states:
            lines.append("世界状态:")
            for target_id, state in relevant_world_states.items():
                state_str = "、".join([f"{k}: {v}" for k, v in state.items()])
                lines.append(f"  {target_id}: {state_str}")
        else:
            lines.append("世界状态: 无相关状态")
        lines.append("")
        
        lines.append("请根据事件内容和已选中的状态变化，使用提供的函数来更新状态。")
        lines.append("这些状态变化已经被确定需要应用，你只需要判断如何正确应用它们（例如：检查前提条件、处理冲突等）。")
        lines.append("如果这是一个剧情比较敏感的事件（如重大转折、人物转变等），请考虑是否需要更新相关人物的静态人设。")
        
        return "\n".join(lines)
    
    def _create_state_snapshot(
        self, 
        updated_states: UpdatedState,
        current_event_id: Optional[str] = None,
    ) -> "StateSnapshot":
        """
        从 UpdatedState 创建 StateSnapshot 📸
        
        用于传递给剧情生成的LLM。
        注意：这个快照是基于更新后的状态（apply_state_change之后）创建的。
        
        Args:
            updated_states: 更新后的状态
            current_event_id: 当前事件ID（用于限制只获取到当前事件为止的世界状态变化）
        """
        # 从 Neo4j 获取到当前事件为止的世界状态变化，构建 WorldState
        # 如果没有指定 current_event_id，则获取所有（用于最终快照）
        world_state_changes = self._get_world_state_changes(max_event_id=current_event_id)
        world_state = build_world_state_from_states(world_state_changes)
        
        return StateSnapshot(
            character_states=updated_states.character_states,
            relationship_states=updated_states.relationship_states,
            world_state=world_state,
        )
    
    def _get_world_state_changes(self, max_event_id: Optional[str] = None) -> List[StateChange]:
        """
        从 Neo4j 获取世界状态变化
        
        使用 state_target_type 索引优化查询性能 🚀
        
        Args:
            max_event_id: 最大事件ID（只获取 source_event <= max_event_id 的状态变化）
                         如果为 None，获取所有世界状态变化
        
        Returns:
            世界状态变化列表
        """
        # 优化查询：使用 state_target_type 单独索引
        # 这个索引专门用于按 target_type 查询，比复合索引 state_target(target_type, target_id) 更高效
        # 获取所有世界状态变化（在 Python 中过滤，因为事件ID是字符串格式，需要数值比较）
        query = """
        MATCH (s:StateChange)
        WHERE s.target_type = 'world'
        RETURN s.source_event as source_event,
               s.target_type as target_type,
               s.target_id as target_id,
               s.dimension as dimension,
               s.change_type as change_type,
               s.value as value,
               s.is_obstacle as is_obstacle,
               s.logic_impact as logic_impact,
               s.plot_sensitive as plot_sensitive
        ORDER BY s.source_event
        """
        
        def extract_event_number(event_id: str) -> int:
            """提取事件ID中的数字部分（E1 -> 1, E10 -> 10）"""
            if event_id and event_id.startswith("E"):
                try:
                    return int(event_id[1:])
                except ValueError:
                    return 0
            return 0
        
        state_changes = []
        with self.neo4j_client._driver.session() as session:
            result = session.run(query)
            for record in result:
                source_event = record.get("source_event")
                
                # 如果指定了 max_event_id，只保留到该事件为止的状态变化
                if max_event_id:
                    if extract_event_number(source_event) > extract_event_number(max_event_id):
                        continue  # 跳过未来事件的状态变化
                # 尝试获取 condition（可能不存在）
                condition = None
                try:
                    condition = record.get("condition")
                    if condition and isinstance(condition, str):
                        try:
                            condition = json.loads(condition)
                        except json.JSONDecodeError:
                            condition = None
                except KeyError:
                    # condition 属性不存在，使用 None
                    condition = None
                
                # 解析 target_id（如果是 JSON 字符串）
                target_id = record.get("target_id")
                if target_id and isinstance(target_id, str):
                    try:
                        target_id = json.loads(target_id)
                    except json.JSONDecodeError:
                        pass
                
                try:
                    state_change = StateChange(
                        source_event=record.get("source_event"),
                        target_type=record.get("target_type"),
                        target_id=target_id,
                        dimension=record.get("dimension"),
                        change_type=record.get("change_type"),
                        value=record.get("value"),
                        condition=condition,
                        is_obstacle=record.get("is_obstacle", False),
                        logic_impact=record.get("logic_impact", ""),
                        plot_sensitive=record.get("plot_sensitive"),
                    )
                    state_changes.append(state_change)
                except Exception as e:
                    print(f"⚠️  解析状态变化失败: {e}")
                    continue
        
        return state_changes
    
    async def _get_all_event_ids(self) -> List[str]:
        """
        从 Neo4j 获取所有事件ID（按顺序）📚
        
        事件ID格式通常是 "E1", "E2", "E10" 等，需要按数字部分排序。
        """
        query = """
        MATCH (e:Event)
        RETURN e.id as id
        """
        
        with self.neo4j_client._driver.session() as session:
            result = session.run(query)
            event_ids = [record["id"] for record in result]
        
        # 按事件ID的数字部分排序（处理 "E1", "E2", "E10" 等情况）
        def extract_event_number(event_id: str) -> int:
            """提取事件ID中的数字部分用于排序"""
            import re
            match = re.search(r'\d+', event_id)
            if match:
                return int(match.group())
            return 0
        
        event_ids.sort(key=extract_event_number)
        return event_ids
    
    def close(self):
        """关闭资源"""
        if hasattr(self, "neo4j_client"):
            self.neo4j_client.close()


__all__ = ["StateApplier"]

