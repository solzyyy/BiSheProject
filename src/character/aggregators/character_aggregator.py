"""
人物聚合器 - 聚合人物节点得到完整画像 🎭✨

功能：
1. 聚合人物相关 Event 和 StateChange
2. 行动模式统计：
   - 行动词提取（ LLM）
   - 频率统计
   - TF-IDF / PMI（区分"他独有的行为"）
"""

import json
import re
import asyncio
from typing import Dict, List, Any, Optional, Set, Union, TYPE_CHECKING
from collections import Counter, defaultdict
from pathlib import Path
import math

from ..models.character_profile import CharacterProfile, ActionPattern
from ..models.state_change import StateChange

if TYPE_CHECKING:
    from core.llm_client import AsyncLLMClient


class ActionExtractor:
    """
    行动词提取器 - 从事件描述中提取行动词 🎯
    
    支持模式：使用 LLM 提取行动词
    """
    
    def __init__(
        self, 
        use_llm: bool = True, 
        llm_client: Optional["AsyncLLMClient"] = None, 
        semaphore: Optional[asyncio.Semaphore] = None
    ):
        """
        初始化提取器
        
        Args:
            use_llm: 是否使用 LLM 提取（默认 True，使用 LLM 提取）
            llm_client: LLM 客户端（如果 use_llm=True，必须提供）
            semaphore: 信号量，用于控制 LLM 调用的并发数（可选）
        """
        self.use_llm = use_llm
        self.llm_client = llm_client
        self.semaphore = semaphore
        if use_llm and not llm_client:
            raise ValueError("❌ 必须提供 llm_client")
    
    async def extract_actions(self, action_text: str, character_name: Optional[str] = None) -> List[str]:
        """
        从行动描述中提取行动词 🎯
        
        Args:
            action_text: 行动描述文本
            character_name: 人物名称（可选，用于在提示词中明确只提取该人物的行动）
            
        Returns:
            行动词列表
        """
        if self.use_llm and self.llm_client:
            return await self._extract_with_llm(action_text, character_name)
        else:
            # 如果没有 LLM，返回空列表
            return []
    
    
    async def _extract_with_llm(self, action_text: str, character_name: Optional[str] = None) -> List[str]:
        """使用 LLM 提取行动词"""
        if not self.llm_client:
            print(f"⚠️ LLM 客户端未初始化")
            return []
        
        # 构建提示词，明确只提取该人物的行动
        if character_name:
            character_context = f"\n\n重要：这是人物「{character_name}」的行动描述。请只提取「{character_name}」本人执行的行动词，不要提取其他人的行动。"
        else:
            character_context = ""
        
        prompt = f"""你是一位专业的文本分析助手。请从以下行动描述中提取行动词（动词），只返回 JSON 数组格式的行动词列表。

行动描述：{action_text}{character_context}

要求：
1. 提取所有表示动作的动词
2. 每个行动词应该是简洁的动词（1-4个字）
3. 去除重复的行动词
4. 只提取描述中明确属于该人物的行动，不要提取其他人的行动
5. 只返回 JSON 数组，不要其他内容

返回格式示例：
["观察", "行走", "绘画"]

现在请提取行动词："""
        
        # 使用信号量控制并发
        if self.semaphore:
            async with self.semaphore:
                return await self._do_extract(prompt)
        else:
            return await self._do_extract(prompt)
    
    async def _do_extract(self, prompt: str) -> List[str]:
        """实际执行 LLM 提取的内部方法"""
        try:
            # AsyncLLMClient.invoke() 只接受 prompt 和 return_json 参数
            # temperature 和 max_tokens 需要在初始化时设置
            # 使用 return_json=False 获取原始字符串，然后手动解析 JSON
            # 注意：LLM 调用可能会比较慢，请耐心等待
            response = await self.llm_client.invoke(prompt, return_json=False)
            content = response if isinstance(response, str) else str(response)
            
            # 清理内容（移除 markdown 代码块标记等）
            content = content.strip()
            if content.startswith("```"):
                lines = content.split("\n")
                content = "\n".join(lines[1:-1]) if len(lines) > 2 else content
                content = content.strip()
            
            # 尝试解析 JSON
            actions = json.loads(content)
            if isinstance(actions, list):
                return actions
            else:
                # 如果不是列表，尝试从字典中提取
                if isinstance(actions, dict) and "actions" in actions:
                    return actions["actions"]
                return []
        except Exception as e:
            print(f"⚠️ LLM 提取失败: {e}")
            # 如果 LLM 提取失败，返回空列表
            return []


