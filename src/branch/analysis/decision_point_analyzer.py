"""
决策点分析器 - 分析决策点，识别最小骨架（关键事件）🎯✨

功能：
1. 识别最小骨架（关键事件）：哪些事件是关键事件，哪些是非关键事件
2. 从**重排后的主线**（canonical_branch_chronological.json）中提取所有决策点
3. 使用 LLM 分析每个决策点，判断：
   - 是否适合做分支（玩家可以选择）
   - 是否不适合做分支（太琐碎或影响太小）
4. 生成分析结果：
   - critical_events: 关键事件列表（最小骨架）
   - branch_points: 适合做分支的决策点
   - skip_points: 不适合做分支的决策点
   
注意：合流点不需要单独识别，合流点 = 最小骨架中的下一个关键事件
"""

import json
import asyncio
from typing import Dict, List, Any, Optional
from pathlib import Path

import sys
from pathlib import Path

# 添加 src 目录到路径
src_dir = Path(__file__).parent.parent.parent
if str(src_dir) not in sys.path:
    sys.path.insert(0, str(src_dir))

from rich.progress import Progress, SpinnerColumn, TextColumn, BarColumn, TaskProgressColumn, TimeElapsedColumn

from core.llm_client import AsyncLLMClient
from core.neo4j_client import Neo4jClient


