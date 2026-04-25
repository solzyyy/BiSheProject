"""
路由逻辑 🔀✨

职责：
- 定义工作流的路由函数（决定下一个节点）
"""

import json
import logging
from typing import Dict, Any
from langchain_core.messages import AIMessage, ToolMessage

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


def route_after_llm_decision(state: Dict[str, Any]) -> str:
    """LLM 决策后的路由"""
    messages = state.get("messages", [])
    last_message = messages[-1] if messages else None
    
    if isinstance(last_message, AIMessage) and last_message.tool_calls:
        try:
            print(f"[LG] route from=llm_decision to=tools tool_calls={len(last_message.tool_calls)}")
        except Exception:
            pass
        return "tools"  # 有工具调用，交给 ToolNode 处理
    else:
        try:
            print("[LG] route from=llm_decision to=END (no tool call)")
        except Exception:
            pass
        return "end"  # 没有工具调用，结束


def route_after_tools(state: Dict[str, Any]) -> str:
    """ToolNode 执行后的路由"""
    messages = state.get("messages", [])
    tool_messages = [msg for msg in messages if isinstance(msg, ToolMessage)]
    
    # 查找最后一个工具调用的结果
    for tool_msg in reversed(tool_messages):
        try:
            result = json.loads(tool_msg.content)
            status = result.get("status")
            
            # 🔧 关键修复：如果工具返回了状态值，更新到 state 中
            # 因为 ToolNode 不会自动把返回值写回 state，我们需要手动更新
            
            # 处理 merge_to_mainline 工具返回的状态
            if "merge_point" in result:
                state["merge_point"] = result["merge_point"]
            
            if "skipped_mainline_events" in result:
                state["skipped_mainline_events"] = result["skipped_mainline_events"]
            
            # 处理 create_early_ending 工具返回的状态
            # 注意：虽然 create_early_ending_node 会重新设置 is_early_ending，但为了保持一致性，我们也更新 state
            if status == "early_ending_created":
                state["is_early_ending"] = True
            
            if status == "generated":
                try:
                    print("[LG] route from=tools to=generate_branch_event status=generated")
                except Exception:
                    pass
                return "generate"  # 生成分支事件 → 去 generate_branch_event 节点 → 然后回到 llm_decision
            elif status == "merged":
                try:
                    print("[LG] route from=tools to=process_mainline status=merged")
                except Exception:
                    pass
                return "merge"  # LLM 已选择合流点 → 直接去 process_mainline 节点
            elif status == "early_ending_created":
                try:
                    print("[LG] route from=tools to=create_early_ending status=early_ending_created")
                except Exception:
                    pass
                return "early_ending"  # 创建提前结局 → 去 create_early_ending 节点
        except Exception:
            pass
    
    # 如果没有明确的工具调用结果，默认继续决策（不应该到这里）
    try:
        print("[LG] route from=tools to=generate_branch_event (fallback)")
    except Exception:
        pass
    return "generate"  # 默认当作生成事件处理

