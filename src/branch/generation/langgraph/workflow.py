"""
LangGraph 工作流定义 🎨✨

职责：
- 创建和配置 LangGraph 工作流
- 组装所有节点、边和路由
"""

import logging
from typing import Dict, Any, Optional

try:
    from langgraph.graph import StateGraph, END
    from langgraph.checkpoint.memory import MemorySaver
    from langgraph.prebuilt import ToolNode
    LANGGRAPH_AVAILABLE = True
except ImportError:
    LANGGRAPH_AVAILABLE = False
    # 避免 Windows 控制台编码问题：不要直接 print emoji
    logging.getLogger(__name__).warning(
        "LangGraph 未安装，请运行: pip install langgraph>=0.2.0"
    )

from .state import PathGenerationState
from .tools import create_tools
from .nodes import (
    initialize_node,
    summarize_history_node,
    llm_decision_node,
    generate_branch_event_node,
    process_mainline_node,
    identify_similar_endings_node,
    merge_similar_endings_node,
    select_dramatic_ending_node,
    create_early_ending_node,
)
from .routing import route_after_llm_decision, route_after_tools

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


def create_path_generation_graph(path_functions_instance=None):
    """
    创建路径生成的 LangGraph 工作流 🎨
    
    混合模式：
    - LangGraph 管理流程（节点流转）
    - 关键决策节点使用 LLM + function calling（让 LLM 决定）
    - 执行节点直接调用方法（不需要 function calling）
    
    Args:
        path_functions_instance: PathGenerationFunctions 实例（如果为 None，会创建默认实例）
        
    Returns:
        编译后的 LangGraph 图
    """
    if not LANGGRAPH_AVAILABLE:
        return None
    
    # 类型检查：确保 path_functions_instance 是正确的类型
    # LangGraph Studio 可能会传递字典（初始状态），我们需要忽略它并创建新实例
    if path_functions_instance is None or isinstance(path_functions_instance, dict):
        # 如果是 None 或字典，创建默认实例（用于 LangGraph Studio）
        import sys
        from pathlib import Path
        # 添加项目根目录和 src 目录到 Python 路径（用于 Studio）
        project_root = Path(__file__).parent.parent.parent.parent.parent
        src_dir = project_root / "src"
        if str(project_root) not in sys.path:
            sys.path.insert(0, str(project_root))
        if str(src_dir) not in sys.path:
            sys.path.insert(0, str(src_dir))
        
        from src.core.llm_client import AsyncLLMClient
        from src.state_manager.state_applier import StateApplier
        from src.branch.generation.path_generation_functions import PathGenerationFunctions
        
        llm_client = AsyncLLMClient.create_default()
        state_applier = StateApplier()
        path_functions_instance = PathGenerationFunctions(
            llm_client=llm_client,
            flow_llm_client=llm_client,
            state_applier=state_applier,
        )
    
    # 最终验证：确保 path_functions_instance 是正确的类型
    if not hasattr(path_functions_instance, 'branch_events'):
        raise TypeError(
            f"path_functions_instance 必须是 PathGenerationFunctions 实例，但收到 {type(path_functions_instance)}。"
            f"如果使用 LangGraph Studio，请确保 langgraph.json 中的路径正确。"
        )
    
    # 创建工具（用于 LLM 决策节点的 function calling）
    tools = create_tools(path_functions_instance)
    tool_node = ToolNode(tools)  # LangGraph 自动处理工具调用
    
    # 创建图
    workflow = StateGraph(PathGenerationState)
    
    # ========== 节点定义 ==========
    
    # 辅助函数：创建异步节点包装器（LangGraph 需要正确的异步函数签名）
    def create_async_node_wrapper(node_name: str, async_func):
        """创建异步节点包装器，确保 LangGraph 能正确等待异步函数。

        同时打印标准化节点事件（供 Streamlit 实时解析并高亮前端图）。
        """
        async def wrapper(state: Dict[str, Any]):
            # 确保 path_functions_instance 是有效的实例
            if path_functions_instance is None:
                raise ValueError("path_functions_instance 不能为 None")
            if not hasattr(path_functions_instance, 'branch_events'):
                raise TypeError(f"path_functions_instance 必须是 PathGenerationFunctions 实例，但收到 {type(path_functions_instance)}")
            try:
                fork_event_id = state.get("fork_event_id")
                iteration = state.get("iteration")
                decision = state.get("decision")
                is_early_ending = state.get("is_early_ending")
                can_merge = state.get("can_merge")
                # 统一前缀：[LG]，方便 UI 解析
                print(
                    f"[LG] enter node={node_name} fork_event_id={fork_event_id} "
                    f"iteration={iteration} can_merge={can_merge} early_ending={is_early_ending} decision={decision}"
                )
            except Exception:
                # 日志不影响流程
                pass

            out = await async_func(state, path_functions_instance)

            try:
                fork_event_id = state.get("fork_event_id")
                iteration = state.get("iteration")
                is_early_ending = state.get("is_early_ending")
                merge_point = state.get("merge_point")
                print(
                    f"[LG] exit node={node_name} fork_event_id={fork_event_id} "
                    f"iteration={iteration} early_ending={is_early_ending} merge_point={merge_point}"
                )
            except Exception:
                pass

            return out
        return wrapper
    
    # 0. 初始化节点 - 从 branches.json 加载分支点信息，设置初始状态 🎯
    workflow.add_node("initialize", create_async_node_wrapper("initialize", initialize_node))
    
    # 1. 记忆总结节点 - 在决策前检查并总结历史消息（避免上下文爆炸）🧠
    workflow.add_node("summarize_history", create_async_node_wrapper("summarize_history", summarize_history_node))
    
    # 2. LLM 决策节点 - 使用 function calling 让 LLM 决定下一步
    workflow.add_node("llm_decision", create_async_node_wrapper("llm_decision", llm_decision_node))
    
    # 2. ToolNode - LangGraph 自动处理工具调用（这才是 LangGraph 的核心！）
    workflow.add_node("tools", tool_node)
    
    # 3. 生成分支事件节点 - 从 ToolMessage 提取结果并更新状态
    workflow.add_node("generate_branch_event", create_async_node_wrapper("generate_branch_event", generate_branch_event_node))
    
    # 4. 处理主线事件节点 - 直接调用方法
    workflow.add_node("process_mainline", create_async_node_wrapper("process_mainline", process_mainline_node))
    
    # 6. 识别相似结局节点 - 直接调用方法
    workflow.add_node("identify_similar_endings", create_async_node_wrapper("identify_similar_endings", identify_similar_endings_node))
    
    # 7. 合并相似结局节点 - 直接调用方法
    workflow.add_node("merge_similar_endings", create_async_node_wrapper("merge_similar_endings", merge_similar_endings_node))
    
    # 8. 选择文学性结局节点 - 直接调用方法
    workflow.add_node("select_dramatic_ending", create_async_node_wrapper("select_dramatic_ending", select_dramatic_ending_node))
    
    # 9. 创建提前结局节点 - 直接调用方法
    workflow.add_node("create_early_ending", create_async_node_wrapper("create_early_ending", create_early_ending_node))
    
    # ========== 设置入口点 ==========
    # 入口点改为 initialize，先初始化状态，然后进入 summarize_history
    workflow.set_entry_point("initialize")
    
    # ========== 添加边和条件路由 ==========
    
    # LLM 决策后 -> 根据消息路由
    workflow.add_conditional_edges(
        "llm_decision",
        lambda state: route_after_llm_decision(state),
        {
            "tools": "tools",  # 有工具调用 -> ToolNode 自动处理
            "end": END,  # 结束
        }
    )
    
    # ToolNode 执行后 -> 根据工具调用结果路由
    # 🔄 路由逻辑：工具函数返回 status → 路由到对应的节点
    workflow.add_conditional_edges(
        "tools",
        lambda state: route_after_tools(state),
        {
            "generate": "generate_branch_event",  # 工具返回 {"status": "generated"} → 去生成分支事件节点
            "merge": "process_mainline",  # 工具返回 {"status": "merged"} → 去处理主线节点
            "early_ending": "create_early_ending",  # 工具返回 {"status": "early_ending_created"} → 去创建提前结局节点
        }
    )
    
    # 初始化后 -> 记忆总结（检查是否需要总结，然后决策）
    workflow.add_edge("initialize", "summarize_history")
    
    # 生成分支事件后 -> 回到记忆总结（检查是否需要总结，然后决策）
    workflow.add_edge("generate_branch_event", "summarize_history")
    
    # 记忆总结后 -> LLM 决策
    workflow.add_edge("summarize_history", "llm_decision")
    
    # 处理主线事件后 -> 识别相似结局
    workflow.add_edge("process_mainline", "identify_similar_endings")
    
    # 识别相似结局后 -> 合并相似结局
    workflow.add_edge("identify_similar_endings", "merge_similar_endings")
    
    # 合并相似结局后 -> 选择文学性结局
    workflow.add_edge("merge_similar_endings", "select_dramatic_ending")
    
    # 选择结局后 -> 结束
    workflow.add_edge("select_dramatic_ending", END)
    
    # 创建提前结局后 -> 识别相似结局（提前结局也需要相似合并+文学性选择）
    workflow.add_edge("create_early_ending", "identify_similar_endings")
    
    # ========== 编译图（带检查点支持）==========
    memory = MemorySaver()
    compiled_graph = workflow.compile(checkpointer=memory)
    
    # 添加图的可视化支持（用于 LangGraph Studio）
    # 可以通过 compiled_graph.get_graph() 获取图结构
    return compiled_graph

