"""
支线生成器 - 基于主线记录生成支线（替代路径）🌿✨

支线 = 在某个历史瞬间，玩家没有照着主线发生

功能：
1. 读取重排后的主线记录（canonical_branch_chronological.json）
2. 识别决策点（decision_point 不为 null 的事件）
3. 为每个决策点生成替代选择
4. 生成分支的状态变化（不应用，只生成）

输出格式：
{
  "branch_id": "branch_E12_choice_1",
  "fork_event_id": "E12",
  "canonical_choice": "cross_now",
  "branch_choice": "wait_until_dawn",
  "description": "王佛选择等到黎明再渡河",
  "generated_state_changes": [...],
  "is_critical_fork": true/false
}

注意：base_snapshot 不保存在分支记录中，需要时可以通过 fork_event_id 从主线记录中查找。
"""

import json
import asyncio
from typing import Dict, List, Any, Optional, Set
from pathlib import Path

from rich.progress import (
    Progress,
    SpinnerColumn,
    TextColumn,
    BarColumn,
    TaskProgressColumn,
    TimeElapsedColumn,
)

from core.llm_client import AsyncLLMClient
from state_manager.state_applier import StateApplier
from state_manager.models import UpdatedState
from ..state.state_change_generator import StateChangeGenerator
from ..narrative_perspective import get_narrative_perspective_instruction