class DecisionPointAnalyzer:
    """
    决策点分析器 - 分析决策点，识别最小骨架（关键事件）🎯
    
    设计理念：
    - **主线 = 最小骨架**：必须经历的关键事件序列（故事的最小骨架）
    - **分支在非关键事件之间走不同的路**：从决策点开始，走分支事件，但最终会回到下一个关键事件
    - **合流点 = 最小骨架中的下一个关键事件**：分支路径会回到主线的关键事件上
    - 不是所有决策点都适合做分支（可能太琐碎或影响太小）
    - 合流点不需要单独识别，合流点会自动识别为最小骨架中的下一个关键事件
    """
    
    def __init__(
        self,
        llm_client: Optional[AsyncLLMClient] = None,
        neo4j_client: Optional[Neo4jClient] = None,
        canonical_branch_path: str = "out/canonical_branch_chronological.json",
        max_concurrent: int = 5,
    ):
        """
        初始化决策点分析器
        
        Args:
            llm_client: LLM 客户端（如果为 None，会创建默认客户端）
            neo4j_client: Neo4j 客户端（如果为 None，会创建新客户端）
            canonical_branch_path: 重排后的主线记录文件路径（用于识别最小骨架，建议 canonical_branch_chronological.json）
            max_concurrent: 最大并发数（默认：5，用于控制 LLM 和数据库查询的并发）
        """
        self.llm_client = llm_client or AsyncLLMClient.create_default()
        self.neo4j_client = neo4j_client or Neo4jClient()
        self.canonical_branch_path = Path(canonical_branch_path)
        self.max_concurrent = max_concurrent
        self.semaphore = asyncio.Semaphore(max_concurrent)
    
    def load_decision_points(
        self,
        canonical_branch_path: str = "out/canonical_branch_chronological.json",
    ) -> List[Dict[str, Any]]:
        """
        从重排后的主线（canonical_branch_chronological.json）中加载所有决策点 📂
        
        Args:
            canonical_branch_path: 重排后的主线记录文件路径（建议用 chronological）
            
        Returns:
            决策点列表，每个元素包含 event_id, decision_point, canonical_choice, description 等
        """
        branch_file = Path(canonical_branch_path)
        if not branch_file.exists():
            raise FileNotFoundError(f"主线记录文件不存在: {canonical_branch_path}")
        
        with open(branch_file, "r", encoding="utf-8") as f:
            data = json.load(f)
        
        processed_events = data.get("processed_events", [])
        
        # 提取所有决策点
        decision_points = []
        for event in processed_events:
            if event.get("decision_point") is not None:
                decision_points.append({
                    "event_id": event.get("event_id"),
                    "decision_point": event.get("decision_point"),
                    "canonical_choice": event.get("canonical_choice"),
                    "description": event.get("description", ""),
                    "state_changes_count": len(event.get("state_changes", [])),
                    "selected_state_changes_count": len(event.get("selected_state_changes", [])),
                    "pre_condition": event.get("pre_condition"),
                })
        
        return decision_points
    
    async def analyze_decision_points(
        self,
        decision_points: List[Dict[str, Any]],
        all_event_ids: Optional[List[str]] = None,
        target_branch_count: Optional[int] = None,
    ) -> Dict[str, Any]:
        """
        分析决策点，识别最小骨架（关键事件）🎯
        
        Args:
            decision_points: 决策点列表
            all_event_ids: 所有事件ID列表（用于判断位置，如果为 None 会从 Neo4j 获取）
            target_branch_count: 可选，在**第二轮整合推荐**时作为「希望不超过 N 个」的软约束写入提示词；
                不再对结果做按顺序截断的机械后处理。
            
        Returns:
            分析结果（节选）：
            {
                "critical_events": [...],
                "branch_point_candidates": [...],  // 首轮 LLM 标为 branch 的全部候选（按故事顺序）
                "recommended_branch_points": [...],  // 第二轮 LLM 的推荐子集
                "recommendation_reasoning": {{ "E1": "..." }},  // 第二轮说明
                "branch_points": [...],  // 默认等于 recommended；可由前端改写入文件，供 generate-branch 使用
                "branch_points_user_overridden": false,
                "skip_points": [...],
                "analysis": {{ ... }},
            }
            
        注意：合流点不需要单独识别，合流点 = 最小骨架中的下一个关键事件
        """
        if not decision_points:
            return {
                "critical_events": [],
                "branch_point_candidates": [],
                "recommended_branch_points": [],
                "recommendation_reasoning": {},
                "branch_points": [],
                "branch_points_user_overridden": False,
                "skip_points": [],
                "analysis": {},
            }
        
        # 获取所有事件ID（用于判断位置）
        if all_event_ids is None:
            all_event_ids = await self._get_all_event_ids()
        
        # **第一步：识别最小骨架（关键事件）**
        # 从重排后的主线（canonical_branch_chronological）中识别关键事件
        critical_events = await self._identify_critical_events_from_canonical_branch(
            decision_points,
        )
        
        # 获取每个决策点的详细信息（从 Neo4j）
        decision_points_with_context = await self._enrich_decision_points(
            decision_points,
            all_event_ids,
        )
        
        # **第二步：分析每个决策点，判断是否适合做分支**
        # 使用并发执行加快速度，但用信号量控制并发数（LLM 调用）
        async def analyze_single_dp(dp: Dict[str, Any]) -> tuple:
            """分析单个决策点"""
            event_id = dp["event_id"]
            is_critical = event_id in critical_events
            
            # 使用信号量控制并发数（LLM 调用）
            async with self.semaphore:
                result = await self._analyze_single_decision_point(
                    dp, 
                    decision_points_with_context,
                    critical_events,
                    all_event_ids,
                )
                result["is_critical"] = is_critical
                return event_id, result
        
        analysis_results = {}
        
        with Progress(
            SpinnerColumn(),
            TextColumn("[progress.description]{task.description}"),
            BarColumn(),
            TaskProgressColumn(),
            TimeElapsedColumn(),
        ) as progress:
            task = progress.add_task("分析决策点...", total=len(decision_points_with_context))
            
            # 并发处理所有决策点
            tasks = [analyze_single_dp(dp) for dp in decision_points_with_context]
            results = await asyncio.gather(*tasks, return_exceptions=True)
            
            for i, result in enumerate(results):
                if isinstance(result, Exception):
                    event_id = decision_points_with_context[i]["event_id"]
                    print(f"⚠️  分析决策点 {event_id} 失败: {result}")
                    progress.update(task, advance=1)
                    continue
                
                event_id, analysis_result = result
                analysis_results[event_id] = analysis_result
                progress.update(task, advance=1)
        
        # 首轮分类：branch / skip
        first_pass_branch: List[str] = []
        skip_points: List[str] = []
        for event_id, result in analysis_results.items():
            category = result.get("category")
            if category == "branch":
                first_pass_branch.append(event_id)
            else:
                skip_points.append(event_id)

        all_dp_ids_ordered = [dp["event_id"] for dp in decision_points_with_context]
        branch_point_candidates = sorted(
            first_pass_branch,
            key=lambda eid: all_dp_ids_ordered.index(eid)
            if eid in all_dp_ids_ordered
            else 9999,
        )

        # 第二轮 LLM：在候选中整合推荐（不再按顺序机械截断）
        print("第二轮：整合分支点推荐（全候选 → 推荐子集）...")
        recommended_branch_points, recommendation_reasoning = (
            await self._llm_recommend_branch_subset(
                branch_point_candidates,
                decision_points_with_context,
                analysis_results,
                critical_events,
                all_event_ids,
                target_branch_count,
            )
        )
        effective_branch_points = (
            recommended_branch_points
            if recommended_branch_points
            else list(branch_point_candidates)
        )

        return {
            "critical_events": critical_events,
            "branch_point_candidates": branch_point_candidates,
            "recommended_branch_points": recommended_branch_points
            if recommended_branch_points
            else list(branch_point_candidates),
            "recommendation_reasoning": recommendation_reasoning,
            "branch_points": list(effective_branch_points),
            "branch_points_user_overridden": False,
            "skip_points": skip_points,
            "analysis": analysis_results,
        }
    
    async def _identify_critical_events_from_canonical_branch(
        self,
        decision_points: List[Dict[str, Any]],
    ) -> List[str]:
        """
        从重排后的主线（canonical_branch_chronological.json）中识别最小骨架（关键事件）🎯
        
        关键事件 = 必须经历的事件，是故事的最小骨架。
        分支路径在非关键事件之间走不同的路，但最终会回到下一个关键事件。
        
        Args:
            decision_points: 决策点列表（用于上下文）
            
        Returns:
            关键事件ID列表（最小骨架）
        """
        # 从重排后的主线加载所有事件（必须使用 chronological，与后续路径生成一致）
        if not self.canonical_branch_path.exists():
            raise FileNotFoundError(f"重排后的主线记录文件不存在: {self.canonical_branch_path}")
        
        with open(self.canonical_branch_path, "r", encoding="utf-8") as f:
            canonical_data = json.load(f)
        
        processed_events = canonical_data.get("processed_events", [])
        
        # 获取所有事件的详细信息（从 canonical_branch.json 和 Neo4j）
        # 使用并发执行加快速度，但用信号量控制并发数
        async def process_event(event_record: Dict[str, Any]) -> Optional[Dict[str, Any]]:
            """处理单个事件，获取详细信息"""
            event_id = event_record.get("event_id")
            if not event_id:
                return None
            
            # 使用信号量控制并发数
            async with self.semaphore:
                # 从 Neo4j 获取事件的指标信息
                event_info = await self._get_event_info(event_id)
                
                # 从重排后的主线获取事件的其他信息
                description = event_record.get("description", "")
                state_changes_count = len(event_record.get("state_changes", []))
                selected_state_changes_count = len(event_record.get("selected_state_changes", []))
                is_decision_point = event_record.get("decision_point") is not None
                
                return {
                    "event_id": event_id,
                    "description": description,
                    "state_changes_count": state_changes_count,
                    "selected_state_changes_count": selected_state_changes_count,
                    "is_decision_point": is_decision_point,
                    "event_info": event_info,  # 从 Neo4j 获取的指标信息
                }
        
        all_events_info = []
        
        with Progress(
            SpinnerColumn(),
            TextColumn("[progress.description]{task.description}"),
            BarColumn(),
            TaskProgressColumn(),
            TimeElapsedColumn(),
        ) as progress:
            task = progress.add_task("获取事件信息...", total=len(processed_events))
            
            # 并发处理所有事件
            tasks = [process_event(event_record) for event_record in processed_events]
            results = await asyncio.gather(*tasks, return_exceptions=True)
            
            for i, result in enumerate(results):
                if isinstance(result, Exception):
                    print(f"⚠️  处理事件失败: {result}")
                    progress.update(task, advance=1)
                    continue
                
                if result is not None:
                    all_events_info.append(result)
                
                progress.update(task, advance=1)
        
        # 使用 LLM 识别关键事件
        prompt = self._build_critical_events_identification_prompt(
            all_events_info,
            decision_points,
        )
        
        response = await self.llm_client.invoke(prompt, return_json=True)
        
        if isinstance(response, str):
            response = json.loads(response)
        
        critical_events = response.get("critical_events", [])
        
        return critical_events
    
    def _build_critical_events_identification_prompt(
        self,
        all_events_info: List[Dict[str, Any]],
        decision_points: List[Dict[str, Any]],
    ) -> str:
        """
        构建关键事件识别提示词 📝
        """
        # 格式化所有事件信息
        events_text = []
        for i, event_data in enumerate(all_events_info, 1):
            event_id = event_data["event_id"]
            description = event_data.get("description", "")[:100]
            info = event_data.get("event_info", {})
            state_changes_count = event_data.get("state_changes_count", 0)
            selected_state_changes_count = event_data.get("selected_state_changes_count", 0)
            is_decision_point = event_data.get("is_decision_point", False)
            
            events_text.append(
                f"[{i}] {event_id}: "
                f"描述={description}, "
                f"影响值={info.get('impact_value', 0.0)}, "
                f"冲突值={info.get('conflict_value', 0.0)}, "
                f"状态变化={state_changes_count}个(应用{selected_state_changes_count}个), "
                f"是否决策点={'是' if is_decision_point else '否'}"
            )
        
        events_text_str = "\n".join(events_text)
        
        prompt = f"""你是一个故事结构分析助手。请识别故事的最小骨架（关键事件）。

**重要理念**：
- **主线 = 最小骨架**：必须经历的关键事件序列（故事的最小骨架）
- **分支在非关键事件之间走不同的路**：从决策点开始，走分支事件，但最终会回到下一个关键事件
- **合流点 = 最小骨架中的下一个关键事件**：分支路径会回到主线的关键事件上

=== 所有事件信息 ===
{events_text_str}

=== 判断标准 ===

**关键事件（最小骨架）的特征**：
1. **故事的核心转折点**：对故事走向有决定性影响的事件
2. **高影响值**：impact_value >= 70，表明事件对故事有重大影响
3. **高冲突值**：conflict_value >= 60，表明事件是故事的重要冲突点
4. **因果关系的关键节点**：后续事件必须依赖这个事件才能发生
5. **故事结构的关键点**：如开始、转折、高潮、结局等

**非关键事件的特征**：
1. **过渡性事件**：连接关键事件的过渡性事件
2. **低影响值**：impact_value < 50，对故事走向影响较小
3. **日常性事件**：日常对话、环境描述等
4. **可选事件**：即使跳过也不影响故事的核心走向

=== 分析要求 ===

请识别出故事的最小骨架（关键事件），这些事件：
- 必须经历，不能跳过
- 是故事的核心结构
- 分支路径最终会回到这些关键事件上

请输出一个 JSON 对象：
{{
    "critical_events": ["E1", "E4", "E12", "E24"],
    "reasoning": "为什么这些事件是关键事件的详细理由（2-3句话）"
}}

注意：
- 关键事件数量应该适中（通常占总事件的 30%-60%，根据故事分支复杂度调整）
- 第一个事件和最后一个事件通常是关键事件
- 关键事件应该均匀分布在故事中，形成清晰的故事结构
"""
        
        return prompt
    
    async def _get_all_event_ids(self) -> List[str]:
        """
        获取所有事件ID（按数字顺序）📚
        
        注意：使用数字排序而不是字符串排序（E1, E2, ..., E10, E11, ...）
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
    
    async def _enrich_decision_points(
        self,
        decision_points: List[Dict[str, Any]],
        all_event_ids: List[str],
    ) -> List[Dict[str, Any]]:
        """
        丰富决策点信息（添加位置、前后事件等上下文）📚
        
        Args:
            decision_points: 决策点列表
            all_event_ids: 所有事件ID列表
            
        Returns:
            丰富后的决策点列表
        """
        # 使用并发执行加快速度，但用信号量控制并发数
        async def enrich_single_decision_point(dp: Dict[str, Any]) -> Dict[str, Any]:
            """丰富单个决策点信息"""
            event_id = dp["event_id"]
            
            # 获取事件在序列中的位置
            try:
                position = all_event_ids.index(event_id)  # 从 0 开始
                total_events = len(all_event_ids)
                
                # 计算位置比例：第一个事件是 0%，最后一个事件是 100%
                if total_events > 1:
                    position_ratio = position / (total_events - 1)
                elif total_events == 1:
                    position_ratio = 0.0  # 只有一个事件，设为 0%
                else:
                    position_ratio = 0.0
            except ValueError:
                position = -1
                position_ratio = 0.0
            
            # 获取前后事件ID
            prev_event_id = None
            next_event_id = None
            if position > 0:
                prev_event_id = all_event_ids[position - 1]
            if position < len(all_event_ids) - 1:
                next_event_id = all_event_ids[position + 1]
            
            # 使用信号量控制并发数
            async with self.semaphore:
                # 获取事件详细信息（从 Neo4j）
                event_info = await self._get_event_info(event_id)
            
            return {
                **dp,
                "position": position,
                "position_ratio": position_ratio,  # 0.0-1.0，事件在故事中的位置比例
                "prev_event_id": prev_event_id,
                "next_event_id": next_event_id,
                "event_info": event_info,
            }
        
        enriched = []
        
        with Progress(
            SpinnerColumn(),
            TextColumn("[progress.description]{task.description}"),
            BarColumn(),
            TaskProgressColumn(),
            TimeElapsedColumn(),
        ) as progress:
            task = progress.add_task("丰富决策点信息...", total=len(decision_points))
            
            # 并发处理所有决策点
            tasks = [enrich_single_decision_point(dp) for dp in decision_points]
            results = await asyncio.gather(*tasks, return_exceptions=True)
            
            for i, result in enumerate(results):
                if isinstance(result, Exception):
                    print(f"⚠️  丰富决策点信息失败: {result}")
                    progress.update(task, advance=1)
                    continue
                
                enriched.append(result)
                progress.update(task, advance=1)
        
        return enriched
    
    async def _get_event_info(self, event_id: str) -> Dict[str, Any]:
        """从 Neo4j 获取事件详细信息"""
        query = """
        MATCH (e:Event {id: $event_id})
        RETURN e.id as id, e.行动 as action, e.结果影响 as impact,
               e.impact_value as impact_value, e.emotion_value as emotion_value,
               e.conflict_value as conflict_value
        """
        
        with self.neo4j_client._driver.session() as session:
            result = session.run(query, event_id=event_id)
            record = result.single()
            
            if record:
                return {
                    "id": record.get("id"),
                    "action": record.get("action", ""),
                    "impact": record.get("impact", ""),
                    "impact_value": record.get("impact_value", 0.0),
                    "emotion_value": record.get("emotion_value", 0.0),
                    "conflict_value": record.get("conflict_value", 0.0),
                }
        
        return {}
    
    async def _analyze_single_decision_point(
        self,
        decision_point: Dict[str, Any],
        all_decision_points: List[Dict[str, Any]],
        critical_events: List[str],
        all_event_ids: List[str],
    ) -> Dict[str, Any]:
        """
        分析单个决策点，判断适合做分支还是跳过 🎯
        
        注意：合流点不需要单独识别，合流点 = 最小骨架中的下一个关键事件
        
        Args:
            decision_point: 决策点信息
            all_decision_points: 所有决策点列表（用于上下文）
            critical_events: 关键事件列表（最小骨架）
            all_event_ids: 所有事件ID列表（用于查找下一个关键事件）
            
        Returns:
            分析结果：
            {
                "category": "branch"|"skip",
                "reasoning": "...",
                "confidence": 0.0-1.0,
            }
        """
        # 构建提示词
        prompt = self._build_analysis_prompt(
            decision_point, 
            all_decision_points,
            critical_events,
            all_event_ids,
        )
        
        # 调用 LLM
        response = await self.llm_client.invoke(prompt, return_json=True)
        
        if isinstance(response, str):
            response = json.loads(response)
        
        return {
            "category": response.get("category", "skip"),
            "reasoning": response.get("reasoning", ""),
            "confidence": response.get("confidence", 0.5),
        }
    
    def _build_analysis_prompt(
        self,
        decision_point: Dict[str, Any],
        all_decision_points: List[Dict[str, Any]],
        critical_events: List[str],
        all_event_ids: List[str],
    ) -> str:
        """
        构建决策点分析提示词 📝
        
        注意：合流点不需要单独识别，合流点 = 最小骨架中的下一个关键事件
        """
        event_id = decision_point["event_id"]
        position_ratio = decision_point.get("position_ratio", 0.0)
        event_info = decision_point.get("event_info", {})
        is_critical = event_id in critical_events
        
        # 找到当前决策点在所有决策点中的位置
        all_dp_ids = [dp["event_id"] for dp in all_decision_points]
        try:
            dp_index = all_dp_ids.index(event_id)
            dp_position_in_dps = f"{dp_index + 1}/{len(all_decision_points)}"
        except ValueError:
            dp_position_in_dps = "?"
        
        # 找到下一个关键事件（合流点）
        next_critical_event = None
        try:
            current_position = all_event_ids.index(event_id)
            # 找到当前事件之后的下一个关键事件
            for i in range(current_position + 1, len(all_event_ids)):
                if all_event_ids[i] in critical_events:
                    next_critical_event = all_event_ids[i]
                    break
        except ValueError:
            pass
        
        prompt = f"""你是一个故事结构分析助手。请分析以下决策点，判断它适合做什么处理。

