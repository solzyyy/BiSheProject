"""
节点定义 🎭✨

职责：
- 定义所有 LangGraph 工作流节点
- 每个节点负责一个具体的执行步骤
"""

import json
import logging
from typing import Dict, Any

try:
    from langgraph.types import interrupt  # 人机协作支持 ✋
    from langchain_core.messages import HumanMessage, AIMessage, ToolMessage
    from langchain_core.messages.tool import ToolCall
    NODES_AVAILABLE = True
except ImportError:
    NODES_AVAILABLE = False

from .decorators import node_decorator
from .prompts import build_decision_prompt

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


@node_decorator(needs_sync=True)
async def initialize_node(state: Dict[str, Any], path_functions) -> Dict[str, Any]:
    """
    初始化节点 🎯✨
    
    从 branches.json 加载分支点信息，设置初始状态。
    如果初始状态中缺少必要信息，自动从 branches.json 中查找并填充。
    """
    fork_event_id = state.get("fork_event_id")
    branch_choice_raw = state.get("branch_choice")
    
    # 🔧 调试信息：记录接收到的初始状态
    logger.info(f"🔍 initialize_node 接收到的 state: fork_event_id={fork_event_id}, branch_choice类型={type(branch_choice_raw)}")
    
    # 确保 branch_choice 是字典类型（如果不是，转换为空字典）
    if isinstance(branch_choice_raw, dict):
        branch_choice = branch_choice_raw
    elif branch_choice_raw is None:
        branch_choice = {}
    else:
        # 如果 branch_choice 不是字典（可能是整数、字符串等），记录警告并重置
        logger.warning(f"⚠️  branch_choice 类型错误: {type(branch_choice_raw)}，期望 dict，重置为空字典")
        branch_choice = {}
    
    # 🔧 关键修复：只有在 fork_event_id 或 branch_choice 真正缺失时才从 branches.json 加载
    # 如果 state 中已经有值，应该使用 state 中的值（这是从调用者传入的正确值）
    if not fork_event_id or not branch_choice:
        logger.warning(f"⚠️  初始状态中缺少 fork_event_id 或 branch_choice，尝试从 branches.json 加载...")
        logger.warning(f"    当前 state 中的 fork_event_id: {fork_event_id}, branch_choice: {bool(branch_choice)}")
        
        # 从 branches.json 中获取第一个分支点（作为示例）
        branches_data = path_functions.data_loader.branches_data
        if branches_data:
            # 使用第一个分支作为默认值
            first_branch = branches_data[0]
            if not fork_event_id:
                fork_event_id = first_branch.get("fork_event_id")
                logger.info(f"  ✅ 从 branches.json 获取 fork_event_id: {fork_event_id}")
            else:
                logger.info(f"  ✅ 使用 state 中的 fork_event_id: {fork_event_id}（不从 branches.json 加载）")
            
            if not branch_choice:
                branch_choice = {
                    "choice_id": first_branch.get("branch_choice"),
                    "description": first_branch.get("description", ""),
                    "reasoning": first_branch.get("reasoning", ""),
                }
                logger.info(f"  ✅ 从 branches.json 获取 branch_choice: {branch_choice.get('choice_id')}")
            else:
                logger.info(f"  ✅ 使用 state 中的 branch_choice: {branch_choice.get('choice_id', 'unknown')}（不从 branches.json 加载）")
        else:
            logger.error("❌ branches.json 为空或不存在，无法初始化")
            raise ValueError("无法从 branches.json 加载分支点信息，请确保文件存在且包含数据")
    else:
        # 如果 state 中已经有值，直接使用，不要从 branches.json 加载
        logger.info(f"  ✅ 使用 state 中的 fork_event_id: {fork_event_id}, branch_choice: {branch_choice.get('choice_id', 'unknown')}")
    
    # 获取合流点选项（如果未提供）
    merge_options_raw = state.get("merge_options_raw", [])
    if not merge_options_raw:
        logger.info("  🔍 从 decision_points_analysis.json 获取合流点选项...")
        merge_options_raw = path_functions.get_merge_options(fork_event_id, max_distance=5)
        merge_options = [opt["event_id"] for opt in merge_options_raw]
    else:
        merge_options = state.get("merge_options", [])
    
    # 初始化当前状态（如果未提供）
    current_states = state.get("current_states")
    if not current_states:
        logger.info("  📊 初始化状态（从基线）...")
        current_states = path_functions.state_applier._initialize_states_from_baseline()
    
    # 初始化路径上下文（调用 PathGenerationFunctions 的方法）
    path_functions.initialize_path_context(
        fork_event_id=fork_event_id,
        branch_choice=branch_choice,
        current_states=current_states,
        merge_options=merge_options,
        path_combination=state.get("path_combination", []),
    )
    
    logger.info(f"✅ 初始化完成：fork_event_id={fork_event_id}, branch_choice={branch_choice.get('choice_id')}")
    
    # 返回更新后的状态
    return {
        "fork_event_id": fork_event_id,
        "branch_choice": branch_choice,
        "current_states": current_states,
        "merge_options": merge_options,
        "merge_options_raw": merge_options_raw,
        "path_combination": state.get("path_combination", []),
        "max_branch_length": state.get("max_branch_length"),
        "iteration": 0,
        "max_iterations": (
            state.get("max_iterations")
            if state.get("max_iterations") is not None
            else (
                (state.get("max_branch_length") + 2)
                if isinstance(state.get("max_branch_length"), int)
                else None
            )
        ),
        "enable_human_in_the_loop": state.get("enable_human_in_the_loop", False),
        "max_messages_before_summary": state.get("max_messages_before_summary", 10),
        "messages": [],
        "branch_events": [],
        "is_early_ending": False,
        "ending_event": None,
        "skipped_mainline_events": [],
        "mainline_events": [],
        "ending_candidates": [],
        "similar_ending_groups": [],
        "merged_endings": [],
        "selected_ending": None,
        "conversation_summary": None,
        "conversation_key_points": [],
    }


