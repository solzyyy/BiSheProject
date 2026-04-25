"""
装饰器模块 🎀✨

职责：
- 提供节点装饰器（自动同步、错误处理、日志监控）
"""

import functools
import time
import logging
from typing import Dict, Any, Callable

from .state import sync_state_to_instance, sync_instance_to_state

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


def auto_sync(needs_sync: bool = True):
    """
    装饰器：自动同步状态 🎀
    
    自动处理 LangGraph 状态 ↔ PathGenerationFunctions 实例的双向同步
    
    Args:
        needs_sync: 是否需要同步状态（某些只读节点可能不需要）
    """
    def decorator(func: Callable) -> Callable:
        @functools.wraps(func)
        async def wrapper(state: Dict[str, Any], path_functions) -> Dict[str, Any]:
            # 进入前同步：LangGraph → PathGenerationFunctions
            if needs_sync:
                sync_state_to_instance(state, path_functions)
            
            # 执行节点逻辑
            result = await func(state, path_functions)
            
            # 出去前同步：PathGenerationFunctions → LangGraph
            if needs_sync:
                instance_state = sync_instance_to_state(path_functions)
                # 🔧 关键修复：合并结果时，如果 result 中没有 branch_events 或 result 中的 branch_events 为空，使用 instance_state 中的值
                # 合并结果（result 中的值会覆盖 instance_state，但 branch_events 和 mainline_events 需要特殊处理）
                merged_result = {**instance_state, **result}
                
                # 🔧 关键修复：如果 result 中没有 branch_events 或 result 中的 branch_events 为空，使用 instance_state 中的值
                if "branch_events" not in result or (isinstance(result.get("branch_events"), list) and len(result.get("branch_events", [])) == 0):
                    # 如果 result 中没有 branch_events 或 result 中的 branch_events 为空，使用 instance_state 中的值
                    if "branch_events" in instance_state and isinstance(instance_state.get("branch_events"), list) and len(instance_state.get("branch_events", [])) > 0:
                        merged_result["branch_events"] = instance_state.get("branch_events")
                
                # 🔧 关键修复：如果 result 中有 mainline_events，优先使用 result 中的值（因为 process_mainline_node 返回的是最新的）
                if "mainline_events" in result and isinstance(result.get("mainline_events"), list) and len(result.get("mainline_events", [])) > 0:
                    merged_result["mainline_events"] = result.get("mainline_events")
                    logger.debug(f"🔍 [DEBUG] auto_sync: 使用 result 中的 mainline_events，count = {len(result.get('mainline_events'))}")
                elif "mainline_events" not in result or (isinstance(result.get("mainline_events"), list) and len(result.get("mainline_events", [])) == 0):
                    # 如果 result 中没有 mainline_events 或 result 中的 mainline_events 为空，使用 instance_state 中的值
                    if "mainline_events" in instance_state and isinstance(instance_state.get("mainline_events"), list) and len(instance_state.get("mainline_events", [])) > 0:
                        merged_result["mainline_events"] = instance_state.get("mainline_events")
                        logger.debug(f"🔍 [DEBUG] auto_sync: 使用 instance_state 中的 mainline_events，count = {len(instance_state.get('mainline_events'))}")
                
                result = merged_result
            
            return result
        return wrapper
    return decorator


def with_error_handling(func: Callable) -> Callable:
    """
    装饰器：统一错误处理 🛡️
    
    自动捕获异常，记录日志，返回错误状态
    """
    @functools.wraps(func)
    async def wrapper(state: Dict[str, Any], path_functions) -> Dict[str, Any]:
        try:
            return await func(state, path_functions)
        except Exception as e:
            node_name = func.__name__
            logger.error(
                f"❌ 节点 {node_name} 执行失败: {e}",
                exc_info=True,
                extra={"node": node_name, "error": str(e)}
            )
            
            # 返回错误状态（LangGraph 可以根据这个决定是否重试或终止）
            return {
                "error": str(e),
                "error_node": node_name,
                "should_retry": False,  # 默认不重试，可以根据错误类型调整
            }
    return wrapper


def with_logging(func: Callable) -> Callable:
    """
    装饰器：添加日志和性能监控 📝
    
    记录节点进入/退出、执行时间、状态信息
    """
    @functools.wraps(func)
    async def wrapper(state: Dict[str, Any], path_functions) -> Dict[str, Any]:
        node_name = func.__name__
        
        # 记录进入节点
        iteration = state.get("iteration", 0)
        branch_events_count = len(state.get("branch_events", []))
        logger.info(
            f"🎯 进入节点: {node_name} | "
            f"迭代: {iteration} | "
            f"分支事件数: {branch_events_count}"
        )
        
        # 记录详细状态（DEBUG 级别）
        logger.debug(
            f"   状态详情: fork_event_id={state.get('fork_event_id')}, "
            f"can_merge={state.get('can_merge')}, "
            f"is_early_ending={state.get('is_early_ending')}"
        )
        
        # 记录执行时间
        start_time = time.time()
        try:
            result = await func(state, path_functions)
            elapsed = time.time() - start_time
            
            # 记录成功完成
            logger.info(
                f"✅ 完成节点: {node_name} | "
                f"耗时: {elapsed:.2f}s"
            )
            
            # 记录结果摘要（DEBUG 级别）
            if result:
                result_keys = list(result.keys())[:5]  # 只显示前5个键
                logger.debug(f"   返回状态键: {result_keys}")
            
            return result
        except Exception as e:
            elapsed = time.time() - start_time
            logger.error(
                f"❌ 节点 {node_name} 失败 | "
                f"耗时: {elapsed:.2f}s | "
                f"错误: {e}"
            )
            raise  # 重新抛出异常，让 with_error_handling 处理
    return wrapper


def node_decorator(needs_sync: bool = True):
    """
    组合装饰器：一键应用所有功能 ✨
    
    包含：自动同步状态、统一错误处理、日志和性能监控
    
    Args:
        needs_sync: 是否需要同步状态
    
    Usage:
        @node_decorator(needs_sync=True)
        async def my_node(state, path_functions):
            ...
    """
    def decorator(func: Callable) -> Callable:
        # 注意：装饰器顺序很重要！
        # 1. with_logging 最外层（最先执行，最后退出）
        # 2. with_error_handling 中间（捕获异常）
        # 3. auto_sync 最内层（处理状态同步）
        return with_logging(with_error_handling(auto_sync(needs_sync)(func)))
    return decorator