**重要理念**：
- **主线 = 最小骨架**：必须经历的关键事件序列（故事的最小骨架）
- **分支在非关键事件之间走不同的路**：从决策点开始，走分支事件，但最终会回到下一个关键事件
- **合流点 = 最小骨架中的下一个关键事件**：分支路径会回到主线的关键事件上
- **合流点不需要单独识别**：合流点会自动识别为最小骨架中的下一个关键事件

=== 决策点信息 ===
事件ID: {event_id}
是否关键事件: {'是' if is_critical else '否'}
决策点标识: {decision_point.get('decision_point')}
主线选择: {decision_point.get('canonical_choice')}
描述: {decision_point.get('description', '')[:200]}...

事件在故事中的位置: {position_ratio:.1%} ({dp_position_in_dps} 个决策点中的位置)
事件行动: {event_info.get('action', '')}
结果影响: {event_info.get('impact', '')}
影响值: {event_info.get('impact_value', 0.0)}
情绪值: {event_info.get('emotion_value', 0.0)}
冲突值: {event_info.get('conflict_value', 0.0)}
状态变化数: {decision_point.get('state_changes_count', 0)}
应用的状态变化数: {decision_point.get('selected_state_changes_count', 0)}

下一个关键事件（合流点）: {next_critical_event if next_critical_event else '无'}