@node_decorator(needs_sync=True)
async def summarize_history_node(state: Dict[str, Any], path_functions) -> Dict[str, Any]:
    """
    记忆总结节点 🧠✨
    
    当 messages 列表过长时（超过阈值），自动总结历史对话，避免上下文爆炸。
    总结后的内容存入 state，旧消息被清空。
    
    阈值：默认 10 条消息（可配置）
    """
    messages = state.get("messages", [])
    message_count = len(messages)
    max_messages_before_summary = state.get("max_messages_before_summary", 10)
    
    # 如果消息数量未超过阈值，直接跳过总结
    if message_count < max_messages_before_summary:
        logger.debug(f"消息数量 ({message_count}) 未超过阈值 ({max_messages_before_summary})，跳过总结")
        return {}  # 不更新状态，直接进入下一个节点
    
    logger.info(f"🧠 消息数量 ({message_count}) 超过阈值 ({max_messages_before_summary})，开始总结历史...")
    
    # 提取需要总结的消息（排除最后几条，保留最近的上下文）
    messages_to_summarize = messages[:-3]  # 保留最后 3 条消息
    recent_messages = messages[-3:]  # 最近 3 条消息保留
    
    if not messages_to_summarize:
        logger.debug("没有需要总结的消息")
        return {}
    
    # 构建总结提示词
    summary_prompt = f"""你是一个对话总结助手。请总结以下对话历史，提取关键信息和决策点。

**对话历史**（共 {len(messages_to_summarize)} 条消息）：
"""
    
    # 添加消息内容（只提取关键信息，避免过长）
    for i, msg in enumerate(messages_to_summarize, 1):
        if hasattr(msg, 'content') and msg.content:
            content_preview = str(msg.content)[:200]  # 只取前 200 字符
            summary_prompt += f"\n{i}. {content_preview}..."
        elif isinstance(msg, dict):
            content_preview = str(msg.get('content', ''))[:200]
            summary_prompt += f"\n{i}. {content_preview}..."
    
    summary_prompt += f"""

**任务**：
1. 总结这段对话的核心内容（分支事件生成、决策点、状态变化等）
2. 提取关键信息：已生成的分支事件、合流点选择、结局选择等
3. 保留重要的上下文信息，但去除冗余细节

**输出格式**（JSON）：
{{
  "summary": "对话总结（2-3句话）",
  "key_points": ["关键点1", "关键点2", "关键点3"],
  "branch_events_count": 已生成的分支事件数量,
  "decisions_made": ["决策1", "决策2"]
}}
"""
    
    # 使用执行 LLM 进行总结（不是流程控制 LLM）
    try:
        response = await path_functions.llm_client.invoke(
            prompt=summary_prompt,
            return_json=True,
        )
        
        if isinstance(response, str):
            response = json.loads(response)
        
        summary_text = response.get("summary", "")
        key_points = response.get("key_points", [])
        
        logger.info(f"✅ 总结完成：{summary_text[:100]}...")
        
        # 更新状态：添加总结，清空旧消息，保留最近的消息
        return {
            "messages": recent_messages,  # 只保留最近的消息
            "conversation_summary": summary_text,  # 添加总结
            "conversation_key_points": key_points,  # 添加关键点
        }
        
    except Exception as e:
        logger.warning(f"总结失败: {e}，继续执行（不清空消息）")
        # 如果总结失败，不清空消息，继续执行
        return {}


