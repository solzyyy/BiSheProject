"""
工具函数模块 🛠️✨

职责：
- 定义 LangChain 工具函数（供 LLM function calling 使用）
- 工具函数只做轻量级操作，返回状态信息用于路由
"""

import json
import logging
from typing import Dict, List, Any, Optional, Annotated

try:
    from langchain_core.tools import tool
    from langgraph.prebuilt.tool_node import InjectedState
    TOOLS_AVAILABLE = True
except ImportError:
    TOOLS_AVAILABLE = False

from .state import sync_state_to_instance

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


def create_tools(path_functions_instance):
    """
    创建 LangChain 工具（用于 ToolNode）🛠️
    
    这些工具会被 LLM 通过 function calling 调用
    ToolNode 会自动处理工具调用，不需要手动解析！
    
    使用 InjectedState 来获取当前的 LangGraph state，确保状态同步！
    """
    if not TOOLS_AVAILABLE:
        raise ImportError("LangChain tools 未安装，请运行: pip install langchain>=0.2.0")
    
    @tool
    async def generate_next_branch_event(
        description: str, 
        reasoning: str,
        state: Annotated[Dict[str, Any], InjectedState()] = None,  # 🔧 注入 LangGraph state
    ) -> str:
        """
        🛠️ 工具函数：生成下一个分支事件（给 LLM 的"菜单"）
        
        作用：LLM 通过 function calling 调用这个工具，告诉系统"我要生成下一个分支事件了"
        职责：只设置状态，不生成事件（实际生成由节点完成）
        返回：状态信息（用于路由到对应的节点）
        """
        try:
            # 同步状态
            if state:
                sync_state_to_instance(state, path_functions_instance)
            
            # 🔧 关键：工具函数只设置状态，不生成事件
            # 实际的事件生成由 _generate_branch_event_node 节点完成
            result = await path_functions_instance.generate_next_branch_event(
                description=description,
                reasoning=reasoning,
            )
            return json.dumps(result, ensure_ascii=False)
        except Exception as e:
            logger.error(f"❌ generate_next_branch_event 执行失败: {e}", exc_info=True)
            return json.dumps({"status": "error", "message": str(e)}, ensure_ascii=False)
    
    @tool
    async def merge_to_mainline(
        merge_point_event_id: str,
        reasoning: str,
        skipped_mainline_events: Optional[List[str]] = None,
        state: Annotated[Dict[str, Any], InjectedState()] = None,  # 🔧 注入 LangGraph state
    ) -> str:
        """
        🛠️ 工具函数：合流到主线事件（给 LLM 的"菜单"）
        
        作用：LLM 通过 function calling 调用这个工具，告诉系统"我要合流到主线了"
        职责：只设置状态，不处理主线事件（实际处理由节点完成）
        返回：状态信息（用于路由到对应的节点）
        """
        try:
            # 同步状态
            if state:
                sync_state_to_instance(state, path_functions_instance)
            
            result = await path_functions_instance.merge_to_mainline(
                merge_point_event_id=merge_point_event_id,
                reasoning=reasoning,
                skipped_mainline_events=skipped_mainline_events or [],
            )
            
            return json.dumps(result, ensure_ascii=False)
        except Exception as e:
            return json.dumps({"status": "error", "message": str(e)}, ensure_ascii=False)
    
    @tool
    async def create_early_ending(
        ending_type: str, 
        ending_reason: str, 
        description: str,
        state: Annotated[Dict[str, Any], InjectedState()] = None,  # 🔧 注入 LangGraph state
    ) -> str:
        """
        🛠️ 工具函数：创建提前结局（给 LLM 的"菜单"）
        
        作用：LLM 通过 function calling 调用这个工具，告诉系统"我要创建提前结局了"
        职责：只设置状态，不生成结局（实际生成由节点完成）
        返回：状态信息（用于路由到对应的节点）
        """
        try:
            # 同步状态
            if state:
                sync_state_to_instance(state, path_functions_instance)
            
            # 🔧 关键：工具函数只设置状态，不生成结局
            # 实际的结局生成由 _create_early_ending_node 节点完成
            result = await path_functions_instance.create_early_ending(
                ending_type=ending_type,
                ending_reason=ending_reason,
                description=description,
            )
            return json.dumps(result, ensure_ascii=False)
        except Exception as e:
            return json.dumps({"status": "error", "message": str(e)}, ensure_ascii=False)
    
    return [generate_next_branch_event, merge_to_mainline, create_early_ending]