=== 关键事件列表（最小骨架） ===
{', '.join(critical_events)}

=== 所有决策点概览（上下文） ===
{self._format_all_decision_points(all_decision_points)}

=== 判断标准 ===

1. **适合做分支（branch）**：
   - 决策对故事走向有重要影响
   - 玩家选择会产生明显不同的后果
   - 状态变化数量较多（>= 10），说明选择影响大
   - 影响值或冲突值较高（>= 50）
   - 位置在故事前半部分或中段（0.0-0.8），给支线足够的发展空间
   - **注意**：分支路径会回到下一个关键事件（合流点）

2. **不适合做分支（skip）**：
   - 决策影响较小或太琐碎
   - 状态变化数量很少（< 5），选择影响不大
   - 影响值很低（< 30），对故事走向影响小
   - 位置太靠后（> 0.9），没有足够空间发展支线
   - 决策点太密集，如果都做分支会导致复杂度爆炸

**注意**：合流点不需要单独识别！合流点 = 最小骨架中的下一个关键事件。

=== 分析要求 ===
请综合考虑：
- 决策点的重要性（影响值、状态变化数）
- 决策点在故事中的位置（给支线足够发展空间）
- 决策点之间的密度（避免分支过多导致复杂度爆炸）
- 故事的整体结构（哪些是关键转折点）
- 下一个关键事件（合流点）的位置

