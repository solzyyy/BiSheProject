"""
提示词构建 📝✨

职责：
- 构建 LLM 决策节点的提示词
"""

import json
from typing import Dict, Any


def build_decision_prompt(state: Dict[str, Any], iteration: int, max_iterations: int, path_functions) -> str:
    """构建决策提示词"""
    fork_event_id = state.get("fork_event_id", "")
    branch_choice_raw = state.get("branch_choice")
    
    # 确保 branch_choice 是字典类型
    if isinstance(branch_choice_raw, dict):
        branch_choice = branch_choice_raw
    else:
        branch_choice = {}  # 如果不是字典，使用空字典
    
    # 🔧 关键修复：优先使用 path_functions.branch_events（已同步的最新状态）
    # 而不是 state.get("branch_events")，因为 state 可能还没有被更新
    if hasattr(path_functions, 'branch_events') and path_functions.branch_events:
        branch_events = path_functions.branch_events
    else:
        branch_events = state.get("branch_events", [])
    
    # 同样，优先使用 path_functions 中的其他状态
    if hasattr(path_functions, 'current_states') and path_functions.current_states:
        current_states = path_functions.current_states
    else:
        current_states = state.get("current_states")
    
    max_branch_length = state.get("max_branch_length", 2)
    merge_options_raw = state.get("merge_options_raw", [])
    
    generated_events_summary = ""
    if branch_events:
        generated_events_summary = f"\n**已生成的分支事件**（共 {len(branch_events)} 个）：\n"
        for i, event in enumerate(branch_events[-3:], 1):
            generated_events_summary += f"{i}. {event.get('description', '')}\n"
    
    states_text = ""
    if current_states:
        states_dict = current_states.model_dump() if hasattr(current_states, "model_dump") else current_states.dict()
        states_text = f"\n当前分支状态：\n{json.dumps(states_dict, ensure_ascii=False, indent=2)}"
    
    merge_options_text = "\n".join([
        f"- {opt['event_id']} (距离: {opt['distance']} 个事件{'，关键事件' if opt.get('is_critical', False) else ''}) - {opt.get('description', '')[:50]}..."
        for opt in merge_options_raw[:10]  # 🔧 修复：显示更多选项（从5个增加到10个）
    ]) if merge_options_raw else "无合流点选项"
    
    # 安全地获取分支选择信息
    branch_choice_info = ""
    if branch_choice:
        choice_id = branch_choice.get('choice_id', '未知')
        description = branch_choice.get('description', '')
        branch_choice_info = f"- 分支选择: {choice_id} - {description}"
    else:
        branch_choice_info = "- 分支选择: 未设置（请检查初始状态）"
    
    return f"""你是一个分支路径生成助手，负责根据分支选择生成分支事件链。

**重要原则**：
- **分支内容可以与主线完全相反**：不必跟随主线走向，可走向截然不同的结局（如提前结局、坏结局）。
- **主线仅作格式/结构参考**：事件格式、字段规范参考主线，但剧情走向不必与主线一致。
- **优先让确定的提前结局尽早发生**：若当前分支选择注定走向提前结局（如关系决裂、彻底拒绝、恐惧退缩），应尽早调用 `create_early_ending`，不要为凑事件数而强行向主线合流。

**当前进度**：
- 迭代次数: {iteration}/{max_iterations}
- 已生成分支事件数: {len(branch_events)}/{max_branch_length}
{generated_events_summary}

**分支信息**：
- 分叉事件ID: {fork_event_id}
{branch_choice_info}
{states_text}

**合流点选项**：
{merge_options_text}

**工作流程**：
1. 若分支逻辑已明显无法回归主线（状态极端偏离、关系破裂等），**优先**调用 `create_early_ending` 创建提前结局。
2. 若分支需要继续发展且仍有合流可能，调用 `generate_next_branch_event` 生成分支事件。
3. 仅当分支已充分发展且能自然回归主线时，才调用 `merge_to_mainline` 合流到主线。

请根据当前进度和状态，决定下一步操作。"""

