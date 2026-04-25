"""
状态定义和同步 🔄✨

职责：
- 定义 LangGraph 状态结构
- 提供状态同步函数（LangGraph ↔ PathGenerationFunctions）
"""

import logging
from typing import Dict, List, Any, Optional, TypedDict, Annotated
from langchain_core.messages import BaseMessage

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


class PathGenerationState(TypedDict):
    """路径生成状态 - 完全由 LangGraph 管理"""
    fork_event_id: str
    branch_choice: Dict[str, Any]
    current_states: Any  # UpdatedState
    branch_events: Annotated[List[Dict[str, Any]], "append"]
    merge_point: Optional[str]
    is_early_ending: bool
    ending_event: Optional[Dict[str, Any]]
    skipped_mainline_events: List[str]
    merge_options: List[str]
    merge_options_raw: List[Dict[str, Any]]
    path_combination: List[Dict[str, Any]]
    max_branch_length: Optional[int]  # None 表示不设分支事件条数上限（仍受图 recursion_limit 约束）
    iteration: int
    max_iterations: Optional[int]  # None 表示决策提示中不强调固定迭代上限
    
    # 合流后的状态（整体替换语义：仅 process_mainline_node 写入，后续节点不覆盖）
    mainline_events: List[Dict[str, Any]]
    final_states_snapshot: Optional[Dict[str, Any]]  # 最终状态 snapshot
    
    # 结局选择相关
    ending_candidates: List[Dict[str, Any]]  # 候选结局列表
    similar_ending_groups: List[List[Dict[str, Any]]]  # 相似结局分组
    merged_endings: List[Dict[str, Any]]  # 合并后的结局
    selected_ending: Optional[Dict[str, Any]]  # 最终选择的结局
    
    # LLM 消息（用于 function calling）
    messages: Annotated[List[BaseMessage], "append"]
    
    # 流程控制
    can_merge: Optional[bool]  # 能否合流
    should_continue_generating: bool  # 是否继续生成分支事件
    decision: Optional[str]  # LLM 的决策（"generate", "merge", "early_ending"）
    
    # 记忆管理（用于避免上下文爆炸）🧠
    conversation_summary: Optional[str]  # 对话总结
    conversation_key_points: List[str]  # 对话关键点
    max_messages_before_summary: int  # 触发总结的消息数量阈值（默认 10）