class CharacterAggregator:
    """
    人物聚合器 - 聚合人物节点得到完整画像 🎭
    """
    
    def __init__(
        self, 
        use_llm_for_actions: bool = False, 
        llm_client: Optional["AsyncLLMClient"] = None, 
        max_concurrent_actions: int = 5
    ):
        """
        初始化聚合器
        
        Args:
            use_llm_for_actions: 是否使用 LLM 提取行动词（默认 False）
            llm_client: LLM 客户端（如果 use_llm_for_actions=True）
            max_concurrent_actions: 最大并发行动词提取数（默认 5，用于控制 LLM 调用并发）
        """
        # 创建信号量控制 LLM 调用的并发数
        self.action_semaphore = asyncio.Semaphore(max_concurrent_actions) if use_llm_for_actions else None
        self.action_extractor = ActionExtractor(
            use_llm=use_llm_for_actions, 
            llm_client=llm_client,
            semaphore=self.action_semaphore
        )
        self.llm_client = llm_client
    
    def load_entities(self, entities_file: str) -> Dict[str, Dict[str, Any]]:
        """
        加载实体数据（entities.json）📚
        
        Args:
            entities_file: entities.json 文件路径
            
        Returns:
            {entity_id: {"id": ..., "canonical_name": ..., "aliases": [...]}}
        """
        with open(entities_file, 'r', encoding='utf-8') as f:
            data = json.load(f)
        
        entities = data.get("entities", []) if isinstance(data, dict) else data
        
        # 转换为字典：entity_id -> entity_data
        return {entity["id"]: entity for entity in entities if "id" in entity}
    
    def extract_characters_from_events_and_states(
        self,
        events: List[Dict[str, Any]],
        state_changes: Dict[str, List[StateChange]],
        entities: Dict[str, Dict[str, Any]],
        mentions_index: Optional[Dict[tuple, str]] = None
    ) -> Dict[str, Dict[str, Any]]:
        """
        从 entities.json 开始，逐个角色提取人物信息 🎭
        
        逻辑：
        1. 从 entities.json 找到所有实体，确定别名
        2. 遍历实体，去 character_states.json 里面找到变化
        3. 从 character_states 里面的 source_event 里又可以找到原事件
        4. 从 mentions 中找到该角色的行为模式（mentions可能用别名）
        
        Args:
            events: 所有事件列表
            state_changes: 状态变化（按人物ID分组）
            entities: 实体数据（从 entities.json 加载）
            mentions_index: mentions 索引 {(event_id, name): action_hint}
            
        Returns:
            {character_id: {"canonical_name": ..., "aliases": [...], "names": [...]}}
        """
        # 建立事件ID到事件的映射
        event_map = {event.get("event_id", ""): event for event in events}
        
        # 从 entities.json 开始，遍历所有实体
        characters = {}
        
        for entity_id, entity in entities.items():
            canonical_name = entity.get("canonical_name", entity_id)
            aliases = entity.get("aliases", [])
            
            # 初始化人物信息（从 entities.json 获取基础信息）
            # mentions 里只有规范名和别名，所以不需要从事件中找名字
            all_names = {canonical_name} | set(aliases)
            
            # 去 character_states.json 里面找到该实体的状态变化
            char_states = state_changes.get(entity_id, [])
            
            # 从 mentions_index 中查找该实体的所有 mention 名称
            # mentions 可能使用别名，所以要尝试所有可能的名称（规范名和别名）
            mention_names = set()
            if mentions_index:
                # 遍历所有 mentions，找到匹配该实体的名称
                for (event_id, mention_name), action_hint in mentions_index.items():
                    # 如果 mention_name 匹配规范名或别名
                    if mention_name == canonical_name or mention_name in aliases:
                        mention_names.add(mention_name)
                        # 如果 mentions 中出现了新的名称，也添加到 all_names（可能是别名）
                        all_names.add(mention_name)
            
            # 构建人物信息（只包含规范名、别名和 mentions 中的名称）
            characters[entity_id] = {
                "character_id": entity_id,
                "canonical_name": canonical_name,
                "aliases": aliases,
                "names": all_names  # 只包含规范名、别名和 mentions 中的名称
            }
        
        return characters
    
    def load_events(self, events_file: str) -> List[Dict[str, Any]]:
        """加载事件数据"""
        with open(events_file, 'r', encoding='utf-8') as f:
            data = json.load(f)
        
        if "new_events" in data:
            return data["new_events"]
        elif "events" in data:
            return data["events"]
        else:
            return data if isinstance(data, list) else []
    
    def load_mentions(self, mentions_file: str) -> Dict[str, Dict[str, str]]:
        """
        加载 mentions 数据并建立索引 📚
        
        Args:
            mentions_file: mentions.json 文件路径
            
        Returns:
            {(event_id, name): action_hint} - 通过事件ID和人物名称快速查找该人物在该事件中的行动
        """
        with open(mentions_file, 'r', encoding='utf-8') as f:
            data = json.load(f)
        
        mentions = data.get("mentions", []) if isinstance(data, dict) else data
        
        # 建立索引：{(event_id, name): action_hint}
        mentions_index = {}
        for mention in mentions:
            event_id = mention.get("event_id", "")
            name = mention.get("name", "")
            action_hint = mention.get("action_hint", "")
            
            if event_id and name and action_hint:
                key = (event_id, name)
                # 如果同一个事件中同一人物有多个 mention，合并 action_hint
                if key in mentions_index:
                    mentions_index[key] += "、" + action_hint
                else:
                    mentions_index[key] = action_hint
        
        return mentions_index
    
    def load_state_changes(
        self,
        character_states_file: Optional[str] = None,
        relationship_states_file: Optional[str] = None
    ) -> Dict[str, List[StateChange]]:
        """
        加载状态变化，按人物ID分组
        
        Returns:
            {character_id: [StateChange, ...]}
        """
        all_states = {}
        
        # 加载人物状态
        if character_states_file and Path(character_states_file).exists():
            with open(character_states_file, 'r', encoding='utf-8') as f:
                data = json.load(f)
                states = data.get("state_changes", [])
                for sc_data in states:
                    try:
                        sc = StateChange(**sc_data)
                        if sc.target_type == "character":
                            char_id = str(sc.target_id)
                            if char_id not in all_states:
                                all_states[char_id] = []
                            all_states[char_id].append(sc)
                    except Exception as e:
                        print(f"⚠️ 跳过无效的状态变化: {e}")
        
        # 加载关系状态（关系状态涉及两个人物）
        # 注意：对于关系状态，target_id 是一个数组 [subject_id, object_id]
        # 只有第一个元素（索引 0）是关系的主体，关系状态应该只添加到主体的状态变化中
        if relationship_states_file and Path(relationship_states_file).exists():
            with open(relationship_states_file, 'r', encoding='utf-8') as f:
                data = json.load(f)
                states = data.get("state_changes", [])
                for sc_data in states:
                    try:
                        sc = StateChange(**sc_data)
                        if sc.target_type == "relationship" and isinstance(sc.target_id, list) and len(sc.target_id) >= 1:
                            # 只取第一个元素（主体）的状态变化
                            subject_id = str(sc.target_id[0])
                            if subject_id not in all_states:
                                all_states[subject_id] = []
                            all_states[subject_id].append(sc)
                    except Exception as e:
                        print(f"⚠️ 跳过无效的关系状态变化: {e}")
        
        return all_states
    
    def get_character_events(
        self,
        events: List[Dict[str, Any]],
        character_name: str,
        aliases: List[str],
        state_changes: Optional[List[StateChange]] = None
    ) -> List[Dict[str, Any]]:
        """
        获取人物相关的事件
        
        逻辑：从状态变化的 source_event 找到原事件（按用户要求的逻辑）
        如果没有状态变化，则从所有事件中搜索
        """
        # 建立事件ID到事件的映射
        event_map = {event.get("event_id", ""): event for event in events}
        
        related_events = []
        related_event_ids = set()
        
        # 优先从状态变化的 source_event 找到原事件
        if state_changes:
            for state in state_changes:
                event_id = state.source_event
                if event_id in event_map:
                    related_event_ids.add(event_id)
        
        # 从状态变化的 source_event 找到原事件
        # 注意：只从状态变化中找事件，不从事件列表中搜索（按用户要求的逻辑）
        for event_id in related_event_ids:
            if event_id in event_map:
                related_events.append(event_map[event_id])
        
        return related_events
    
    async def extract_action_patterns(
        self,
        events: List[Dict[str, Any]],
        character_name: str,
        all_names_set: Union[Set[str], List[str]],
        all_characters_actions: Dict[str, List[str]],
        mentions_index: Dict[tuple, str],
        character_id: Optional[str] = None
    ) -> List[ActionPattern]:
        """
        提取行动模式并计算 TF-IDF/PMI 🎯
        
        Args:
            events: 人物相关的事件列表
            character_name: 人物规范名称
            all_names_set: 所有可能的名称集合或列表（包括规范名、别名）
            all_characters_actions: 所有人物及其行动词（用于计算 TF-IDF/PMI）
            mentions_index: mentions 索引 {(event_id, name): action_hint}
            
        Returns:
            行动模式列表（按频率排序）
        """
        # 提取该人物的所有行动词
        character_actions = []
        action_contexts = defaultdict(list)
        
        # 转换为列表，方便遍历（确保包含所有可能的名称）
        if isinstance(all_names_set, set):
            all_names = list(all_names_set)
        elif isinstance(all_names_set, list):
            all_names = all_names_set
        else:
            all_names = [character_name]
        
        # 确保包含规范名称（如果不在集合中）
        if character_name not in all_names:
            all_names.append(character_name)
        
        # 事件列表已经是从状态变化中获取的，不需要再检查事件中的人物列表
        # 直接从 mentions 中查找该人物在该事件中的 action_hint
        for event in events:
            event_id = event.get("event_id", "")
            
            # 从 mentions 中查找该人物在该事件中的 action_hint
            # mentions 里只有规范名和别名，所以用 all_names 匹配
            action_text = None
            for name in all_names:
                key = (event_id, name)
                if key in mentions_index:
                    action_text = mentions_index[key]
                    break
            
            # 如果 mentions 中没有该人物的 action_hint，跳过这个事件
            # 因为事件的"行动"字段可能描述的是多个人的行动，不能算到一个人头上
            if not action_text:
                continue
            
            # 提取行动词（传入人物名称，确保只提取该人物的行动）
            actions = await self.action_extractor.extract_actions(action_text, character_name=character_name)
            character_actions.extend(actions)
            
            # 保存上下文
            for action in actions:
                action_contexts[action].append(action_text)
        
        # 统计频率
        action_counter = Counter(character_actions)
        
        # 计算 TF-IDF 和 PMI
        total_documents = len(all_characters_actions)  # 总人物数
        character_doc_freq = {}  # 每个行动词出现在多少个人物中
        
        for action in action_counter.keys():
            doc_freq = sum(1 for char_actions in all_characters_actions.values() if action in char_actions)
            character_doc_freq[action] = doc_freq
        
        # 构建行动模式
        action_patterns = []
        for action, freq in action_counter.most_common():
            # 计算 TF-IDF
            tf = freq / len(character_actions) if character_actions else 0
            idf = math.log(total_documents / (character_doc_freq.get(action, 1) + 1))
            tf_idf = tf * idf
            
            # 计算 PMI（点互信息）
            # PMI = log(P(x,y) / (P(x) * P(y)))
            # 这里简化为：log(该人物使用该行动词的概率 / 所有人物使用该行动词的平均概率)
            char_action_prob = freq / len(character_actions) if character_actions else 0
            all_action_freq = sum(actions.count(action) for actions in all_characters_actions.values())
            all_action_prob = all_action_freq / sum(len(actions) for actions in all_characters_actions.values()) if all_characters_actions else 0
            
            if all_action_prob > 0:
                pmi = math.log((char_action_prob + 1e-10) / (all_action_prob + 1e-10))
            else:
                pmi = 0
            
            # 判断是否是独有行为（TF-IDF > 阈值 或 PMI > 阈值）
            is_unique = tf_idf > 0.5 or pmi > 1.0
            
            action_patterns.append(ActionPattern(
                action_word=action,
                frequency=freq,
                tf_idf_score=tf_idf,
                pmi_score=pmi,
                is_unique=is_unique,
                contexts=action_contexts[action]# [:10]  # 最多保留10个上下文
            ))
        
        return action_patterns
    
    async def aggregate_character(
        self,
        character_id: str,
        character_info: Dict[str, Any],
        events: List[Dict[str, Any]],
        state_changes: Dict[str, List[StateChange]],
        mentions_index: Dict[tuple, str],
        all_characters_actions: Optional[Dict[str, List[str]]] = None
    ) -> CharacterProfile:
        """
        聚合单个人物节点 🎭
        
        Args:
            character_id: 人物ID
            character_info: 人物信息（从事件和状态变化中提取）
            events: 所有事件列表
            state_changes: 状态变化（按人物ID分组）
            all_characters_actions: 所有人物及其行动词（用于计算 TF-IDF/PMI）
            
        Returns:
            人物画像
        """
        canonical_name = character_info.get("canonical_name", character_id)
        aliases = character_info.get("aliases", [])
        all_names = character_info.get("names", {canonical_name})
        
        # 1. 获取状态变化（先从状态变化开始）
        char_states = state_changes.get(character_id, [])
        
        # 2. 从状态变化的 source_event 找到相关事件（按用户要求的逻辑）
        related_events_list = self.get_character_events(events, canonical_name, list(all_names), char_states)
        related_event_ids = [e.get("event_id", "") for e in related_events_list]
        
        # 筛选人物内在状态（target_type="character"）
        character_states = [sc.dict() for sc in char_states if sc.target_type == "character"]
        
        # 筛选关系状态（target_type="relationship"）
        # 注意：对于关系状态，target_id 是一个数组 [subject_id, object_id]
        # 只有当 target_id[0]（主体）等于当前 character_id 时，才是该实体的关系状态
        relationship_states = []
        for sc in char_states:
            if sc.target_type == "relationship":
                if isinstance(sc.target_id, list) and len(sc.target_id) >= 1:
                    # 检查第一个元素（主体）是否等于当前 character_id
                    subject_id = str(sc.target_id[0])
                    if subject_id == character_id:
                        relationship_states.append(sc.dict())
        
        # 3. 提取行动模式（需要先构建 all_characters_actions）
        if all_characters_actions is None:
            all_characters_actions = await self.build_all_characters_actions(
                {character_id: character_info}, events, mentions_index
            )
        
        action_patterns = await self.extract_action_patterns(
            related_events_list,
            canonical_name,
            all_names,  # 传递 names 集合
            all_characters_actions,
            mentions_index,
            character_id=character_id  # 传递 character_id
        )
        
        # 4. 提取独有行为
        unique_behaviors = [ap.action_word for ap in action_patterns if ap.is_unique]
        
        # 5. 统计信息
        total_actions = sum(ap.frequency for ap in action_patterns)
        most_common_action = action_patterns[0].action_word if action_patterns else None
        
        return CharacterProfile(
            character_id=character_id,
            canonical_name=canonical_name,
            aliases=aliases,
            related_events=related_event_ids,
            character_states=character_states,
            relationship_states=relationship_states,
            action_patterns=action_patterns,
            unique_behaviors=unique_behaviors,
            total_events=len(related_events_list),
            total_actions=total_actions,
            most_common_action=most_common_action
        )
    
    async def build_all_characters_actions(
        self,
        characters_info: Dict[str, Dict[str, Any]],
        events: List[Dict[str, Any]],
        mentions_index: Dict[tuple, str],
        state_changes: Dict[str, List[StateChange]],
        progress=None,
        task=None
    ) -> Dict[str, List[str]]:
        """
        构建所有人物及其行动词（用于计算 TF-IDF/PMI）
        
        Args:
            characters_info: 人物信息字典
            events: 所有事件列表
            mentions_index: mentions 索引 {(event_id, name): action_hint}
            progress: rich.progress.Progress 对象（可选）
            task: rich.progress.TaskID（可选）
        """
        all_characters_actions = {}
        total_characters = len(characters_info)
        
        for idx, (char_id, char_info) in enumerate(characters_info.items(), 1):
            canonical_name = char_info.get("canonical_name", char_id)
            all_names = char_info.get("names", {canonical_name})
            # 从状态变化中找到相关事件（按用户要求的逻辑）
            char_states = state_changes.get(char_id, []) if state_changes else []
            related_events = self.get_character_events(events, canonical_name, list(all_names), char_states)
            
            if progress and task:
                progress.update(task, description=f"提取行动词: {canonical_name} ({idx}/{total_characters})")
            
            # 打印当前处理的人物信息（帮助调试）
            print(f"  📝 处理人物 {idx}/{total_characters}: {canonical_name} (共 {len(related_events)} 个相关事件)")
            
            actions = []
            skipped_events = 0  # 统计跳过的事件（没有 mentions 的）
            total_events = len(related_events)
            
            for event_idx, event in enumerate(related_events, 1):
                event_id = event.get("event_id", "")
                
                # 更新进度条描述，显示当前处理的事件
                if progress and task and total_events > 0:
                    progress.update(
                        task, 
                        description=f"提取行动词: {canonical_name} ({idx}/{total_characters}) - 事件 {event_idx}/{total_events}"
                    )
                
                # 从 mentions 中查找该人物在该事件中的 action_hint
                # 只使用 mentions 中的 action_hint，确保是人物特定的行动
                action_text = None
                matched_name = None
                for name in all_names:
                    key = (event_id, name)
                    if key in mentions_index:
                        action_text = mentions_index[key]
                        matched_name = name
                        break
                
                # 如果 mentions 中没有该人物的 action_hint，跳过这个事件
                # 因为事件的"行动"字段可能描述的是多个人的行动，不能算到一个人头上
                if not action_text:
                    skipped_events += 1
                    # 调试信息：显示为什么找不到
                    available_mentions = [name for (eid, name) in mentions_index.keys() if eid == event_id]
                    print(f"    ⚠️ {canonical_name} 在事件 {event_id} 中没有找到 mentions")
                    print(f"       尝试的名称: {list(all_names)}")
                    print(f"       该事件中可用的 mentions: {available_mentions}")
                    continue
                
                # 提取行动词（传入人物名称，确保只提取该人物的行动）
                try:
                    event_actions = await self.action_extractor.extract_actions(action_text, character_name=canonical_name)
                    actions.extend(event_actions)
                except Exception as e:
                    print(f"    ⚠️ 提取行动词失败 (事件 {event_id}): {e}")
                    continue
            
            all_characters_actions[char_id] = actions
            
            if skipped_events > 0:
                print(f"    ⚠️ {canonical_name}: 跳过了 {skipped_events} 个没有 mentions 的事件（确保行动词按人物分组）")
            
            if progress and task:
                progress.update(task, advance=1)
        
        return all_characters_actions


__all__ = ["CharacterAggregator", "ActionExtractor"]