@node_decorator(needs_sync=True)
async def llm_decision_node(state: Dict[str, Any], path_functions) -> Dict[str, Any]:
    """
    LLM 决策节点 🧠
    
    使用 flow_llm_client + function calling 让 LLM 决定下一步操作
    注意：使用 path_functions.flow_llm_client，保持 LLM 客户端的统一管理
    """
    iteration = state.get("iteration", 0) + 1

    # 检查 flow_llm_client 是否存在
    if not path_functions.flow_llm_client:
        logger.warning("flow_llm_client 未设置，使用 llm_client")
        path_functions.flow_llm_client = path_functions.llm_client
    
    # 构建提示词
    max_iterations = state.get("max_iterations")
    prompt = build_decision_prompt(state, iteration, max_iterations, path_functions)
    
    # 如果有对话总结，添加到提示词中（让 LLM 知道之前的上下文）🧠
    conversation_summary = state.get("conversation_summary")
    conversation_key_points = state.get("conversation_key_points", [])
    if conversation_summary:
        summary_context = f"\n\n**之前的对话总结**：{conversation_summary}"
        if conversation_key_points:
            summary_context += f"\n**关键点**：{', '.join(conversation_key_points)}"
        prompt = prompt + summary_context
    
    # 获取函数定义（给流程控制 LLM 看！）
    function_definitions = path_functions.get_function_definitions()
    
    # 调用流程控制 LLM（使用 function calling）
    response = await path_functions.flow_llm_client.invoke(
        prompt=prompt,
        return_json=True,
        tools=function_definitions,  # 这里需要 function definitions！
        tool_choice="auto",
    )
    
    if isinstance(response, str):
        response = json.loads(response)
    
    # 转换为 LangGraph 消息格式
    messages = state.get("messages", [])
    messages.append(HumanMessage(content=prompt))
    
    # 将 AsyncLLMClient 返回的 tool_calls 转换为 LangChain 格式
    tool_calls = response.get("tool_calls", [])
    if tool_calls:
        # 转换为 LangChain 的 ToolCall 格式
        langchain_tool_calls = []
        for tc in tool_calls:
            func_info = tc.get("function", {})
            langchain_tool_calls.append(
                ToolCall(
                    name=func_info.get("name", ""),
                    args=json.loads(func_info.get("arguments", "{}")),
                    id=tc.get("id", ""),
                )
            )
        messages.append(AIMessage(content="", tool_calls=langchain_tool_calls))
    else:
        messages.append(AIMessage(content="没有调用函数"))
    
    return {
        "iteration": iteration,
        "messages": messages,
    }


