"""
结局候选池确定器 - 根据分支选择确定可能的结局范围 🎯✨

功能：
1. 分析分支选择和主线选择，确定可能的结局类型范围
2. 在分支事件生成之前调用，用于指导后续的事件生成
3. 缩小结局范围，提高结局匹配的准确性
"""

import json
import asyncio
from typing import Dict, List, Any, Optional
from pathlib import Path
from itertools import product

from core.llm_client import AsyncLLMClient
from ..utils.path_data_loader import PathDataLoader


class EndingCandidateDeterminer:
    """
    结局候选池确定器 🎯
    
    核心功能：
    - 根据分支选择分析可能的结局类型
    - 缩小结局范围，提高匹配准确性
    - 为分支事件生成提供方向指导
    """
    
    def __init__(
        self,
        llm_client: Optional[AsyncLLMClient] = None,
        canonical_branch_path: str = "out/canonical_branch_chronological.json",
        branches_path: str = "out/branches.json",
    ):
        """
        初始化结局候选池确定器
        
        Args:
            llm_client: LLM 客户端
            canonical_branch_path: 重排后的主线记录文件路径（建议 canonical_branch_chronological.json，与 generate-all-paths 一致）
            branches_path: 分支记录文件路径
        """
        self.llm_client = llm_client or AsyncLLMClient.create_default()
        
        # 重要：优先使用 branches.json 所在目录的同批产物，避免误读项目根 out/ 的旧文件
        base_dir = Path(branches_path).resolve().parent
        ending_candidates_path = base_dir / "ending_candidates.json"
        decision_analysis_path = base_dir / "decision_points_analysis.json"
        extract_chain_path = base_dir / "extract_chain.json"

        # 使用 PathDataLoader 统一管理数据加载（避免重复代码）✨
        self.data_loader = PathDataLoader(
            ending_candidates_path=str(ending_candidates_path),
            branches_path=branches_path,
            decision_analysis_path=str(decision_analysis_path),
            extract_chain_path=str(extract_chain_path),
        )
        
        # 从 data_loader 获取数据（复用加载逻辑）✨
        self.branches = self.data_loader.branches_data
        
        # 加载主线记录（PathDataLoader 不包含 canonical_branch，所以保留单独加载）
        self.canonical_branch_path = Path(canonical_branch_path)
        self._load_canonical_branch()
        
        # 创建信号量控制并发数（默认最多同时处理3个分支/路径）
        self.semaphore = asyncio.Semaphore(3)
    
    @staticmethod
    def _extract_event_number(event_id: str) -> int:
        """从事件ID中提取数字部分（如 E4 -> 4, E14 -> 14）"""
        if not event_id:
            return 0
        import re
        match = re.search(r'(\d+)', event_id)
        return int(match.group(1)) if match else 0
    
    def _load_canonical_branch(self) -> None:
        """
        加载重排后的主线记录（用于获取事件顺序）📚
        
        注意：PathDataLoader 不包含 canonical_branch 的加载，所以保留单独加载逻辑；应使用 chronological 与路径生成一致。
        """
        if not self.canonical_branch_path.exists():
            self.canonical_branch = {}
            return
        
        try:
            with open(self.canonical_branch_path, "r", encoding="utf-8") as f:
                self.canonical_branch = json.load(f)
        except Exception as e:
            print(f"  ⚠️  加载主线记录失败: {e}")
            self.canonical_branch = {}
    
    def _get_canonical_event_info(self, fork_event_id: str) -> Dict[str, Any]:
        """
        获取主线事件信息
        
        Args:
            fork_event_id: 分叉事件ID
            
        Returns:
            包含事件信息的字典
        """
        for record in self.canonical_branch.get("processed_events", []):
            if record.get("event_id") == fork_event_id:
                # canonical_choice 是事件记录的直接字段，可能是字符串或字典
                canonical_choice = record.get("canonical_choice")
                
                # 处理 canonical_choice 的格式
                choice_id = ""
                choice_description = ""
                if canonical_choice:
                    if isinstance(canonical_choice, dict):
                        choice_id = canonical_choice.get("choice_id", "")
                        choice_description = canonical_choice.get("description", "")
                    else:
                        # 如果是字符串，就是 choice_id
                        choice_id = str(canonical_choice)
                
                return {
                    "event_id": fork_event_id,
                    "description": record.get("description", ""),
                    "canonical_choice_id": choice_id,
                    "canonical_choice_description": choice_description,
                }
        return {}
    
    def _group_branches_by_fork_event(self) -> Dict[str, List[Dict[str, Any]]]:
        """
        按分叉事件ID分组分支
        
        Returns:
            字典，key 是 fork_event_id，value 是该事件的所有分支列表
        """
        grouped = {}
        for branch in self.branches:
            fork_id = branch.get("fork_event_id")
            if fork_id:
                if fork_id not in grouped:
                    grouped[fork_id] = []
                grouped[fork_id].append(branch)
        return grouped
    
    def _generate_path_combinations(self) -> List[List[Dict[str, Any]]]:
        """
        生成所有可能的路径组合（含主线）🎯
        
        每个分支点 = 1 个主线选择 + 若干分支选择；组合数 = 各分支点选择数之积。
        例如：3 个分支点，每点 3 个选择（1 主线 + 2 分支）→ 3^3 = 27 条路径；
        第一条组合为「全主线」 (canonical, canonical, ...)。
        
        Returns:
            路径组合列表，每个组合是一个分支选择列表
        """
        grouped_branches = self._group_branches_by_fork_event()
        
        if not grouped_branches:
            return []
        
        fork_events = sorted(grouped_branches.keys(), key=self._extract_event_number)
        choices_per_fork = []
        
        for fork_id in fork_events:
            branches = grouped_branches[fork_id]
            canonical_event = self._get_canonical_event_info(fork_id)
            canonical_choice_id = canonical_event.get("canonical_choice_id", "")
            # 若主线记录里没有该分叉点，从该分叉点的任一分支的 canonical_choice 取主线选择
            if not canonical_choice_id and branches:
                canonical_choice_id = branches[0].get("canonical_choice", "")
            
            choices = []
            if canonical_choice_id:
                choices.append({
                    "fork_event_id": fork_id,
                    "branch_id": f"canonical_{fork_id}",
                    "canonical_choice": canonical_choice_id,
                    "branch_choice": canonical_choice_id,
                    "description": f"主线选择: {canonical_choice_id}",
                    "reasoning": "这是主线选择，代表'正确'或'理想'的路径",
                    "is_canonical": True,
                })
            for branch in branches:
                branch_copy = branch.copy()
                branch_copy["is_canonical"] = False
                branch_copy.pop("generated_state_changes", None)
                choices.append(branch_copy)
            
            choices_per_fork.append(choices)
        
        all_combinations = list(product(*choices_per_fork))
        return [list(combo) for combo in all_combinations]
    
    async def _analyze_combined_tendency(
        self,
        path_combination: List[Dict[str, Any]],
    ) -> Dict[str, Any]:
        """
        分析路径组合的累积倾向性 🎯
        
        分析多个分支选择的组合带来的累积状态变化趋势。
        
        Args:
            path_combination: 路径组合（分支选择列表）
            
        Returns:
            包含累积倾向性分析的字典
        """
        # 构建路径组合信息
        path_info = ""
        for i, choice in enumerate(path_combination, 1):
            fork_id = choice.get("fork_event_id", "unknown")
            choice_id = choice.get("branch_choice", "unknown")
            description = choice.get("description", "")
            reasoning = choice.get("reasoning", "")[:200]
            is_canonical = choice.get("is_canonical", False)
            canonical_mark = " [主线选择]" if is_canonical else " [分支选择]"
            
            path_info += f"""
选择 {i}（分叉点 {fork_id}）{canonical_mark}:
- 选择ID: {choice_id}
- 描述: {description}
- 理由: {reasoning}...
"""
        
        # 构建提示词
        prompt = f"""你是一个倾向性分析助手，负责分析多个分支选择组合带来的累积状态变化趋势。

**核心理念**：
- **累积倾向性**：多个分支选择的组合会产生累积效应，需要综合分析
- **倾向性分析**：不要简单判断"好/坏"，而是分析选择的"因果趋势"和"倾向性"
- **提前结局**：若**前面的选择**（尤其第一个分叉点）已极端偏离（如彻底拒绝、恐惧退缩、关系决裂），剧情可能在该点后即**提前结局**，后续分叉点不会发生；此时应视为「可能提前结局」路径，便于后续匹配提前结局类候选。

**路径组合信息**（玩家经历的所有分支选择，按发生顺序）：
{path_info}

**任务**：
1. **累积倾向性分析**：分析这个路径组合带来的累积状态变化趋势
   - 关系变化：是增加"连接"还是增加"疏离"？（考虑所有选择的累积效应）
   - 情绪变化：是增加"平静"还是增加"焦虑"？（考虑所有选择的累积效应）
   - 成长变化：是增加"领悟"还是增加"固化"？（考虑所有选择的累积效应）
   - 自由变化：是增加"自由"还是增加"束缚"？（考虑所有选择的累积效应）

2. **生成标签**：根据累积倾向性分析，生成：
   - tendency_tags: 路径组合导致的状态变化标签（用于正向匹配，如 ["高领悟", "内心平静"]）
   - conflict_tags: 路径组合导致的冲突标签（用于负向匹配，如 ["封闭固化", "关系破裂"]）
   - 若前面选择可能导致提前结局，在标签中体现（如 "可能提前结局" "仅前段选择生效"），以便匹配 is_premature 类结局。

3. **判定剧情张力与是否可能提前结局**：
   - 若**第一个或靠前的分叉点**的选择已极端（彻底拒绝、恐惧退缩、决裂等），导致故事在该点后无法继续合流，则 is_immediate_trigger 应为 true，且该路径实际只会经历到该点即结束，后续分叉点不会发生。
   - 若路径组合极其致命（如：多次背叛、完全封闭），也可能导致立即触发结局。
   - 若只是小插曲，保留大部分候选结局，允许后续合流。

4. **合流压力测试**：判断这个路径组合是"发散"还是"回归"
   - 关键分歧点组合：高合流压力（路径组合导致大幅偏离）
   - 小插曲组合：低合流压力（路径组合影响较小）

输出格式（JSON）：
{{
  "tendency_analysis": {{
    "relationship": "增加连接/增加疏离/保持稳定",
    "emotion": "增加平静/增加焦虑/保持稳定",
    "growth": "增加领悟/增加固化/保持稳定",
    "freedom": "增加自由/增加束缚/保持稳定"
  }},
  "tendency_tags": ["标签1", "标签2"],
  "conflict_tags": ["冲突标签1", "冲突标签2"],
  "deviation_level": "low/medium/high",
  "merge_pressure": "low/medium/high",
  "is_immediate_trigger": false,
  "reasoning": "分析理由（说明累积效应）"
}}
"""
        
        # 调用 LLM 分析累积倾向性
        response = await self.llm_client.invoke(
            prompt,
            return_json=True,
        )
        
        # 解析响应
        if isinstance(response, str):
            response = json.loads(response)
        
        return {
            "tendency_analysis": response.get("tendency_analysis", {}),
            "tendency_tags": response.get("tendency_tags", []),
            "conflict_tags": response.get("conflict_tags", []),
            "deviation_level": response.get("deviation_level", "medium"),
            "merge_pressure": response.get("merge_pressure", "medium"),
            "is_immediate_trigger": response.get("is_immediate_trigger", False),
            "reasoning": response.get("reasoning", ""),
        }
    
    async def _generate_endings_for_fork_event(
        self,
        fork_event_id: str,
        fork_branches: List[Dict[str, Any]],
    ) -> List[Dict[str, Any]]:
        """
        为单个分叉点生成结局池 🎯
        
        根据主线事件、主线选择和该分叉点的所有分支选择，生成可能的结局类型。
        
        Args:
            fork_event_id: 分叉点事件ID
            fork_branches: 该分叉点的所有分支选择列表
            
        Returns:
            该分叉点对应的结局列表
        """
        # 获取主线事件信息
        canonical_event = self._get_canonical_event_info(fork_event_id)
        
        # 构建主线上下文
        canonical_context = ""
        if canonical_event:
            canonical_context = f"""
**主线事件信息**：
- 事件ID: {canonical_event.get("event_id", "")}
- 事件描述: {canonical_event.get("description", "")}
- 主线选择ID: {canonical_event.get("canonical_choice_id", "")}
- 主线选择描述: {canonical_event.get("canonical_choice_description", "")}
"""
        
        # 构建所有分支选择上下文
        branches_context = ""
        if fork_branches:
            branches_context = "\n**该分叉点的所有分支选择**：\n"
            for i, branch in enumerate(fork_branches, 1):
                branches_context += f"""
分支选择 {i}：
- 分支ID: {branch.get("branch_id", "unknown")}
- 分支选择ID: {branch.get("branch_choice", "unknown")}
- 分支选择描述: {branch.get("description", "")}
- 分支选择理由: {branch.get("reasoning", "")[:200]}...
- 主线选择（对比）: {branch.get("canonical_choice", "无")}
"""
        
        # 构建提示词
        prompt = f"""你是一个结局设计助手，负责根据主线事件和该分叉点的所有分支选择生成可能的结局类型。

**任务**：
根据主线事件、主线选择，以及该分叉点的所有分支选择，综合考虑生成可能的结局类型。
你需要考虑该分叉点的所有可能性，生成一个全面的结局池。

{canonical_context}
{branches_context}

**要求**：
1. **严格限制**：最多生成 3-4 个结局，不要超过这个数量
2. **结局结构**：
   - **1个最好结局**：如果该分叉点的某些分支选择很好，可能导致的最佳结果（如：深度领悟、关系修复、艺术成就等）
   - **1-2个中等结局**：正常发展的结局，代表该分叉点选择带来的常规影响（如：完成旅程但未达到特殊成就、保持现状等）
   - **1个最坏结局**：如果该分叉点的某些分支选择很糟，可能导致的最差结果（如：关系破裂、认知固化、提前结束等）

3. 考虑所有分支选择与主线选择的差异，综合分析可能导致的结局变化
4. 每个结局需要包含：
   - id: 结局的唯一标识符（如 "ending_master"）
   - name: 结局名称（如 "大师结局"）
   - description: 结局描述
   - tags: 正向标签列表（描述结局的特征，如 ["高领悟", "内心平静", "艺术成就"]）
   - conflict_tags: 冲突标签列表（描述与结局冲突的特征，如 ["封闭固化", "恐惧焦虑", "关系破裂"]）
   - is_premature: 是否为提前结局（boolean，通常只有最坏结局可能是提前结局）

输出格式（JSON）：
{{
  "default_endings": [
    {{
      "id": "ending_id1",
      "name": "结局名称",
      "description": "结局描述",
      "tags": ["标签1", "标签2"],
      "conflict_tags": ["冲突标签1", "冲突标签2"],
      "is_premature": false
    }},
    ...
  ]
}}
"""
        
        def _rule_fallback_endings() -> List[Dict[str, Any]]:
            """
            兜底：当 LLM 返回空/不合规时，用规则生成最小可用的结局池，
            确保不会出现“0 个结局”导致下游筛选崩溃或质量骤降。
            """
            base = str(fork_event_id or "unknown").strip() or "unknown"
            return [
                {
                    "id": f"ending_{base}_best",
                    "name": "较好结局",
                    "description": f"在分叉点 {base} 做出更积极的选择后，局势向更好的方向收束。",
                    "tags": ["积极", "成长", "修复"],
                    "conflict_tags": ["封闭", "决裂", "崩溃"],
                    "is_premature": False,
                },
                {
                    "id": f"ending_{base}_neutral",
                    "name": "普通结局",
                    "description": f"在分叉点 {base} 的选择没有带来极端后果，故事以较为常规的方式结束。",
                    "tags": ["常规", "平稳"],
                    "conflict_tags": ["极端偏离"],
                    "is_premature": False,
                },
                {
                    "id": f"ending_{base}_worst",
                    "name": "较坏结局",
                    "description": f"在分叉点 {base} 的选择导致关系破裂或认知固化，故事以更糟的方式提前收束。",
                    "tags": ["破裂", "固化", "失落"],
                    "conflict_tags": ["修复", "领悟", "平静"],
                    "is_premature": True,
                },
            ]

        async def _invoke_once(p: str) -> List[Dict[str, Any]]:
            response = await self.llm_client.invoke(p, return_json=True)
            if isinstance(response, str):
                response = json.loads(response)
            endings = response.get("default_endings", [])
            return endings if isinstance(endings, list) else []

        # 1) 第一次调用
        try:
            default_endings = await _invoke_once(prompt)
        except Exception as e:
            print(f"  ⚠️  分叉点 {fork_event_id} 生成结局池失败（首次调用异常）：{e}")
            default_endings = []

        # 2) 若为空/不合规：再强约束重试一次
        if not default_endings:
            retry_prompt = prompt + (
                "\n\n【强制要求】\n"
                "- 你必须输出 default_endings 数组，且长度必须是 3 或 4。\n"
                "- 每个元素必须包含 id/name/description/tags/conflict_tags/is_premature。\n"
                "- 不能输出空数组。\n"
            )
            try:
                default_endings = await _invoke_once(retry_prompt)
            except Exception as e:
                print(f"  ⚠️  分叉点 {fork_event_id} 生成结局池失败（重试调用异常）：{e}")
                default_endings = []

        # 3) 仍为空：规则兜底
        if not default_endings:
            print(f"  ⚠️  分叉点 {fork_event_id} 生成结局池为空，使用规则兜底生成 3 个结局")
            default_endings = _rule_fallback_endings()

        return default_endings
    
    async def _generate_canonical_ending(self) -> Dict[str, Any]:
        """
        生成主线真结局 🎯
        
        更稳定的做法：主线最后一个事件通常就是小说结尾（或紧邻结尾），
        直接将其“格式化”为结局结构，作为主线真结局。
        这样可避免再调用一次 LLM 重新编造结局，减少 token 消耗并提高可控性。
        
        Returns:
            主线真结局字典
        """
        # 获取主线所有事件信息
        processed_events = self.canonical_branch.get("processed_events", [])
        if not processed_events:
            return {}
        
        last_event = processed_events[-1]

        last_event_id = str(last_event.get("event_id") or "").strip()
        last_desc = str(last_event.get("description") or "").strip()

        # 最后事件描述为空时，再 fallback（保底兼容旧数据/异常数据）
        if not last_desc:
            return {
                "id": "ending_canonical_true",
                "name": "真结局",
                "description": "主线已抵达终点。",
                "tags": ["主线", "真结局"],
                "conflict_tags": [],
                "is_premature": False,
                "is_canonical": True,
                "source_event_id": last_event_id or None,
            }

        # 直接把最后事件“包装”为结局
        # 说明：此处不强行生成 tags/conflict_tags（避免引入新的 LLM 调用与不稳定性）
        # 如需更精细标签，可后续在筛选逻辑里由 LLM/规则生成。
        return {
            "id": "ending_canonical_true",
            "name": "真结局",
            "description": last_desc,
            "tags": ["主线", "真结局"],
            "conflict_tags": [],
            "is_premature": False,
            "is_canonical": True,
            "source_event_id": last_event_id or None,
        }
    
    async def _generate_default_endings(
        self,
        branches: Optional[List[Dict[str, Any]]] = None,
    ) -> List[Dict[str, Any]]:
        """
        第一步：生成默认结局池 🎯
        
        根据分叉点，为每个分叉点调用 LLM 生成结局池，然后合并所有结果，最后加入主线真结局。
        
        Args:
            branches: 分支列表（如果为 None，使用 self.branches）
            
        Returns:
            合并后的默认结局池列表，每个结局包含：id、name、description、tags、conflict_tags、is_premature、is_canonical（可选）
        """
        if branches is None:
            branches = self.branches
        
        all_endings = []
        seen_ending_ids = set()
        
        # 按分叉点分组
        grouped_branches = self._group_branches_by_fork_event()
        
        # 为每个分叉点生成结局池（并发处理）
        if grouped_branches:
            # 按数字顺序排序分叉点（E4 在前，E14 在后）
            fork_events = sorted(grouped_branches.keys(), key=self._extract_event_number)
            print(f"     为 {len(fork_events)} 个分叉点生成结局池（并发处理）...")
            
            async def process_single_fork_event(fork_event_id: str, fork_branches: List[Dict[str, Any]], index: int) -> List[Dict[str, Any]]:
                """处理单个分叉点的辅助函数"""
                async with self.semaphore:
                    print(f"     [{index}/{len(fork_events)}] 处理分叉点: {fork_event_id}")
                    try:
                        fork_endings = await self._generate_endings_for_fork_event(fork_event_id, fork_branches)
                        print(f"         ✅ 分叉点 {fork_event_id} 完成，生成了 {len(fork_endings)} 个结局")
                        return fork_endings
                    except Exception as e:
                        print(f"         ❌ 分叉点 {fork_event_id} 处理失败: {e}")
                        import traceback
                        traceback.print_exc()
                        return []
            
            # 并发处理所有分叉点
            tasks = [
                process_single_fork_event(fork_event_id, grouped_branches[fork_event_id], i + 1)
                for i, fork_event_id in enumerate(fork_events)
            ]
            all_fork_endings = await asyncio.gather(*tasks)
            
            # 合并所有分叉点的结局（去重）
            for fork_endings in all_fork_endings:
                for ending in fork_endings:
                    ending_id = ending.get("id")
                    if ending_id and ending_id not in seen_ending_ids:
                        seen_ending_ids.add(ending_id)
                        all_endings.append(ending)
        
        # 生成并加入主线真结局
        print("     生成主线真结局...")
        canonical_ending = await self._generate_canonical_ending()
        if canonical_ending and canonical_ending.get("id"):
            canonical_id = canonical_ending.get("id")
            if canonical_id not in seen_ending_ids:
                seen_ending_ids.add(canonical_id)
                all_endings.append(canonical_ending)
            else:
                # 如果已存在，更新为标记为主线真结局
                for ending in all_endings:
                    if ending.get("id") == canonical_id:
                        ending["is_canonical"] = True
                        break
        
        return all_endings
    
    async def _filter_candidates_by_tags(
        self,
        default_endings: List[Dict[str, Any]],
        tendency_tags: List[str],
        conflict_tags: List[str],
        branch_choice: Dict[str, Any],
        tendency_analysis: Dict[str, Any],
        is_immediate_trigger: bool = False,
    ) -> tuple[List[str], str]:
        """
        第三步：根据标签匹配筛选候选结局 🎯
        
        使用 LLM 根据倾向性标签从默认结局池中筛选候选结局。
        
        Args:
            default_endings: 默认结局池
            tendency_tags: 分支选择导致的状态变化标签（正向匹配）
            conflict_tags: 分支选择导致的冲突标签（负向匹配）
            branch_choice: 分支选择信息
            tendency_analysis: 倾向性分析结果
            is_immediate_trigger: 该路径是否可能提前结局（前面选择已导致无法合流）
            
        Returns:
            (筛选后的候选结局ID列表, 推理理由)
        """
        # 格式化默认结局池信息
        endings_info = ""
        for ending in default_endings:
            tags = ending.get("tags", [])
            conflict_tags_ending = ending.get("conflict_tags", [])
            tags_text = f" [标签: {', '.join(tags)}]" if tags else ""
            conflict_text = f" [冲突标签: {', '.join(conflict_tags_ending)}]" if conflict_tags_ending else ""
            premature_text = " [提前结局]" if ending.get("is_premature", False) else ""
            canonical_text = " [主线真结局]" if ending.get("is_canonical", False) else ""
            endings_info += f"""
- {ending.get("id")}: {ending.get("name")} - {ending.get("description", "")}{tags_text}{conflict_text}{premature_text}{canonical_text}
"""
        
        # 构建提示词 - 使用 LLM 进行匹配
        prompt = f"""你是一个结局匹配助手，负责根据分支选择的倾向性从默认结局池中筛选候选结局。

**任务**：
根据分支选择的倾向性分析，从默认结局池中筛选出可能的候选结局。

**分支选择信息**：
- 分支选择ID: {branch_choice.get('choice_id', 'unknown')}
- 分支选择描述: {branch_choice.get('description', '')}
- 分支选择理由: {branch_choice.get('reasoning', '')}...

**倾向性分析**：
- 关系变化: {tendency_analysis.get('relationship', '未知')}
- 情绪变化: {tendency_analysis.get('emotion', '未知')}
- 成长变化: {tendency_analysis.get('growth', '未知')}
- 自由变化: {tendency_analysis.get('freedom', '未知')}
- 倾向性标签: {', '.join(tendency_tags) if tendency_tags else '无'}
- 冲突标签: {', '.join(conflict_tags) if conflict_tags else '无'}
- **可能提前结局**: {"是（前面选择已导致无法合流，路径只经历到前段即结束）" if is_immediate_trigger else "否"}

**默认结局池**：
{endings_info}

**匹配规则**：
1. **负向匹配（优先级最高）**：如果分支选择的冲突标签与结局的冲突标签匹配，则必须剔除该结局
   - 示例：结局 A 有冲突标签 ["关系破裂"]，如果分支选择导致"关系破裂"，则必须剔除结局 A
   - **冲突标签的优先级高于正向标签**：即使正向标签匹配，只要冲突标签匹配，就必须剔除

2. **可能提前结局**：若倾向性分析标明「可能提前结局」，应**优先保留**标记为 [提前结局] 的结局作为候选（因该路径实际只经历前段选择即结束，不会走到后续分叉点，适合提前结局类结局）。

3. **正向匹配**：如果分支选择导致的状态变化标签与结局的标签匹配，则保留该结局
   - 示例：分支选择导致"增加领悟"，结局有标签"高领悟"，则保留
   - 如果没有明确的倾向性标签，也保留（允许后续合流）

4. **逻辑连贯性**：考虑分支选择与结局之间的逻辑连贯性
   - 如果分支选择导致的状态变化与结局描述相符，则保留
   - 如果分支选择导致的状态变化与结局描述相矛盾，则剔除

**输出格式（JSON）**：
{{
  "ending_candidates": ["ending_id1", "ending_id2", "ending_id3"],
  "reasoning": "为什么这几个结局在经历了此分支选择后依然是可能的？说明逻辑连贯性。"
}}
"""
        
        # 调用 LLM 进行匹配
        response = await self.llm_client.invoke(
            prompt,
            return_json=True,
        )
        
        # 解析响应
        if isinstance(response, str):
            response = json.loads(response)
        
        ending_candidates = response.get("ending_candidates", [])
        reasoning = response.get("reasoning", "")
        
        # 验证候选结局是否在默认结局池中
        default_ending_ids = [ending.get("id") for ending in default_endings]
        valid_candidates = [
            ending_id for ending_id in ending_candidates
            if ending_id in default_ending_ids
        ]
        
        return valid_candidates, reasoning
    
    async def determine_ending_candidates_for_all_paths(
        self,
    ) -> Dict[str, Any]:
        """
        为所有路径组合确定结局候选池 🎯
        
        生成所有可能的路径组合，为每个组合筛选结局候选池。
        
        Returns:
            包含所有路径组合及其结局候选池的字典
        """
        # 第一步：生成默认结局池（包含主线真结局）
        print("  📋 第一步：生成默认结局池...")
        try:
            default_endings = await self._generate_default_endings()
            # 确保主线真结局在池中（供全主线路径使用）
            has_canonical = any(e.get("is_canonical", False) for e in default_endings)
            if not has_canonical:
                canonical_ending = await self._generate_canonical_ending()
                if canonical_ending and canonical_ending.get("id"):
                    default_endings.append(canonical_ending)
                    print("     已补入主线真结局到默认池")
            print(f"     生成了 {len(default_endings)} 个默认结局（含主线真结局）")
        except Exception as e:
            print(f"     ❌ 生成默认结局池时出错: {e}")
            import traceback
            traceback.print_exc()
            raise RuntimeError(f"第一步失败：生成默认结局池时出错: {e}") from e
        
        # 第二步：生成所有路径组合
        print("  🔀 第二步：生成所有路径组合...")
        try:
            path_combinations = self._generate_path_combinations()
            print(f"     生成了 {len(path_combinations)} 个路径组合")
        except Exception as e:
            print(f"     ❌ 生成路径组合时出错: {e}")
            import traceback
            traceback.print_exc()
            raise RuntimeError(f"第二步失败：生成路径组合时出错: {e}") from e
        
        # 第三步：为每个路径组合筛选结局候选池（并发处理）
        print("  🎯 第三步：为每个路径组合筛选结局候选池（并发处理）...")
        
        async def process_single_path(path_combo: List[Dict[str, Any]], index: int) -> Optional[Dict[str, Any]]:
            """处理单个路径组合的辅助函数"""
            async with self.semaphore:
                try:
                    # 检查是否是主线路径（所有选择都是主线选择）
                    is_canonical_path = all(choice.get("is_canonical", False) for choice in path_combo)
                    
                    if is_canonical_path:
                        # 主线路径：必须纳入排列组合，使用主线真结局（无则用默认池第一个作兜底）
                        canonical_ending = next(
                            (ending for ending in default_endings if ending.get("is_canonical", False)),
                            None
                        )
                        if not canonical_ending and default_endings:
                            canonical_ending = default_endings[0]
                        if canonical_ending:
                            print(f"     [{index}/{len(path_combinations)}] 主线路径 → {canonical_ending.get('id')}")
                            return {
                                "path_combination": path_combo,
                                "path_id": "canonical_path",
                                "is_canonical": True,
                                "candidates": [canonical_ending.get("id")],
                                "candidate_details": [{
                                    "id": canonical_ending.get("id"),
                                    "name": canonical_ending.get("name"),
                                    "description": canonical_ending.get("description"),
                                    "tags": canonical_ending.get("tags", []),
                                    "conflict_tags": canonical_ending.get("conflict_tags", []),
                                    "is_premature": canonical_ending.get("is_premature", False),
                                    "is_canonical": canonical_ending.get("is_canonical", False),
                                }],
                                "reasoning": "这是主线路径，使用主线真结局",
                            }
                        # 仍无结局时也保留主线路径条目，避免主线从列表中消失
                        print(f"     [{index}/{len(path_combinations)}] 主线路径（无结局兜底，保留条目）")
                        return {
                            "path_combination": path_combo,
                            "path_id": "canonical_path",
                            "is_canonical": True,
                            "candidates": [],
                            "candidate_details": [],
                            "reasoning": "主线路径，默认结局池为空时保留占位",
                        }
                    
                    # 分支路径：分析累积倾向性并筛选
                    print(f"     [{index}/{len(path_combinations)}] 分析路径组合...")
                    
                    # 分析累积倾向性
                    combined_tendency = await self._analyze_combined_tendency(path_combo)
                    
                    tendency_tags = combined_tendency.get("tendency_tags", [])
                    conflict_tags = combined_tendency.get("conflict_tags", [])
                    tendency_analysis = combined_tendency.get("tendency_analysis", {})
                    
                    # 构建路径组合信息（用于筛选）
                    path_choice_info = {
                        "choice_id": f"path_{index}",
                        "description": f"路径组合: {' + '.join([c.get('branch_choice', 'unknown') for c in path_combo])}",
                        "reasoning": combined_tendency.get("reasoning", ""),
                    }
                    
                    # 筛选候选结局（若倾向性判定为可能提前结局，会优先保留 is_premature 结局）
                    valid_candidates, matching_reasoning = await self._filter_candidates_by_tags(
                        default_endings=default_endings,
                        tendency_tags=tendency_tags,
                        conflict_tags=conflict_tags,
                        branch_choice=path_choice_info,
                        tendency_analysis=tendency_analysis,
                        is_immediate_trigger=combined_tendency.get("is_immediate_trigger", False),
                    )
                    
                    # 如果没有有效的候选，至少返回一个默认结局（排除主线真结局）
                    if not valid_candidates:
                        non_canonical_endings = [
                            ending for ending in default_endings
                            if not ending.get("is_canonical", False)
                        ]
                        if non_canonical_endings:
                            valid_candidates = [non_canonical_endings[0].get("id")]
                    
                    # 生成路径ID
                    path_id = "_".join([
                        f"{c.get('fork_event_id', 'unknown')}_{c.get('branch_choice', 'unknown')}"
                        for c in path_combo
                    ])
                    
                    # 将候选结局ID转换为包含详细信息的对象
                    candidate_details = []
                    for candidate_id in valid_candidates:
                        ending_detail = next(
                            (ending for ending in default_endings if ending.get("id") == candidate_id),
                            None
                        )
                        if ending_detail:
                            candidate_details.append({
                                "id": ending_detail.get("id"),
                                "name": ending_detail.get("name"),
                                "description": ending_detail.get("description"),
                                "tags": ending_detail.get("tags", []),
                                "conflict_tags": ending_detail.get("conflict_tags", []),
                                "is_premature": ending_detail.get("is_premature", False),
                                "is_canonical": ending_detail.get("is_canonical", False),
                            })
                        else:
                            # 如果找不到详细信息，至少保留ID
                            candidate_details.append({"id": candidate_id})
                    
                    print(f"        → {len(valid_candidates)} 个候选结局: {valid_candidates}")
                    
                    return {
                        "path_combination": path_combo,
                        "path_id": path_id,
                        "is_canonical": False,
                        "candidates": valid_candidates,  # 保留ID列表（向后兼容）
                        "candidate_details": candidate_details,  # 添加详细信息
                        "tendency_analysis": tendency_analysis,
                        "tendency_tags": tendency_tags,
                        "conflict_tags": conflict_tags,
                        "deviation_level": combined_tendency.get("deviation_level", "medium"),
                        "merge_pressure": combined_tendency.get("merge_pressure", "medium"),
                        "is_immediate_trigger": combined_tendency.get("is_immediate_trigger", False),
                        "reasoning": f"{combined_tendency.get('reasoning', '')}\n\n匹配筛选：{matching_reasoning}",
                    }
                except Exception as e:
                    print(f"        ❌ 处理路径组合 {index} 时出错: {e}")
                    import traceback
                    traceback.print_exc()
                    return None
        
        # 并发处理所有路径组合
        tasks = [
            process_single_path(path_combo, i + 1)
            for i, path_combo in enumerate(path_combinations)
        ]
        path_results = await asyncio.gather(*tasks)
        
        # 过滤掉 None 结果（处理失败的路径组合）
        results = [r for r in path_results if r is not None]
        
        # 对结果进行排序：按照分叉点的顺序（E4在前，E14在后）
        # 排序规则：按照 path_combination 中第一个选择的分叉点ID排序，按数字排序
        def get_sort_key(result):
            path_combo = result.get("path_combination", [])
            if not path_combo:
                return (0, 0)
            # 获取所有分叉点ID，提取数字部分进行排序
            fork_ids = [c.get("fork_event_id", "") for c in path_combo]
            # 按照分叉点ID的数字顺序排序（E4 < E14）
            first_fork_number = self._extract_event_number(fork_ids[0] if fork_ids else "")
            return (first_fork_number, len(fork_ids))
        
        sorted_results = sorted(results, key=get_sort_key)
        
        return {
            "default_endings": default_endings,
            "path_results": sorted_results,
            "total_paths": len(path_combinations),
            "canonical_path_count": sum(1 for r in results if r.get("is_canonical", False)),
        }
    


__all__ = ["EndingCandidateDeterminer"]