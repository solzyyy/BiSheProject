"""
路径生成函数 - 供 LLM 通过 function calling 调用的函数集 🛠️✨

职责：
1. 定义 function calling 的函数 schema
2. 实现所有可调用的函数（包含完整的事件生成逻辑）
3. 管理路径状态（branch_events, current_states 等）
4. 使用 LangGraph 处理 function calling 的循环逻辑

"""

import asyncio
import json
import logging
from typing import Dict, List, Any, Optional, Tuple
from pathlib import Path

# 配置日志记录器 📝
logger = logging.getLogger(__name__)
if not logger.handlers:
    handler = logging.StreamHandler()
    formatter = logging.Formatter(
        '%(asctime)s - %(name)s - %(levelname)s - %(message)s',
        datefmt='%Y-%m-%d %H:%M:%S'
    )
    handler.setFormatter(formatter)
    logger.addHandler(handler)
    logger.setLevel(logging.INFO)

from core.llm_client import AsyncLLMClient
from state_manager.models import UpdatedState
from state_manager.state_applier import StateApplier
from ..state.state_change_generator import StateChangeGenerator
from ..utils.path_data_loader import PathDataLoader
from ..utils.path_event_extractor import PathEventExtractor
from ..utils.path_formatter import StoryContentFormatter
from ..narrative_perspective import get_narrative_perspective_instruction as _get_narrative_perspective_instruction

# 占位符：表示该前缀正在由某 worker 跑 LangGraph，其他 worker 应等待 Event
_EARLY_ENDING_CACHE_IN_PROGRESS = object()

# 文风系统 prompt（供叙事生成 LLM 使用，去 AI 味与油腻感）
_STORY_SYSTEM_PROMPT_CACHE: Optional[str] = None

def _get_story_system_prompt() -> str:
    """加载并缓存 prompts/story_generation_system_prompt.txt，用于叙事生成时约束文风。"""
    global _STORY_SYSTEM_PROMPT_CACHE
    if _STORY_SYSTEM_PROMPT_CACHE is not None:
        return _STORY_SYSTEM_PROMPT_CACHE
    try:
        # 项目根目录下的 prompts/
        project_root = Path(__file__).resolve().parent.parent.parent.parent
        path = project_root / "prompts" / "story_generation_system_prompt.txt"
        if path.exists():
            _STORY_SYSTEM_PROMPT_CACHE = path.read_text(encoding="utf-8").strip()
        else:
            _STORY_SYSTEM_PROMPT_CACHE = ""
    except Exception as e:
        logger.warning(f"⚠️  无法加载故事系统 prompt: {e}")
        _STORY_SYSTEM_PROMPT_CACHE = ""
    return _STORY_SYSTEM_PROMPT_CACHE

# LangGraph 支持
try:
    # 注意：LangGraph 相关的类在 langgraph/ 子模块中使用，这里只需要检查是否可用
    import langgraph
    LANGGRAPH_AVAILABLE = True
except ImportError:
    LANGGRAPH_AVAILABLE = False
    # 注意：Windows 控制台编码可能不是 UTF-8，避免打印 emoji 导致 UnicodeEncodeError
    logger.warning("LangGraph 未安装，请运行: pip install langgraph>=0.2.0")