@node_decorator(needs_sync=True)
async def generate_branch_event_node(state: Dict[str, Any], path_functions) -> Dict[str, Any]:
    """
    🎭 节点：生成分支事件（实际执行步骤）
    
    作用：执行完整的业务逻辑，生成分支事件并应用状态变化
    职责：
    1. 从工具消息中提取 description 和 reasoning
    2. 生成分支事件（调用 _generate_single_branch_event）
    3. 应用状态变化（调用 state_applier.apply_state_change）
    4. 更新 LangGraph 状态（branch_events, current_states 等）
    
    注意：这个节点在工具函数返回 {"status": "generated"} 后被路由调用
    """
    # 从 ToolMessage 中提取工具调用结果
    messages = state.get("messages", [])
    tool_messages = [msg for msg in messages if isinstance(msg, ToolMessage)]
    
    # 查找 generate_next_branch_event 的调用结果
    description = None
    reasoning = None
    
    for tool_msg in reversed(tool_messages):  # 从最新的开始查找
        try:
            # 确保 content 是字符串，然后解析 JSON
            content = tool_msg.content if isinstance(tool_msg.content, str) else str(tool_msg.content)
            result = json.loads(content)
            
            # 确保 result 是字典类型
            if not isinstance(result, dict):
                logger.debug(f"工具消息内容不是字典: {type(result)}")
                continue
                
            if result.get("status") == "generated":
                # 提取 description 和 reasoning（供生成事件使用）
                description = result.get("description", "")
                reasoning = result.get("reasoning", "")
                break
        except (json.JSONDecodeError, AttributeError, TypeError) as e:
            logger.debug(f"解析工具消息失败: {e}, content type: {type(tool_msg.content) if hasattr(tool_msg, 'content') else 'N/A'}")
            continue
    
    # 如果没有找到结果，返回空（不应该发生）
    if not description or not reasoning:
        logger.warning("未找到分支事件生成结果或缺少必要参数")
        return {}
    
    # 🔧 关键：执行完整的业务逻辑（生成事件、应用状态变化）
    # 验证 fork_event_id 是否存在
    if not path_functions.fork_event_id:
        logger.error("❌ fork_event_id 未设置！无法生成分支事件")
        return {}
    
    # 🔧 调试信息：显示当前 branch_events 的状态
    logger.info(f"📊 当前 branch_events 数量: {len(path_functions.branch_events)}")
    if path_functions.branch_events:
        logger.info(f"📋 已生成的事件: {[e.get('event_id', 'unknown') for e in path_functions.branch_events]}")
    else:
        logger.info(f"⚠️  branch_events 为空（这是第一个事件）")
    
    logger.info(f"🎯 当前 fork_event_id: {path_functions.fork_event_id}")
    
    # 生成分支事件（调用内部方法）
    # 🔢 传递 fork_event_id 用于生成绝对序号
    branch_event = await path_functions._generate_single_branch_event(
        branch_id=f"{path_functions.fork_event_id}_branch_{path_functions.branch_choice.get('choice_id', 'unknown')}",
        sequence_number=len(path_functions.branch_events) + 1,
        previous_event=path_functions.branch_events[-1] if path_functions.branch_events else None,
        branch_choice=path_functions.branch_choice,
        current_states=path_functions.current_states,
        merge_options=path_functions.merge_options,
        fork_event_id=path_functions.fork_event_id,  # 🔢 传递分叉点ID，用于生成绝对序号
    )
    
    # 🔧 调试：检查 branch_event 是否被正确生成
    # 添加到分支事件列表
    path_functions.branch_events.append(branch_event)
    
    # 【状态变化应用】使用 StateApplier 应用分支事件的状态变化
    # 注意：状态变化已经在 _generate_single_branch_event 中通过 StateChangeGenerator 生成
    if branch_event.get("state_changes") and path_functions.state_applier:
        event_info = branch_event.get("event_data", {})
        updated_states, _, snapshot = await path_functions.state_applier.apply_state_change(
            event_id=branch_event.get("event_id", f"{path_functions.fork_event_id}_branch_{len(path_functions.branch_events)}"),
            event_info=event_info,
            selected_state_changes=branch_event.get("state_changes", []),
            current_states=path_functions.current_states,
        )
        path_functions.current_states = updated_states
        branch_event["snapshot"] = snapshot.to_dict_for_llm() if snapshot else None
    
    # 返回状态更新（LangGraph 会自动合并，装饰器会自动同步）
    result = {
        "branch_events": path_functions.branch_events.copy() if path_functions.branch_events else [],  # 🔧 使用 copy() 避免引用问题
        "current_states": path_functions.current_states,
    }
    
    return result