class BranchGenerator:
    """
    支线生成器 - 基于主线记录生成支线 🌿
    
    1. **find_branch_points**: 从主线记录中找到所有分支点（被选中作为分支的决策点）
    2. **generate_alternative_choices**: 为决策点生成替代选择（LLM生成）
    3. **generate_branch**: 生成单个分支的基础记录（包含状态变化，但不应用）
    4. **generate_all_branches**: 生成所有分支点的基础记录
    
    注意：
    - 只负责生成分支的基础信息（选择、状态变化），不负责分支的后续演化
    - 分支的后续演化（分支事件链、合流、结局）由 BranchEventGenerator 处理
    """
    
    def __init__(
        self,
        canonical_branch_path: str = "out/canonical_branch_chronological.json",
        llm_client: Optional[AsyncLLMClient] = None,
        state_applier: Optional[StateApplier] = None,
        decision_analysis_path: str = "out/decision_points_analysis.json",
        # 控制 LLM 调用并发；提示词已要求只生成 N 条，
        # 这里把默认并发提升到 10 以便更快生成（但网络抖动时错误概率也会上升）。
        max_concurrent: int = 10,
        narrative_perspective: str = "第三人称",
    ):
        """
        初始化支线生成器
        
        Args:
            canonical_branch_path: 主线记录文件路径（默认已重排的 chronological）
            llm_client: LLM 客户端（用于生成替代选择）
            state_applier: 状态应用器（用于获取事件信息）
            decision_analysis_path: 决策点分析文件路径
            max_concurrent: 最大并发数（用于控制 LLM 调用的并发）
            narrative_perspective: 叙述人称（与 generate-branch 阶段推断的 content_context 一致），用于 description/reasoning 的叙述约束
        """
        self.canonical_branch_path = Path(canonical_branch_path)
        self.narrative_perspective = narrative_perspective or "第三人称"
        self.llm_client = llm_client or AsyncLLMClient.create_default()
        self.state_applier = state_applier or StateApplier()
        self.decision_analysis_path = Path(decision_analysis_path)
        self.decision_analysis: Optional[Dict[str, Any]] = None
        self.branch_points_whitelist: Optional[Set[str]] = None
        self.critical_events: Optional[Set[str]] = None  # 关键事件（最小骨架）
        self.skip_points: Optional[Set[str]] = None
        self.max_concurrent = max_concurrent
        # 控制 LLM 调用的并发，避免打爆接口
        self.semaphore = asyncio.Semaphore(max_concurrent)
        
        # 创建状态变化生成器（统一的状态变化生成逻辑）
        self.state_change_generator = StateChangeGenerator(
            llm_client=self.llm_client,
            state_applier=self.state_applier,
            canonical_branch_path=str(self.canonical_branch_path),
            decision_analysis_path=str(self.decision_analysis_path),
        )
        
        # 加载主线记录
        self.canonical_branch = self._load_canonical_branch()
        # 加载决策点分析结果（如果存在）
        self._load_decision_analysis()
    
    def _load_canonical_branch(self) -> Dict[str, Any]:
        """加载主线记录"""
        if not self.canonical_branch_path.exists():
            raise FileNotFoundError(f"主线记录文件不存在: {self.canonical_branch_path}")
        
        with open(self.canonical_branch_path, "r", encoding="utf-8") as f:
            return json.load(f)
    
    def _load_decision_analysis(self) -> None:
        """
        加载决策点分析结果 📊（必需）

        结果来自 DecisionPointAnalyzer，包含：
        - critical_events: 关键事件列表（最小骨架）
        - branch_points: 适合做分支的事件ID
        - skip_points: 不适合做分支的事件ID
        """
        if not self.decision_analysis_path.exists():
            raise FileNotFoundError(
                f"未找到决策点分析结果文件: {self.decision_analysis_path}，"
                f"请先运行 `python -m src.cli analyze-decision-points` 生成该文件。"
            )

        with open(self.decision_analysis_path, "r", encoding="utf-8") as f:
            self.decision_analysis = json.load(f)

        critical_events = self.decision_analysis.get("critical_events", [])
        branch_points = self.decision_analysis.get("branch_points", [])
        skip_points = self.decision_analysis.get("skip_points", [])

        self.critical_events = set(critical_events)
        self.branch_points_whitelist = set(branch_points)
        self.skip_points = set(skip_points)

        print(
            f"已加载决策点分析结果："
            f"关键事件（最小骨架）{len(critical_events)} 个，分支点 {len(branch_points)} 个，跳过点 {len(skip_points)} 个"
        )
    
    def find_branch_points(self) -> List[Dict[str, Any]]:
        """
        找到所有分支点（被选中作为分支的决策点）🎯
        
        从重排后的主线中找到所有决策点（decision_point 不为 null），
        然后过滤出 decision_points_analysis.json 中 branch_points 里的事件。
        
        注意：
        - 主线中的 decision_point = 所有有选择的事件（决策点）
        - branch_points = 被选中作为分支的决策点（分支点）
        
        Returns:
            分支点列表，每个元素包含事件信息和所有状态变化
        """
        if not self.branch_points_whitelist:
            print("⚠️  警告：没有分支点白名单，无法生成分支。请先运行 `python -m src.cli analyze-decision-points`")
            return []
        
        decision_points = []
        
        # 只处理 branch_points 中的事件
        for event_record in self.canonical_branch.get("processed_events", []):
            event_id = event_record.get("event_id")
            if event_id is None:
                continue
            
            # 只保留被标记为 branch_points 的事件
            if event_id in self.branch_points_whitelist:
                # 确保是决策点（有 decision_point）
                if event_record.get("decision_point") is not None:
                    decision_points.append(event_record)
        
        return decision_points
    
    async def generate_alternative_choices(
        self,
        event_record: Dict[str, Any],
        max_branches_per_point: int,
    ) -> List[Dict[str, Any]]:
        """
        为决策点生成替代选择 🤔
        
        使用 LLM 分析决策点，生成合理的替代选择。
        重要：支线选择只影响人物状态、情绪、关系等，不影响主线关键事件。
        
        Args:
            event_record: 主线事件记录（包含 decision_point 和 canonical_choice）
            
        Returns:
            替代选择列表，每个选择包含：
            - choice_id: 选择ID
            - description: 选择描述
            - reasoning: 选择理由
        """
        event_id = event_record.get("event_id")
        decision_point = event_record.get("decision_point")
        canonical_choice = event_record.get("canonical_choice")
        description = event_record.get("description", "")
        snapshot = event_record.get("snapshot")
        
        # 构建提示词
        prompt = self._build_choice_generation_prompt(
            event_id=event_id,
            decision_point=decision_point,
            canonical_choice=canonical_choice,
            description=description,
            snapshot=snapshot,
            max_branches_per_point=max_branches_per_point,
        )
        
        # 调用 LLM 生成替代选择
        response = await self.llm_client.invoke(
            prompt,
            return_json=True,
        )
        
        # 解析响应
        if isinstance(response, str):
            response = json.loads(response)
        
        alternatives = response.get("alternative_choices", [])
        
        # 确保每个选择都有必要的字段
        for alt in alternatives:
            if "choice_id" not in alt:
                alt["choice_id"] = f"choice_{len(alternatives)}"
            if "reasoning" not in alt:
                alt["reasoning"] = ""
        
        # 为了保证后续状态变化生成能稳定对应选择，
        # 这里也做一个“尽量不多不少”的轻校正：只裁剪多出来的部分。
        # 若数量过少，直接使用现有 choices（避免再额外调用 LLM）。
        if len(alternatives) > max_branches_per_point:
            alternatives = alternatives[:max_branches_per_point]

        return alternatives
    
    def _build_choice_generation_prompt(
        self,
        event_id: str,
        decision_point: str,
        canonical_choice: str,
        description: str,
        snapshot: Optional[Dict[str, Any]],
        max_branches_per_point: int,
    ) -> str:
        """
        构建生成替代选择的提示词 📝
        
        Args:
            event_id: 事件ID
            decision_point: 决策点标识
            canonical_choice: 主线选择
            description: 事件描述
            snapshot: 当前状态快照
            
        Returns:
            提示词字符串
        """
        prompt = f"""你是一个支线生成助手，负责为主线的决策点生成**戏剧性、极端、可玩性强**的替代选择。

**核心目标：让玩家感受到"选择真的有意义"！**

**重要理念**：
- **分支的合流方式**：分支路径会通过合流点回到主线，合流点有四种可能：
  1. 下一个主线事件（立即合流）
  2. 跳过几个事件后的下一个主线事件（延迟合流）
  3. 下一个关键事件（跳转到关键节点）
  4. 无法合流，提前进入结局（分支偏离太远）
  合流点的选择由系统根据分支状态动态决定
- **支线选择只影响人物状态、情绪、关系等，不影响主线关键事件**
- **支线选择会导致不同的状态变化**：每个选择会产生不同的状态变化，而不是从主线状态变化中选择

主线事件信息：
- 事件ID: {event_id}
- 事件描述: {description}
- 决策点: {decision_point}
- 主线选择: {canonical_choice}

**行为主体（必守）**：
- 从上述**事件描述**中识别：本决策点**是谁在做选择**（谁的行为会导致状态变化）。例如：若描述为「皇帝向老画家发问」「皇帝指控画家」，则行为主体是**皇帝**；若描述为「林面对王佛的教导」「林拒绝或接受」，则行为主体是**林**；若涉及王佛在朝堂的应对，则行为主体是**王佛**。
- 替代选择的 description 与 reasoning **必须且仅描写该行为主体的替代行为**，不得把其他场景的人物写成本事件的行为人（例如：若本事件是皇帝与老画家的场景，则不能写「林突然暴怒」；若本事件是林与王佛的场景，则不能写「皇帝如何如何」）。

任务：
1. 分析这个决策点，理解主线选择（{canonical_choice}）的含义和**明确后果**，并确认**本事件的行为主体**是谁
2. 生成 **{max_branches_per_point} 个****极端、戏剧性、可玩性强**的替代选择，这些选择应该：
   - 在逻辑上可行（符合当前状态和事件背景）
   - **必须承接同一主线事件语境**：先基于「事件描述」交代该事件正在发生的场景/冲突，再给出不同选择；不得跳过事件过程直接给结论
   - **允许复用主线事件的原始叙事信息**（人物、场景、冲突）作为描述锚点，只改变“这个节点上的选择动作/态度/回应”
   - **必须与主线选择形成极端对比**：
     * 如果主线选择是"完全信任/开放/投入"，分支选择应该是"完全拒绝/封闭/抽离"
     * 如果主线选择是"积极行动/勇敢面对"，分支选择应该是"消极逃避/恐惧退缩"
     * 如果主线选择是"情感投入/建立连接"，分支选择应该是"情感抽离/切断连接"
   - **必须产生极端不同的状态变化**：
     * 主线选择导致关系大幅提升、信任增加、勇气增强
     * 分支选择应该导致关系大幅下降、警惕增加、恐惧增强（极端相反）
   - **必须说明戏剧性后果**：在 reasoning 中明确说明这个选择会导致什么**极端、可感知、影响深远**的状态变化和后续影响
   - **必须让玩家感受到"选错了会很糟糕"或"选对了会很棒"**：选择应该有明显的风险或收益
   - **重要**：支线选择只影响人物状态、情绪、关系等，不影响主线关键事件
   - **重要**：支线选择会改变主线剧情的表现形式，但不会改变主线的大方向
   - **重要**：分支路径最终会通过合流点回到主线，或如果无法合流则提前进入结局（合流点由系统根据分支状态动态选择）

**极端差异要求（最重要）**：
- **状态变化差异要极端**：
  * 如果主线选择导致关系大幅提升，分支选择应该导致关系大幅下降
  * 如果主线选择导致信任增加，分支选择应该导致警惕或怀疑大幅增加
  * 如果主线选择导致勇气增强，分支选择应该导致恐惧或退缩大幅增加
- **维度必须完全不同**：
  * 主线"勇气" → 分支必须是"恐惧"、"焦虑"、"退缩"（不能是"谨慎"，要更极端）
  * 主线"信任" → 分支必须是"怀疑"、"警惕"、"敌意"（不能是"谨慎"，要更极端）
- **后果必须极端可感知**：
  * 玩家应该能清楚地感受到"我选了这个，所以现在关系彻底破裂了"或"我选了这个，所以现在完全信任了"
  * 状态变化应该影响后续事件中人物的行为、情绪、关系互动等，让玩家感受到选择的真实影响
- **同一决策点下的多个替代选择之间也要区分**：
  * 每个替代选择应导向**不同的结局走向类型**（例如：一种偏「认知固化、自我封闭」，一种偏「精神崩溃、存在焦虑」，一种偏「关系决裂但冷静抽离」等），避免所有分支路径在结局选择时都收敛到同一结局
  * 在 reasoning 中可简要点明该选择更可能导向哪类结局走向（如「易导致后续走向认知固化/精神崩溃/关系决裂」），以便不同路径最终能对应不同结局
- **增加戏剧性冲突**：
  * 如果主线选择是"和解"，分支选择应该是"彻底决裂"
  * 如果主线选择是"接受教导"，分支选择应该是"拒绝并质疑"
  * 如果主线选择是"勇敢面对"，分支选择应该是"恐惧逃避"

**极端差异示例**：
- 主线"完全接受教导" → 后果：关系大幅提升、信任增加、世界观转变、勇气增强
- 分支"拒绝并质疑" → 后果：关系大幅下降、警惕大幅增加、世界观固化、恐惧增加（极端相反！）
- 主线"立即行动" → 后果：勇气增强、信任增加、关系提升、行动效率提高
- 分支"恐惧退缩" → 后果：恐惧大幅增加、怀疑增加、关系下降、行动效率降低（极端相反！）
- 主线"信任他人" → 后果：信任大幅增加、关系提升、开放度增加
- 分支"彻底封闭" → 后果：警惕大幅增加、独立性增强、关系下降、封闭度增加（极端相反！）

输出格式（JSON）：
{{
  "alternative_choices": [
    {{
      "choice_id": "wait_until_dawn",
      "description": "选择等到黎明再渡河",
      "reasoning": "等待更安全的时机，但可能错过重要机会。这个选择会导致人物更加谨慎，但可能影响后续的节奏。"
    }},
    {{
      "choice_id": "find_another_way",
      "description": "寻找其他路径",
      "reasoning": "避免直接面对危险，但可能绕远路。这个选择会增强人物的探索精神，但可能消耗更多时间。"
    }}
  ]
}}

**叙述人称**（必须遵守）：
- {get_narrative_perspective_instruction(self.narrative_perspective)}
- description 与 reasoning 的表述均须符合上述人称；若为第三人称则不要用「你」指代角色。

注意：
- **行为主体**：description 与 reasoning 中的动作必须由「事件描述里的行为人」做出，不要混入其他角色（如事件是皇帝与老画家则只写皇帝/老画家，是林与王佛则只写林/王佛）
- **叙事连续性（强约束）**：
  1) 每个替代选择的 description 必须显式对应当前主线事件（同人物、同场景、同冲突锚点）
  2) 不能写成“事件已经发生完毕后的总结句”，而要写“该事件当下的不同选择”
  3) 禁止“跳过事件过程直接做决定”的写法（例如直接写“他最终走向XX结局/从此如何如何”）
  4) 推荐句式：先一句承接事件现场，再一句给出分支选择动作（保持 1-2 句）
- **因果链（强约束）**：
  * reasoning 必须包含「触发原因 -> 角色动机 -> 行为后果」三段逻辑
  * 若涉及资源/利益行为（如变卖家产、借贷、夺权等），必须说明“为谁/为何需要”与“为何在此刻发生”
  * 禁止只给“行为结论”而不给“触发原因”
- 不需要指定状态变化索引，状态变化会根据选择动态生成
- 每个选择应该有不同的逻辑后果和状态影响
- 确保选择的逻辑自洽（符合前提条件）
- **必须生成恰好 {max_branches_per_point} 个替代选择**（如果 {max_branches_per_point}=1，也必须只生成 1 个）
"""
        return prompt
    
    
    async def generate_branch(
        self,
        fork_event_id: str,
        branch_choice: Dict[str, Any],
    ) -> Dict[str, Any]:
        """
        生成单个支线 🌿
        
        重要：分支选择应该生成新的状态变化，而不是从主线事件的状态变化中选择。
        不同的选择会导致不同的状态变化。
        
        Args:
            fork_event_id: 分叉事件ID
            branch_choice: 支线选择（包含 choice_id, description, reasoning）
            
        Returns:
            支线记录字典，包含：
            - branch_id: 分支ID
            - fork_event_id: 分叉事件ID
            - canonical_choice: 主线选择
            - branch_choice: 分支选择ID
            - description: 分支选择描述
            - reasoning: 分支选择理由
            - generated_state_changes: 生成的状态变化（未应用）
            - is_critical_fork: 是否是关键分叉点
        """
        # 找到主线事件记录（用于获取事件信息）
        event_record = None
        for record in self.canonical_branch.get("processed_events", []):
            if record.get("event_id") == fork_event_id:
                event_record = record
                break
        
        if event_record is None:
            raise ValueError(f"找不到事件 {fork_event_id} 的主线记录")
        
        # 获取基础快照（分叉点之前的状态）
        base_snapshot = self._get_base_snapshot(fork_event_id)
        
        # 获取事件信息
        event_info = await self.state_applier.get_event_info(fork_event_id)
        if event_info is None:
            raise ValueError(f"事件 {fork_event_id} 不存在")
        
        # 获取原事件的所有可能状态变化（从 Neo4j 获取，用于参考）
        original_state_changes = await self.state_applier.get_event_state_changes(fork_event_id)
        
        # 获取主线选择的状态变化（从 event_record 中提取）
        canonical_selected_state_changes = []
        if event_record.get("selected_state_changes"):
            # selected_state_changes 是索引列表，需要从 state_changes 中提取
            all_state_changes = event_record.get("state_changes", [])
            selected_indices = event_record.get("selected_state_changes", [])
            for idx in selected_indices:
                if 0 <= idx < len(all_state_changes):
                    # 移除元数据字段（以 _ 开头的字段），只保留标准 StateChange 格式
                    sc = all_state_changes[idx].copy()
                    canonical_selected_state_changes.append({
                        k: v for k, v in sc.items() if not k.startswith("_")
                    })
        
        # **关键**：根据分支选择生成新的状态变化，参考原事件和主线选择的状态变化
        # 不同的选择会导致不同的状态变化
        # 注意：这里只生成状态变化，不应用（apply），应用会在后续的支线路径处理中进行
        # 使用统一的状态变化生成器
        generated_state_changes = await self.state_change_generator.generate_for_branch_choice(
            fork_event_id=fork_event_id,
            event_info=event_info,
            branch_choice=branch_choice,
            base_snapshot=base_snapshot,
            original_state_changes=original_state_changes,
            canonical_selected_state_changes=canonical_selected_state_changes,
            canonical_choice=event_record.get("canonical_choice"),
        )
        
        # 构建支线记录
        # 注意：这里只保存生成的状态变化，不应用，所以没有 updated_states 和 snapshot
        # 注意：不保存 base_snapshot，需要时可以通过 fork_event_id 从主线记录中查找
        branch_record = {
            "branch_id": f"branch_{fork_event_id}_{branch_choice.get('choice_id', 'unknown')}",
            "fork_event_id": fork_event_id,
            "canonical_choice": event_record.get("canonical_choice"),
            "branch_choice": branch_choice.get("choice_id"),
            "description": branch_choice.get("description", ""),
            "reasoning": branch_choice.get("reasoning", ""),
            "generated_state_changes": generated_state_changes,  # 生成的状态变化（未应用）
            "is_critical_fork": fork_event_id in self.critical_events if self.critical_events else False,
        }
        
        return branch_record
    
    def _get_base_snapshot(self, fork_event_id: str) -> Optional[Dict[str, Any]]:
        """
        获取分叉点之前的基础状态快照 📸
        
        找到分叉事件之前最后一个事件的状态快照。
        
        Args:
            fork_event_id: 分叉事件ID
            
        Returns:
            基础状态快照字典，如果找不到则返回 None
        """
        # 找到分叉事件在主线记录中的位置
        fork_index = None
        for idx, record in enumerate(self.canonical_branch.get("processed_events", [])):
            if record.get("event_id") == fork_event_id:
                fork_index = idx
                break
        
        if fork_index is None or fork_index == 0:
            # 如果是第一个事件，返回 None（使用基线状态）
            return None
        
        # 返回前一个事件的状态快照
        prev_record = self.canonical_branch["processed_events"][fork_index - 1]
        return prev_record.get("snapshot")
    
    async def generate_all_branches(
        self,
        fork_event_id: Optional[str] = None,
        max_branches_per_point: int = 2,
    ) -> List[Dict[str, Any]]:
        """
        生成所有支线（或指定事件的支线）🌿✨
        
        控制分支广度：
        - 每个分支点最多生成 max_branches_per_point 条支线
        
        Args:
            fork_event_id: 如果指定，只生成该事件的支线；否则生成所有决策点的支线
            max_branches_per_point: 每个决策点最多生成多少条支线（控制广度，默认：2）
            
        Returns:
            支线记录列表
        """
        # 找到决策点
        if fork_event_id:
            # 只处理指定事件，但需要检查是否在 branch_points 白名单中
            if not self.branch_points_whitelist:
                print("⚠️  警告：没有分支点白名单，无法生成分支。请先运行 `python -m src.cli analyze-decision-points`")
                return []
            
            if fork_event_id not in self.branch_points_whitelist:
                print(f"⚠️  警告：事件 {fork_event_id} 不在分支点白名单中（不在 decision_points_analysis.json 的 branch_points 中）")
                print(f"   当前分支点白名单: {sorted(self.branch_points_whitelist)}")
                return []
            
            # 只处理指定事件（确保在白名单中且是决策点）
            decision_points = [
                record for record in self.canonical_branch.get("processed_events", [])
                if record.get("event_id") == fork_event_id
                and record.get("decision_point") is not None
            ]
        else:
            # 处理所有分支点（使用 find_branch_points，它已经过滤了白名单）
            decision_points = self.find_branch_points()
        
        async def process_single_branch_point(
            event_record: Dict[str, Any],
            event_index: int,
        ) -> tuple[int, List[Dict[str, Any]]]:
            """
            处理单个分支点：
            1. 生成替代选择
            2. 为每个替代选择生成支线
            使用信号量控制并发数。
            
            Args:
                event_record: 事件记录
                event_index: 事件在决策点列表中的索引（用于排序）
            
            Returns:
                (event_index, 该分支点生成的所有支线列表) 元组
            """
            event_id = event_record.get("event_id")
            if not event_id:
                return (event_index, [])
            
            branches_for_this_point: List[Dict[str, Any]] = []
            
            print(f"🌿 为分支点 {event_id} 生成支线...")
            
            # 生成替代选择（在信号量保护下调用 LLM）
            async with self.semaphore:
                alternatives = await self.generate_alternative_choices(
                    event_record,
                    max_branches_per_point=max_branches_per_point,
                )

            # 安全兜底：理论上 prompt 已要求只生成恰好 N 个，
            # 这里仅在模型不守约时做轻量校正，避免后续状态变化生成爆炸。
            if len(alternatives) > max_branches_per_point:
                alternatives = alternatives[:max_branches_per_point]
            
            # 为每个替代选择生成支线（并发生成）
            async def generate_single_branch(
                event_id: str,
                alt_choice: Dict[str, Any],
            ) -> Optional[Dict[str, Any]]:
                """在信号量保护下生成单个支线（嵌套函数）"""
                async with self.semaphore:
                    try:
                        branch = await self.generate_branch(event_id, alt_choice)
                        print(f"  ✅ 生成支线: {branch['branch_id']}")
                        return branch
                    except Exception as e:
                            print(f"  ❌ 生成支线失败 ({event_id}): {e}")
                            return None
            
            # 创建所有支线生成任务
            tasks = [
                generate_single_branch(event_id, alt_choice)
                for alt_choice in alternatives
            ]
            
            # 并发执行所有支线生成任务
            results = await asyncio.gather(*tasks, return_exceptions=True)
            
            # 处理结果
            for result in results:
                if isinstance(result, Exception):
                    print(f"  ❌ 生成支线失败 ({event_id}): {result}")
                elif result is not None:
                    branches_for_this_point.append(result)
            
            return (event_index, branches_for_this_point)
        
        # 使用进度条并发处理所有分支点（最后按事件顺序排序）
        if not decision_points:
            return []
        
        with Progress(
            SpinnerColumn(),
            TextColumn("[progress.description]{task.description}"),
            BarColumn(),
            TaskProgressColumn(),
            TimeElapsedColumn(),
        ) as progress:
            task_id = progress.add_task("生成支线...", total=len(decision_points))
            
            # 并发处理所有分支点
            tasks = [
                process_single_branch_point(event_record, idx)
                for idx, event_record in enumerate(decision_points)
            ]
            
            # 等待所有任务完成，并更新进度
            results_with_index = []
            for coro in asyncio.as_completed(tasks):
                result = await coro
                results_with_index.append(result)
                progress.update(task_id, advance=1)
        
        # 按事件索引排序，然后合并所有支线
        results_with_index.sort(key=lambda x: x[0])  # 按 event_index 排序
        all_branches = []
        for _, branches in results_with_index:
            all_branches.extend(branches)
        
        return all_branches
    
    def save_branches(self, branches: List[Dict[str, Any]], output_path: str = "out/branches.json"):
        """
        保存支线记录到文件 💾
        
        Args:
            branches: 支线记录列表
            output_path: 输出文件路径
        """
        output_file = Path(output_path)
        output_file.parent.mkdir(parents=True, exist_ok=True)
        
        with open(output_file, "w", encoding="utf-8") as f:
            json.dump(branches, f, ensure_ascii=False, indent=2)
        
        print(f"💾 支线记录已保存到: {output_path}")


__all__ = ["BranchGenerator"]