class PathGenerationFunctions:
    """
    路径生成函数集合 🛠️
    
    职责：
    - 定义和实现 function calling 的所有函数
    - 包含完整的事件生成逻辑
    - 管理路径生成的状态
    """
    
    def __init__(
        self,
        llm_client: Optional[AsyncLLMClient] = None,
        flow_llm_client: Optional[AsyncLLMClient] = None,
        state_applier: Optional[StateApplier] = None,
        state_change_generator: Optional[StateChangeGenerator] = None,
        canonical_branch: Optional[Dict[str, Any]] = None,
        critical_events: Optional[set] = None,
        decision_analysis_path: str = "out/decision_points_analysis.json",
        ending_candidates_path: str = "out/ending_candidates.json",
        narrative_perspective: Optional[str] = None,
    ):
        """
        初始化路径生成函数集合
        
        Args:
            llm_client: LLM 客户端（用于执行函数，如生成事件、选择结局等）
            flow_llm_client: 流程控制 LLM 客户端（用于 function calling 流程编排，如果为 None 则使用 llm_client）
            state_applier: 状态应用器
            state_change_generator: 状态变化生成器
            canonical_branch: 主线记录（由调用方加载后传入；CLI 默认使用已重排的 canonical_branch_chronological.json）
            critical_events: 关键事件集合
            decision_analysis_path: 决策点分析文件路径
            ending_candidates_path: 结局候选文件路径
            narrative_perspective: 叙述人称（第一/二/三人称），在生成分支事件之前由 CLI 推断并传入，用于分支事件描述的叙述约束
        """
        # 执行 LLM：用于生成事件、选择结局等具体任务
        self.llm_client = llm_client or AsyncLLMClient.create_default()
        # 流程控制 LLM：用于 function calling 流程编排（避免上下文混乱）
        # 如果未提供，创建一个独立的实例（与执行 LLM 分离）
        if flow_llm_client is None:
            self.flow_llm_client = AsyncLLMClient.create_default()
        else:
            self.flow_llm_client = flow_llm_client
        
        self.state_applier = state_applier or StateApplier()
        self.state_change_generator = state_change_generator
        if not self.state_change_generator:
            # 如果没有提供，创建一个默认的
            from ..state.state_change_generator import StateChangeGenerator
            self.state_change_generator = StateChangeGenerator(
                llm_client=self.llm_client,  # 使用执行 LLM
                state_applier=self.state_applier,
            )
        
        self.canonical_branch = canonical_branch or {}
        # 主线顺序：由调用方保证；CLI generate-all-paths 默认传入 canonical_branch_chronological.json（重排后）
        self.decision_analysis_path = Path(decision_analysis_path)
        self.ending_candidates_path = Path(ending_candidates_path)

        # 路径对齐：与当前 run 的 ending_candidates 同目录读取辅助文件，
        # 避免误读项目根 out/ 下旧数据导致 scene_description/source_text 错位。
        data_dir = self.ending_candidates_path.parent if self.ending_candidates_path.parent != Path("") else Path("out")
        branches_path = data_dir / "branches.json"
        extract_chain_path = data_dir / "extract_chain.json"
        
        # 初始化工具类（统一管理数据加载、事件提取、格式化）✨
        self.data_loader = PathDataLoader(
            ending_candidates_path=ending_candidates_path,
            branches_path=str(branches_path),
            decision_analysis_path=decision_analysis_path,
            extract_chain_path=str(extract_chain_path),
        )
        self.event_extractor = PathEventExtractor(data_loader=self.data_loader)
        self.formatter = StoryContentFormatter(event_extractor=self.event_extractor)
        
        # 为了向后兼容，保留这些属性（从 data_loader 中获取）
        self.ending_candidates = self.data_loader.ending_candidates
        self.branches_data = self.data_loader.branches_data
        self.decision_analysis = self.data_loader.decision_analysis
        self.extract_chain_events = self.data_loader.extract_chain_events
        
        # 🔧 关键修复：如果没有传入 critical_events，从 decision_analysis 中加载
        if critical_events is not None:
            self.critical_events = critical_events
        else:
            # 从 decision_points_analysis.json 中加载关键事件
            if self.decision_analysis:
                critical_events_list = self.decision_analysis.get("critical_events", [])
                self.critical_events = set(critical_events_list) if critical_events_list else set()
                if self.critical_events:
                    print(f"  ✅ 已从 decision_points_analysis.json 加载 {len(self.critical_events)} 个关键事件: {sorted(self.critical_events)}")
            else:
                self.critical_events = set()
                print(f"  ⚠️  未找到 decision_points_analysis.json，critical_events 为空")
        
        # 路径状态（由这些函数管理）
        self.branch_events: List[Dict[str, Any]] = []
        self.current_states: Optional[UpdatedState] = None
        self.merge_point: Optional[str] = None
        self.is_early_ending: bool = False
        self.ending_event: Optional[Dict[str, Any]] = None
        self.skipped_mainline_events: List[str] = []
        
        # 当前路径的上下文（在开始生成路径时设置）
        self.fork_event_id: Optional[str] = None
        self.branch_choice: Optional[Dict[str, Any]] = None
        self.merge_options: List[str] = []
        self.path_combination: List[Dict[str, Any]] = []  # 用于匹配 ending_candidates
        # 叙述人称（在生成分支事件之前由 CLI 推断），用于分支事件/结局描述的叙述约束
        self.narrative_perspective: str = narrative_perspective if narrative_perspective else "第三人称"
        # 提前结局路径缓存：key=「当前步之前」的已选分支前缀（不含当前选择），value=该前缀跑完后的 state + branch_events
        # 这样「前面大部分重复」的路径可复用已生成内容，只跑当前不同的一步；存时用 prefix_including
        self._early_ending_path_cache: Dict[Tuple[Tuple[str, str], ...], Dict[str, Any]] = {}
        # 按「当前步」(fork_event_id, branch_choice_id) 的提前结局缓存：任一路径跑过该步并得到提前结局后，其它路径同一步直接复用，不重复生成分支事件、不重复保存
        self._early_ending_by_step: Dict[Tuple[str, str], Dict[str, Any]] = {}
        # 并发时用：按 prefix_including 加锁，同一「前缀+当前选择」只跑一次；不同则并行。
        self._early_ending_cache_meta_lock: Optional[asyncio.Lock] = None
        self._early_ending_prefix_locks: Optional[Dict[Tuple[Tuple[str, str], ...], asyncio.Lock]] = None
        self._early_ending_in_progress_events: Optional[Dict[Tuple[Tuple[str, str], ...], asyncio.Event]] = None
    
    async def _get_prefix_lock(self, path_prefix: Tuple[Tuple[str, str], ...]) -> Optional[asyncio.Lock]:
        """并发时返回该前缀的锁。锁的是「含当前步」的 prefix_including：同一 prefix_including 只跑一次并写缓存，不同则并行。"""
        if self._early_ending_cache_meta_lock is None or self._early_ending_prefix_locks is None:
            return None
        async with self._early_ending_cache_meta_lock:
            if path_prefix not in self._early_ending_prefix_locks:
                self._early_ending_prefix_locks[path_prefix] = asyncio.Lock()
            return self._early_ending_prefix_locks[path_prefix]
    
    @staticmethod
    def _branch_choice_prefix(choices: List[Dict[str, Any]], up_to_index: int) -> Tuple[Tuple[str, str], ...]:
        """分支选择序列，取 choices[0:up_to_index] 中的分支选择。up_to_index 为右开界。查缓存用「当前步之前」= prefix(..., idx-1)；存缓存用「含当前步」= prefix(..., idx)。"""
        return tuple(
            (choices[i]["fork_event_id"], choices[i]["branch_choice"])
            for i in range(up_to_index)
            if not choices[i].get("is_canonical", False)
        )
    
    @staticmethod
    def _branch_choice_prefix_from_list(combination: List[Dict[str, Any]]) -> Tuple[Tuple[str, str], ...]:
        """从 choice 列表得到「仅分支选择」的前缀元组。"""
        return tuple(
            (c["fork_event_id"], c["branch_choice"])
            for c in combination
            if not c.get("is_canonical", False)
        )
    
    def _find_sibling_early_ending_cache(
        self,
        prefix_before: Tuple[Tuple[str, str], ...],
        prefix_including: Tuple[Tuple[str, str], ...],
    ) -> Optional[Dict[str, Any]]:
        """
        同分支点、已存在提前结局时复用：若缓存中已有「同一 prefix_before + 任一选择」的提前结局，
        则直接复用该结果，避免同一分支点下多选都演化为同一结局时重复演化和重复保存。
        调用方应在持锁或单线程下调用以保证缓存一致性。
        """
        n = len(prefix_including)
        if n != len(prefix_before) + 1:
            return None
        for key, val in self._early_ending_path_cache.items():
            if val is _EARLY_ENDING_CACHE_IN_PROGRESS or not isinstance(val, dict):
                continue
            if not val.get("is_early_ending"):
                continue
            if len(key) != n or key[:-1] != prefix_before:
                continue
            return dict(val)
        return None
    
    # ========== 辅助方法：代码复用 ⚡ ==========
    
    def _format_states_for_llm(self, states: Optional[UpdatedState]) -> str:
        """
        格式化状态为 LLM 可读的文本格式 📝
        
        Args:
            states: 状态对象（可以是 UpdatedState 对象或字典）
            
        Returns:
            格式化后的状态文本
        """
        if not states:
            return ""
        
        # 🔧 修复：处理 states 可能是字典的情况（LangGraph 状态同步后可能是字典）
        if isinstance(states, dict):
            states_dict = states
        elif hasattr(states, "model_dump"):
            states_dict = states.model_dump()
        elif hasattr(states, "dict"):
            states_dict = states.dict()
        else:
            # 如果都不是，尝试直接使用
            states_dict = states
        
        return f"""
当前分支状态（累积的状态）：
{json.dumps(states_dict, ensure_ascii=False, indent=2)}
"""
    
    def _convert_states_to_dict(self, states: Optional[UpdatedState]) -> Dict[str, Any]:
        """
        将状态对象转换为字典格式 📊
        
        Args:
            states: 状态对象
            
        Returns:
            状态字典
        """
        if not states:
            return {}
        return states.model_dump() if hasattr(states, "model_dump") else (states.dict() if states else {})
    
    def _build_character_relation_summary(self, current_states: Optional[Any]) -> str:
        """
        从 current_states 与 character_personas 生成「人物与关系」可读清单，供 prompt 约束身份与关系。
        current_states 结构：character_states { "C008": {维度: 值} }, relationship_states { "C011|C002": {维度: 值} }。
        """
        if not current_states:
            return ""
        try:
            personas_path = self.ending_candidates_path.parent / "character_personas.json"
            if not personas_path.exists():
                return ""
            with open(personas_path, "r", encoding="utf-8") as f:
                data = json.load(f)
            personas_list = data.get("personas", [])
            id_to_name = {p["id"]: p.get("name", p["id"]) for p in personas_list}
            id_to_role = {p["id"]: p.get("role", "") for p in personas_list}
        except Exception:
            id_to_name = {}
            id_to_role = {}
        if isinstance(current_states, dict):
            states_dict = current_states
        elif hasattr(current_states, "model_dump") or hasattr(current_states, "dict"):
            states_dict = self._convert_states_to_dict(current_states)
        else:
            states_dict = {}
        if not isinstance(states_dict, dict):
            return ""
        lines = []
        char_states = states_dict.get("character_states", {})
        if char_states:
            lines.append("**人物**（不得改变身份与称谓）：")
            for cid, dims in char_states.items():
                name = id_to_name.get(cid, cid)
                role = id_to_role.get(cid, "")
                lines.append(f"- {cid} {name}" + (f"（{role}）" if role else ""))
        rel_states = states_dict.get("relationship_states", {})
        if rel_states:
            lines.append("**关系**（不得颠倒或篡改）：")
            for rel_key, dims in rel_states.items():
                parts = rel_key.replace("|", "-").split("-")
                if len(parts) >= 2:
                    a, b = parts[0], parts[1]
                    na, nb = id_to_name.get(a, a), id_to_name.get(b, b)
                    dim_str = "，".join(f"{k}={v}" for k, v in list(dims.items())[:5])
                    lines.append(f"- {na}-{nb}: {dim_str}")
        return "\n".join(lines) if lines else ""
    
    def _format_path_combination(self, path_combination: List[Dict[str, Any]]) -> str:
        """
        格式化路径组合为文本格式 📋
        
        Args:
            path_combination: 路径组合列表
            
        Returns:
            格式化后的路径文本
        """
        if not path_combination:
            return ""
        
        path_text = ""
        for choice in path_combination:
            path_text += f"- {choice.get('fork_event_id', 'unknown')}: {choice.get('branch_choice', 'unknown')}\n"
        return path_text
    
    def _get_all_event_ids(self) -> List[str]:
        """
        获取所有主线事件ID列表 📜

        Returns:
            事件ID列表
        """
        all_event_ids = []
        for record in self.canonical_branch.get("processed_events", []):
            eid = record.get("event_id")
            if eid:
                all_event_ids.append(eid)
        return all_event_ids
    
    def _find_canonical_record(self, event_id: str) -> Optional[Dict[str, Any]]:
        """
        查找主线事件记录 🔍
        
        Args:
            event_id: 事件ID
            
        Returns:
            主线事件记录，如果没找到返回 None
        """
        for record in self.canonical_branch.get("processed_events", []):
            if record.get("event_id") == event_id:
                return record
        return None
    
    def get_mainline_summary_after_fork(self, fork_event_id: str, max_events: int = 8) -> str:
        """返回分叉点之后的主线事件摘要文本（供 prompt 注入，让 LLM 了解主线后续走向）。

        只取描述，不暴露完整数据；截取 max_events 条以控制 token 消耗。
        """
        events = self.canonical_branch.get("processed_events", [])
        found_fork = False
        after_fork: List[Dict[str, Any]] = []
        for record in events:
            if found_fork:
                after_fork.append(record)
                if len(after_fork) >= max_events:
                    break
            elif record.get("event_id") == fork_event_id:
                found_fork = True
        if not after_fork:
            return ""
        lines = [f"- {r.get('event_id', '?')}: {(r.get('description') or '')[:80]}" for r in after_fork]
        remaining = len(events) - (events.index(after_fork[-1]) + 1) if after_fork[-1] in events else 0
        suffix = f"\n（后续还有 {remaining} 个事件直到结局）" if remaining > 0 else ""
        return "\n".join(lines) + suffix

    def _parse_llm_response(self, response: Any) -> Dict[str, Any]:
        """
        解析 LLM 响应（支持字符串和字典格式）📥
        
        Args:
            response: LLM 响应（可能是字符串或字典）
            
        Returns:
            解析后的字典
        """
        if isinstance(response, str):
            return json.loads(response)
        return response
    
    def _find_ending_by_id(self, candidates: List[Dict[str, Any]], ending_id: str) -> Optional[Dict[str, Any]]:
        """
        根据ID查找结局候选 🔍
        
        Args:
            candidates: 结局候选列表
            ending_id: 结局ID
            
        Returns:
            找到的结局候选，如果没找到返回 None
        """
        for candidate in candidates:
            if candidate.get("id") == ending_id:
                return candidate
        return None
    
    def _format_ending_candidates(self, candidates: List[Dict[str, Any]]) -> str:
        """
        格式化结局候选为文本格式 📋
        
        Args:
            candidates: 结局候选列表
            
        Returns:
            格式化后的文本
        """
        candidates_text = ""
        for i, candidate in enumerate(candidates, 1):
            candidates_text += f"""
{i}. **{candidate.get('name', '未知结局')}** (ID: {candidate.get('id', 'unknown')})
   - 描述: {candidate.get('description', '')}
   - 标签: {', '.join(candidate.get('tags', []))}
   - 冲突标签: {', '.join(candidate.get('conflict_tags', []))}
"""
        return candidates_text
    
    # ========== 业务逻辑方法 🎯 ==========
    
    def _find_matching_ending_candidates(
        self,
        fork_event_id: str,
        branch_choice: Dict[str, Any],
        *,
        full_path_combination: Optional[List[Dict[str, Any]]] = None,
    ) -> List[Dict[str, Any]]:
        """
        查找匹配的提前结局候选 🎯

        **重要**：当能提供「当前正在生成的完整 path_combination」时，只从 **该条**
        ``ending_candidates`` 路径记录里取 ``is_premature`` 结局，避免多条路径在
        同一分叉点共享候选池（旧逻辑是「任意路径含该分叉就并池」，会混进其它路径的提前结局）。

        Args:
            fork_event_id: 分叉事件ID
            branch_choice: 分支选择（含 choice_id，对应 path_combination 里的 branch_choice）
            full_path_combination: 可选；不传则用 ``self.path_combination``（LangGraph 初始化时写入的完整路径）
        """
        branch_choice_id = branch_choice.get("choice_id", "")
        combo: List[Dict[str, Any]] = list(
            full_path_combination
            if full_path_combination is not None
            else (getattr(self, "path_combination", None) or [])
        )

        if combo:
            matching_path = self._find_matching_path_result(combo)
            if matching_path:
                path_combo = matching_path.get("path_combination", [])
                if not isinstance(path_combo, list):
                    path_combo = []
                fork_on_path = any(
                    choice_info.get("fork_event_id") == fork_event_id
                    and choice_info.get("branch_choice") == branch_choice_id
                    for choice_info in path_combo
                    if isinstance(choice_info, dict)
                )
                if not fork_on_path:
                    return []
                out: List[Dict[str, Any]] = []
                for candidate in matching_path.get("candidate_details", []) or []:
                    if not isinstance(candidate, dict) or not candidate.get("is_premature", False):
                        continue
                    out.append(
                        {
                            **candidate,
                            "path_id": matching_path.get("path_id", "unknown"),
                            "path_combination": path_combo,
                        }
                    )
                return out
            print(
                "  ⚠️  提前结局：完整 path_combination 无法在 ending_candidates 中精确匹配；"
                "跳过提前结局候选（避免混入其它路径的池子）。请检查 path_results 与生成侧路径是否一致。"
            )
            return []

        # 无完整路径上下文时的兜底（旧行为）：任意路径「包含该分叉」即并池
        matching_endings: List[Dict[str, Any]] = []
        for path_result in self.ending_candidates:
            path_combination = path_result.get("path_combination", [])
            if path_result.get("is_canonical", False):
                continue
            matches = False
            for choice_info in path_combination:
                if not isinstance(choice_info, dict):
                    continue
                choice_fork_id = choice_info.get("fork_event_id", "")
                choice_branch_id = choice_info.get("branch_choice", "")
                if choice_fork_id == fork_event_id and choice_branch_id == branch_choice_id:
                    matches = True
                    break
            if matches:
                candidate_details = path_result.get("candidate_details", [])
                for candidate in candidate_details:
                    if isinstance(candidate, dict) and candidate.get("is_premature", False):
                        matching_endings.append(
                            {
                                **candidate,
                                "path_id": path_result.get("path_id", "unknown"),
                                "path_combination": path_combination,
                            }
                        )
        return matching_endings
    
    def _path_combination_prefix_up_to(
        self,
        fork_event_id: str,
        branch_choice_id: str,
    ) -> List[Dict[str, Any]]:
        """返回 path_combination 中到「当前分叉+选择」为止的前缀（含），用于提前结局时只按实际经历选结局。"""
        path = getattr(self, "path_combination", []) or []
        for i, choice in enumerate(path):
            if (choice.get("fork_event_id") == fork_event_id and
                    choice.get("branch_choice") == branch_choice_id):
                return path[: i + 1]
        return path
    
    def get_function_definitions(self) -> List[Dict[str, Any]]:
        """
        获取所有函数的定义（用于 function calling）📋
        
        这些函数定义会被传递给流程控制 LLM，让 LLM 知道可以调用哪些函数。
        函数定义需要清晰、详细，帮助 LLM 做出正确的决策。
        
        Returns:
            函数定义列表（符合 OpenAI function calling 格式）
        """
        # 获取合流点选项（用于在函数描述中提供上下文）
        merge_options_text = ""
        if hasattr(self, 'merge_options') and self.merge_options:
            merge_options_text = f"\n\n当前可用的合流点选项：{', '.join(self.merge_options[:5])}"
            if len(self.merge_options) > 5:
                merge_options_text += f" 等（共 {len(self.merge_options)} 个）"
        
        return [
            {
                "type": "function",
                "function": {
                    "name": "generate_next_branch_event",
                    "description": (
                        "生成下一个分支事件。当分支路径仍需继续发展时调用此函数。"
                        "\n\n使用场景："
                        "\n- 分支事件不足 3 个，需要更多事件来展现选择的后果"
                        "\n- 分支故事尚未发展到可以自然合流的程度"
                        f"{merge_options_text}"
                        "\n\n注意："
                        "\n- 分支通常需要 3~8 个事件，达到 3 个后应考虑合流。"
                        "\n- 生成的内容可以与主线完全相反，主线仅作格式/结构参考。"
                        "\n- 每次调用会生成一个分支事件，并自动应用状态变化。"
                    ),
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "description": {
                                "type": "string",
                                "description": (
                                    "分支事件的描述（1-2句话，符合事件格式规范）。"
                                    "应该描述事件的核心行动、结果和影响。"
                                    "例如：'林在王佛的指导下，开始学习绘画的基本技巧，逐渐理解了艺术的本质。'"
                                )
                            },
                            "reasoning": {
                                "type": "string",
                                "description": (
                                    "为什么需要生成这个分支事件（解释逻辑，2-3句话）。"
                                    "应该说明："
                                    "\n1. 为什么现在需要生成新事件（而不是合流或提前结局）"
                                    "\n2. 这个事件如何推进分支路径的发展"
                                    "\n3. 这个事件如何向合流点靠近"
                                )
                            }
                        },
                        "required": ["description", "reasoning"]
                    }
                }
            },
            {
                "type": "function",
                "function": {
                    "name": "merge_to_mainline",
                    "description": (
                        "合流到主线事件。当分支已有 3 个以上事件且当前状态允许自然回归主线时调用此函数。"
                        "\n\n使用场景（推荐在 3~8 个分支事件后合流）："
                        "\n- 分支路径已有足够发展（≥3 个事件），当前状态允许回归主线"
                        "\n- 分支事件已替代了部分主线事件，可以自然地回到主线继续"
                        f"{merge_options_text}"
                        "\n\n注意：合流后会自动处理主线事件直到结局，并从 ending_candidates.json 中选择结局。"
                        "\n\n规范：每条路径必须以结局结束，最后一个事件必须是 ending 类型（type=ending）。"
                    ),
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "merge_point_event_id": {
                                "type": "string",
                                "description": (
                                    "合流点的事件ID（必须是主线事件ID）。"
                                    f"当前可用的合流点选项：{', '.join(self.merge_options[:5]) if hasattr(self, 'merge_options') and self.merge_options else '无'}"
                                    "\n应该选择距离分叉点最近且合理的合流点。"
                                    "合流点通常是关键事件，不能跳过。"
                                )
                            },
                            "reasoning": {
                                "type": "string",
                                "description": (
                                    "为什么选择这个合流点（解释逻辑，2-3句话）。"
                                    "应该说明："
                                    "\n1. 为什么现在可以合流（当前状态与主线的关系）"
                                    "\n2. 为什么选择这个合流点（距离、合理性、关键性）"
                                    "\n3. 分支路径如何自然地回到主线"
                                )
                            },
                            "skipped_mainline_events": {
                                "type": "array",
                                "items": {"type": "string"},
                                "description": (
                                    "被跳过的主线事件ID列表（分支事件替代了这些主线事件，可选）。"
                                    "这些事件会被标记为'已跳过'，不会在主线处理中重复应用。"
                                    "注意：关键事件不能被跳过！"
                                )
                            }
                        },
                        "required": ["merge_point_event_id", "reasoning"]
                    }
                }
            },
            {
                "type": "function",
                "function": {
                    "name": "create_early_ending",
                    "description": (
                        "创建提前结局。当分支路径的状态确实无法回归主线时调用此函数（最后手段）。"
                        "\n\n使用场景："
                        "\n- 分支已有足够发展，但累积状态变化使得回归主线不合理"
                        "\n- 当前状态已不可逆（如关系彻底破裂、核心冲突无解）"
                        "\n\n注意：大多数分支应通过 merge_to_mainline 合流，只有确实无法回归时才使用此函数。"
                        "\n提前结局会从 ending_candidates.json 中选择匹配的提前结局候选。"
                        "\n\n规范：每条路径必须以结局结束，最后一个事件必须是 ending 类型（type=ending）。"
                    ),
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "ending_type": {
                                "type": "string",
                                "description": (
                                    "结局类型。可选值："
                                    "\n- 'bad_ending': 坏结局（悲剧、失败、负面结果）"
                                    "\n- 'neutral_ending': 中性结局（平淡、未完成、开放式）"
                                    "\n- 'good_ending': 好结局（虽然提前，但结果是正面的）"
                                    "\n\n应该根据分支选择的逻辑后果和累积状态来判断结局类型。"
                                ),
                                "enum": ["bad_ending", "neutral_ending", "good_ending"]
                            },
                            "ending_reason": {
                                "type": "string",
                                "description": (
                                    "提前结局的原因（解释为什么无法合流，2-3句话）。"
                                    "应该说明："
                                    "\n1. 为什么分支路径无法合流到主线（状态不符合、偏离太远等）"
                                    "\n2. 分支选择如何导致这个提前结局"
                                    "\n3. 这个结局的合理性（基于累积的状态变化）"
                                )
                            },
                            "description": {
                                "type": "string",
                                "description": (
                                    "结局事件的描述（1-2句话，符合事件格式规范）。"
                                    "应该描述故事的最终状态和结果。"
                                    "例如：'由于林的选择，他未能完成王佛的传承，最终独自一人离开了画室。'"
                                )
                            }
                        },
                        "required": ["ending_type", "ending_reason", "description"]
                    }
                }
            }
        ]
    
    def initialize_path_context(
        self,
        fork_event_id: str,
        branch_choice: Dict[str, Any],
        current_states: UpdatedState,
        merge_options: List[str],
        path_combination: Optional[List[Dict[str, Any]]] = None,
    ) -> None:
        """
        初始化路径上下文（在开始生成路径时调用）
        
        Args:
            fork_event_id: 分叉事件ID
            branch_choice: 分支选择
            current_states: 当前状态
            merge_options: 合流点选项列表
            path_combination: 路径组合（用于匹配 ending_candidates）
        """
        self.fork_event_id = fork_event_id
        self.branch_choice = branch_choice
        self.current_states = current_states
        self.merge_options = merge_options
        self.path_combination = path_combination or []
        
        # 重置路径状态
        # 🔧 关键修复：如果已经提前结局，不要重置 is_early_ending，确保后续分支选择被跳过
        was_early_ending = self.is_early_ending
        self.branch_events = []
        self.merge_point = None
        # 🔧 只有在不是提前结局的情况下才重置 is_early_ending
        if not was_early_ending:
            self.is_early_ending = False
        self.ending_event = None
        self.skipped_mainline_events = []
    
    async def generate_next_branch_event(
        self,
        description: str,
        reasoning: str,
    ) -> Dict[str, Any]:
        """
        生成下一个分支事件 🌿
        
        这个函数会被 LLM 调用，用于标记需要生成下一个分支事件。
        注意：实际的事件生成由 _generate_branch_event_node 节点完成，这里只设置状态。
        
        Args:
            description: 分支事件的描述（LLM 提供，用于上下文）
            reasoning: 为什么需要生成这个分支事件
            
        Returns:
            状态信息（不生成事件，由节点生成）
        """
        print(f"  📝 LLM 调用: generate_next_branch_event")
        print(f"     理由: {reasoning}")
        # 🔧 关键修复：只设置状态，不生成事件
        # 实际的事件生成由 _generate_branch_event_node 节点完成，避免重复执行
        
        # 验证 fork_event_id 是否存在
        if not self.fork_event_id:
            logger.error("❌ fork_event_id 未设置！无法生成分支事件")
            return {
                "status": "error",
                "tool_name": "generate_next_branch_event",
                "message": "fork_event_id 未设置，无法生成分支事件",
            }
        
        # 不生成事件，只返回状态信息（包含 description 和 reasoning，供节点使用）
        return {
            "status": "generated",
            "tool_name": "generate_next_branch_event",  # 🔧 添加 tool_name 用于调试
            "description": description,
            "reasoning": reasoning,
        }
    
    async def merge_to_mainline(
        self,
        merge_point_event_id: str,
        reasoning: str,
        skipped_mainline_events: Optional[List[str]] = None,
    ) -> Dict[str, Any]:
        """
        合流到主线事件 🔄
        
        这个函数会被 LLM 调用，用于标记需要合流到主线。
        注意：实际的主线事件处理由 _process_mainline_node 节点完成，这里只设置状态。
        
        Args:
            merge_point_event_id: 合流点的事件ID
            reasoning: 为什么选择这个合流点
            skipped_mainline_events: 被跳过的主线事件ID列表（可选）
            
        Returns:
            状态信息（不处理主线事件，由节点处理）
        """
        print(f"  🔄 LLM 调用: merge_to_mainline")
        print(f"     理由: {reasoning}")
        
        # 验证合流点是否有效（传递 fork_event_id 进行完整验证）
        if not self._is_valid_merge_point(merge_point_event_id, self.fork_event_id):
            return {
                "status": "error",
                "message": f"无效的合流点: {merge_point_event_id}（必须在分叉点 {self.fork_event_id} 之后）",
            }
        
        # 验证跳过的主线事件（不能跳过关键事件）
        if skipped_mainline_events:
            invalid_skipped = [
                event_id for event_id in skipped_mainline_events
                if event_id in self.critical_events
            ]
            if invalid_skipped:
                return {
                    "status": "error",
                    "message": f"不能跳过关键事件: {invalid_skipped}",
                }
        
        # 🔧 关键修复：只设置状态，不处理主线事件
        # 实际的主线事件处理由 _process_mainline_node 节点完成，避免重复执行
        self.merge_point = merge_point_event_id
        self.skipped_mainline_events = skipped_mainline_events or []
        
        print(f"  ✅ 已设置合流点: {self.merge_point}")
        
        if self.skipped_mainline_events:
            print(f"     跳过的主线事件: {self.skipped_mainline_events}")
        
        # 不处理主线事件，只返回状态信息
        return {
            "status": "merged",
            "merge_point": merge_point_event_id,
            "skipped_mainline_events": self.skipped_mainline_events,
        }
    
    async def create_early_ending(
        self,
        ending_type: str,
        ending_reason: str,
        description: str,
    ) -> Dict[str, Any]:
        """
        创建提前结局 🎭
        
        这个函数会被 LLM 调用，用于标记需要创建提前结局。
        注意：实际的结局生成由 _create_early_ending_node 节点完成，这里只设置状态。
        
        Args:
            ending_type: 结局类型
            ending_reason: 提前结局的原因
            description: 结局事件的描述（LLM 提供，用于上下文）
            
        Returns:
            状态信息（不生成结局，由节点生成）
        """
        print(f"  🎭 LLM 调用: create_early_ending")
        print(f"     原因: {ending_reason}")
        
        # 🔧 关键修复：只设置状态，不生成结局
        # 实际的结局生成由 _create_early_ending_node 节点完成，避免重复执行
        self.is_early_ending = True
        
        # 不生成结局，只返回状态信息
        return {
            "status": "early_ending_created",
            "ending_type": ending_type,
            "ending_reason": ending_reason,
            "description": description,
        }
    
    async def _generate_single_branch_event(
        self,
        branch_id: str,
        sequence_number: int,
        previous_event: Optional[Dict[str, Any]],
        branch_choice: Dict[str, Any],
        current_states: UpdatedState,
        merge_options: List[str],
        fork_event_id: Optional[str] = None,  # 🔢 新增：用于生成绝对序号
    ) -> Dict[str, Any]:
        """
        生成单个分支事件 🎯
        
        使用 LLM 生成符合以下条件的事件：
        1. 符合分支选择的逻辑后果
        2. 符合当前状态（人物、关系、世界）
        3. 累积状态变化（影响人物、关系、物品）
        4. 逐步向合流点靠近
        """
        # 格式化当前状态
        states_text = self._format_states_for_llm(current_states)
        
        # 格式化上一个事件（如果有）
        previous_event_text = ""
        if previous_event:
            previous_event_text = f"""
上一个分支事件：
- 事件ID: {previous_event.get('event_id', '未知')}
- 描述: {previous_event.get('description', '')}
- 场景: {previous_event.get('event_data', {}).get('场景', '')}
- 结果: {previous_event.get('event_data', {}).get('结果', '')}
"""
        
        # 格式化已生成的分支事件（使用滑动窗口，只显示最近的事件）✨
        # 策略：只显示最近 3-5 个事件，避免上下文过长
        all_previous_events_text = ""
        if self.branch_events:
            max_recent_events = 3  # 最多显示最近 3 个事件
            total_events = len(self.branch_events)
            
            # 只取最近的事件
            recent_events = self.branch_events[-max_recent_events:]
            start_index = max(1, total_events - max_recent_events + 1)
            
            all_previous_events_text = f"""
**已生成的分支事件序列**（共 {total_events} 个，显示最近 {len(recent_events)} 个）：
"""
            for i, event in enumerate(recent_events, start_index):
                event_id = event.get('event_id', '未知')
                event_desc = event.get('description', '')
                all_previous_events_text += f"{i}. [{event_id}] {event_desc}\n"
            
            # 如果还有更早的事件，添加提示
            if total_events > max_recent_events:
                all_previous_events_text += f"\n（已省略前 {total_events - max_recent_events} 个事件，当前状态已包含其累积影响）"
        
        # 格式化合流点选项（主线事件）
        merge_options_text = ""
        if merge_options:
            merge_options_text = f"""
合流点选项（主线事件，分支路径最终会回到这些事件之一）：
{', '.join(merge_options)}
注意：这些是主线事件，分支路径会合流到这些事件，然后继续主线路径。
"""
        
        # 主线后续走向摘要（让 LLM 知道主线后面发生了什么，帮助它做更好的分支叙事）
        mainline_summary_text = ""
        effective_fork = fork_event_id or self.fork_event_id
        if effective_fork:
            summary = self.get_mainline_summary_after_fork(effective_fork)
            if summary:
                mainline_summary_text = f"""
**主线后续走向**（仅供参考，分支不必跟随，但可据此判断故事背景和合流时机）：
{summary}
"""
        
        # 🔢 计算绝对序号的事件ID（用于 prompt 说明）
        absolute_event_id_example = ""
        if fork_event_id:
            fork_number = self._extract_event_number(fork_event_id)
            if fork_number is not None:
                absolute_number = fork_number + sequence_number
                absolute_event_id_example = f"E{absolute_number}"
        
        prompt = f"""你是一个分支事件生成助手，负责为分支路径生成全新的事件。

**核心理念**：
- **分支事件是全新的，不是主线事件的变体**：分支路径应该有自己的独特事件序列
- **符合分支选择的逻辑后果**：事件应该反映分支选择带来的影响
- **符合当前状态**：事件应该符合分支的累积状态（人物、关系、世界）
- **累积状态变化**：事件应该产生新的状态变化，影响后续事件
- **逐步向合流点靠近**：事件应该推动分支路径向合流点靠近

分支信息：
- 分支ID: {branch_id}
- 序列号: {sequence_number}（第 {sequence_number} 个分支事件）
- 分支选择: {branch_choice.get('choice_id')} - {branch_choice.get('description', '')}
- 选择理由: {branch_choice.get('reasoning', '')}
{all_previous_events_text}
{previous_event_text}
{states_text}
{merge_options_text}
{mainline_summary_text}
任务：
1. 生成一个符合分支选择逻辑后果的事件
2. 事件应该符合当前分支状态（人物、关系、世界）
3. 事件应该产生新的状态变化（影响人物、关系、物品）
4. 事件应该推动分支路径向合流点靠近（但不能跳过关键事件）
5. **叙述人称**：{_get_narrative_perspective_instruction(self.narrative_perspective)}（description 与 source_text 均须遵守）

输出格式（JSON）：
{{
  "event_data": {{
    "id": "{absolute_event_id_example if absolute_event_id_example else branch_id}_{sequence_number}",
    "人物": ["人物ID列表"],
    "行动": "事件的核心行动",
    "目标": "行动的目标",
    "结果": "行动的结果",
    "前提条件": "事件的前提条件",
    "结果影响": "事件的结果影响",
    "场景": "事件发生的场景",
    "时间": "事件发生的时间"
  }},
  "description": "事件的简洁描述（1-2句话）",
  "state_changes": [
    {{
      "target_type": "character|relationship|world",
      "target_id": "目标ID",
      "dimension": "维度名称（人物：情绪状态/动机/处境认知/内在冲突/健康状态/价值观/行动能力；关系：信任度/情感倾向/权力动态/连接强度；世界：场景状态/时间状态/物品状态/环境状态/社会状态/资源状态）",
      "value": "描述性文本"
    }}
  ],
  "source_text": "与场景风格一致的原文片段（100-400字），用于保持文学风格",
  "dialogue": "可选。仅当本事件确有直接引语对话时填写，格式 [{{\"speaker\":\"角色名\",\"text\":\"引语内容\",\"tone\":\"语气\"}}]，无对话则省略或空数组 []"
}}

注意：
- 事件ID格式：使用绝对序号（例如：如果分叉点是 E14，第一个分支事件是 E15，第二个是 E16）
- 状态变化只使用上方列出的合法维度，不要发明新维度
- 世界状态只追踪影响角色行动自由的东西（道具可用性、位置约束、时间限制），场景氛围写在 description/source_text 里
- dialogue 仅在有必要时填写：无对话则 dialogue 不写或 []
"""
        system_block = _get_story_system_prompt()
        full_prompt = (system_block + "\n\n---\n\n" + prompt) if system_block else prompt
        # 调用 LLM 生成分支事件
        response = await self.llm_client.invoke(
            full_prompt,
            return_json=True,
        )
        
        # 解析响应
        response = self._parse_llm_response(response)
        
        # 🔢 关键修改：使用绝对序号生成 event_id（例如：E14 分叉，第一个分支事件是 E15）
        if fork_event_id:
            event_id = self._generate_absolute_event_id(fork_event_id, sequence_number)
            logger.info(f"✅ 生成绝对序号 event_id: {event_id} (分叉点: {fork_event_id}, 序号: {sequence_number})")
        else:
            # 如果没有提供 fork_event_id，回退到旧格式
            llm_provided_id = response.get("event_data", {}).get("id", "")
            if llm_provided_id and llm_provided_id.startswith(f"{branch_id}_"):
                event_id = llm_provided_id
            else:
                event_id = f"{branch_id}_{sequence_number}"
            logger.warning(f"⚠️  未提供 fork_event_id，使用旧格式: {event_id}")
        
        event_data = response.get("event_data", {})
        # 确保 event_data 中的 id 字段与 event_id 一致
        if event_data:
            event_data["id"] = event_id
        
        description = response.get("description", "")
        source_text = response.get("source_text") or None
        dialogue = response.get("dialogue")
        if not isinstance(dialogue, list):
            dialogue = None
        
        # 【状态变化生成】使用 StateChangeGenerator 为分支事件生成状态变化
        # 注意：这里只是生成状态变化列表，不会修改状态，状态变化会在后续通过 StateApplier 应用
        state_changes = await self.state_change_generator.generate_for_branch_new_event(
            event_id=event_id,
            event_description=description,
            branch_choice=branch_choice,
            current_states=current_states,
            previous_event=previous_event,
        )
        
        out = {
            "event_id": event_id,
            "event_data": event_data,
            "description": description,
            "state_changes": state_changes,
            "is_branch_event": True,  # 🔧 标记这是分支事件，用于后续判断
        }
        if source_text is not None:
            out["source_text"] = source_text
        if dialogue is not None:
            out["dialogue"] = dialogue
        return out
    
    async def _generate_ending_event(
        self,
        branch_id: str,
        branch_choice: Dict[str, Any],
        current_states: UpdatedState,
        previous_event: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        """
        生成提前结局事件 🎯
        
        当分支无法合流到主线时，生成一个结局事件。
        优先使用 ending_candidates.json 中的提前结局作为参考。
        """
        # 格式化当前状态
        states_text = self._format_states_for_llm(current_states)
        
        # 格式化上一个事件（如果有）
        previous_event_text = ""
        if previous_event:
            previous_event_text = f"""
上一个分支事件：
- 事件ID: {previous_event.get('event_id')}
- 描述: {previous_event.get('description', '')}
"""
        
        # 查找匹配的提前结局候选，并使用相似结局合并逻辑
        matching_endings = self._find_matching_ending_candidates(
            fork_event_id=self.fork_event_id,
            branch_choice=branch_choice,
        )
        
        # 如果有匹配的提前结局，使用相似结局合并逻辑选择最有文学张力的（仅按当前分叉前的实际选择评估）
        selected_premature_ending = None
        if matching_endings:
            path_for_ending = self._path_combination_prefix_up_to(
                self.fork_event_id, branch_choice.get("choice_id", "")
            )
            print(f"  🎯 找到 {len(matching_endings)} 个匹配的提前结局候选，进行相似合并和文学张力评估...")
            merged_endings = await self._merge_similar_endings(
                candidates=matching_endings,
                final_states=current_states,
                path_combination=path_for_ending,
                is_premature=True,
            )
            
            if merged_endings:
                # 从合并后的候选中选择最符合的
                if len(merged_endings) > 1:
                    selected_premature_ending = await self._select_ending_with_llm(
                        candidates=merged_endings,
                        final_states=current_states,
                        path_combination=path_for_ending,
                        is_premature=True,
                    )
                else:
                    selected_premature_ending = merged_endings[0]
                
                if selected_premature_ending:
                    print(f"  ✅ 选择提前结局: {selected_premature_ending.get('name', '未知结局')} (ID: {selected_premature_ending.get('id', 'unknown')})")
        
        # 构建提前结局候选信息
        ending_candidates_text = ""
        if selected_premature_ending:
            # 如果已经选择了提前结局，直接使用它作为参考
            ending_candidates_text = f"""
**已选择的提前结局（来自 ending_candidates.json，经过相似合并和文学张力评估）**：
**{selected_premature_ending.get('name', '未知结局')}** (ID: {selected_premature_ending.get('id', 'unknown')})
- 描述: {selected_premature_ending.get('description', '')}
- 标签: {', '.join(selected_premature_ending.get('tags', []))}
- 冲突标签: {', '.join(selected_premature_ending.get('conflict_tags', []))}

注意：你应该基于这个已选择的提前结局，生成一个符合当前分支状态的结局事件。
这个结局已经经过相似合并和文学张力评估，是最符合路径组合的结局。
"""
        elif matching_endings:
            # 如果没有选择（合并失败），列出所有候选
            ending_candidates_text = f"""
**提前结局候选（来自 ending_candidates.json）**：
请从以下提前结局中选择一个最符合当前分支状态的结局，或基于这些结局生成一个类似的结局。

"""
            for i, ending in enumerate(matching_endings, 1):
                ending_candidates_text += f"""
{i}. **{ending.get('name', '未知结局')}** (ID: {ending.get('id', 'unknown')})
   - 描述: {ending.get('description', '')}
   - 标签: {', '.join(ending.get('tags', []))}
   - 冲突标签: {', '.join(ending.get('conflict_tags', []))}
"""
            ending_candidates_text += """
注意：你应该基于这些提前结局候选，生成一个符合当前分支状态的结局事件。
如果多个候选都合适，应该选择最有文学张力的一个。
"""
        else:
            # 如果没有找到匹配的提前结局，使用主线结局作为格式参考
            canonical_ending = self._get_canonical_ending_event()
            if canonical_ending:
                ending_candidates_text = f"""
主线结局事件格式参考（E24）：
- event_id: {canonical_ending.get('event_id')}
- description: {canonical_ending.get('description', '')[:200]}...
- decision_point: {canonical_ending.get('decision_point')}
- canonical_choice: {canonical_ending.get('canonical_choice')}
- state_changes 数量: {len(canonical_ending.get('state_changes', []))}

注意：分支结局事件的格式应该参考主线结局事件，但内容应该反映分支选择的后果。
"""
        
        prompt = f"""你是一个分支结局生成助手，负责为无法合流到主线的分支生成提前结局。

**核心理念**：
- **分支无法合流到主线**：分支路径已经偏离主线太远，无法回到主线
- **提前结局**：基于分支选择的逻辑后果和累积状态，生成一个合理的结局
- **结局应该符合分支选择**：结局应该反映分支选择带来的影响
- **结局应该符合当前状态**：结局应该基于分支的累积状态（人物、关系、世界）
- **优先使用提前结局候选**：如果提供了提前结局候选，应该优先参考这些候选

分支信息：
- 分支ID: {branch_id}
- 分叉事件ID: {self.fork_event_id}
- 分支选择: {branch_choice.get('choice_id')} - {branch_choice.get('description', '')}
- 选择理由: {branch_choice.get('reasoning', '')}
{previous_event_text}
{states_text}
{ending_candidates_text}

任务：
1. 生成一个符合分支选择逻辑后果的结局事件
2. 结局应该符合当前分支状态（人物、关系、世界）
3. 结局应该是一个合理的结局（基于累积的状态）
4. 结局应该反映分支选择带来的影响（可能是好结局、坏结局或中性结局）
5. **格式应该参考主线结局事件**（E24），但内容应该反映分支选择的后果
6. **叙述人称**：{_get_narrative_perspective_instruction(self.narrative_perspective)}（description 须遵守）

输出格式（JSON）：
{{
  "event_id": "{branch_id}_ending",
  "description": "结局的简洁描述（1-2句话）",
  "state_changes": [
    {{
      "target_type": "character|relationship|world",
      "target_id": "目标ID",
      "dimension": "从合法维度中选择（人物：情绪状态/动机/处境认知/内在冲突/健康状态/价值观/行动能力；关系：信任度/情感倾向/权力动态/连接强度；世界：场景状态/时间状态/物品状态/环境状态/社会状态/资源状态）",
      "value": "描述性文本"
    }}
  ],
  "ending_type": "good|bad|neutral",
  "ending_reason": "结局的原因（为什么无法合流到主线）"
}}

注意：
- 事件ID格式：{branch_id}_ending
- 状态变化只使用上方列出的合法维度，不要发明新维度
- 世界状态变化只追踪影响角色行动自由的东西，场景氛围细节写在 description 里
"""
        
        # 调用 LLM 生成结局事件
        response = await self.llm_client.invoke(
            prompt,
            return_json=True,
        )
        
        # 解析响应
        response = self._parse_llm_response(response)
        
        event_id = response.get("event_id", f"{branch_id}_ending")
        description = response.get("description", "")
        decision_point = response.get("decision_point")
        pre_condition = response.get("pre_condition")
        state_changes = response.get("state_changes", [])
        ending_type = response.get("ending_type", "neutral")
        ending_reason = response.get("ending_reason", "分支路径无法合流到主线")
        
        return {
            "event_id": event_id,
            "description": description,
            "decision_point": decision_point,
            "canonical_choice": None,
            "pre_condition": pre_condition,
            "state_changes": state_changes,
            "is_ending": True,
            "ending_type": ending_type,
            "ending_reason": ending_reason,
        }
    
    def _get_canonical_ending_event(self) -> Optional[Dict[str, Any]]:
        """
        获取主线最后一个事件（结局事件）作为格式参考 📚
        """
        events = self.canonical_branch.get("processed_events", [])
        if events:
            return events[-1]
        return None
    
    def _is_valid_merge_point(self, event_id: str, fork_event_id: Optional[str] = None) -> bool:
        """
        验证合流点是否有效 🔍
        
        检查：
        1. 合流点是否在主线的 processed_events 中
        2. 合流点是否在分叉点之后（如果提供了 fork_event_id）
        3. 合流点不能是分叉点本身
        """
        # 获取所有事件ID（从主线记录中）
        all_event_ids = []
        for record in self.canonical_branch.get("processed_events", []):
            eid = record.get("event_id")
            if eid:
                all_event_ids.append(eid)
        
        # 1. 检查合流点是否存在
        if event_id not in all_event_ids:
            logger.warning(f"⚠️  合流点 {event_id} 不在主线事件列表中")
            return False
        
        # 2. 如果提供了 fork_event_id，检查合流点是否在分叉点之后
        if fork_event_id:
            try:
                fork_index = all_event_ids.index(fork_event_id)
                merge_index = all_event_ids.index(event_id)
                
                if merge_index <= fork_index:
                    logger.warning(f"⚠️  合流点 {event_id} 不能在分叉点 {fork_event_id} 之前或等于分叉点（分叉点索引: {fork_index}, 合流点索引: {merge_index}）")
                    return False
            except ValueError:
                logger.warning(f"⚠️  找不到分叉点 {fork_event_id} 或合流点 {event_id} 在主线事件列表中的位置")
                return False
        
        return True
    
    def get_merge_options(
        self,
        fork_event_id: str,
        max_distance: int = 5,
    ) -> List[Dict[str, Any]]:
        """
        获取合流点选项列表（主线事件）🔄
        
        合流点选项来自主线事件序列，因为分支最终要回到主线。
        
        策略：
        1. 下一个事件（如果存在）
        2. 隔几个事件的下一个事件（最多 max_distance 个）
        3. 下一个关键事件（如果存在）
        """
        # 获取所有事件ID（从主线记录中）
        all_event_ids: List[str] = []
        event_descriptions: Dict[str, str] = {}
        for record in self.canonical_branch.get("processed_events", []):
            eid = record.get("event_id")
            if not eid:
                continue
            all_event_ids.append(eid)
            event_descriptions[eid] = record.get("description", "")

        # 找到分叉事件的位置
        try:
            fork_index = all_event_ids.index(fork_event_id)
        except ValueError:
            print(f"  ⚠️  找不到分叉事件 {fork_event_id} 在主线的位置")
            return []

        merge_options: List[Dict[str, Any]] = []

        # 1) 下一个事件（距离=1）
        if fork_index + 1 < len(all_event_ids):
            next_event = all_event_ids[fork_index + 1]
            is_critical = bool(self.critical_events) and next_event in self.critical_events
            merge_options.append(
                {
                    "event_id": next_event,
                    "is_critical": is_critical,
                    "distance": 1,
                    "description": event_descriptions.get(next_event, ""),
                }
            )

        # 2) 距离 2~max_distance 的所有事件
        max_i_exclusive = min(max_distance + 1, len(all_event_ids) - fork_index)
        for i in range(2, max_i_exclusive):
            event_id = all_event_ids[fork_index + i]
            is_critical = bool(self.critical_events) and event_id in self.critical_events
            merge_options.append(
                {
                    "event_id": event_id,
                    "is_critical": is_critical,
                    "distance": i,
                    "description": event_descriptions.get(event_id, ""),
                }
            )

        # 3) 如果距离 max_distance 内没有关键事件，添加下一个关键事件（如果存在且不在选项中）
        if self.critical_events:
            has_critical_in_range = any(
                opt.get("is_critical", False) and opt.get("distance", 0) <= max_distance
                for opt in merge_options
            )
            if not has_critical_in_range:
                for i in range(fork_index + max_distance + 1, len(all_event_ids)):
                    event_id = all_event_ids[i]
                    if event_id in self.critical_events:
                        if not any(opt.get("event_id") == event_id for opt in merge_options):
                            merge_options.append(
                                {
                                    "event_id": event_id,
                                    "is_critical": True,
                                    "distance": i - fork_index,
                                    "description": event_descriptions.get(event_id, ""),
                                }
                            )
                        break  # 找到并添加了关键事件，退出循环

        return merge_options
    
    async def identify_skipped_mainline_events(
        self,
        fork_event_id: str,
        branch_events: List[Dict[str, Any]],
        merge_point: Optional[str],
    ) -> List[str]:
        """
        识别被跳过的主线事件（分支事件替代了主线事件）⏭️
        
        策略：
        1. 获取分叉点和合流点之间的主线事件
        2. 如果分支生成了事件，这些事件可能替代了主线的一些非关键事件
        3. 关键事件不能被跳过（必须保留）
        """
        if not merge_point:
            # 提前结局，没有合流点，不跳过任何事件
            return []
        
        # 获取所有主线事件ID（从主线记录中）
        all_event_ids = []
        for record in self.canonical_branch.get("processed_events", []):
            eid = record.get("event_id")
            if eid:
                all_event_ids.append(eid)
        
        try:
            fork_index = all_event_ids.index(fork_event_id)
            merge_index = all_event_ids.index(merge_point)
        except ValueError:
            # 找不到分叉点或合流点，返回空列表
            return []
        
        # 获取分叉点和合流点之间的主线事件
        mainline_events_between = all_event_ids[fork_index + 1:merge_index]
        
        if not mainline_events_between:
            # 分叉点和合流点之间没有主线事件，不跳过
            return []
        
        # 如果分支生成了事件，这些事件可能替代了主线的一些非关键事件
        skipped_events = []
        
        # 如果分支生成了事件，且分支事件数量 >= 主线事件数量的一半
        # 说明分支事件可能替代了主线的一些非关键事件
        if len(branch_events) > 0 and len(mainline_events_between) > 0:
            # 计算可以跳过的非关键事件数量
            num_skippable = min(len(branch_events), len(mainline_events_between))
            
            for event_id in mainline_events_between:
                # 关键事件不能被跳过
                if event_id in self.critical_events:
                    continue
                
                # 如果已经跳过了足够的非关键事件，停止
                if len(skipped_events) >= num_skippable:
                    break
                
                skipped_events.append(event_id)
        
        return skipped_events
    
    def _find_matching_path_result(
        self,
        path_combination: List[Dict[str, Any]],
    ) -> Optional[Dict[str, Any]]:
        """
        查找匹配的路径结果（用于选择结局）🎯
        
        根据 path_combination 在 ending_candidates.json 中查找匹配的路径结果
        
        Args:
            path_combination: 路径组合（包含所有分叉点和分支选择）
            
        Returns:
            匹配的路径结果，如果没找到返回 None
        """
        if not path_combination:
            return None
        
        # 构建路径组合的匹配键（用于匹配）
        # 匹配策略：检查 path_combination 是否与 ending_candidates 中的路径组合匹配
        for path_result in self.ending_candidates:
            candidate_path_combo = path_result.get("path_combination", [])
            
            # 如果长度不同，不匹配
            if len(candidate_path_combo) != len(path_combination):
                continue
            
            # 检查每个分叉点是否匹配
            matches = True
            for i, choice_info in enumerate(path_combination):
                candidate_choice = candidate_path_combo[i] if i < len(candidate_path_combo) else None
                if not candidate_choice:
                    matches = False
                    break
                
                # 匹配：分叉点ID和分支选择ID都相同
                if (candidate_choice.get("fork_event_id") != choice_info.get("fork_event_id") or
                    candidate_choice.get("branch_choice") != choice_info.get("branch_choice")):
                    matches = False
                    break
            
            if matches:
                return path_result
        
        return None
    
    def _find_matching_path_result_by_prefix(
        self,
        path_combination: List[Dict[str, Any]],
    ) -> Optional[Dict[str, Any]]:
        """
        按前缀匹配路径结果（用于提前结局：实际只经历了前若干选择）。
        当 path_combination 是某条候选路径的前缀时，返回该候选路径（用于取候选结局列表）。
        """
        if not path_combination:
            return None
        n = len(path_combination)
        for path_result in self.ending_candidates:
            candidate_path_combo = path_result.get("path_combination", [])
            if len(candidate_path_combo) < n:
                continue
            matches = True
            for i in range(n):
                c = candidate_path_combo[i] if i < len(candidate_path_combo) else None
                p = path_combination[i] if i < len(path_combination) else None
                if not c or not p:
                    matches = False
                    break
                if (c.get("fork_event_id") != p.get("fork_event_id") or
                        c.get("branch_choice") != p.get("branch_choice")):
                    matches = False
                    break
            if matches:
                return path_result
        return None
    
    async def select_ending_from_candidates(
        self,
        path_combination: List[Dict[str, Any]],
        final_states: Optional[UpdatedState] = None,
        is_premature: bool = False,
    ) -> Optional[Dict[str, Any]]:
        """
        从 ending_candidates.json 中选择结局 🎯
        
        在合流后、处理完所有主线事件后调用，根据路径组合和最终状态选择结局。
        包含相似结局识别、合并和选择有文学张力结局的逻辑。
        
        Args:
            path_combination: 路径组合（包含所有分叉点和分支选择）
            final_states: 最终状态（可选，用于更精确的匹配）
            is_premature: 是否是提前结局（默认：False）
            
        Returns:
            选择的结局信息，如果没找到匹配的路径或没有候选结局，返回 None
        """
        # 查找匹配的路径结果（提前结局时可用前缀匹配，因 path_combination 仅为实际经历的选择）
        matching_path = self._find_matching_path_result(path_combination)
        if not matching_path and is_premature:
            matching_path = self._find_matching_path_result_by_prefix(path_combination)
            if matching_path:
                print(f"  📌 提前结局路径：按实际经历的前缀匹配到候选路径（共 {len(path_combination)} 个选择）")
        if not matching_path:
            print(f"  ⚠️  未找到匹配的路径结果，无法选择结局")
            return None
        
        # 获取候选结局列表
        candidates = matching_path.get("candidates", [])
        candidate_details = matching_path.get("candidate_details", [])
        
        if not candidates:
            print(f"  ⚠️  路径没有候选结局")
            return None
        
        # 根据 is_premature 过滤候选
        if is_premature:
            # 提前结局：只选择提前结局
            filtered_candidates = [
                detail for detail in candidate_details
                if detail.get("is_premature", False)
            ]
        else:
            # 正常结局：排除提前结局
            filtered_candidates = [
                detail for detail in candidate_details
                if not detail.get("is_premature", False)
            ]
        
        if not filtered_candidates:
            print(f"  ⚠️  路径没有符合条件的候选结局（is_premature={is_premature}）")
            return None
        
        # 提前结局路径：已在演化时选过结局，此处不再做「识别相似结局」「选张力」，直接用第一个候选
        if is_premature:
            selected = filtered_candidates[0]
            print(f"  ✅ 提前结局路径：直接使用候选（跳过识别相似/选张力）→ {selected.get('name', '未知结局')} (ID: {selected.get('id', 'unknown')})")
            return selected
        
        # 非提前结局：识别相似、合并、再选最有文学张力
        print(f"  🔍 识别相似结局并合并...")
        merged_candidates = await self._merge_similar_endings(
            candidates=filtered_candidates,
            final_states=final_states or self.current_states,
            path_combination=path_combination,
            is_premature=is_premature,
        )
        
        if not merged_candidates:
            print(f"  ⚠️  合并后没有候选结局")
            return None
        
        if len(merged_candidates) > 1:
            return await self._select_ending_with_llm(
                candidates=merged_candidates,
                final_states=final_states or self.current_states,
                path_combination=path_combination,
                is_premature=is_premature,
            )
            selected = merged_candidates[0]
            print(f"  ✅ 选择结局: {selected.get('name', '未知结局')} (ID: {selected.get('id', 'unknown')})")
            return selected
    
    async def _identify_similar_endings(
        self,
        candidates: List[Dict[str, Any]],
    ) -> List[List[Dict[str, Any]]]:
        """
        识别相似的结局候选 🎯
        
        根据描述、标签、冲突标签等特征识别相似的结局，将它们分组。
        
        Args:
            candidates: 候选结局列表
            
        Returns:
            相似结局的分组列表，每个分组包含相似的结局
        """
        if len(candidates) <= 1:
            return [[c] for c in candidates]
        
        # 使用 LLM 识别相似的结局
        candidates_text = ""
        for i, candidate in enumerate(candidates, 1):
            candidates_text += f"""
{i}. **{candidate.get('name', '未知结局')}** (ID: {candidate.get('id', 'unknown')})
   - 描述: {candidate.get('description', '')}
   - 标签: {', '.join(candidate.get('tags', []))}
   - 冲突标签: {', '.join(candidate.get('conflict_tags', []))}
"""
        
        prompt = f"""你是一个结局分析助手，负责识别相似的结局候选。

**候选结局**：
{candidates_text}

**任务**：
分析这些结局，识别哪些结局是相似的（主题、情感、结果等相似）。
相似的结局应该被合并，只保留最有文学张力的那个。

**相似性判断标准**：
1. **主题相似**：结局的核心主题或核心冲突相似
2. **情感相似**：结局的情感基调相似（如都是悲剧、都是开放式结局等）
3. **结果相似**：结局的最终结果相似（如都是主角死亡、都是关系破裂等）
4. **标签重叠**：结局的标签有大量重叠

**输出格式（JSON）**：
{{
  "similar_groups": [
    {{
      "ending_ids": ["ending_id1", "ending_id2", ...],
      "similarity_reason": "为什么这些结局相似（主题、情感、结果等）"
    }},
    ...
  ],
  "unique_endings": ["ending_id1", "ending_id2", ...]
}}

注意：
- 每个结局ID只能出现在一个分组中
- 如果某个结局与其他所有结局都不相似，它应该在 unique_endings 中
- 相似分组应该包含至少2个结局
"""
        
        # 调用 LLM 识别相似结局
        response = await self.llm_client.invoke(
            prompt,
            return_json=True,
        )
        
        # 解析响应
        response = self._parse_llm_response(response)
        
        similar_groups = response.get("similar_groups", [])
        unique_endings = response.get("unique_endings", [])
        
        # 构建分组列表
        groups = []
        
        # 添加相似分组
        for group_info in similar_groups:
            ending_ids = group_info.get("ending_ids", [])
            group = [c for c in candidates if c.get("id") in ending_ids]
            if len(group) >= 2:  # 至少2个结局才算是相似分组
                groups.append(group)
        
        # 添加唯一结局（每个单独成组）
        for ending_id in unique_endings:
            unique_ending = next((c for c in candidates if c.get("id") == ending_id), None)
            if unique_ending:
                groups.append([unique_ending])
        
        # 确保所有候选都被分组（防止遗漏）
        grouped_ids = set()
        for group in groups:
            for ending in group:
                grouped_ids.add(ending.get("id"))
        
        for candidate in candidates:
            if candidate.get("id") not in grouped_ids:
                groups.append([candidate])
        
        return groups
    
    async def _merge_similar_endings(
        self,
        candidates: List[Dict[str, Any]],
        final_states: Optional[UpdatedState],
        path_combination: List[Dict[str, Any]],
        is_premature: bool = False,
    ) -> List[Dict[str, Any]]:
        """
        合并相似的结局，选择有文学张力的结局 🎨
        
        识别相似的结局，然后从每个相似分组中选择最有文学张力的结局。
        
        Args:
            candidates: 候选结局列表
            final_states: 最终状态（用于评估文学张力）
            path_combination: 路径组合（用于评估文学张力，提前结局时仅为实际经历的选择）
            is_premature: 是否为提前结局路径
            
        Returns:
            合并后的候选结局列表（每个相似分组只保留一个最有文学张力的结局）
        """
        if len(candidates) <= 1:
            return candidates
        
        # 识别相似结局
        similar_groups = await self._identify_similar_endings(candidates)
        
        print(f"  📊 识别到 {len(similar_groups)} 个结局分组（包含相似结局）")
        
        # 从每个相似分组中选择最有文学张力的结局
        merged_candidates = []
        
        for group in similar_groups:
            if len(group) == 1:
                # 只有一个结局，直接添加
                merged_candidates.append(group[0])
            else:
                # 多个相似结局，选择最有文学张力的
                print(f"  🎨 从 {len(group)} 个相似结局中选择最有文学张力的...")
                selected = await self._select_most_dramatic_ending(
                    similar_endings=group,
                    final_states=final_states,
                    path_combination=path_combination,
                    is_premature=is_premature,
                )
                if selected:
                    merged_candidates.append(selected)
                    print(f"     选择: {selected.get('name', '未知结局')} (ID: {selected.get('id', 'unknown')})")
                else:
                    # 如果选择失败，使用第一个
                    merged_candidates.append(group[0])
        
        return merged_candidates
    
    async def _select_most_dramatic_ending(
        self,
        similar_endings: List[Dict[str, Any]],
        final_states: Optional[UpdatedState],
        path_combination: List[Dict[str, Any]],
        is_premature: bool = False,
    ) -> Optional[Dict[str, Any]]:
        """
        从相似的结局中选择最有文学张力的结局 🎨
        
        文学张力的评估标准：
        1. 情感冲击力：结局是否能引起强烈的情感反应
        2. 主题深度：结局是否深刻反映主题
        3. 冲突解决：结局是否以有意义的方式解决冲突
        4. 与路径的契合度：结局是否与路径组合的逻辑高度契合
        
        Args:
            similar_endings: 相似的结局列表
            final_states: 最终状态
            path_combination: 路径组合（提前结局时仅为实际经历的选择）
            is_premature: 是否为提前结局路径
            
        Returns:
            最有文学张力的结局
        """
        if len(similar_endings) == 1:
            return similar_endings[0]
        
        # 格式化相似结局信息
        endings_text = self._format_ending_candidates(similar_endings)
        
        # 格式化最终状态
        states_text = self._format_states_for_llm(final_states)
        if states_text:
            states_text = states_text.replace("当前分支状态（累积的状态）", "最终状态")
        
        # 格式化路径组合
        path_text = self._format_path_combination(path_combination)
        early_ending_notice = ""
        if is_premature:
            early_ending_notice = """
**重要（提前结局路径）**：本路径为提前结局——仅在以下分叉点做了选择即结束，**未经历**后续分叉点（如 E13、E14 等）。请**仅根据以下「实际经历的分支选择」**评估因果关系与人物一致性并选择结局，不要根据未发生的后续选择来选。
"""
        
        prompt = f"""你是一个文学分析助手，负责从相似的结局中选择最有文学张力的结局。
{early_ending_notice}
**路径组合**（本路径实际经历的选择）：
{path_text}

**最终状态**：
{states_text}

**相似的结局候选**：
{endings_text}

**任务**：
从这些相似的结局中选择最有文学张力的结局。

**重要**：当多个结局都与本路径逻辑一致时，应选择**与本路径的具体选择、人物走向最贴合**的那一个；不要仅因「悲剧性最强/张力最高」而总是选同一类结局——不同路径应能对应不同结局，以体现选择的分流意义。

**文学张力的评估标准**（须同时满足因果关系与人物一致性，再比较张力）：
1. **因果关系（硬性条件）**：结局必须是本路径中已出现的人物与事件的直接延续与收束，由路径选择与状态自然导致；若某结局主要讲「另一批人」或与前文无因果的支线，必须排除，不得入选。
2. **人物一致性**：结局应聚焦本路径核心人物，是对前文这些人的收束，而非突兀换角或另开故事线。
3. **与路径的契合度（优先）**：结局是否与**本路径**的具体选择、状态走向最贴合（而非泛泛的「最惨/最震撼」）。
4. **情感冲击力**：结局是否能引起强烈的情感反应（悲伤、震撼、满足等）
5. **主题深度**：结局是否深刻反映故事的主题和核心冲突
6. **冲突解决**：结局是否以有意义、令人满意的方式解决或深化冲突
7. **文学价值**：结局是否具有文学价值，不仅仅是功能性的结局

**输出格式（JSON）**：
{{
  "selected_ending_id": "选择的结局ID",
  "dramatic_reasons": [
    "原因1：为什么这个结局更有文学张力",
    "原因2：...",
    ...
  ],
  "literary_value": "这个结局的文学价值说明"
}}
"""
        
        # 调用 LLM 选择最有文学张力的结局
        response = await self.llm_client.invoke(
            prompt,
            return_json=True,
        )
        
        # 解析响应
        response = self._parse_llm_response(response)
        
        selected_id = response.get("selected_ending_id")
        dramatic_reasons = response.get("dramatic_reasons", [])
        literary_value = response.get("literary_value", "")
        
        # 查找选择的结局
        selected_ending = self._find_ending_by_id(similar_endings, selected_id) if selected_id else None
        
        if selected_ending:
            print(f"     文学张力原因: {', '.join(dramatic_reasons[:2])}...")
            if literary_value:
                print(f"     文学价值: {literary_value[:100]}...")
        else:
            print(f"  ⚠️  LLM 选择的结局ID {selected_id} 不在候选列表中，使用第一个候选")
            selected_ending = similar_endings[0] if similar_endings else None
        
        return selected_ending
    
    async def _select_ending_with_llm(
        self,
        candidates: List[Dict[str, Any]],
        final_states: Optional[UpdatedState],
        path_combination: List[Dict[str, Any]],
        is_premature: bool = False,
    ) -> Optional[Dict[str, Any]]:
        """
        使用 LLM 从多个候选结局中选择最符合最终状态且有文学张力的结局 🎯
        """
        # 格式化候选结局信息
        candidates_text = self._format_ending_candidates(candidates)
        
        # 格式化最终状态
        states_text = self._format_states_for_llm(final_states)
        if states_text:
            states_text = states_text.replace("当前分支状态（累积的状态）", "最终状态")
        
        # 格式化路径组合
        path_text = self._format_path_combination(path_combination)
        early_ending_notice = ""
        if is_premature:
            early_ending_notice = """
**重要（提前结局路径）**：本路径为提前结局——仅在以下分叉点做了选择即结束，**未经历**后续分叉点（如 E13、E14 等）。请**仅根据以下「实际经历的分支选择」**评估并选择结局，不要根据未发生的后续选择来选。
"""
        
        prompt = f"""你是一个结局选择助手，负责根据最终状态和路径组合，从多个候选结局中选择最符合且有文学张力的结局。
{early_ending_notice}
**路径组合**（本路径实际经历的选择）：
{path_text}

**最终状态**：
{states_text}

**候选结局**：
{candidates_text}

**任务**：
根据最终状态和路径组合，选择最符合且有文学张力的结局。考虑：
1. **因果关系（最重要）**：结局必须是本路径中已出现的人物与事件的直接延续与收束，由路径中的选择与状态自然导致；不得选择描述突然切换到「另一批人」或另一条故事线的结局——若某结局主要讲与前文无关的角色或支线，应排除。
2. **人物一致性**：优先选择聚焦于本路径核心人物的结局，结局应是对「前文这些人」的收束，而非另起炉灶。
3. **与本路径的贴合度（优先）**：应选择与**本路径**的具体选择、状态走向最贴合的结局，而非泛泛的「最惨/最震撼」；不同路径应能对应不同结局，避免所有路径都选成同一结局。
4. 结局的标签是否与最终状态匹配
5. 结局的描述是否与路径组合的逻辑一致
6. 结局的冲突标签是否与最终状态冲突
7. **文学张力**：结局是否具有情感冲击力、主题深度和文学价值

**输出格式（JSON）**：
{{
  "selected_ending_id": "选择的结局ID",
  "reasoning": "选择理由（包括匹配度和文学张力）"
}}
"""
        
        # 调用 LLM 选择结局
        response = await self.llm_client.invoke(
            prompt,
            return_json=True,
        )
        
        # 解析响应
        response = self._parse_llm_response(response)
        
        selected_id = response.get("selected_ending_id")
        reasoning = response.get("reasoning", "")
        
        # 查找选择的结局
        selected_ending = None
        for candidate in candidates:
            if candidate.get("id") == selected_id:
                selected_ending = candidate
                break
        
        if selected_ending:
            print(f"  ✅ LLM 选择结局: {selected_ending.get('name', '未知结局')} (ID: {selected_id})")
            print(f"     理由: {reasoning}")
        else:
            print(f"  ⚠️  LLM 选择的结局ID {selected_id} 不在候选列表中，使用第一个候选")
            selected_ending = candidates[0] if candidates else None
        
        return selected_ending
    
    async def _generate_mainline_event_description_after_merge(
        self,
        event_id: str,
        canonical_record: Dict[str, Any],
        current_states: Optional[Any],
        previous_event: Optional[Dict[str, Any]],
        target_ending: Dict[str, Any],
    ) -> Optional[Dict[str, Any]]:
        """
        为合流后的主线事件重新生成场景描述、原文片段与对话，使叙事导向已确定的目标结局。
        仅当先确定了 target_ending 时使用；失败时返回 None，调用方用 canonical 兜底。
        返回 dict：description（必填）、source_text（必填）、dialogue（可选，仅在有直接引语时填写）。
        """
        canonical_desc = canonical_record.get("description", "")
        if not canonical_desc:
            return None
        prev_desc = ""
        if previous_event:
            prev_desc = previous_event.get("description", "") or previous_event.get("event_data", {}).get("场景", "")
        states_text = self._format_states_for_llm(current_states) if current_states else ""
        character_relation_summary = self._build_character_relation_summary(current_states)
        ending_name = target_ending.get("name", "")
        ending_desc = (target_ending.get("description", "") or "")[:300]
        # 获取主线原文与对话（用于重写参考）
        extract_event = self.data_loader.get_extract_event(event_id) if getattr(self, "data_loader", None) else None
        original_source = ""
        original_dialogue_json = "[]"
        if extract_event:
            event_core = extract_event.get("事件", {})
            original_source = (event_core.get("source_text") or "")[:600]
            di = extract_event.get("对话信息", {}) or {}
            if di.get("has_dialogue") and di.get("dialogue_info", {}).get("dialogue_content"):
                import json as _json
                original_dialogue_json = _json.dumps(di["dialogue_info"]["dialogue_content"], ensure_ascii=False, indent=2)
        prompt = f"""你是一个叙事改写助手。合流后的主线事件需要根据**已确定的目标结局**重写场景描述、原文片段与对话（若有），使剧情自然导向该结局。

**目标结局**：{ending_name}
**结局概要**：{ending_desc}

**当前事件（主线原文 = 文风参考）**：
- 事件ID: {event_id}
- 原文描述: {canonical_desc}
- 原文片段（source_text）: {original_source[:500] if original_source else "（无）"}
- 原文对话（仅当有直接引语时存在）: {original_dialogue_json if original_dialogue_json != "[]" else "（无对话）"}

**文笔要求**：请**严格模仿**上方案「当前事件」原文的用词、句式与节奏；允许重写内容以衔接目标结局，但必须保持与原文一致的文风，不得改用套话、口语或与原文风格不符的表达。

**上一事件概要**（用于衔接）：
{prev_desc[:400] if prev_desc else "（无）"}

**人物与关系**（不得改变人物身份与人物关系）：
{character_relation_summary if character_relation_summary else "（见下方状态）"}

**当前状态概要**（用于语气与走向）：
{states_text[:800] if states_text else "（无）"}

**任务**：在保持本事件核心信息（谁、做了什么、结果）且**不改变人物身份与关系**的前提下，用与原文一致的文风重写：
1. description：一段 100～400 字的场景描述，使叙事走向与目标结局一致。
2. source_text：一段与描述风格一致的原文叙述（200～500 字），可与 description 呼应或略扩展。
3. dialogue：**仅当本事件确有直接引语对话时**填写，格式 [{{"speaker":"角色名","text":"引语内容","tone":"语气"}}]；若无对话则填空数组 []。

**输出格式（JSON）**：
{{ "description": "重写后的场景描述", "source_text": "重写后的原文片段", "dialogue": [] 或 [{{"speaker":"","text":"","tone":""}}] }}
"""
        def _parse(resp: Any) -> Optional[Dict[str, Any]]:
            resp = self._parse_llm_response(resp)
            desc = resp.get("description") or resp.get("scene_description")
            if not desc or not isinstance(desc, str) or not desc.strip():
                return None
            out = {"description": desc.strip()}
            if resp.get("source_text") and isinstance(resp["source_text"], str) and resp["source_text"].strip():
                out["source_text"] = resp["source_text"].strip()
            dl = resp.get("dialogue")
            if isinstance(dl, list) and len(dl) > 0:
                out["dialogue"] = []
                for item in dl:
                    if isinstance(item, dict) and (item.get("text") or item.get("content")):
                        out["dialogue"].append({
                            "speaker": item.get("speaker", ""),
                            "text": item.get("text") or item.get("content", ""),
                            "tone": item.get("tone", ""),
                        })
            else:
                out["dialogue"] = []
            return out

        system_block = _get_story_system_prompt()
        full_prompt = (system_block + "\n\n---\n\n" + prompt) if system_block else prompt
        try:
            response = await self.llm_client.invoke(full_prompt, return_json=True)
            return _parse(response)
        except Exception as e:
            logger.warning(f"⚠️  合流后主线事件 {event_id} 描述生成失败: {e}")
        return None
    
    async def _check_reached_target_ending(
        self,
        current_states: Optional[Any],
        last_mainline_event: Dict[str, Any],
        target_ending: Dict[str, Any],
    ) -> bool:
        """
        判断当前状态与刚发生的事件是否已经抵达目标结局；若已抵达则合流后主线可提前结束。
        """
        event_id = last_mainline_event.get("event_id", "")
        desc = last_mainline_event.get("description", "")[:500]
        ending_name = target_ending.get("name", "")
        ending_desc = (target_ending.get("description", "") or "")[:200]
        states_text = self._format_states_for_llm(current_states) if current_states else ""
        prompt = f"""你是一个剧情判断助手。请判断：在**当前状态**和**刚发生的事件**下，故事是否已经**抵达**目标结局？

**目标结局**：{ending_name}
**结局含义**：{ending_desc}

**刚发生的事件**（{event_id}）：
{desc}

**当前状态概要**：
{states_text[:600] if states_text else "（无）"}

**任务**：若剧情已经自然收束到该结局（情感/关系/冲突已到位，再往后写会冗余），回答 reached=true；否则回答 reached=false。只做二选一。

**输出格式（JSON）**：
{{ "reached": true 或 false, "reason": "一句话理由" }}
"""
        try:
            response = await self.llm_client.invoke(prompt, return_json=True)
            response = self._parse_llm_response(response)
            reached = response.get("reached", False)
            if isinstance(reached, bool) and reached:
                logger.info(f"  🎯 判定已抵达目标结局「{ending_name}」于事件 {event_id}，提前结束合流后主线")
                return True
        except Exception as e:
            logger.warning(f"⚠️  抵达目标结局判断失败: {e}")
        return False
    
    async def process_mainline_after_merge(
        self,
    ) -> Dict[str, Any]:
        """
        合流后处理主线事件直到结局，并选择结局 🔄🎯
        
        在合流后调用，处理合流点之后的所有主线事件，得到最终状态 snapshot，
        然后从 ending_candidates.json 中选择结局。
        
        Returns:
            包含主线事件处理结果和选择的结局
        """
        if not self.merge_point:
            print(f"  ⚠️  没有合流点，无法处理主线事件")
            return {
                "mainline_events": [],
                "final_states": self._convert_states_to_dict(self.current_states),
                "selected_ending": None,
            }
        
        print(f"  🔄 合流后继续处理主线事件直到结局...")
        
        # 获取所有主线事件ID
        all_event_ids = self._get_all_event_ids()
        
        try:
            merge_index = all_event_ids.index(self.merge_point)
            remaining_event_ids = all_event_ids[merge_index + 1:]  # 合流点之后的事件
        except ValueError:
            print(f"  ⚠️  找不到合流点 {self.merge_point} 在事件列表中")
            return {
                "mainline_events": [],
                "final_states": self._convert_states_to_dict(self.current_states),
                "selected_ending": None,
            }
        
        if self.skipped_mainline_events:
            print(f"  ⏭️  分支事件替代了以下主线事件: {self.skipped_mainline_events}")
        
        print(f"  📊 需要处理 {len(remaining_event_ids)} 个主线事件（从 {self.merge_point} 之后到结局）")
        
        # 🔧 先得到合流点状态的 UpdatedState，供选结局和后续循环使用
        current_states_raw = self.current_states
        if isinstance(current_states_raw, dict):
            from state_manager.models import UpdatedState
            try:
                current_states = UpdatedState(**current_states_raw)
            except Exception as e:
                logger.warning(f"⚠️  无法将 current_states 字典转换为 UpdatedState 对象: {e}，使用字典格式")
                current_states = current_states_raw
        else:
            current_states = current_states_raw
        
        # 🎯 仅当「非提前结局」且「当前路径已在全部分叉点做过选择」时，先确定结局，再让后续事件导向该结局
        # 分支选择次数 = 故事中的分支点数量（decision_analysis.branch_points）
        # 当前路径选择次数 = len(path_combination)，含主线选择与分支选择（每个分叉点选主线或分支都算一次）
        branch_points_count = len(self.decision_analysis.get("branch_points", [])) if self.decision_analysis else 0
        path_choices_count = len(self.path_combination)
        target_ending = None
        if self.is_early_ending:
            print(f"  ⏸️  提前结局路径，不先确定结局，最后再选")
        elif branch_points_count == 0 or path_choices_count < branch_points_count:
            print(f"  ⏸️  当前路径选择次数 {path_choices_count} < 分支点数量 {branch_points_count}，不先确定结局，最后再选")
        else:
            target_ending = await self.select_ending_from_candidates(
                path_combination=self.path_combination,
                final_states=current_states,
            )
            if target_ending:
                print(f"  ✅ 已先确定目标结局: {target_ending.get('name', '未知')} (ID: {target_ending.get('id', 'unknown')})，后续事件将导向该结局")
            else:
                print(f"  ⚠️  未找到匹配的结局，将按原逻辑在最后一事件再尝试选择")
        
        # 处理合流点之后的主线事件（事件生成时可参考 target_ending，使剧情导向该结局）
        mainline_events = []
        
        # 先确定结局，后续事件导向该结局（若未先选则 selected_ending 为 None，最后一事件再选）
        selected_ending = target_ending

        # ── 合流增量包：从分叉点开始累积的“净变化摘要” ─────────────────────────
        # A 方案：仅由“分支事件 + 合流后主线事件”的 state_changes_to_apply 累积得到。
        delta_raw: Dict[str, Dict[str, Any]] = {}

        def _delta_key_from_sc(sc: Dict[str, Any]) -> Optional[str]:
            tt = (sc.get("target_type") or "").strip()
            tid = sc.get("target_id")
            dim = (sc.get("dimension") or "").strip()
            if not tt or not dim:
                return None
            if tt == "relationship":
                if isinstance(tid, list) and len(tid) >= 2:
                    rel_key = f"{tid[0]}|{tid[1]}"
                else:
                    rel_key = str(tid or "").strip()
                if not rel_key:
                    return None
                return f"relationship::{rel_key}::{dim}"
            if tt in ("character", "world"):
                tid_s = str(tid or "").strip()
                if not tid_s:
                    return None
                return f"{tt}::{tid_s}::{dim}"
            tid_s = str(tid or "").strip()
            if not tid_s:
                return None
            return f"{tt}::{tid_s}::{dim}"

        def _delta_update(delta: Dict[str, Dict[str, Any]], sc_list: List[Dict[str, Any]], *, source_event: str) -> None:
            for sc in sc_list or []:
                if not isinstance(sc, dict):
                    continue
                k = _delta_key_from_sc(sc)
                if not k:
                    continue
                delta[k] = {
                    "target_type": sc.get("target_type"),
                    "target_id": sc.get("target_id"),
                    "dimension": sc.get("dimension"),
                    "value": sc.get("value"),
                    "last_source_event": source_event,
                }

        def _delta_to_text(delta: Dict[str, Dict[str, Any]], *, limit: int = 8) -> str:
            if not delta:
                return ""
            items = list(delta.values())
            prio = {"relationship": 0, "character": 1, "world": 2}

            def _score(it: Dict[str, Any]) -> Tuple[int, int]:
                tt = (it.get("target_type") or "").strip()
                return (prio.get(tt, 9), 0)

            items.sort(key=_score)
            lines: List[str] = []
            for it in items[:limit]:
                tt = (it.get("target_type") or "").strip()
                tid = it.get("target_id")
                dim = (it.get("dimension") or "").strip()
                val = it.get("value")
                if isinstance(val, str):
                    val = val.strip()
                src = (it.get("last_source_event") or "").strip()
                if tt == "relationship" and isinstance(tid, list) and len(tid) >= 2:
                    tid_s = f"{tid[0]}|{tid[1]}"
                else:
                    tid_s = str(tid)
                lines.append(f"- [{src}] {tt} {tid_s} · {dim} → {val}")
            return "\n".join(lines)

        # 初始化 delta：把分支事件里已有的 state_changes 先折叠进去
        for be in (self.branch_events or []):
            if not isinstance(be, dict):
                continue
            scs = be.get("state_changes") or []
            if isinstance(scs, list) and scs:
                _delta_update(delta_raw, scs, source_event=str(be.get("event_id") or "branch"))

        delta_text = _delta_to_text(delta_raw)

        # 把 path_combination 里“确实发生了分叉”的事件点，在合流后的主线 event 上显式标注为 decision_point。
        # 这样 formatter 才能识别并生成 player_choice（用于 UI 插入玩家选项）。
        decision_point_by_fork: Dict[str, Any] = {}
        for ch in self.path_combination or []:
            if not isinstance(ch, dict):
                continue
            fid = ch.get("fork_event_id")
            choice = ch.get("branch_choice")
            if not (isinstance(fid, str) and fid.strip() and choice is not None):
                continue

            # 只有 branches.json 里确实存在该 (fork_event_id, branch_choice) 的记录，
            # 才把它标为 decision_point；否则 UI 会拿不到 options/jump_target。
            fid_s = fid.strip()
            choice_s = choice if isinstance(choice, str) else str(choice)
            if self.data_loader:
                branch_data = self.data_loader.get_branch_data(fid_s, choice_s)
                if not branch_data:
                    continue

            decision_point_by_fork[fid_s] = choice_s
        
        for idx, event_id in enumerate(remaining_event_ids, 1):
            print(f"  📝 [{idx}/{len(remaining_event_ids)}] 处理主线事件: {event_id}")
            # 如果这个事件被标记为"跳过"，则跳过它（分支事件已经替代了它）
            if event_id in self.skipped_mainline_events:
                print(f"  ⏭️  跳过主线事件 {event_id}（已被分支事件替代）")
                mainline_events.append({
                    "event_id": event_id,
                    "description": f"主线事件 {event_id}（已跳过，被分支事件替代）",
                    "is_skipped": True,
                    "reason": "被分支事件替代",
                })
                continue
            
            # 获取主线事件记录
            canonical_record = None
            for record in self.canonical_branch.get("processed_events", []):
                if record.get("event_id") == event_id:
                    canonical_record = record
                    break
            
            if not canonical_record:
                continue
            
            # 检查是否是关键事件
            is_critical = self.critical_events and event_id in self.critical_events if self.critical_events else False
            
            # 🚀 优化方案：混合策略
            # - 关键事件：重新生成状态变化（保持准确性）
            # - 非关键事件：直接使用主线记录的状态变化（快速）
            # - 所有事件：直接应用状态变化（不需要 LLM function calling，更快）
            snapshot = None
            if self.state_applier:
                # 获取主线记录中的状态变化
                canonical_state_changes = []
                if canonical_record.get("selected_state_changes"):
                    all_state_changes = canonical_record.get("state_changes", [])
                    selected_indices = canonical_record.get("selected_state_changes", [])
                    
                    for sc_idx in selected_indices:
                        if isinstance(sc_idx, int) and 0 <= sc_idx < len(all_state_changes):
                            sc = all_state_changes[sc_idx].copy()
                            canonical_state_changes.append({
                                k: v for k, v in sc.items() if not k.startswith("_")
                            })
                        elif isinstance(sc_idx, dict):
                            canonical_state_changes.append(sc_idx)
                
                # 决定使用哪种策略
                state_changes_to_apply = []
                
                if is_critical and self.state_change_generator:
                    # 关键事件：重新生成状态变化（保持准确性）
                    print(f"    🔄 为关键事件 {event_id} 重新生成状态变化（基于分支路径后的新状态）...")

                    # 🔧 正确获取上一个事件
                    previous_event = None
                    if mainline_events:
                        # 有已处理的主线事件，使用最后一个主线事件
                        previous_event = mainline_events[-1]
                    elif self.branch_events:
                        # 没有已处理的主线事件，但有分支事件，使用最后一个分支事件
                        previous_event = self.branch_events[-1]
                        print(f"    📋 上一个事件是分支事件: {previous_event.get('event_id', 'unknown')}")

                    try:
                        event_info = await self.state_applier.get_event_info(event_id)
                        if event_info:
                            state_changes_to_apply = await self.state_change_generator.generate_for_mainline_event_after_merge(
                                event_id=event_id,
                                event_info=event_info,
                                current_states=current_states,
                                canonical_state_changes=canonical_state_changes,
                                previous_event=previous_event,
                                delta_since_fork_text=delta_text,
                                target_ending=target_ending,
                            )
                    except Exception as e:
                        logger.warning(f"⚠️  为 {event_id} 生成状态变化时出错: {e}")
                        state_changes_to_apply = []

                    if not state_changes_to_apply:
                        print(f"    ⚠️  {event_id} 重新生成失败，使用主线记录中的状态变化")
                        state_changes_to_apply = canonical_state_changes
                else:
                    # 非关键事件：直接使用主线记录的状态变化（快速）
                    state_changes_to_apply = canonical_state_changes
                    if state_changes_to_apply:
                        print(
                            f"    ⚡ 非关键事件 {event_id}，直接使用主线记录中的 {len(state_changes_to_apply)} 个状态变化（快速路径）"
                        )
                
                # 🚀 直接应用状态变化（不需要 LLM function calling，更快）
                if state_changes_to_apply:
                    try:
                        current_states, snapshot = self.state_applier.apply_state_changes_directly(
                            selected_state_changes=state_changes_to_apply,
                            current_states=current_states,
                            event_id=event_id,
                        )
                        print(f"    ✅ {event_id} 状态变化已直接应用（{len(state_changes_to_apply)} 个状态变化）")
                    except Exception as e:
                        logger.warning(f"⚠️  直接应用 {event_id} 的状态变化时出错: {e}")
                        print(f"    ⚠️  {event_id} 状态变化应用失败，跳过状态更新")
                else:
                    print(f"    ⚠️  {event_id} 没有可应用的状态变化")

                # 将“本事件实际应用的状态变化”折叠进 delta（供下一个事件使用）
                if isinstance(state_changes_to_apply, list) and state_changes_to_apply:
                    _delta_update(delta_raw, state_changes_to_apply, source_event=event_id)
                    delta_text = _delta_to_text(delta_raw)
            
            # 方案2：若已先确定目标结局，为合流后所有主线事件重新生成描述、source_text、dialogue，使叙事导向该结局
            description_to_use = canonical_record.get("description", "")
            # 默认使用重排后主线里的 source_text（与 canonical_branch_chronological 一致）
            source_text_to_use = canonical_record.get("source_text") or None
            dialogue_to_use = None
            if target_ending:
                previous_event_for_desc = mainline_events[-1] if mainline_events else (self.branch_events[-1] if self.branch_events else None)
                generated = await self._generate_mainline_event_description_after_merge(
                    event_id=event_id,
                    canonical_record=canonical_record,
                    current_states=current_states,
                    previous_event=previous_event_for_desc,
                    target_ending=target_ending,
                )
                if generated:
                    description_to_use = generated.get("description", description_to_use)
                    if generated.get("source_text") is not None:
                        source_text_to_use = generated["source_text"]
                    if generated.get("dialogue") is not None:
                        dialogue_to_use = generated["dialogue"]
                    print(f"    📖 {event_id} 已重写描述（导向目标结局）" + (" + source_text/dialogue" if (source_text_to_use is not None or dialogue_to_use is not None) else ""))
            
            # 🔧 关键修复：创建完整的事件对象，包含 state_changes 以便格式化时使用
            mainline_event = {
                "event_id": event_id,
                "description": description_to_use,
                "canonical_record": canonical_record,
                "is_mainline_event": True,
                "is_critical": is_critical,
                "snapshot": snapshot.to_dict_for_llm() if snapshot else None,
            }

            # 若该主线 event_id 在 path_combination 中是某个 fork_event_id，
            # 则补充决策点字段：decision_point + canonical_choice。
            # 这样后续 formatter 才能把它识别成 decision_point 并生成 player_choice。
            if isinstance(event_id, str) and event_id in decision_point_by_fork:
                mainline_event["decision_point"] = event_id
                mainline_event["canonical_choice"] = decision_point_by_fork[event_id]
            if source_text_to_use is not None:
                mainline_event["source_text"] = source_text_to_use
            if dialogue_to_use is not None:
                mainline_event["dialogue"] = dialogue_to_use
            
            # 🔧 关键修复：添加 state_changes 字段，以便格式化时能正确提取
            if state_changes_to_apply:
                # 将状态变化列表转换为字典格式（用于格式化）
                mainline_event["selected_state_changes"] = state_changes_to_apply
                # 同时保留完整的 state_changes 列表（从 canonical_record 中获取）
                if canonical_record.get("state_changes"):
                    mainline_event["state_changes"] = canonical_record.get("state_changes")
            
            mainline_events.append(mainline_event)
            
            # 若已先确定目标结局：判断是否已抵达该结局，抵达则提前结束合流后主线
            if target_ending:
                if await self._check_reached_target_ending(current_states, mainline_event, target_ending):
                    mainline_events[-1]["selected_ending"] = target_ending
                    self.current_states = current_states
                    selected_ending = target_ending
                    print(f"  ✅ 已抵达目标结局「{target_ending.get('name', '')}」，提前结束（后续主线事件不再处理）")
                    break
            
            # 检查是否是结局事件（通常是最后一个事件）：挂上先确定的目标结局
            if event_id == all_event_ids[-1]:
                print(f"  ✅ 到达结局事件: {event_id}")
                self.current_states = current_states
                if selected_ending:
                    mainline_events[-1]["selected_ending"] = selected_ending
                    print(f"  ✅ 结局已确定: {selected_ending.get('name', '未知结局')}（与先确定的目标结局一致）")
                else:
                    # 兜底：若之前未选到，再尝试按最终状态选一次
                    selected_ending = await self.select_ending_from_candidates(
                        path_combination=self.path_combination,
                        final_states=current_states,
                    )
                    if selected_ending:
                        mainline_events[-1]["selected_ending"] = selected_ending
                        print(f"  ✅ 选择结局: {selected_ending.get('name', '未知结局')}")
                    else:
                        print(f"  ⚠️  未找到匹配的结局")
        
        # 保存主线事件和选择的结局到实例变量，供 get_path_result 使用
        self._mainline_events = mainline_events
        self._selected_ending = selected_ending
        
        print(f"  🔍 [DEBUG] process_mainline_after_merge 返回:")
        print(f"        - mainline_events 数量: {len(mainline_events)}")
        if mainline_events:
            print(f"        - mainline_events 前3个事件的 event_id: {[e.get('event_id') for e in mainline_events[:3]]}")
            print(f"        - mainline_events 最后3个事件的 event_id: {[e.get('event_id') for e in mainline_events[-3:]]}")
        print(f"        - self._mainline_events 已设置: {hasattr(self, '_mainline_events')}")
        if hasattr(self, '_mainline_events'):
            print(f"        - self._mainline_events 数量: {len(self._mainline_events) if isinstance(self._mainline_events, list) else 'N/A'}")
        
        return {
            "mainline_events": mainline_events,
            "final_states": current_states.model_dump() if hasattr(current_states, "model_dump") else (current_states.dict() if current_states else {}),
            "selected_ending": selected_ending,
        }
    
    async def generate_path_with_function_calling(
        self,
        fork_event_id: str,
        branch_choice: Dict[str, Any],
        current_states: UpdatedState,
        max_branch_length: Optional[int] = None,
        path_combination: Optional[List[Dict[str, Any]]] = None,
        enable_human_in_the_loop: bool = False,  # 人机协作开关 ✋
    ) -> Dict[str, Any]:
        """
        使用 function calling 生成完整路径 🛠️✨
        
        这是主要的入口方法，使用 LangGraph 协调 LLM 的 function calling 来完成路径生成。
        
        Args:
            fork_event_id: 分叉事件ID
            branch_choice: 分支选择
            current_states: 当前状态
            max_branch_length: 最大分支事件条数；``None`` 表示不设上限（仍受 LangGraph ``recursion_limit`` 约束）
            path_combination: 路径组合（用于匹配 ending_candidates）
            
        Returns:
            完整的路径生成结果
            
        Raises:
            ImportError: 如果 LangGraph 未安装
        """
        if not LANGGRAPH_AVAILABLE:
            raise ImportError(
                "LangGraph 未安装，请运行: pip install langgraph>=0.2.0\n"
                "路径生成功能需要 LangGraph 支持。"
            )
        
        return await self._generate_path_with_langgraph(
            fork_event_id=fork_event_id,
            branch_choice=branch_choice,
            current_states=current_states,
            max_branch_length=max_branch_length,
            path_combination=path_combination,
            enable_human_in_the_loop=enable_human_in_the_loop,
        )
    
    async def _generate_path_with_langgraph(
        self,
        fork_event_id: str,
        branch_choice: Dict[str, Any],
        current_states: UpdatedState,
        max_branch_length: Optional[int] = None,
        path_combination: Optional[List[Dict[str, Any]]] = None,
        enable_human_in_the_loop: bool = False,  # 人机协作开关 ✋
    ) -> Dict[str, Any]:
        """
        使用 LangGraph 生成路径 🎨
        """
        from .langgraph.workflow import create_path_generation_graph
        from .langgraph import LANGGRAPH_AVAILABLE as LG_AVAILABLE
        
        if not LG_AVAILABLE:
            raise ImportError(
                "LangGraph 未安装，请运行: pip install langgraph>=0.2.0\n"
                "路径生成功能需要 LangGraph 支持。"
            )
        
        # 获取合流点选项
        merge_options_raw = self.get_merge_options(fork_event_id, max_distance=5)
        merge_options = [opt["event_id"] for opt in merge_options_raw]
        
        print(f"🌿 开始使用 LangGraph 生成分支事件链（分叉点: {fork_event_id}）...")
        if merge_options_raw:
            print(f"   合流点选项（主线事件）:")
            for opt in merge_options_raw:
                critical_mark = " [关键事件]" if opt["is_critical"] else ""
                print(f"     - {opt['event_id']} (距离: {opt['distance']}){critical_mark}")
        
        # 初始化路径上下文
        self.initialize_path_context(
            fork_event_id=fork_event_id,
            branch_choice=branch_choice,
            current_states=current_states,
            merge_options=merge_options,
            path_combination=path_combination or [],
        )
        
        # 创建 LangGraph 工作流
        workflow = create_path_generation_graph(self)
        if not workflow:
            raise RuntimeError("无法创建 LangGraph 工作流，请检查 LangGraph 是否正确安装。")
        
        # 导入状态定义（使用 LangGraph 文件中统一的状态结构）
        from .langgraph.state import PathGenerationState
        
        # 优化：提前查找匹配的结局候选，设置到初始状态中（避免重复查找）✨
        matching_path = self._find_matching_path_result(path_combination or [])
        ending_candidates = []
        if matching_path:
            candidate_details = matching_path.get("candidate_details", [])
            # 只包含提前结局（is_premature=True）
            ending_candidates = [
                detail for detail in candidate_details
                if detail.get("is_premature", False)
            ]
        
        # 🔧 调试信息：记录设置的初始状态
        logger.info(f"🔍 _generate_path_with_langgraph 设置初始状态: fork_event_id={fork_event_id}, branch_choice={branch_choice.get('choice_id', 'unknown') if isinstance(branch_choice, dict) else 'unknown'}")
        
        # 初始化状态（使用 LangGraph 的状态结构）
        initial_state: PathGenerationState = {
            "fork_event_id": fork_event_id,
            "branch_choice": branch_choice,
            "current_states": current_states,
            "branch_events": [],
            "merge_point": None,
            "is_early_ending": False,
            "ending_event": None,
            "skipped_mainline_events": [],
            "merge_options": merge_options,
            "merge_options_raw": merge_options_raw,
            "path_combination": path_combination or [],
            "max_branch_length": max_branch_length,
            "iteration": 0,
            "max_iterations": (
                None if max_branch_length is None else (max_branch_length + 2)
            ),
            "mainline_events": [],
            "final_states_snapshot": None,
            "ending_candidates": ending_candidates,  # 优化：提前设置结局候选 ✨
            "similar_ending_groups": [],
            "merged_endings": [],
            "selected_ending": None,
            "messages": [],
            "can_merge": None,
            "should_continue_generating": True,
            "decision": None,
            "conversation_summary": None,  # 记忆管理
            "conversation_key_points": [],
            "max_messages_before_summary": 10,  # 默认 10 条消息后触发总结
            "enable_human_in_the_loop": enable_human_in_the_loop,  # 人机协作开关 ✋
        }
        
        # 运行工作流（使用检查点支持）
        config = {
            # 不设分支条数上限时，单路径可能多轮「决策→工具→生成」，提高上限避免误触顶
            "recursion_limit": 320 if max_branch_length is None else 100,
            "configurable": {
                "thread_id": f"path_{fork_event_id}_{branch_choice.get('choice_id', 'unknown')}"
            },
        }
        
        final_state = None
        try:
            # 使用 astream 运行工作流（支持中断和恢复）
            # LangGraph 会自动管理状态流转，所有节点都调用 PathGenerationFunctions 的方法
            print("  📊 LangGraph 工作流开始执行（流式输出）...")
            node_count = 0
            current_state = initial_state.copy()
            
            # astream 返回字典：{节点名: 状态更新}
            async for node_updates in workflow.astream(initial_state, config, stream_mode="values"):
                # node_updates 是字典，键是节点名，值是状态更新
                for node_name, node_state_update in node_updates.items():
                    node_count += 1
                    
                    # 合并状态更新到当前状态
                    if isinstance(node_state_update, dict):
                        current_state.update(node_state_update)
                        final_state = current_state
                    
                    # 显示当前执行的节点和状态 🎯
                    iteration = current_state.get("iteration", 0) if isinstance(current_state, dict) else 0
                    branch_events_count = len(current_state.get("branch_events", [])) if isinstance(current_state, dict) else 0
                    decision = current_state.get("decision", "unknown") if isinstance(current_state, dict) else "unknown"
                    
                    # 显示消息数量（用于监控上下文长度）
                    messages_count = len(current_state.get("messages", [])) if isinstance(current_state, dict) else 0
                    summary = current_state.get("conversation_summary") if isinstance(current_state, dict) else None
                    hitl_enabled = current_state.get("enable_human_in_the_loop", False) if isinstance(current_state, dict) else False
                    
                    # 构建状态显示
                    status_parts = [
                        f"迭代: {iteration}",
                        f"分支事件: {branch_events_count}",
                        f"决策: {decision}",
                        f"消息数: {messages_count}",
                    ]
                    if hitl_enabled:
                        status_parts.append("✋ 人机协作: 开启")
                    
                    print(f"    [{node_count}] 节点: {node_name} | {' | '.join(status_parts)}")
                    
                    # 如果刚刚进行了总结，显示总结信息
                    if summary and node_name == "summarize_history":
                        print(f"      🧠 已总结历史对话: {summary[:80]}...")
                    
                    # 如果启用了人机协作，在关键节点显示提示
                    if hitl_enabled:
                        if node_name == "process_mainline":
                            merge_point = current_state.get("merge_point") if isinstance(current_state, dict) else None
                            if merge_point:
                                print(f"      ✋ 等待用户确认合流点: {merge_point}")
                        elif node_name == "select_dramatic_ending":
                            print(f"      ✋ 等待用户确认最终结局选择")
                    
                    # 显示详细状态（如果有关键变化）
                    if isinstance(current_state, dict):
                        if current_state.get("merge_point"):
                            print(f"      ✅ 合流点: {current_state.get('merge_point')}")
                        if current_state.get("is_early_ending"):
                            print(f"      ⏸️  提前结局触发")
                        if current_state.get("selected_ending"):
                            ending = current_state.get("selected_ending", {})
                            if isinstance(ending, dict):
                                print(f"      🎭 选择结局: {ending.get('name', ending.get('id', 'unknown'))}")
                
                    # 从 LangGraph 状态同步到实例（用于后续访问）
                if isinstance(current_state, dict):
                    # 🔧 关键修复：只有当 state 中的 branch_events 不为空时，才更新实例
                    state_branch_events = current_state.get("branch_events")
                    if state_branch_events is not None:
                        if isinstance(state_branch_events, list) and len(state_branch_events) > 0:
                            # 只有当 state 中的 branch_events 不为空时，才更新实例
                            self.branch_events = state_branch_events
                        elif isinstance(state_branch_events, list) and len(state_branch_events) == 0:
                            # 如果 state 中的 branch_events 是空列表，且实例中已有分支事件，不覆盖
                            if len(self.branch_events) == 0:
                                self.branch_events = []
                        else:
                            self.branch_events = state_branch_events
                    # 如果 state 中没有 branch_events，保持实例中的值不变
                    if "current_states" in current_state:
                        self.current_states = current_state.get("current_states", self.current_states)
                    # 🔧 合流点：仅当 state 中有非空值时才覆盖，避免被后续节点的 None 覆盖
                    state_merge_point = current_state.get("merge_point")
                    if state_merge_point is not None:
                        self.merge_point = state_merge_point
                    # 若 state 为 None 且实例已有合流点，不覆盖
                    if "is_early_ending" in current_state:
                        self.is_early_ending = current_state.get("is_early_ending", self.is_early_ending)
                    if "skipped_mainline_events" in current_state:
                        self.skipped_mainline_events = current_state.get("skipped_mainline_events", self.skipped_mainline_events)
                    if "selected_ending" in current_state:
                        self._selected_ending = current_state.get("selected_ending")
                    # 🔧 合流后主线事件：仅当 state 中非空时覆盖，避免被后续节点的空列表覆盖
                    state_mainline = current_state.get("mainline_events")
                    if state_mainline is not None and isinstance(state_mainline, list) and len(state_mainline) > 0:
                        self._mainline_events = state_mainline
                    elif state_mainline is not None and isinstance(state_mainline, list) and len(state_mainline) == 0:
                        if not (hasattr(self, "_mainline_events") and isinstance(getattr(self, "_mainline_events", None), list) and len(self._mainline_events) > 0):
                            self._mainline_events = []
                    # 若 state 无 mainline_events 键，不改动实例
            
            print(f"  ✅ LangGraph 工作流完成（共执行 {node_count} 个节点）")
            
            # 🔍 DEBUG：检查 final_state 中的 mainline_events
            if final_state and isinstance(final_state, dict):
                final_mainline_events = final_state.get("mainline_events", [])
                print(f"  🔍 [DEBUG] final_state 中的 mainline_events: {len(final_mainline_events)} 个事件")
                if final_mainline_events:
                    print(f"  🔍 [DEBUG] final_state.mainline_events 前3个事件的 event_id: {[e.get('event_id') if isinstance(e, dict) else 'N/A' for e in final_mainline_events[:3]]}")
                    print(f"  🔍 [DEBUG] final_state.mainline_events 最后3个事件的 event_id: {[e.get('event_id') if isinstance(e, dict) else 'N/A' for e in final_mainline_events[-3:]]}")
            
            # 🔧 修复：如果 self.branch_events 为空，尝试从 final_state 中获取
            if not self.branch_events and final_state and isinstance(final_state, dict):
                final_branch_events = final_state.get("branch_events", [])
                if final_branch_events:
                    self.branch_events = final_branch_events
                    print(f"  ✅ 已从 final_state 恢复 branch_events")
            
            # 🔧 修复：如果 self._mainline_events 为空，尝试从 final_state 中获取
            if (not hasattr(self, '_mainline_events') or not self._mainline_events) and final_state and isinstance(final_state, dict):
                final_mainline_events = final_state.get("mainline_events", [])
                if final_mainline_events:
                    self._mainline_events = final_mainline_events
                    print(f"  ✅ 已从 final_state 恢复 mainline_events（{len(final_mainline_events)} 个事件）")
        except Exception as e:
            # LangGraph 的错误处理
            print(f"  ❌ LangGraph 工作流错误: {e}")
            # 可以尝试从检查点恢复
            # state = await workflow.aget_state(config)
            # if state and state.values:
            #     # 恢复状态
            #     pass
            raise
        
        # 如果合流了，识别被跳过的主线事件
        if self.merge_point:
            skipped_mainline_events = await self.identify_skipped_mainline_events(
                fork_event_id=fork_event_id,
                branch_events=self.branch_events,
                merge_point=self.merge_point,
            )
            all_skipped = list(set(self.skipped_mainline_events + skipped_mainline_events))
            self.skipped_mainline_events = all_skipped
        
        # 🔧 调试：在返回结果前再次检查
        # 返回结果
        return self.get_path_result()
    
    def get_path_result(self) -> Dict[str, Any]:
        """
        获取路径生成结果 📊
        """
        result = {
            "branch_events": self.branch_events.copy() if self.branch_events else [],  # 🔧 使用 copy() 避免引用问题
            "merge_point": self.merge_point,
            "is_early_ending": self.is_early_ending,
            "ending_event": self.ending_event,
            "skipped_mainline_events": self.skipped_mainline_events.copy() if self.skipped_mainline_events else [],
            "final_states": self.current_states.model_dump() if hasattr(self.current_states, "model_dump") else (self.current_states.dict() if self.current_states else {}),
        }
        
        # 如果合流了，添加主线事件和选择的结局（这些信息在 process_mainline_after_merge 中设置）
        print(f"  🔍 [DEBUG] get_path_result:")
        print(f"        - hasattr(self, '_mainline_events'): {hasattr(self, '_mainline_events')}")
        if hasattr(self, '_mainline_events'):
            mainline_events_count = len(self._mainline_events) if isinstance(self._mainline_events, list) else 0
            print(f"        - self._mainline_events 数量: {mainline_events_count}")
            if mainline_events_count > 0:
                print(f"        - self._mainline_events 前3个事件的 event_id: {[e.get('event_id') if isinstance(e, dict) else 'N/A' for e in self._mainline_events[:3]]}")
            result["mainline_events"] = self._mainline_events
        else:
            print(f"        - ⚠️  self._mainline_events 不存在！")
        
        if hasattr(self, '_selected_ending'):
            result["selected_ending"] = self._selected_ending
        
        print(f"  🔍 [DEBUG] get_path_result 返回:")
        print(f"        - result.get('mainline_events') 数量: {len(result.get('mainline_events', []))}")
        
        return result
    
    # ========== 遍历9条路径的方法 🎯 ==========
    
    async def process_all_paths(
        self,
        output_format: str = "story_content",  # "story_content" | "raw"
        worker_pool: Optional[List["PathGenerationFunctions"]] = None,
        max_concurrent: Optional[int] = None,
    ) -> List[Dict[str, Any]]:
        """
        处理所有路径 🎯
        
        遍历 ending_candidates.json 中的所有路径，为每条路径生成完整内容。
        若提供 worker_pool（多个实例），分支路径会并发处理以加速。
        
        Args:
            output_format: 输出格式（"story_content" 适配 story_content_format_spec.md）
            worker_pool: 用于并发处理分支路径的实例列表；None 或仅 1 个则分支路径顺序执行
            max_concurrent: 同时运行的分支路径上限（Semaphore）；None 则等于 len(worker_pool)。可小于 worker 数以限流
        Returns:
            所有路径的处理结果列表
        """
        results = []
        
        # 1. 分类路径
        canonical_paths = [p for p in self.ending_candidates if p.get("is_canonical", False)]
        branch_paths = [p for p in self.ending_candidates if not p.get("is_canonical", False)]
        
        print(f"📋 开始处理 {len(canonical_paths)} 条主线路径和 {len(branch_paths)} 条分支路径...")
        
        # 2. 处理主线路径（简单，直接使用已有数据，顺序执行）
        for i, path_data in enumerate(canonical_paths, 1):
            print(f"\n📜 [{i}/{len(canonical_paths)}] 处理主线路径: {path_data.get('path_id')}")
            result = await self._process_canonical_path(path_data, output_format)
            results.append(result)
        
        # 3. 处理分支路径（可并发：多 worker 时用 asyncio.gather + 队列限制并发数）
        use_parallel = worker_pool and len(worker_pool) > 1
        if use_parallel:
            # 目标：前面大部分重复的路径复用已生成内容，只跑当前不同的一步；不同前缀并行。
            # 缓存：查用「当前步之前」的前缀，存用「含当前步」的前缀；value = 该前缀的 state + branch_events。
            # 实现：共享缓存 + 按 prefix_including 锁 + Event（同一步只跑一次，不同步并行）
            shared_early_ending_cache: Dict[Tuple[Tuple[str, str], ...], Dict[str, Any]] = {}
            shared_early_ending_by_step: Dict[Tuple[str, str], Dict[str, Any]] = {}
            shared_meta_lock = asyncio.Lock()
            shared_prefix_locks: Dict[Tuple[Tuple[str, str], ...], asyncio.Lock] = {}
            shared_in_progress_events: Dict[Tuple[Tuple[str, str], ...], asyncio.Event] = {}
            for w in worker_pool:
                w._early_ending_path_cache = shared_early_ending_cache
                w._early_ending_by_step = shared_early_ending_by_step
                w._early_ending_cache_meta_lock = shared_meta_lock
                w._early_ending_prefix_locks = shared_prefix_locks
                w._early_ending_in_progress_events = shared_in_progress_events
            worker_queue: asyncio.Queue = asyncio.Queue()
            for w in worker_pool:
                worker_queue.put_nowait(w)
            concurrency_limit = max_concurrent if max_concurrent is not None else len(worker_pool)
            semaphore = asyncio.Semaphore(concurrency_limit)
            async def process_one_branch(path_data: Dict[str, Any], index: int) -> Dict[str, Any]:
                async with semaphore:
                    worker = await worker_queue.get()
                    try:
                        path_id = path_data.get("path_id", "")
                        print(f"\n🌿 [{index + 1}/{len(branch_paths)}] 处理分支路径: {path_id}")
                        return await worker._process_branch_path(path_data, output_format)
                    finally:
                        await worker_queue.put(worker)
            branch_results = await asyncio.gather(
                *[process_one_branch(p, i) for i, p in enumerate(branch_paths)],
                return_exceptions=True,
            )
            for i, r in enumerate(branch_results):
                if isinstance(r, Exception):
                    logger.exception(f"分支路径 {branch_paths[i].get('path_id', i)} 处理失败")
                    raise r
                results.append(r)
        else:
            # 顺序执行：单 worker 或 None 时用 self
            worker = worker_pool[0] if worker_pool else self
            for i, path_data in enumerate(branch_paths, 1):
                print(f"\n🌿 [{i}/{len(branch_paths)}] 处理分支路径: {path_data.get('path_id')}")
                result = await worker._process_branch_path(path_data, output_format)
                results.append(result)
        
        print(f"\n✅ 所有路径处理完成！共 {len(results)} 条路径")
        return results
    
    async def _process_canonical_path(
        self,
        path_data: Dict[str, Any],
        output_format: str = "story_content",
    ) -> Dict[str, Any]:
        """
        处理主线路径（工作量小）📜
        
        直接使用传入的 canonical_branch（通常为已重排的 chronological 主线）数据，不需要重新生成。
        """
        path_id = path_data.get("path_id", "canonical_path")
        path_combination = path_data.get("path_combination", [])
        
        # 1. 直接从主线记录（canonical_branch，通常为 chronological）获取所有事件
        events = list(self.canonical_branch.get("processed_events", []))
        
        # 2. 从 ending_candidates.json 获取结局
        candidate_details = path_data.get("candidate_details", [])
        ending = candidate_details[0] if candidate_details else None  # 主线只有一个结局
        
        # 2.5 规范：每条路径最后一个事件必须是 ending（流程/提示词已要求，此处兜底）
        if ending and (not events or not self._is_ending_event(events[-1])):
            events.append(self.formatter.build_ending_event(ending))
        
        # 3. 格式化输出
        if output_format == "story_content":
            return self.formatter.format_path(
                path_id=path_id,
                path_combination=path_combination,
                events=events,
                ending=ending,
                is_canonical=True,
            )
        else:
            return {
                "path_id": path_id,
                "path_combination": path_combination,
                "events": events,
                "ending": ending,
                "is_canonical": True,
            }
    
    async def _process_branch_path(
        self,
        path_data: Dict[str, Any],
        output_format: str = "story_content",
    ) -> Dict[str, Any]:
        """
        处理分支路径（需要重新演化）🌿
        
        基于现有的 generate_path_with_function_calling() 方法。
        充分利用 branches.json、主线记录（canonical_branch，通常为 chronological）、ending_candidates.json 中的数据。
        """
        path_id = path_data.get("path_id", "")
        path_combination = path_data.get("path_combination", [])
        # 实际经历的选择（提前结局时只含到当前分支为止，用于选结局时给 LLM）
        actual_path_combination = list(path_combination)
        # 本路径是否曾调用过 LLM（若从未调用则 from_cache_only，可不写 out）
        path_used_llm = False
        # 每条路径独立：重置提前结局标记，避免上一条路径的 is_early_ending 导致本路径循环体未执行
        self.is_early_ending = False
        # 循环前初始化，避免第一次迭代就因 is_early_ending  break 时 path_result 未定义
        path_result = None
        
        # 1. 识别路径中的所有选择（包括主线选择和分支选择）
        # 🔧 关键修复：path_combination 中的选择应该按顺序处理
        # 因为前面的选择（包括主线选择）会影响后续选择的状态
        all_choices = path_combination  # 使用所有选择，不过滤
        
        # 识别分支选择（用于判断是否需要处理分支路径）
        branch_choices = [
            choice for choice in path_combination 
            if not choice.get("is_canonical", False)
        ]
        
        if not branch_choices:
            # 如果没有分支选择，当作主线路径处理
            return await self._process_canonical_path(path_data, output_format)
        
        # 2. 从主线记录（canonical_branch，通常为 chronological）获取基础状态快照
        # 🔧 关键修复：使用第一个选择（无论是否为主线选择）作为起始点
        first_choice = all_choices[0] if all_choices else None
        if not first_choice:
            logger.error("❌ path_combination 为空，无法处理分支路径")
            return await self._process_canonical_path(path_data, output_format)
        
        first_fork_id = first_choice.get("fork_event_id")
        if not first_fork_id:
            logger.error(f"❌ 第一个选择缺少 fork_event_id: {first_choice}")
            return await self._process_canonical_path(path_data, output_format)
        
        base_snapshot = self._get_base_snapshot_from_canonical(first_fork_id)
        current_states = self._load_states_from_snapshot(base_snapshot)
        
        # 3. 🔧 关键逻辑：找到第一个分支选择的位置
        # 在第一个分支选择之前的所有选择（都是主线选择），只需要更新状态（使用快照）
        # 从第一个分支选择开始，后续所有选择（包括主线选择）都需要重新生成事件和应用状态变化！
        first_branch_index = None
        for i, choice in enumerate(all_choices):
            if not choice.get("is_canonical", False):
                first_branch_index = i
                break
        
        if first_branch_index is None:
            # 没有分支选择，应该已经在上面的检查中返回了
            return await self._process_canonical_path(path_data, output_format)
        
        all_events = []
        all_branch_events = []
        
        # 3.1 处理第一个分支选择之前的所有主线选择（需要保存事件并更新状态）
        # 🔧 关键修复：第一个分支选择之前的主线选择事件也应该保存
        first_branch_choice = all_choices[first_branch_index]
        first_branch_fork_id = first_branch_choice.get("fork_event_id")
        
        # 找到第一个分支选择之前的所有主线选择
        # 从主线记录（canonical_branch）中获取这些主线事件并保存
        prev_mainline_event_id = None
        for record in self.canonical_branch.get("processed_events", []):
            event_id = record.get("event_id")
            if event_id == first_branch_fork_id:
                # 找到当前事件，获取前一个事件的ID
                current_index = self.canonical_branch.get("processed_events", []).index(record)
                if current_index > 0:
                    prev_record = self.canonical_branch.get("processed_events", [])[current_index - 1]
                    prev_mainline_event_id = prev_record.get("event_id")
                break
        
        # 🔧 修复：保存第一个分支选择之前的所有主线事件
        if prev_mainline_event_id:
            # 找到第一个分支选择之前的所有主线事件（从第一个事件到 prev_mainline_event_id）
            saved_count = 0
            for record in self.canonical_branch.get("processed_events", []):
                event_id = record.get("event_id")
                if event_id == first_branch_fork_id:
                    break  # 到达第一个分支选择，停止
                
                # 保存主线事件对象（这些事件在分支之前，状态还没改变，直接使用主线记录）
                mainline_event = {
                    "event_id": event_id,
                    "description": record.get("description", ""),
                    # 关键：chronological 主线会重编号 event_id，旧 ID 在 original_event_id；用于 extract_chain 对齐 source_text。
                    "original_event_id": record.get("original_event_id") or event_id,
                    "canonical_record": record,
                    "is_mainline_event": True,
                    "is_canonical": True,
                    "state_changes": [],  # 这些事件的状态变化已经在主线中应用过了
                    "snapshot": record.get("snapshot"),
                }
                all_events.append(mainline_event)
                saved_count += 1
            
            # 使用前一个主线事件的快照（E13 的快照，用于 E14 分支）
            prev_record = None
            for record in self.canonical_branch.get("processed_events", []):
                if record.get("event_id") == prev_mainline_event_id:
                    prev_record = record
                    break
            
            if prev_record and prev_record.get("snapshot"):
                snapshot = prev_record.get("snapshot")
                current_states = self._load_states_from_snapshot(snapshot)
                print(f"  📜 使用主线事件 {prev_mainline_event_id} 的快照（{first_branch_fork_id} 分支之前的状态）")
                print(f"     ✅ 已保存 {saved_count} 个主线事件（第一个分支选择之前）")
            else:
                logger.warning(f"⚠️  主线事件 {prev_mainline_event_id} 没有快照，保持当前状态")
        else:
            logger.warning(f"⚠️  找不到 {first_branch_fork_id} 之前的主线事件，保持当前状态")
        
        # 3.1.5 进入分支循环前先判断：本路径是否为「重复的提前结局路径」（该分支选择序列已在缓存中）
        # 锁 = 到当前 idx 为止的前缀 path_prefix（与循环内 prefix_including 同义）；并发时若 IN_PROGRESS 则等 Event 后再查
        for idx in range(first_branch_index + 1, len(all_choices) + 1):
            path_prefix = self._branch_choice_prefix(all_choices, idx)
            prefix_lock = await self._get_prefix_lock(path_prefix)
            cached = None
            if prefix_lock is not None and self._early_ending_in_progress_events is not None:
                async with prefix_lock:
                    cached = self._early_ending_path_cache.get(path_prefix)
                if cached is _EARLY_ENDING_CACHE_IN_PROGRESS:
                    ev = self._early_ending_in_progress_events.get(path_prefix)
                    if ev is not None:
                        await ev.wait()
                    async with prefix_lock:
                        cached = self._early_ending_path_cache.get(path_prefix)
            elif prefix_lock is None:
                cached = self._early_ending_path_cache.get(path_prefix)
            if cached is not None and cached is not _EARLY_ENDING_CACHE_IN_PROGRESS and cached.get("is_early_ending"):
                # 重复的提前结局路径：直接用缓存组装结果并返回
                actual_path_combination = all_choices[:idx]
                branch_events = list(cached.get("branch_events", []))
                all_events.extend(branch_events)
                all_branch_events.extend(branch_events)
                selected_ending = cached.get("selected_ending")
                if not selected_ending:
                    candidate_details = path_data.get("candidate_details", [])
                    matching = self._find_matching_path_result_by_prefix(actual_path_combination)
                    if matching:
                        details = matching.get("candidate_details", [])
                        prem = [d for d in details if d.get("is_premature")]
                        if prem:
                            selected_ending = prem[0]
                    if not selected_ending and candidate_details:
                        selected_ending = candidate_details[0]
                # 终端明确标出「重复提前结局」，便于区分
                ending_name = selected_ending.get("name", "未知") if selected_ending else "未知"
                print(f"  📌 [重复提前结局] path_id={path_id}")
                print(f"     → 分支选择前缀已缓存（{len(path_prefix)} 个分支选择），跳过演化与选择，结局: 「{ending_name}」，不写入文件")
                if selected_ending and (not all_events or not self._is_ending_event(all_events[-1])):
                    all_events.append(self.formatter.build_ending_event(selected_ending))
                if output_format == "story_content":
                    out = self.formatter.format_path(
                        path_id=path_id,
                        path_combination=path_combination,
                        events=all_events,
                        ending=selected_ending,
                        is_canonical=False,
                        final_states=current_states,
                    )
                else:
                    out = {
                        "path_id": path_id,
                        "path_combination": path_combination,
                        "branch_events": all_branch_events,
                        "mainline_events": all_events,
                        "ending": selected_ending,
                        "final_states": current_states,
                        "is_canonical": False,
                    }
                out["from_cache_only"] = True
                return out
        
        # 3.2 从第一个分支选择开始，后续所有选择（包括主线选择）都需要重新生成事件和应用状态变化！
        # 🔧 关键：一旦出现分支选择，后续所有事件（包括主线事件）都需要重新生成和应用状态变化
        # 缓存复用：key=当前步之前的已选分支前缀，前面大部分重复的路径可复用 state+events，只跑当前一步
        mainline_event_count = len(all_events)
        for idx, choice_info in enumerate(all_choices[first_branch_index:], first_branch_index + 1):
            # 🔧 关键修复：在循环开始时就检查提前结局，确保提前结局后立即跳过所有后续分支选择
            # 这个检查必须在获取 fork_event_id 之前进行，避免不必要的处理
            if self.is_early_ending:
                actual_path_combination = all_choices[:idx]  # 仅实际经历的选择，供选结局用
                print(f"     ⏭️  前面的分支已提前结局，跳过后续所有分支选择（当前索引：{idx}）")
                break
            
            fork_event_id = choice_info.get("fork_event_id")
            branch_choice_id = choice_info.get("branch_choice")
            is_canonical = choice_info.get("is_canonical", False)
            
            if not fork_event_id:
                logger.error(f"❌ choice_info 中缺少 fork_event_id: {choice_info}")
                continue
            
            # 构建 branch_choice 字典（用于传递给 generate_path_with_function_calling）
            branch_choice = {
                "choice_id": branch_choice_id,
                "description": choice_info.get("description", ""),
                "reasoning": choice_info.get("reasoning", ""),
            }
            
            # 🔧 无论是主线选择还是分支选择，都需要重新生成事件和应用状态变化！
            if is_canonical:
                print(f"  📜 [{idx}/{len(all_choices)}] 主线选择（分支后）: {fork_event_id} - {branch_choice_id}（需要重新生成，因为状态已改变）")
            else:
                print(f"  🔀 [{idx}/{len(all_choices)}] 分支选择: {fork_event_id} - {branch_choice_id}")
            
            logger.info(f"🔍 _process_branch_path: fork_event_id={fork_event_id}, branch_choice_id={branch_choice_id}, is_canonical={is_canonical}")
            
            # 检查：如果前面的分支合流点在当前分支之后，当前分支直接跳过
            if self.merge_point:
                current_fork_index = self._get_event_index(fork_event_id)
                merge_index = self._get_event_index(self.merge_point)
                
                if merge_index is not None and current_fork_index is not None:
                    if merge_index < current_fork_index:
                        print(f"     ⏭️  前面的分支已在当前分支之后合流，跳过当前分支")
                        continue
            
            # 🔧 关键修复：主线选择和分支选择需要不同的处理逻辑
            # - 主线选择：直接创建事件对象，不调用 generate_path_with_function_calling
            # - 分支选择：调用 generate_path_with_function_calling 生成分支事件
            
            if is_canonical:
                # 🔧 修复1：主线选择（在分支之后）应该重新生成状态变化、应用状态变化，并保存事件
                # 从主线记录（canonical_branch）获取主线选择的状态变化（作为参考）
                canonical_record = self._find_canonical_record(fork_event_id)
                
                if canonical_record:
                    # 提取主线记录中的状态变化（作为参考）
                    canonical_state_changes = []
                    if canonical_record.get("selected_state_changes"):
                        all_state_changes = canonical_record.get("state_changes", [])
                        selected_indices = canonical_record.get("selected_state_changes", [])
                        for sc_idx in selected_indices:
                            if isinstance(sc_idx, int) and 0 <= sc_idx < len(all_state_changes):
                                sc = all_state_changes[sc_idx].copy()
                                canonical_state_changes.append({
                                    k: v for k, v in sc.items() if not k.startswith("_")
                                })
                            elif isinstance(sc_idx, dict):
                                canonical_state_changes.append(sc_idx)
                    
                    # 🔧 关键修复：基于分支后的新状态重新生成状态变化
                    state_changes_to_apply = []
                    snapshot = None
                    
                    if self.state_change_generator:
                        try:
                            event_info = await self.state_applier.get_event_info(fork_event_id)
                            if event_info:
                                # 获取上一个事件（可能是分支事件或主线事件）
                                previous_event = None
                                if all_branch_events:
                                    previous_event = all_branch_events[-1]
                                elif all_events:
                                    previous_event = all_events[-1]
                                
                                # 重新生成状态变化（基于分支路径后的新状态）
                                state_changes_to_apply = await self.state_change_generator.generate_for_mainline_event_after_merge(
                                    event_id=fork_event_id,
                                    event_info=event_info,
                                    current_states=current_states,
                                    canonical_state_changes=canonical_state_changes,
                                    previous_event=previous_event,
                                )
                                
                                if not state_changes_to_apply:
                                    # 如果重新生成失败，使用主线记录的状态变化
                                    state_changes_to_apply = canonical_state_changes
                                    print(f"     ⚠️  {fork_event_id} 重新生成状态变化失败，使用主线记录的状态变化")
                                else:
                                    print(f"     ✅ {fork_event_id} 已重新生成状态变化（基于分支路径后的新状态）")
                        except Exception as e:
                            logger.warning(f"⚠️  为主线选择 {fork_event_id} 重新生成状态变化时出错: {e}")
                            # 如果出错，使用主线记录的状态变化
                            state_changes_to_apply = canonical_state_changes
                    else:
                        # 如果没有 state_change_generator，使用主线记录的状态变化
                        state_changes_to_apply = canonical_state_changes
                    
                    # 应用状态变化
                    if state_changes_to_apply:
                        try:
                            event_info = await self.state_applier.get_event_info(fork_event_id)
                            if event_info:
                                # 使用直接应用方法（更快）
                                current_states, snapshot = self.state_applier.apply_state_changes_directly(
                                    selected_state_changes=state_changes_to_apply,
                                    current_states=current_states,
                                    event_id=fork_event_id,
                                )
                                print(f"     ✅ 已应用主线选择的状态变化（{len(state_changes_to_apply)} 个状态变化）")
                        except Exception as e:
                            logger.warning(f"⚠️  应用主线选择 {fork_event_id} 的状态变化时出错: {e}")
                    
                    # 🔧 关键修复：创建主线事件对象并添加到 all_events（保存生成的事件）
                    mainline_event = {
                        "event_id": fork_event_id,
                        "description": canonical_record.get("description", ""),
                        # 同上：保证主线事件（分支后）仍携带原始 event_id，供 source_text 对齐
                        "original_event_id": canonical_record.get("original_event_id") or fork_event_id,
                        "canonical_record": canonical_record,
                        "is_mainline_event": True,
                        "is_canonical": True,
                        "state_changes": state_changes_to_apply,
                        "snapshot": snapshot.to_dict_for_llm() if snapshot else None,
                    }
                    all_events.append(mainline_event)
                    print(f"     ✅ 已创建并保存主线事件对象: {fork_event_id}")
            else:
                # 🔧 修复2：分支选择才调用 generate_path_with_function_calling
                # 分支选择：从 branches.json 获取分支决策信息和状态变化
                branch_data = self._get_branch_data_from_branches_json(
                    fork_event_id,
                    branch_choice_id,
                )

                if branch_data:
                    print(f"     ✅ 从 branches.json 获取分支决策信息")

                    # 应用分支选择的状态变化（从 branches.json 中获取）
                    if branch_data.get("generated_state_changes"):
                        state_changes = branch_data["generated_state_changes"]
                        event_info = await self.state_applier.get_event_info(fork_event_id)
                        if event_info:
                            updated_states, _, snapshot = await self.state_applier.apply_state_change(
                                event_id=fork_event_id,
                                event_info=event_info,
                                selected_state_changes=state_changes,
                                current_states=current_states,
                            )
                            current_states = updated_states
                            print(f"     ✅ 已应用分支选择的状态变化")
            
            # 3.3 获取合流点选项（使用现有的方法）
            merge_options_raw = self.get_merge_options(fork_event_id, max_distance=5)
            merge_options = [opt["event_id"] for opt in merge_options_raw]
            
            # 3.4 生成分支事件：查 prefix_before 复用前面；查/存 prefix_including 保证「同一前缀+当前步」只跑一次
            # 加锁对象 = prefix_including（含当前步的完整分支序列）。同一 prefix_including 只允许一个 worker 跑并写缓存，其它等 Event 后读。
            path_result = None
            used_cache_for_this_branch = False
            prefix_before = self._branch_choice_prefix(all_choices, idx - 1)
            prefix_including = self._branch_choice_prefix(all_choices, idx)
            prefix_lock = await self._get_prefix_lock(prefix_including)
            in_progress_events = self._early_ending_in_progress_events
            if prefix_lock is not None and in_progress_events is not None and not is_canonical:
                need_run = False
                ev_to_wait = None
                async with prefix_lock:
                    cached = self._early_ending_path_cache.get(prefix_before)
                    if cached is not None and cached is not _EARLY_ENDING_CACHE_IN_PROGRESS:
                        if cached.get("is_early_ending"):
                            used_cache_for_this_branch = True
                            path_result = dict(cached)
                            print(f"     📌 [重复提前结局] 复用前缀（{len(prefix_before)} 个分支选择）的结局，跳过演化")
                        else:
                            # 复用「前面大部分」的 state+events，本步只跑当前选择
                            from state_manager.models import UpdatedState
                            if cached.get("final_states"):
                                fs = cached["final_states"]
                                current_states = UpdatedState(**fs) if isinstance(fs, dict) else fs
                            branch_from_cache = list(cached.get("branch_events", []))
                            all_branch_events.clear()
                            all_branch_events.extend(branch_from_cache)
                            all_events[:] = all_events[:mainline_event_count] + branch_from_cache
                            if branch_from_cache:
                                print(f"     📂 [前缀复用] 已从缓存加载 {len(branch_from_cache)} 个分支事件（prefix_before），本步仅跑当前选择")
                            need_run = True
                    # 若尚未得到 path_result，再查 prefix_including（同一「前缀+当前步」只跑一次，需占位/等）
                    if path_result is None:
                        cached_incl = self._early_ending_path_cache.get(prefix_including)
                        if cached_incl is not None and cached_incl is not _EARLY_ENDING_CACHE_IN_PROGRESS:
                            used_cache_for_this_branch = True
                            path_result = dict(cached_incl)
                            if path_result.get("is_early_ending"):
                                print(f"     📌 [重复提前结局] 复用已缓存的前缀（{len(prefix_including)} 个），跳过演化")
                        elif cached_incl is _EARLY_ENDING_CACHE_IN_PROGRESS:
                            ev_to_wait = in_progress_events.get(prefix_including)
                        else:
                            # 同分支点、同提前结局：若已有同一 prefix_before 下其它选择导致的提前结局，直接复用，不重复演化、不重复保存
                            sibling = self._find_sibling_early_ending_cache(prefix_before, prefix_including)
                            if sibling is not None:
                                used_cache_for_this_branch = True
                                path_result = sibling
                                print(f"     📌 [重复提前结局] 同分支点已有提前结局，复用（{len(prefix_including)} 个选择），跳过演化与保存")
                            else:
                                need_run = True
                                self._early_ending_path_cache[prefix_including] = _EARLY_ENDING_CACHE_IN_PROGRESS
                                in_progress_events[prefix_including] = asyncio.Event()
                if ev_to_wait is not None:
                    await ev_to_wait.wait()
                    async with prefix_lock:
                        cached = self._early_ending_path_cache.get(prefix_including)
                        if cached is not None and cached is not _EARLY_ENDING_CACHE_IN_PROGRESS:
                            path_result = dict(cached)
                            if path_result.get("is_early_ending"):
                                used_cache_for_this_branch = True
                                print(f"     📌 [重复提前结局] 复用已缓存的前缀（{len(prefix_including)} 个），跳过演化")
                        else:
                            sibling = self._find_sibling_early_ending_cache(prefix_before, prefix_including)
                            if sibling is not None:
                                used_cache_for_this_branch = True
                                path_result = sibling
                                print(f"     📌 [重复提前结局] 同分支点已有提前结局，复用（{len(prefix_including)} 个选择），跳过演化与保存")
                            else:
                                need_run = True
                                self._early_ending_path_cache[prefix_including] = _EARLY_ENDING_CACHE_IN_PROGRESS
                                if prefix_including not in in_progress_events:
                                    in_progress_events[prefix_including] = asyncio.Event()
                # 按「当前步」提前结局缓存：任一路径已跑过 (fork_event_id, branch_choice) 并得到提前结局时，直接复用，不重复生成分支事件
                if need_run and path_result is None and getattr(self, "_early_ending_by_step", None) is not None:
                    step_key = (fork_event_id, branch_choice_id)
                    step_cached = self._early_ending_by_step.get(step_key)
                    if step_cached and step_cached.get("is_early_ending"):
                        used_cache_for_this_branch = True
                        need_run = False
                        _fs = step_cached.get("final_states")
                        if _fs is None and current_states is not None:
                            _fs = getattr(current_states, "model_dump", None) and current_states.model_dump() or (getattr(current_states, "dict", None) and current_states.dict() or {})
                        path_result = {
                            "branch_events": list(step_cached.get("branch_events", [])),
                            "is_early_ending": True,
                            "ending_event": step_cached.get("ending_event"),
                            "mainline_events": [],
                            "merge_point": None,
                            "final_states": _fs,
                        }
                        if prefix_lock is not None:
                            async with prefix_lock:
                                self._early_ending_path_cache[prefix_including] = {
                                    "branch_events": list(all_branch_events) + list(path_result.get("branch_events", [])),
                                    "final_states": path_result.get("final_states"),
                                    "is_early_ending": True,
                                    "ending_event": path_result.get("ending_event"),
                                    "merge_point": None,
                                    "mainline_events": [],
                                }
                                if in_progress_events is not None and prefix_including in in_progress_events:
                                    in_progress_events[prefix_including].set()
                        print(f"     📌 [重复提前结局] 按步复用（{fork_event_id} / {branch_choice_id}），跳过演化与保存")
                if need_run and path_result is None:
                    path_used_llm = True
                    enable_hitl = getattr(self, '_enable_hitl_for_all_paths', False)
                    path_result = await self.generate_path_with_function_calling(
                        fork_event_id=fork_event_id,
                        branch_choice=branch_choice,
                        current_states=current_states,
                        path_combination=path_combination,
                        enable_human_in_the_loop=enable_hitl,
                    )

                    async with prefix_lock:
                        self._early_ending_path_cache[prefix_including] = {
                            "branch_events": list(all_branch_events) + list(path_result.get("branch_events", [])),
                            "final_states": path_result.get("final_states"),
                            "is_early_ending": path_result.get("is_early_ending", False),
                            "ending_event": path_result.get("ending_event"),
                            "merge_point": path_result.get("merge_point"),
                            "mainline_events": list(path_result.get("mainline_events", [])),
                        }

                        if path_result.get("is_early_ending"):
                            print(
                                f"     🔍 判断: 本分支（{fork_event_id} / {branch_choice_id}）导致提前结局，已缓存供复用"
                            )
                            if getattr(self, "_early_ending_by_step", None) is not None:
                                step_key = (fork_event_id, branch_choice_id)
                                self._early_ending_by_step[step_key] = {
                                    "branch_events": list(path_result.get("branch_events", [])),
                                    "ending_event": path_result.get("ending_event"),
                                    "is_early_ending": True,
                                    "final_states": path_result.get("final_states"),
                                }

                        if prefix_including not in in_progress_events:
                            in_progress_events[prefix_including] = asyncio.Event()
                        in_progress_events[prefix_including].set()
            else:
                if prefix_lock is None and not is_canonical:
                    cached = self._early_ending_path_cache.get(prefix_before)
                    if cached is not None and cached.get("is_early_ending"):
                        used_cache_for_this_branch = True
                        path_result = dict(cached)
                        print(f"     📌 [重复提前结局] 复用前缀（{len(prefix_before)} 个）的结局，跳过演化")
                    elif cached is not None and not cached.get("is_early_ending"):
                        from state_manager.models import UpdatedState
                        if cached.get("final_states"):
                            fs = cached["final_states"]
                            current_states = UpdatedState(**fs) if isinstance(fs, dict) else fs
                        branch_from_cache = list(cached.get("branch_events", []))
                        all_branch_events.clear()
                        all_branch_events.extend(branch_from_cache)
                        all_events[:] = all_events[:mainline_event_count] + branch_from_cache
                        if branch_from_cache:
                            print(f"     📂 [前缀复用] 已从缓存加载 {len(branch_from_cache)} 个分支事件（prefix_before），本步仅跑当前选择")
                    # 同分支点、同提前结局：无锁时也检查是否已有兄弟提前结局可复用
                    if path_result is None:
                        sibling = self._find_sibling_early_ending_cache(prefix_before, prefix_including)
                        if sibling is not None:
                            used_cache_for_this_branch = True
                            path_result = sibling
                            print(f"     📌 [重复提前结局] 同分支点已有提前结局，复用（{len(prefix_including)} 个选择），跳过演化与保存")
                if path_result is None:
                    step_key = (fork_event_id, branch_choice_id)
                    if getattr(self, "_early_ending_by_step", None) is not None:
                        step_cached = self._early_ending_by_step.get(step_key)
                        if step_cached and step_cached.get("is_early_ending"):
                            used_cache_for_this_branch = True
                            _fs = step_cached.get("final_states") or (getattr(current_states, "model_dump", None) and current_states.model_dump() or (getattr(current_states, "dict", None) and current_states.dict() or {}))
                            path_result = {
                                "branch_events": list(step_cached.get("branch_events", [])),
                                "is_early_ending": True,
                                "ending_event": step_cached.get("ending_event"),
                                "mainline_events": [],
                                "merge_point": None,
                                "final_states": _fs,
                            }
                            if not is_canonical:
                                self._early_ending_path_cache[prefix_including] = {
                                    "branch_events": list(all_branch_events) + list(path_result.get("branch_events", [])),
                                    "final_states": path_result.get("final_states"),
                                    "is_early_ending": True,
                                    "ending_event": path_result.get("ending_event"),
                                    "merge_point": None,
                                    "mainline_events": [],
                                }
                            print(f"     📌 [重复提前结局] 按步复用（{fork_event_id} / {branch_choice_id}），跳过演化与保存")
                    if path_result is None:
                        path_used_llm = True
                        enable_hitl = getattr(self, '_enable_hitl_for_all_paths', False)
                        path_result = await self.generate_path_with_function_calling(
                            fork_event_id=fork_event_id,
                            branch_choice=branch_choice,
                            current_states=current_states,
                            path_combination=path_combination,
                            enable_human_in_the_loop=enable_hitl,
                        )
                        if not is_canonical:
                            self._early_ending_path_cache[prefix_including] = {
                                "branch_events": list(all_branch_events) + list(path_result.get("branch_events", [])),
                                "final_states": path_result.get("final_states"),
                                "is_early_ending": path_result.get("is_early_ending", False),
                                "ending_event": path_result.get("ending_event"),
                                "merge_point": path_result.get("merge_point"),
                                "mainline_events": list(path_result.get("mainline_events", [])),
                            }
                            if path_result.get("is_early_ending") and getattr(self, "_early_ending_by_step", None) is not None:
                                self._early_ending_by_step[step_key] = {
                                    "branch_events": list(path_result.get("branch_events", [])),
                                    "ending_event": path_result.get("ending_event"),
                                    "is_early_ending": True,
                                    "final_states": path_result.get("final_states"),
                                }
                            if path_result.get("is_early_ending"):
                                print(f"     🔍 判断: 本分支（{fork_event_id} / {branch_choice_id}）导致提前结局，已缓存供复用")
            
            # 🔍 DEBUG：检查 generate_path_with_function_calling 返回的 path_result
            print(f"     🔍 [DEBUG] generate_path_with_function_calling 返回的 path_result:")
            print(f"        - path_result.keys(): {list(path_result.keys())}")
            print(f"        - path_result.get('mainline_events'): {len(path_result.get('mainline_events', []))} 个事件")
            print(f"        - path_result.get('merge_point'): {path_result.get('merge_point')}")
            print(f"        - path_result.get('branch_events'): {len(path_result.get('branch_events', []))} 个事件")
            if path_result.get('mainline_events'):
                print(f"        - mainline_events 前3个事件的 event_id: {[e.get('event_id') if isinstance(e, dict) else 'N/A' for e in path_result.get('mainline_events', [])[:3]]}")
            
            # 🔧 关键修复：检查 branch_events 中是否有提前结局事件（通过 event_id 或 is_ending 标志）
            if path_result.get("branch_events"):
                for event in path_result.get("branch_events", []):
                    event_id = event.get("event_id", "")
                    is_ending = event.get("is_ending", False)
                    if "ending" in event_id.lower() or is_ending:
                        if not path_result.get("is_early_ending", False):
                            path_result["is_early_ending"] = True
                            self.is_early_ending = True
            
            # 🔧 关键修复：立即设置 self.is_early_ending，这样在循环开始检查时就能正确跳过后续分支
            is_current_early_ending = path_result.get("is_early_ending", False)
            if is_current_early_ending:
                self.is_early_ending = True
                if not used_cache_for_this_branch:
                    print(f"     🔍 判断: 本分支（{fork_event_id} / {branch_choice_id}）导致提前结局，后续分叉点将不再演化")
                print(f"     ⏭️  当前分支已提前结局，将在处理完当前分支后跳过后续分支选择")
            
            # 🔧 修复3：收集分支事件和主线事件（包括提前结局的情况）
            # 判断「当前分支导致提前结局」的时机：在 generate_path_with_function_calling 内部，当 LLM 调用
            # create_early_ending 时即确定（LangGraph 的 create_early_ending_node 会设置 is_early_ending 并
            # 将 ending_event append 到 self.branch_events）。此处仅根据返回的 path_result 做一次收集，不会重复收集。
            branch_events = path_result.get("branch_events", [])
            
            # 🔧 修复4：如果提前结局，ending_event 可能已在 LangGraph 中 append 进 branch_events，此处仅兜底补全
            if path_result.get("is_early_ending", False):
                ending_event = path_result.get("ending_event")
                if ending_event:
                    # 检查 ending_event 是否已经在 branch_events 中
                    ending_event_id = ending_event.get("event_id")
                    if ending_event_id:
                        # 检查是否已存在相同 ID 的事件
                        existing_ids = [e.get("event_id") for e in branch_events if e.get("event_id")]
                        if ending_event_id not in existing_ids:
                            # 如果 ending_event 不在 branch_events 中，添加它
                            branch_events.append(ending_event)
                            print(f"     ✅ 已添加提前结局事件到分支事件列表: {ending_event_id}")
                    else:
                        # 如果没有 event_id，直接添加
                        branch_events.append(ending_event)
                        print(f"     ✅ 已添加提前结局事件到分支事件列表（无 event_id）")
            
            # 🔧 关键修复：确保分支事件被添加到 all_branch_events 和 all_events
            # 来自缓存时用 path_result 覆盖，避免重复追加；本步新生成时才 extend
            if used_cache_for_this_branch:
                cached_branch = list(path_result.get("branch_events", []))
                all_branch_events.clear()
                all_branch_events.extend(cached_branch)
                all_events[:] = all_events[:mainline_event_count] + cached_branch
            if branch_events:
                if not used_cache_for_this_branch:
                    all_branch_events.extend(branch_events)
                    all_events.extend(branch_events)
                source_tag = "来自缓存" if used_cache_for_this_branch else "本步新生成"
                early_note = "，当前分支为提前结局" if path_result.get("is_early_ending", False) else ""
                print(f"     ✅ 已收集 {len(branch_events)} 个分支事件（{source_tag}{early_note}，已添加到 all_branch_events 和 all_events）")
            else:
                if not used_cache_for_this_branch:
                    print(f"     ⚠️  没有收集到分支事件（可能提前结局或未生成）")
            
            # 3.6 如果合流了，获取主线事件（LangGraph 工作流已经处理过了）
            # 🔧 DEBUG：添加详细的调试信息
            print(f"     🔍 [DEBUG] 检查合流状态:")
            print(f"        - self.is_early_ending: {self.is_early_ending}")
            print(f"        - path_result.get('merge_point'): {path_result.get('merge_point')}")
            print(f"        - self.merge_point: {self.merge_point}")
            print(f"        - path_result.get('mainline_events'): {len(path_result.get('mainline_events', []))} 个事件")
            print(f"        - path_result.get('branch_events'): {len(path_result.get('branch_events', []))} 个事件")
            print(f"        - path_result.keys(): {list(path_result.keys())}")
            
            # 🔧 关键修复：如果提前结局了，不应该处理合流和主线事件
            # 🔧 关键修复2：如果没有合流点，说明分支无法合流，直接提前结局，不处理后续主线事件
            # 🔧 关键修复3：LangGraph 工作流中的 process_mainline_node 已经调用了 process_mainline_after_merge()
            # 所以 path_result 中应该已经包含了 mainline_events，直接使用即可
            if not self.is_early_ending:
                # 检查是否有合流点（从 path_result 或 self.merge_point）
                merge_point = path_result.get("merge_point") or self.merge_point
                
                print(f"     🔍 [DEBUG] 合流点检查: merge_point = {merge_point}")
                
                if merge_point:
                    # 🔧 关键修复：LangGraph 工作流已经处理了合流后的主线事件，直接使用 path_result 中的 mainline_events
                    mainline_events = path_result.get("mainline_events", [])
                    print(f"     🔍 [DEBUG] path_result 中的 mainline_events: {len(mainline_events)} 个事件")
                    if mainline_events:
                        print(f"     🔍 [DEBUG] mainline_events 前3个事件的 event_id: {[e.get('event_id') for e in mainline_events[:3]]}")
                        # 合流后的主线事件用 mainline_event_merged 标注，与合流前的主线事件区分
                        merged_list = []
                        for ev in mainline_events:
                            if isinstance(ev, dict):
                                e = {**ev, "is_mainline_event_merged": True}
                                merged_list.append(e)
                            else:
                                merged_list.append(ev)
                        all_events.extend(merged_list)
                        print(f"     ✅ 已收集 {len(mainline_events)} 个主线事件（合流后，type=mainline_event_merged）")
                    else:
                        # 如果 path_result 中没有 mainline_events，说明 LangGraph 工作流可能没有处理，需要手动处理
                        print(f"     ⚠️  path_result 中没有 mainline_events，手动处理合流后的主线事件...")
                        print(f"     🔍 [DEBUG] 调用 process_mainline_after_merge()...")
                        mainline_result = await self.process_mainline_after_merge()
                        mainline_events = mainline_result.get("mainline_events", [])
                        print(f"     🔍 [DEBUG] process_mainline_after_merge() 返回了 {len(mainline_events)} 个事件")
                        if mainline_events:
                            print(f"     🔍 [DEBUG] mainline_events 前3个事件的 event_id: {[e.get('event_id') for e in mainline_events[:3]]}")
                            merged_list = [{**e, "is_mainline_event_merged": True} if isinstance(e, dict) else e for e in mainline_events]
                            all_events.extend(merged_list)
                        print(f"     ✅ 已收集 {len(mainline_events)} 个主线事件（合流后，手动处理，type=mainline_event_merged）")
                    
                    # 🔧 修复：使用 path_result 或 mainline_result 的最终状态
                    final_states_from_result = path_result.get("final_states")
                    if final_states_from_result:
                        # 确保 final_states 是 UpdatedState 对象
                        if isinstance(final_states_from_result, dict):
                            from state_manager.models import UpdatedState
                            try:
                                current_states = UpdatedState(**final_states_from_result)
                            except Exception:
                                current_states = final_states_from_result
                        else:
                            current_states = final_states_from_result
                else:
                    # 🔧 关键修复3：如果没有合流点，说明分支无法合流，直接提前结局，不处理后续主线事件
                    print(f"     🎬 没有合流点，分支路径将直接提前结局（不处理后续主线事件）")
                    # 标记为提前结局
                    self.is_early_ending = True
                    if path_result.get("final_states"):
                        final_states_from_path = path_result["final_states"]
                        if isinstance(final_states_from_path, dict):
                            from state_manager.models import UpdatedState
                            try:
                                current_states = UpdatedState(**final_states_from_path)
                            except Exception:
                                current_states = final_states_from_path
                        else:
                            current_states = final_states_from_path
            elif self.is_early_ending:
                print(f"     ⏭️  提前结局，跳过合流和主线事件处理")
                # 🔧 修复：提前结局时，使用 path_result 的最终状态（因为提前结局没有合流，所以没有主线事件）
                if path_result.get("final_states"):
                    final_states_from_path = path_result["final_states"]
                    if isinstance(final_states_from_path, dict):
                        from state_manager.models import UpdatedState
                        try:
                            current_states = UpdatedState(**final_states_from_path)
                        except Exception:
                            current_states = final_states_from_path
                    else:
                        current_states = final_states_from_path
            
            print(f"     🔍 [DEBUG] 处理完成后，all_events 总数: {len(all_events)}")
            if all_events:
                print(f"     🔍 [DEBUG] all_events 最后3个事件的 event_id: {[e.get('event_id') if isinstance(e, dict) else getattr(e, 'event_id', 'N/A') for e in all_events[-3:]]}")
            
            # 3.8 如果提前结局，本路径结束：break 后不再进入循环，后续分支点不会生成任何分支事件
            if path_result.get("is_early_ending", False) or self.is_early_ending:
                actual_path_combination = all_choices[:idx]  # 仅实际经历的选择，供选结局用
                print(f"     ⏭️  当前分支已提前结局，跳过后续分支选择")
                # 🔧 确保 self.is_early_ending 被设置（防止被重置）
                self.is_early_ending = True
                break
        
        # 4. 选择结局（从 ending_candidates.json）；提前结局时用 actual_path_combination 让 LLM 只按实际经历选
        selected_ending = None
        candidate_details = path_data.get("candidate_details", [])
        # 重复的提前结局路径（本路径来自缓存、未跑过 LLM）：直接使用缓存的结局，跳过选择逻辑，进入下一条路径
        if self.is_early_ending and not path_used_llm:
            selected_ending = path_result.get("selected_ending") if path_result else None
            if not selected_ending and candidate_details:
                # 缓存里尚未写入 selected_ending 时的兜底：按前缀匹配取第一个 is_premature 候选（不调 LLM）
                matching = self._find_matching_path_result_by_prefix(actual_path_combination)
                if matching:
                    details = matching.get("candidate_details", [])
                    prem = [d for d in details if d.get("is_premature")]
                    if prem:
                        selected_ending = prem[0]
            if selected_ending:
                print(f"  📌 [重复提前结局] path_id={path_id} → 直接复用缓存的结局「{selected_ending.get('name', '未知')}」，跳过选择与重写，不写入文件")
        if selected_ending is None and candidate_details:
            ending_path = actual_path_combination if self.is_early_ending else path_combination
            selected_ending = await self.select_ending_from_candidates(
                path_combination=ending_path,
                final_states=current_states,
                is_premature=self.is_early_ending,
            )
            if not selected_ending:
                selected_ending = candidate_details[0]
            # 首次处理的提前结局路径：将选中的结局写回缓存，供后续相同分支选择前缀的路径直接复用（并发时加锁）
            if self.is_early_ending and path_used_llm and selected_ending and actual_path_combination:
                path_prefix = self._branch_choice_prefix_from_list(actual_path_combination)
                prefix_lock = await self._get_prefix_lock(path_prefix)
                if prefix_lock is not None:
                    async with prefix_lock:
                        if path_prefix in self._early_ending_path_cache:
                            self._early_ending_path_cache[path_prefix]["selected_ending"] = selected_ending
                elif path_prefix in self._early_ending_path_cache:
                    self._early_ending_path_cache[path_prefix]["selected_ending"] = selected_ending
        
        # 4.5 规范：每条路径最后一个事件必须是 ending（流程/提示词已要求，此处兜底）
        if selected_ending and (not all_events or not self._is_ending_event(all_events[-1])):
            all_events.append(self.formatter.build_ending_event(selected_ending))
        
        # 5. 格式化输出（若本路径从未调用 LLM，标记 from_cache_only，CLI 可不写 out）
        if output_format == "story_content":
            out = self.formatter.format_path(
                path_id=path_id,
                path_combination=path_combination,
                events=all_events,
                ending=selected_ending,
                is_canonical=False,
                final_states=current_states,
            )
            # 供「all_paths 补全脚本」使用：写入 branch_events，脚本可用其重排 events
            out["branch_events"] = list(all_branch_events)
        else:
            out = {
                "path_id": path_id,
                "path_combination": path_combination,
                "branch_events": all_branch_events,
                "mainline_events": all_events,
                "ending": selected_ending,
                "final_states": current_states,
                "is_canonical": False,
            }
        # 重复的提前结局路径 path_used_llm=False → from_cache_only=True，CLI 不会保存到文件
        out["from_cache_only"] = not path_used_llm
        return out
    
    # ========== 辅助方法：数据获取 ⚡ ==========
    
    @staticmethod
    def _event_id_key(event: Any) -> Optional[str]:
        """用于去重/比对的 event_id，无则返回 None。"""
        if not event or not isinstance(event, dict):
            return None
        eid = event.get("event_id")
        return str(eid).strip() if eid is not None and str(eid).strip() else None
    
    def _is_ending_event(self, event: Dict[str, Any]) -> bool:
        """判断事件是否为结局类型（供流程保证最后一个是 ending）。"""
        if not event or not isinstance(event, dict):
            return False
        if event.get("is_ending"):
            return True
        eid = str(event.get("event_id") or "")
        if eid.lower().startswith("ending"):
            return True
        if event.get("type") == "ending":
            return True
        return False
    
    def _get_branch_data_from_branches_json(
        self,
        fork_event_id: str,
        branch_choice_id: str,
    ) -> Optional[Dict[str, Any]]:
        """从 branches.json 中获取分支决策信息（优化：直接使用已有数据）✨"""
        return self.data_loader.get_branch_data(fork_event_id, branch_choice_id)
    
    def _get_base_snapshot_from_canonical(self, fork_event_id: str) -> Optional[Dict[str, Any]]:
        """从主线记录（canonical_branch，通常为 chronological）获取分叉点之前的基础状态快照✨"""
        # 找到分叉事件在主线记录中的位置
        fork_index = None
        for idx, record in enumerate(self.canonical_branch.get("processed_events", [])):
            if record.get("event_id") == fork_event_id:
                fork_index = idx
                break
        
        if fork_index is None or fork_index == 0:
            return None  # 如果是第一个事件，返回 None（使用基线状态）
        
        # 返回前一个事件的状态快照
        prev_record = self.canonical_branch["processed_events"][fork_index - 1]
        return prev_record.get("snapshot")
    
    def _load_states_from_snapshot(self, snapshot: Optional[Dict[str, Any]]) -> UpdatedState:
        """
        从快照加载状态（优化：直接使用已有数据）✨
        
        根据 StateSnapshot 的结构，从 snapshot 字典中提取状态
        """
        if not snapshot:
            # 如果没有快照，返回基线状态
            return UpdatedState()
        
        # StateSnapshot.to_dict_for_llm() 返回的格式：
        # {
        #   "人物状态": {...},
        #   "关系状态": {...},
        #   "世界状态": {...}
        # }
        
        # 提取人物状态
        character_states = {}
        formatted_characters = snapshot.get("人物状态", {})
        for char_id, char_data in formatted_characters.items():
            state_dimensions = char_data.get("状态维度", {})
            character_states[char_id] = state_dimensions
        
        # 提取关系状态
        relationship_states = {}
        formatted_relationships = snapshot.get("关系状态", {})
        for rel_key, rel_data in formatted_relationships.items():
            relationship_state = rel_data.get("关系状态", {})
            relationship_states[rel_key] = relationship_state
        
        # 世界状态比较复杂，暂时不加载（可以从事件序列重建）
        # 或者如果需要，可以从 world_state 中提取
        
        return UpdatedState(
            character_states=character_states,
            relationship_states=relationship_states,
            world_states={},  # 暂时为空，可以从事件序列重建
        )
    
    def _get_event_index(self, event_id: str) -> Optional[int]:
        """获取事件在主线中的索引位置"""
        all_event_ids = []
        for record in self.canonical_branch.get("processed_events", []):
            eid = record.get("event_id")
            if eid:
                all_event_ids.append(eid)
        
        try:
            return all_event_ids.index(event_id)
        except ValueError:
            return None
    
    def _extract_event_number(self, event_id: str) -> Optional[int]:
        """
        从 event_id 提取数字（例如：E14 -> 14）🔢
        
        Args:
            event_id: 事件ID（例如：E14, E15）
            
        Returns:
            事件数字（例如：14），如果无法提取则返回 None
        """
        import re
        match = re.match(r'E(\d+)', event_id)
        if match:
            return int(match.group(1))
        return None
    
    def _generate_absolute_event_id(self, fork_event_id: str, sequence_number: int) -> str:
        """
        生成绝对序号的事件ID（例如：E14 分叉，第一个分支事件是 E15）🔢✨
        
        Args:
            fork_event_id: 分叉点事件ID（例如：E14）
            sequence_number: 分支事件的序号（从 1 开始）
            
        Returns:
            绝对序号的事件ID（例如：E15, E16, E17...）
        """
        fork_number = self._extract_event_number(fork_event_id)
        if fork_number is None:
            # 如果无法提取数字，回退到旧格式
            logger.warning(f"⚠️  无法从 {fork_event_id} 提取数字，使用旧格式")
            return f"{fork_event_id}_branch_{sequence_number}"
        
        # 计算绝对序号：分叉点 + 序号
        absolute_number = fork_number + sequence_number
        return f"E{absolute_number}"
    

__all__ = ["PathGenerationFunctions"]