@node_decorator(needs_sync=True)
async def process_mainline_node(state: Dict[str, Any], path_functions) -> Dict[str, Any]:
    """
    处理主线事件节点 🔄
    
    直接调用 PathGenerationFunctions.process_mainline_after_merge
    在合流前可以添加人机协作确认（如果启用）
    """
    # 🔧 关键修复：从 ToolMessage 中提取 merge_point（如果 state 中没有）
    # 因为路由函数不能真正更新 state，我们需要从工具消息中提取
    merge_point = path_functions.merge_point or state.get("merge_point")
    
    if not merge_point:
        # 从 ToolMessage 中查找 merge_to_mainline 的调用结果
        messages = state.get("messages", [])
        tool_messages = [msg for msg in messages if isinstance(msg, ToolMessage)]
        
        for tool_msg in reversed(tool_messages):
            try:
                result = json.loads(tool_msg.content)
                if result.get("status") == "merged" and "merge_point" in result:
                    merge_point = result["merge_point"]
                    # 更新到 path_functions 和 state
                    path_functions.merge_point = merge_point
                    break
            except:
                pass
    
    # 人机协作：确认合流点（如果启用）✋
    enable_hitl = state.get("enable_human_in_the_loop", False)
    
    if enable_hitl and merge_point:
        try:
            # 构建确认信息
            branch_events_count = len(state.get("branch_events", []))
            confirmation_message = f"""
🤔 **人机协作确认：合流点选择**

**当前状态**：
- 已生成分支事件数: {branch_events_count}
- 选择的合流点: {merge_point}
- 合流后将处理主线事件直到结局

**请确认**：
1. 继续 - 使用此合流点继续执行
2. 修改 - 修改合流点（需要提供新的合流点ID）
3. 取消 - 取消合流，继续生成分支事件

请输入选择（1/2/3）：
"""
            
            # 使用 interrupt 暂停执行，等待用户输入
            user_response = interrupt(confirmation_message)
            
            # 处理用户响应
            if isinstance(user_response, str):
                user_response = user_response.strip().lower()
                
                if user_response in ["1", "继续", "continue", "y", "yes"]:
                    logger.info("✅ 用户确认：继续合流")
                elif user_response in ["2", "修改", "modify", "edit"]:
                    # 请求新的合流点
                    new_merge_point = interrupt("请输入新的合流点ID（例如：E15）：")
                    if new_merge_point and isinstance(new_merge_point, str):
                        merge_point = new_merge_point.strip()
                        logger.info(f"✅ 用户修改合流点: {merge_point}")
                        # 更新状态中的合流点
                        state["merge_point"] = merge_point
                        path_functions.merge_point = merge_point
                elif user_response in ["3", "取消", "cancel", "n", "no"]:
                    logger.info("❌ 用户取消合流，继续生成分支事件")
                    # 返回空，让工作流继续生成分支事件
                    return {"merge_point": None, "should_continue_generating": True}
        except Exception as e:
            logger.warning(f"人机协作确认失败: {e}，继续执行")
            # 如果 interrupt 失败（可能不在支持的环境中），继续执行
    
    # 调用方法
    result = await path_functions.process_mainline_after_merge()
    
    # 🔍 DEBUG：检查 process_mainline_after_merge 返回的结果
    mainline_events_from_result = result.get("mainline_events", [])
    logger.info(f"🔍 [DEBUG] process_mainline_node: process_mainline_after_merge 返回了 {len(mainline_events_from_result)} 个主线事件")
    if mainline_events_from_result:
        logger.info(f"🔍 [DEBUG] process_mainline_node: mainline_events 前3个事件的 event_id: {[e.get('event_id') if isinstance(e, dict) else 'N/A' for e in mainline_events_from_result[:3]]}")
        logger.info(f"🔍 [DEBUG] process_mainline_node: mainline_events 最后3个事件的 event_id: {[e.get('event_id') if isinstance(e, dict) else 'N/A' for e in mainline_events_from_result[-3:]]}")
    
    # 🔍 DEBUG：检查 path_functions._mainline_events
    if hasattr(path_functions, '_mainline_events'):
        logger.info(f"🔍 [DEBUG] process_mainline_node: path_functions._mainline_events 数量: {len(path_functions._mainline_events) if isinstance(path_functions._mainline_events, list) else 'N/A'}")
    else:
        logger.warning(f"🔍 [DEBUG] process_mainline_node: path_functions._mainline_events 不存在！")
    
    # 获取候选结局
    path_combination = state.get("path_combination", [])
    matching_path = path_functions._find_matching_path_result(path_combination)
    
    ending_candidates = []
    if matching_path:
        candidate_details = matching_path.get("candidate_details", [])
        ending_candidates = [
            detail for detail in candidate_details
            if not detail.get("is_premature", False)
        ]
    
    return_value = {
        "mainline_events": result.get("mainline_events", []),
        "final_states_snapshot": result.get("final_states", {}),
        "current_states": path_functions.current_states,
        "ending_candidates": ending_candidates,
    }
    
    # 🔍 DEBUG：检查返回的值
    logger.info(f"🔍 [DEBUG] process_mainline_node: 返回的 mainline_events 数量: {len(return_value.get('mainline_events', []))}")
    
    return return_value


