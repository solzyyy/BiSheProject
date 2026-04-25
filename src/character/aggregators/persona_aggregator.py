"""
人物静态人设聚合器 🎭✨

从 character_profiles.json 聚合得到人物的静态人设。
"""

import json
import asyncio
from typing import Dict, List, Any, Optional

from ..models.character_persona import CharacterPersona
from core.llm_client import AsyncLLMClient


class PersonaAggregator:
    """
    人物静态人设聚合器 🎭
    
    从 character_profiles.json 中提取信息，生成静态人设。
    """
    
    def __init__(self, llm_client: Optional[AsyncLLMClient] = None, max_concurrent: int = 5, max_retries: int = 3):
        """
        初始化聚合器
        
        Args:
            llm_client: LLM 客户端（用于生成 persona_summary）
            max_concurrent: 最大并发数（默认 5）
            max_retries: 最大重试次数（默认 3）
        """
        self.llm_client = llm_client
        self.semaphore = asyncio.Semaphore(max_concurrent) if llm_client else None
        self.max_retries = max_retries
    
    async def _invoke_with_retry(self, prompt: str, return_json: bool = False) -> Any:
        """
        带重试和信号量控制的 LLM 调用
        
        Args:
            prompt: 提示词
            return_json: 是否返回 JSON
            
        Returns:
            LLM 响应
        """
        if not self.llm_client:
            raise ValueError("LLM 客户端未初始化")
        
        last_exception = None
        
        for attempt in range(self.max_retries):
            try:
                # 使用信号量控制并发（确保所有 LLM 调用都通过信号量）
                if self.semaphore:
                    # 信号量会限制同时进行的 LLM 调用数量
                    async with self.semaphore:
                        response = await self.llm_client.invoke(prompt, return_json=return_json)
                else:
                    # 如果没有信号量，直接调用（不应该发生，但保留作为后备）
                    response = await self.llm_client.invoke(prompt, return_json=return_json)
                
                return response
            except Exception as e:
                last_exception = e
                # 如果是最后一次尝试，直接抛出异常
                if attempt == self.max_retries - 1:
                    raise
                
                # 指数退避：等待时间 = 2^attempt 秒
                wait_time = 2 ** attempt
                print(f"⚠️ LLM 调用失败（尝试 {attempt + 1}/{self.max_retries}），{wait_time} 秒后重试: {e}")
                await asyncio.sleep(wait_time)
        
        # 如果所有重试都失败，抛出最后一个异常
        raise last_exception
    
    def load_profiles(self, profiles_file: str) -> List[Dict[str, Any]]:
        """
        加载 character_profiles.json
        
        Args:
            profiles_file: character_profiles.json 文件路径
            
        Returns:
            人物画像列表
        """
        with open(profiles_file, 'r', encoding='utf-8') as f:
            data = json.load(f)
        
        if "profiles" in data:
            return data["profiles"]
        elif isinstance(data, list):
            return data
        else:
            return []
    
    async def extract_role(self, profile: Dict[str, Any]) -> str:
        """
        提取叙事角色定位（role）
        
        从关系状态、行动模式等推断角色定位。
        例如：弟子、师父、逃犯、异端、官员等。
        
        使用 LLM 提取。
        """
        if not self.llm_client:
            raise ValueError("LLM 客户端未初始化，无法提取 role")
        
        canonical_name = profile.get("canonical_name", "")
        relationship_states = profile.get("relationship_states", [])
        action_patterns = profile.get("action_patterns", [])
        
        # 构建上下文信息
        rel_info = []
        for rel_state in relationship_states[:5]:  # 只取前5个关系状态
            if rel_state.get("dimension") == "权力动态":
                value = rel_state.get("value", "")
                target_id = rel_state.get("target_id", [])
                if isinstance(target_id, list) and len(target_id) >= 2:
                    rel_info.append(f"对{target_id[1]}的关系：{value}")
        
        top_actions = [ap.get("action_word", "") for ap in action_patterns[:5]]
        
        prompt = f"""你是一位专业的人物分析助手。请根据以下信息，判断人物「{canonical_name}」的叙事角色定位（role）。

角色定位是指"他在这个故事里被当作什么角色？"，例如：弟子、导师、逃犯、异端、官员、执法者、路人等。

人物信息：
- 关系状态：{'; '.join(rel_info) if rel_info else '无'}
- 主要行动：{', '.join(top_actions) if top_actions else '无'}

要求：
1. 只返回一个简洁的角色定位词（1-4个字）
2. 这是叙事定位，不是身份卡
3. 稳定，不随数值抖动
4. 只返回角色定位词，不要其他内容

角色定位："""
        
        try:
            response = await self._invoke_with_retry(prompt, return_json=False)
            content = response if isinstance(response, str) else str(response)
            content = content.strip()
            # 清理可能的引号或标记
            content = content.strip('"').strip("'").strip()
            if content and len(content) <= 10:  # 角色定位应该很短
                return content
            else:
                return "角色未明"
        except Exception as e:
            print(f"⚠️ LLM 提取 role 失败（已重试 {self.max_retries} 次）: {e}")
            return "角色未明"
    
    async def extract_social_position(self, profile: Dict[str, Any]) -> str:
        """
        提取社会位置（social_position）
        
        从状态变化、关系状态等推断社会位置。
        例如：富商之子、宗门掌门、被污名化的异端等。
        
        使用 LLM 提取。
        """
        if not self.llm_client:
            raise ValueError("LLM 客户端未初始化，无法提取 social_position")
        
        canonical_name = profile.get("canonical_name", "")
        character_states = profile.get("character_states", [])
        relationship_states = profile.get("relationship_states", [])
        
        # 构建上下文信息
        worldview_states = [sc for sc in character_states if sc.get("dimension") == "世界观"]
        worldview_info = [sc.get("value", "") for sc in worldview_states[-3:]]  # 取最后3个
        
        rel_info = []
        for rel_state in relationship_states[:5]:
            if rel_state.get("dimension") == "权力动态":
                value = rel_state.get("value", "")
                target_id = rel_state.get("target_id", [])
                if isinstance(target_id, list) and len(target_id) >= 2:
                    rel_info.append(f"对{target_id[1]}：{value}")
        
        prompt = f"""你是一位专业的人物分析助手。请根据以下信息，判断人物「{canonical_name}」的社会位置（social_position）。

社会位置是指"世界如何结构性地对待他？"，例如：富商之子、宗门掌门、被污名化的异端、臣子、平民等。

人物信息：
- 世界观状态：{'; '.join(worldview_info) if worldview_info else '无'}
- 关系状态：{'; '.join(rel_info) if rel_info else '无'}

要求：
1. 只返回一个简洁的社会位置描述（2-8个字）
2. 可以变，但只在阶段切换时变
3. 只返回社会位置，不要其他内容

社会位置："""
        
        try:
            response = await self._invoke_with_retry(prompt, return_json=False)
            content = response if isinstance(response, str) else str(response)
            content = content.strip()
            content = content.strip('"').strip("'").strip()
            if content and len(content) <= 15:  # 社会位置应该不会太长
                return content
            else:
                return "社会位置未明"
        except Exception as e:
            print(f"⚠️ LLM 提取 social_position 失败（已重试 {self.max_retries} 次）: {e}")
            return "社会位置未明"
    
    async def extract_disposition(self, profile: Dict[str, Any]) -> str:
        """
        提取稳定的性格倾向（disposition）
        
        从行动模式的反复模式推断性格倾向。
        例如：顺从/抗争、冷静/冲动、被动/主动。
        
        使用 LLM 提取。
        """
        if not self.llm_client:
            raise ValueError("LLM 客户端未初始化，无法提取 disposition")
        
        canonical_name = profile.get("canonical_name", "")
        action_patterns = profile.get("action_patterns", [])
        relationship_states = profile.get("relationship_states", [])
        
        # 构建上下文信息
        top_actions = [ap.get("action_word", "") for ap in action_patterns[:10]]  # 取前10个行动词
        action_freq = {ap.get("action_word", ""): ap.get("frequency", 0) for ap in action_patterns[:10]}
        
        rel_info = []
        for rel_state in relationship_states[:5]:
            if rel_state.get("dimension") == "权力动态":
                value = rel_state.get("value", "")
                rel_info.append(value)
        
        prompt = f"""你是一位专业的人物分析助手。请根据以下信息，判断人物「{canonical_name}」的稳定性格倾向（disposition）。

性格倾向是指"他面对世界时的惯常姿态"，不是情绪！而是：顺从/抗争、冷静/冲动、被动/主动等。

人物信息：
- 主要行动模式（按频率）：{', '.join([f"{action}({freq}次)" for action, freq in action_freq.items()]) if action_freq else '无'}
- 关系权力动态：{'; '.join(rel_info) if rel_info else '无'}

要求：
1. 只返回一个简洁的性格倾向词（1-4个字）
2. 来自行为的反复模式，不是情绪
3. 只返回性格倾向词，不要其他内容

性格倾向："""
        
        try:
            response = await self._invoke_with_retry(prompt, return_json=False)
            content = response if isinstance(response, str) else str(response)
            content = content.strip()
            content = content.strip('"').strip("'").strip()
            if content and len(content) <= 8:  # 性格倾向应该很短
                return content
            else:
                return "性格倾向未明"
        except Exception as e:
            print(f"⚠️ LLM 提取 disposition 失败（已重试 {self.max_retries} 次）: {e}")
            return "性格倾向未明"
    
    async def extract_worldview(self, profile: Dict[str, Any]) -> str:
        """
        提取世界观（worldview）
        
        从 dimension="世界观" 的 StateChange 中提取，使用 LLM 进行总结和提炼。
        """
        if not self.llm_client:
            raise ValueError("LLM 客户端未初始化，无法提取 worldview")
        
        canonical_name = profile.get("canonical_name", "")
        character_states = profile.get("character_states", [])
        
        # 查找所有世界观状态变化
        worldview_states = [
            sc for sc in character_states 
            if sc.get("dimension") == "世界观"
        ]
        
        if not worldview_states:
            return "世界观未明"
        
        # 收集所有世界观状态值
        worldview_values = [sc.get("value", "") for sc in worldview_states if sc.get("value")]
        
        if not worldview_values:
            return "世界观未明"
        
        # 如果只有一个世界观状态，直接返回
        if len(worldview_values) == 1:
            return worldview_values[0]
        
        # 如果有多个世界观状态，使用 LLM 进行总结
        prompt = f"""你是一位专业的人物分析助手。请根据以下世界观状态变化，总结人物「{canonical_name}」的世界观（worldview）。

世界观状态变化（按时间顺序）：
{chr(10).join([f"- {i+1}. {val}" for i, val in enumerate(worldview_values)])}

要求：
1. 总结人物的核心世界观，即"他如何理解世界的运作方式"
2. 如果世界观有变化，取最新的、最完整的表述
3. 只返回世界观描述，不要其他内容
4. 保持简洁（1-3句话）

世界观："""
        
        try:
            response = await self._invoke_with_retry(prompt, return_json=False)
            content = response if isinstance(response, str) else str(response)
            content = content.strip()
            content = content.strip('"').strip("'").strip()
            if content:
                return content
            else:
                # 如果 LLM 返回空，返回最新的世界观状态
                return worldview_values[-1]
        except Exception as e:
            print(f"⚠️ LLM 提取 worldview 失败（已重试 {self.max_retries} 次）: {e}，使用最新世界观状态")
            # 失败时返回最新的世界观状态
            return worldview_values[-1]
    
    async def generate_persona_summary(
        self,
        profile: Dict[str, Any],
        role: str,
        social_position: str,
        disposition: str,
        worldview: str
    ) -> str:
        """
        生成综合性描述（persona_summary）
        
        使用 LLM 生成 1-2 段综合性描述。
        """
        if not self.llm_client:
            raise ValueError("LLM 客户端未初始化，无法生成 persona_summary")
        
        canonical_name = profile.get("canonical_name", "")
        action_patterns = profile.get("action_patterns", [])
        top_actions = [ap.get("action_word", "") for ap in action_patterns[:5]]
        
        prompt = f"""你是一位专业的人物分析助手。请根据以下信息，生成人物「{canonical_name}」的1-2段综合性静态人设描述。

人物信息：
- 叙事角色定位：{role}
- 社会位置：{social_position}
- 性格倾向：{disposition}
- 世界观：{worldview}
- 主要行动模式：{', '.join(top_actions) if top_actions else '无'}

要求：
1. 写1-2段综合性描述
2. 这是"压缩区"，不加新事实，不写剧情，不预测未来
3. 只是阶段性人物画像，解释这个人物的核心特征
4. 用简洁、准确的中文描述
5. 不要使用"他"、"她"等代词，直接使用人物名称

请生成描述："""
        
        try:
            response = await self._invoke_with_retry(prompt, return_json=False)
            content = response if isinstance(response, str) else str(response)
            # 清理内容
            content = content.strip()
            if content.startswith("```"):
                lines = content.split("\n")
                content = "\n".join(lines[1:-1]) if len(lines) > 2 else content
                content = content.strip()
            if content:
                return content
            else:
                # 如果 LLM 返回空，返回简单拼接
                return f"{canonical_name}是一位{role}，{social_position}。性格倾向为{disposition}，世界观是{worldview}。"
        except Exception as e:
            print(f"⚠️ 生成 persona_summary 失败（已重试 {self.max_retries} 次）: {e}")
            # 返回简单拼接
            return f"{canonical_name}是一位{role}，{social_position}。性格倾向为{disposition}，世界观是{worldview}。"
    
    async def aggregate_persona(self, profile: Dict[str, Any]) -> CharacterPersona:
        """
        聚合单个人物的静态人设 🎭
        
        Args:
            profile: 人物画像（从 character_profiles.json 加载）
            
        Returns:
            静态人设
        """
        character_id = profile.get("character_id", "")
        canonical_name = profile.get("canonical_name", character_id)
        
        # 并发提取各个字段（4个方法同时执行，每个方法内部的 LLM 调用都通过信号量控制并发）
        # 这样可以在单个任务内部实现并发，同时通过信号量控制总体并发数
        role, social_position, disposition, worldview = await asyncio.gather(
            self.extract_role(profile),           # 内部调用 _invoke_with_retry（带信号量）
            self.extract_social_position(profile), # 内部调用 _invoke_with_retry（带信号量）
            self.extract_disposition(profile),     # 内部调用 _invoke_with_retry（带信号量）
            self.extract_worldview(profile),       # 内部调用 _invoke_with_retry（带信号量）
            return_exceptions=True
        )
        
        # 处理异常
        if isinstance(role, Exception):
            print(f"⚠️ 提取 role 失败: {role}")
            role = "角色未明"
        if isinstance(social_position, Exception):
            print(f"⚠️ 提取 social_position 失败: {social_position}")
            social_position = "社会位置未明"
        if isinstance(disposition, Exception):
            print(f"⚠️ 提取 disposition 失败: {disposition}")
            disposition = "性格倾向未明"
        if isinstance(worldview, Exception):
            print(f"⚠️ 提取 worldview 失败: {worldview}")
            worldview = "世界观未明"
        
        # 生成综合性描述（需要等待前面的结果）
        persona_summary = await self.generate_persona_summary(
            profile, role, social_position, disposition, worldview
        )
        
        return CharacterPersona(
            id=character_id,
            name=canonical_name,
            role=role,
            social_position=social_position,
            disposition=disposition,
            worldview=worldview,
            persona_summary=persona_summary
        )
    
    async def aggregate_all_personas(
        self, 
        profiles: List[Dict[str, Any]], 
        progress=None, 
        task=None
    ) -> List[CharacterPersona]:
        """
        聚合所有人物的静态人设 🎭
        
        Args:
            profiles: 人物画像列表
            progress: 进度条对象（可选）
            task: 进度条任务（可选）
            
        Returns:
            静态人设列表
        """
        # 并发处理所有人物（内部 LLM 调用已使用信号量控制并发）
        tasks = [self.aggregate_persona(profile) for profile in profiles]
        results = await asyncio.gather(*tasks, return_exceptions=True)
        
        personas = []
        for idx, result in enumerate(results):
            if isinstance(result, Exception):
                profile_name = profiles[idx].get("canonical_name", profiles[idx].get("character_id", "未知人物"))
                print(f"[red]❌ 聚合人物 {profile_name} 的人设失败: {result}[/red]")
                # 即使失败也尝试创建一个默认人设
                personas.append(CharacterPersona(
                    id=profiles[idx].get("character_id", ""),
                    name=profile_name,
                    role="角色未明",
                    social_position="社会位置未明",
                    disposition="性格倾向未明",
                    worldview="世界观未明",
                    persona_summary=f"未能生成 {profile_name} 的人设总结。"
                ))
            else:
                personas.append(result)
            
            # 更新进度条
            if progress and task:
                progress.advance(task)
        
        return personas


__all__ = ["PersonaAggregator"]