请输出一个 JSON 对象：
{{
  "category": "branch"|"skip",
  "reasoning": "为什么这样分类的详细理由（2-3句话）",
  "confidence": 0.0-1.0  // 判断的置信度
}}

注意：
- 如果决策点太密集（前后都有很多决策点），优先选择最重要的做分支
- 如果位置太靠后（> 0.9），通常不适合做分支
- 如果影响值很低（< 30）且状态变化很少（< 5），通常应该跳过
- **合流点不需要单独识别**：合流点会自动识别为最小骨架中的下一个关键事件
"""
        
        return prompt
    
    def _format_all_decision_points(
        self,
        all_decision_points: List[Dict[str, Any]],
    ) -> str:
        """格式化所有决策点概览"""
        lines = []
        for i, dp in enumerate(all_decision_points, 1):
            event_id = dp["event_id"]
            position_ratio = dp.get("position_ratio", 0.0)
            impact_value = dp.get("event_info", {}).get("impact_value", 0.0)
            state_changes_count = dp.get("state_changes_count", 0)
            
            lines.append(
                f"  [{i}] {event_id}: 位置 {position_ratio:.1%}, "
                f"影响值 {impact_value:.1f}, 状态变化 {state_changes_count} 个"
            )
        
        return "\n".join(lines) if lines else "  无"

    def _find_next_critical_event(
        self,
        event_id: str,
        critical_events: List[str],
        all_event_ids: List[str],
    ) -> Optional[str]:
        """查找指定事件之后的下一个关键事件。"""
        try:
            current_position = all_event_ids.index(event_id)
        except ValueError:
            return None

        critical_set = set(critical_events)
        for index in range(current_position + 1, len(all_event_ids)):
            candidate_id = all_event_ids[index]
            if candidate_id in critical_set:
                return candidate_id
        return None

    def _format_branch_recommendation_candidate(
        self,
        decision_point: Dict[str, Any],
        first_pass_result: Dict[str, Any],
        next_critical_event: Optional[str],
    ) -> str:
        """格式化第二轮推荐时单个候选分支点的完整信息。"""
        event_id = decision_point.get("event_id", "")
        event_info = decision_point.get("event_info", {})
        description = (decision_point.get("description") or "").strip()
        pre_condition = decision_point.get("pre_condition")
        first_pass_reasoning = (first_pass_result.get("reasoning") or "").strip()

        parts = [
            f"- {event_id}",
            f"  - 故事位置: {float(decision_point.get('position_ratio') or 0):.1%}",
            f"  - 决策标识: {decision_point.get('decision_point')}",
            f"  - 主线选择: {decision_point.get('canonical_choice')}",
            f"  - 事件描述: {description if description else '无'}",
            f"  - 前序事件: {decision_point.get('prev_event_id') or '无'}",
            f"  - 后续事件: {decision_point.get('next_event_id') or '无'}",
            f"  - 下一个关键事件(预计合流点): {next_critical_event or '无'}",
            f"  - 事件行动: {event_info.get('action', '') or '无'}",
            f"  - 结果影响: {event_info.get('impact', '') or '无'}",
            (
                f"  - 指标: 影响值={event_info.get('impact_value', 0.0)}, "
                f"情绪值={event_info.get('emotion_value', 0.0)}, "
                f"冲突值={event_info.get('conflict_value', 0.0)}"
            ),
            (
                f"  - 状态变化: {decision_point.get('state_changes_count', 0)} 个, "
                f"其中主线采用 {decision_point.get('selected_state_changes_count', 0)} 个"
            ),
            f"  - 前置条件: {pre_condition if pre_condition else '无'}",
            (
                f"  - 首轮判断: branch, confidence={first_pass_result.get('confidence', 0):.2f}, "
                f"reasoning={first_pass_reasoning if first_pass_reasoning else '无'}"
            ),
        ]
        return "\n".join(parts)

    async def _llm_recommend_branch_subset(
        self,
        candidates_ordered: List[str],
        decision_points_with_context: List[Dict[str, Any]],
        analysis_results: Dict[str, Dict[str, Any]],
        critical_events: List[str],
        all_event_ids: List[str],
        target_branch_count: Optional[int],
    ) -> tuple[List[str], Dict[str, str]]:
        """
        第二轮 LLM：在首轮均已标为 branch 的候选中，从整体叙事与交互成本出发给出推荐子集。
        """
        if not candidates_ordered:
            return [], {}

        dp_by_id = {dp["event_id"]: dp for dp in decision_points_with_context}
        lines: List[str] = []
        for eid in candidates_ordered:
            dp = dp_by_id.get(eid, {})
            ar = analysis_results.get(eid, {})
            next_critical_event = self._find_next_critical_event(
                eid,
                critical_events,
                all_event_ids,
            )
            lines.append(
                self._format_branch_recommendation_candidate(
                    dp,
                    ar,
                    next_critical_event,
                )
            )

        n_hint = ""
        if target_branch_count is not None and int(target_branch_count) > 0:
            n = int(target_branch_count)
            n_hint = (
                f"\n【数量偏好】希望最终 ``recommended`` 列表长度**不超过 {n}**；"
                "若候选都很重要可略超，但须在 reasoning 中说明取舍；若必须删减，优先去掉影响较弱或与其它分支点过密的。"
            )

        ce_line = ""
        if critical_events:
            ce_line = "关键事件（最小骨架）顺序参考：" + " -> ".join(
                str(x) for x in critical_events[:48]
            )

        prompt = f"""你是文字冒险 / 互动叙事结构编辑。以下事件已在第一轮分析中均被标为「适合做分支」的候选。