@node_decorator(needs_sync=False)
async def identify_similar_endings_node(state: Dict[str, Any], path_functions) -> Dict[str, Any]:
    """
    识别相似结局节点 🔍
    
    直接调用 PathGenerationFunctions._identify_similar_endings
    注意：这个节点不需要同步状态，因为只是读取状态并调用方法
    """
    ending_candidates = state.get("ending_candidates", [])
    is_early_ending = state.get("is_early_ending", False)
    
    # 如果是提前结局，获取提前结局候选
    if is_early_ending and not ending_candidates:
        fork_event_id = state.get("fork_event_id")
        branch_choice = state.get("branch_choice")
        ending_candidates = path_functions._find_matching_ending_candidates(
            fork_event_id=fork_event_id,
            branch_choice=branch_choice,
            full_path_combination=state.get("path_combination") or [],
        )
    
    if not ending_candidates:
        return {
            "similar_ending_groups": [],
            "merged_endings": [],
        }
    
    similar_groups = await path_functions._identify_similar_endings(ending_candidates)
    return {
        "similar_ending_groups": similar_groups,
        "ending_candidates": ending_candidates,  # 保留原始候选
    }


@node_decorator(needs_sync=False)
async def merge_similar_endings_node(state: Dict[str, Any], path_functions) -> Dict[str, Any]:
    """
    合并相似结局节点 🔗
    
    直接调用 PathGenerationFunctions._merge_similar_endings
    注意：这个节点不需要同步状态，因为只是读取状态并调用方法
    """
    ending_candidates = state.get("ending_candidates", [])
    final_states = state.get("current_states")
    path_combination = state.get("path_combination", [])
    
    if not ending_candidates:
        return {"merged_endings": []}
    
    merged_endings = await path_functions._merge_similar_endings(
        candidates=ending_candidates,
        final_states=final_states,
        path_combination=path_combination,
    )
    return {"merged_endings": merged_endings}


@node_decorator(needs_sync=False)
async def select_dramatic_ending_node(state: Dict[str, Any], path_functions) -> Dict[str, Any]:
    """
    选择文学性结局节点 🎭
    
    直接调用 PathGenerationFunctions._select_most_dramatic_ending
    在最终选择前可以添加人机协作确认（如果启用）
    注意：这个节点不需要同步状态，因为只是读取状态并调用方法
    """
    merged_endings = state.get("merged_endings", [])
    final_states = state.get("current_states")
    path_combination = state.get("path_combination", [])
    
    if not merged_endings:
        return {"selected_ending": None}
    
    if len(merged_endings) == 1:
        selected = merged_endings[0]
    else:
        selected = await path_functions._select_most_dramatic_ending(
            similar_endings=merged_endings,
            final_states=final_states,
            path_combination=path_combination,
        )
    
    # 人机协作：确认最终结局（如果启用）✋
    enable_hitl = state.get("enable_human_in_the_loop", False)
    
    if enable_hitl and selected:
        try:
            # 构建确认信息
            ending_name = selected.get("name", selected.get("id", "未知结局"))
            ending_description = selected.get("description", "")[:200]
            confirmation_message = f"""
🎭 **人机协作确认：最终结局选择**

**选择的结局**：
- 名称: {ending_name}
- 描述: {ending_description}...

**候选结局列表**（共 {len(merged_endings)} 个）：
"""
            for i, ending in enumerate(merged_endings, 1):
                ending_name_item = ending.get("name", ending.get("id", "未知"))
                ending_desc_item = ending.get("description", "")[:100]
                is_selected = ending.get("id") == selected.get("id")
                mark = "✅ [已选择]" if is_selected else ""
                confirmation_message += f"\n{i}. {ending_name_item} {mark}\n   {ending_desc_item}...\n"
            
            confirmation_message += """
**请确认**：
1. 确认 - 使用此结局
2. 重新选择 - 从候选列表中选择其他结局（需要提供序号）
3. 取消 - 取消选择

请输入选择（1/2/3）：
"""
            
            # 使用 interrupt 暂停执行，等待用户输入
            user_response = interrupt(confirmation_message)
            
            # 处理用户响应
            if isinstance(user_response, str):
                user_response = user_response.strip().lower()
                
                if user_response in ["1", "确认", "confirm", "y", "yes"]:
                    logger.info(f"✅ 用户确认结局: {ending_name}")
                elif user_response in ["2", "重新选择", "reselect", "choose"]:
                    # 请求选择其他结局
                    choice_input = interrupt(f"请输入要选择的结局序号（1-{len(merged_endings)}）：")
                    try:
                        choice_index = int(choice_input) - 1
                        if 0 <= choice_index < len(merged_endings):
                            selected = merged_endings[choice_index]
                            logger.info(f"✅ 用户选择结局: {selected.get('name', selected.get('id'))}")
                        else:
                            logger.warning(f"无效的序号: {choice_input}，使用原选择")
                    except (ValueError, TypeError):
                        logger.warning(f"无法解析序号: {choice_input}，使用原选择")
                elif user_response in ["3", "取消", "cancel", "n", "no"]:
                    logger.info("❌ 用户取消结局选择")
                    return {"selected_ending": None}
        except Exception as e:
            logger.warning(f"人机协作确认失败: {e}，继续执行")
            # 如果 interrupt 失败（可能不在支持的环境中），继续执行
    
    return {"selected_ending": selected}