def sync_state_to_instance(state: Dict[str, Any], path_functions) -> None:
    """
    将 LangGraph 状态同步到 PathGenerationFunctions 实例 🔄
    
    这是一个辅助函数，避免在每个节点中重复写同步逻辑
    
    注意：fork_event_id 和 branch_choice 是路径的固定属性，不应该被覆盖
    """
    # 类型检查：确保 path_functions 是 PathGenerationFunctions 实例，而不是字典
    if not hasattr(path_functions, 'branch_events'):
        # 如果 path_functions 是字典，说明传递错误了
        logger.error(f"❌ sync_state_to_instance 收到错误的参数类型: {type(path_functions)}")
        logger.error(f"   期望: PathGenerationFunctions 实例")
        logger.error(f"   实际: {path_functions}")
        raise TypeError(f"path_functions 必须是 PathGenerationFunctions 实例，但收到 {type(path_functions)}")
    
    # 🔧 关键修复：只有当 state 中确实有 branch_events 时才更新，避免用空列表覆盖已有的分支事件
    state_branch_events = state.get("branch_events")
    if state_branch_events is not None:
        # 如果 state 中有 branch_events，更新实例（可能是列表，也可能是其他类型）
        if isinstance(state_branch_events, list) and len(state_branch_events) > 0:
            # 只有当 state 中的 branch_events 不为空时，才更新实例
            path_functions.branch_events = state_branch_events
            logger.debug(f"🔍 [DEBUG] sync_state_to_instance: 更新 branch_events，count = {len(state_branch_events)}")
        elif isinstance(state_branch_events, list) and len(state_branch_events) == 0:
            # 如果 state 中的 branch_events 是空列表，且实例中已有分支事件，不覆盖
            if len(path_functions.branch_events) > 0:
                logger.debug(f"🔍 [DEBUG] sync_state_to_instance: state.branch_events 为空，保留实例中的 {len(path_functions.branch_events)} 个分支事件")
            else:
                path_functions.branch_events = []
        else:
            # 如果 state 中的 branch_events 不是列表，直接使用
            path_functions.branch_events = state_branch_events
    # 如果 state 中没有 branch_events，保持实例中的值不变
    
    # 🔧 关键修复：current_states 可能是字典（LangGraph 序列化后），需要转换回 UpdatedState 对象
    current_states_raw = state.get("current_states")
    if current_states_raw is None:
        path_functions.current_states = None
    elif isinstance(current_states_raw, dict):
        # 如果是字典，转换回 UpdatedState 对象
        from state_manager.models import UpdatedState
        try:
            path_functions.current_states = UpdatedState(**current_states_raw)
            logger.debug(f"🔍 [DEBUG] sync_state_to_instance: 将字典转换为 UpdatedState 对象")
        except Exception as e:
            logger.warning(f"⚠️  无法将 current_states 字典转换为 UpdatedState 对象: {e}，保持字典格式")
            path_functions.current_states = current_states_raw
    else:
        # 如果已经是 UpdatedState 对象，直接使用
        path_functions.current_states = current_states_raw
    
    # 🔧 关键修复：fork_event_id 和 branch_choice 是路径的固定属性，不应该被覆盖
    # 只有在它们还没有设置时才从 state 中获取
    if not path_functions.fork_event_id:
        state_fork_id = state.get("fork_event_id")
        if state_fork_id:
            path_functions.fork_event_id = state_fork_id
            logger.debug(f"✅ 从 state 设置 fork_event_id: {state_fork_id}")
        else:
            logger.warning("⚠️  state 中也没有 fork_event_id")
    else:
        # 如果已经设置，验证 state 中的值是否一致
        state_fork_id = state.get("fork_event_id")
        if state_fork_id and state_fork_id != path_functions.fork_event_id:
            logger.warning(f"⚠️  fork_event_id 不一致：path_functions={path_functions.fork_event_id}, state={state_fork_id}，保持 path_functions 的值")
    
    if not path_functions.branch_choice:
        state_branch_choice = state.get("branch_choice")
        if state_branch_choice:
            path_functions.branch_choice = state_branch_choice
            logger.debug(f"✅ 从 state 设置 branch_choice: {state_branch_choice.get('choice_id', 'unknown')}")
    else:
        # 如果已经设置，验证 state 中的值是否一致
        state_branch_choice = state.get("branch_choice")
        if state_branch_choice:
            state_choice_id = state_branch_choice.get('choice_id') if isinstance(state_branch_choice, dict) else None
            current_choice_id = path_functions.branch_choice.get('choice_id') if isinstance(path_functions.branch_choice, dict) else None
            if state_choice_id and state_choice_id != current_choice_id:
                logger.warning(f"⚠️  branch_choice 不一致：path_functions={current_choice_id}, state={state_choice_id}，保持 path_functions 的值")
    
    path_functions.merge_options = state.get("merge_options", [])
    
    # ⚠️ 合流点的同步要小心：
    # - 工具函数 merge_to_mainline 会先在 path_functions 实例上设置 merge_point
    # - ToolNode 并不会把返回的 JSON 自动写回 state["merge_point"]
    # - 如果这里无条件用 state 中的 None 覆盖实例，就会出现：
    #   「LLM 说要合流到 E15」→ process_mainline_node 里变成 merge_point=None
    state_merge_point = state.get("merge_point", None)
    
    if state_merge_point is not None:
        # 只有当 state 里真的有值时才覆盖实例，避免误把有效的实例值抹掉
        path_functions.merge_point = state_merge_point
    
    path_functions.is_early_ending = state.get("is_early_ending", False)
    path_functions.skipped_mainline_events = state.get("skipped_mainline_events", [])
    path_functions.path_combination = state.get("path_combination", [])
    
    # 🔧 关键修复：同步 mainline_events（合流后的主线事件）
    state_mainline_events = state.get("mainline_events")
    if state_mainline_events is not None:
        # 如果 state 中有 mainline_events，更新实例
        if isinstance(state_mainline_events, list) and len(state_mainline_events) > 0:
            path_functions._mainline_events = state_mainline_events
            logger.debug(f"🔍 [DEBUG] sync_state_to_instance: 更新 mainline_events，count = {len(state_mainline_events)}")
        elif isinstance(state_mainline_events, list) and len(state_mainline_events) == 0:
            # 如果 state 中的 mainline_events 是空列表，且实例中已有主线事件，不覆盖
            if hasattr(path_functions, '_mainline_events') and len(path_functions._mainline_events) > 0:
                logger.debug(f"🔍 [DEBUG] sync_state_to_instance: state.mainline_events 为空，保留实例中的 {len(path_functions._mainline_events)} 个主线事件")
            else:
                path_functions._mainline_events = []
        else:
            path_functions._mainline_events = state_mainline_events
    # 如果 state 中没有 mainline_events，保持实例中的值不变


def sync_instance_to_state(path_functions) -> Dict[str, Any]:
    """
    将 PathGenerationFunctions 实例状态同步回 LangGraph 状态 🔄
    
    返回需要更新的状态字典
    """
    # 类型检查：确保 path_functions 是 PathGenerationFunctions 实例
    if not hasattr(path_functions, 'branch_events'):
        logger.error(f"❌ sync_instance_to_state 收到错误的参数类型: {type(path_functions)}")
        raise TypeError(f"path_functions 必须是 PathGenerationFunctions 实例，但收到 {type(path_functions)}")
    
    # 🔧 关键修复：current_states 如果是 UpdatedState 对象，需要转换为字典
    # LangGraph 会自动序列化，但为了确保一致性，我们显式转换
    current_states_for_state = path_functions.current_states
    if current_states_for_state is not None:
        if hasattr(current_states_for_state, "model_dump"):
            # 如果是 Pydantic 模型，转换为字典
            current_states_for_state = current_states_for_state.model_dump()
        elif hasattr(current_states_for_state, "dict"):
            # 兼容旧版本的 Pydantic
            current_states_for_state = current_states_for_state.dict()
    
    state_dict = {
        "branch_events": path_functions.branch_events,
        "current_states": current_states_for_state,  # 确保是字典格式
        "fork_event_id": path_functions.fork_event_id,
        "branch_choice": path_functions.branch_choice,
        "merge_options": path_functions.merge_options,
        "merge_point": path_functions.merge_point,
        "is_early_ending": path_functions.is_early_ending,
        "skipped_mainline_events": path_functions.skipped_mainline_events,
        "path_combination": path_functions.path_combination,
    }
    # 🔧 关键修复：同步 mainline_events（合流后的主线事件）- 必须加入返回值，否则 instance 上的 9 个事件不会写回 state
    if hasattr(path_functions, '_mainline_events'):
        state_dict["mainline_events"] = path_functions._mainline_events
        logger.debug(f"🔍 [DEBUG] sync_instance_to_state: 同步 mainline_events，count = {len(path_functions._mainline_events) if isinstance(path_functions._mainline_events, list) else 'N/A'}")
    return state_dict