请站在**整体故事节奏、玩家认知负荷、支线展开空间**的角度，给出一份**推荐给作者默认启用**的分支点列表。

{ce_line}

=== 分支候选完整信息（按故事时间顺序）===
{chr(10).join(lines)}
{n_hint}

请特别关注：
- 候选事件本身是不是明确的剧情转折，而不只是局部动作差异。
- 候选事件到下一个关键事件之间是否真的有足够的支线展开空间。
- 相邻候选之间是否信息冗余，是否存在保留一个就足够的情况。
- 主线选择、前置条件、状态变化和结果影响是否表明这个点值得玩家明确做选择。

=== 输出要求 ===
仅输出一个 JSON 对象，不要其它文字：
{{
  "recommended": ["事件ID1", "事件ID2"],
  "reasoning": {{
     "事件ID1": "为何推荐保留或调整（一句话）"
  }}
}}

规则：
1. "recommended" 必须是上述候选 ID 的**子集**（不可引入新 ID），顺序与故事从早到晚一致。
2. 可以推荐全部候选，也可以只推荐一部分。
3. "reasoning" 需覆盖 "recommended" 中每一个 ID。
"""

        try:
            async with self.semaphore:
                resp = await self.llm_client.invoke(prompt, return_json=True)
            if isinstance(resp, str):
                resp = json.loads(resp)
            raw_rec = resp.get("recommended") or resp.get("branch_points")
            if not isinstance(raw_rec, list):
                return list(candidates_ordered), {}
            cand_set = set(candidates_ordered)
            seen: set[str] = set()
            ordered_rec: List[str] = []
            for x in raw_rec:
                xs = str(x).strip()
                if xs in cand_set and xs not in seen:
                    seen.add(xs)
                    ordered_rec.append(xs)
            reasoning_raw = resp.get("reasoning")
            reasoning: Dict[str, str] = {}
            if isinstance(reasoning_raw, dict):
                reasoning = {str(k): str(v) for k, v in reasoning_raw.items()}
            if not ordered_rec:
                return list(candidates_ordered), reasoning
            return ordered_rec, reasoning
        except Exception as e:
            print(f"⚠️  第二轮分支点推荐失败，回退为全部候选: {e}")
            return list(candidates_ordered), {}

    def save_analysis_result(
        self,
        analysis_result: Dict[str, Any],
        output_path: str = "out/decision_points_analysis.json",
    ) -> None:
        """
        保存分析结果到文件 💾
        
        Args:
            analysis_result: 分析结果
            output_path: 输出文件路径
        """
        output_file = Path(output_path)
        output_file.parent.mkdir(parents=True, exist_ok=True)
        
        with open(output_file, "w", encoding="utf-8") as f:
            json.dump(analysis_result, f, ensure_ascii=False, indent=2)
        
        print(f"分析结果已保存到: {output_path}")
    
    def close(self):
        """关闭资源"""
        if hasattr(self, "neo4j_client"):
            self.neo4j_client.close()


__all__ = ["DecisionPointAnalyzer"]