@node_decorator(needs_sync=True)
async def create_early_ending_node(state: Dict[str, Any], path_functions) -> Dict[str, Any]:
    """
    🎭 节点：创建提前结局（实际执行步骤）
    
    作用：执行完整的业务逻辑，生成结局事件并应用状态变化
    职责：
    1. 生成结局事件（调用 _generate_ending_event）
    2. 应用状态变化（调用 state_applier.apply_state_change）
    3. 更新 LangGraph 状态（branch_events, is_early_ending 等）
    
    注意：这个节点在工具函数返回 {"status": "early_ending_created"} 后被路由调用
    """
    # 获取提前结局候选
    fork_event_id = state.get("fork_event_id")
    branch_choice = state.get("branch_choice")
    premature_endings = path_functions._find_matching_ending_candidates(
        fork_event_id=fork_event_id,
        branch_choice=branch_choice,
        full_path_combination=state.get("path_combination") or [],
    )
    
    # 🔧 修复：确保 current_states 是 UpdatedState 对象，而不是字典
    current_states_raw = state.get("current_states")
    if isinstance(current_states_raw, dict):
        from state_manager.models import UpdatedState
        try:
            current_states = UpdatedState(**current_states_raw)
        except Exception as e:
            logger.warning(f"⚠️  无法将 current_states 字典转换为 UpdatedState 对象: {e}，使用字典格式")
            current_states = current_states_raw
    else:
        current_states = current_states_raw or path_functions.current_states
    
    ending_event = await path_functions._generate_ending_event(
        branch_id=f"{fork_event_id}_branch_{branch_choice.get('choice_id', 'unknown')}",
        branch_choice=branch_choice,
        current_states=current_states,
        previous_event=state.get("branch_events", [])[-1] if state.get("branch_events") else None,
    )
    
    # 应用状态变化
    if ending_event.get("state_changes") and path_functions.state_applier:
        event_info = ending_event.get("event_data", {})
        updated_states, _, snapshot = await path_functions.state_applier.apply_state_change(
            event_id=ending_event.get("event_id", f"{fork_event_id}_branch_ending"),
            event_info=event_info,
            selected_state_changes=ending_event.get("state_changes", []),
            current_states=path_functions.current_states,
        )
        path_functions.current_states = updated_states
        ending_event["snapshot"] = snapshot.to_dict_for_llm() if snapshot else None
    
    path_functions.branch_events.append(ending_event)
    path_functions.is_early_ending = True
    path_functions.ending_event = ending_event
    
    # 截断 path_combination 为实际经历的前缀，避免后续结局选择节点看到「未来没走到的分支选择」
    branch_choice_id = branch_choice.get("choice_id", "")
    actual_path_combination = path_functions._path_combination_prefix_up_to(
        fork_event_id, branch_choice_id,
    )
    logger.info(
        f"🔀 提前结局：path_combination 从 {len(state.get('path_combination', []))} "
        f"截断为 {len(actual_path_combination)}（仅保留实际经历的选择）"
    )
    
    return {
        "is_early_ending": True,
        "ending_event": ending_event,
        "branch_events": path_functions.branch_events,
        "current_states": path_functions.current_states,
        "ending_candidates": premature_endings,
        "path_combination": actual_path_combination,
    }

