"""
维度注册表 — 状态变化中合法的维度定义

所有状态变化必须使用此处定义的维度名称，LLM 不得自行发明新维度。
"""

from typing import Dict, List, Set


# ── 人物维度（7）──────────────────────────────────────────
CHARACTER_DIMENSIONS: List[str] = [
    "情绪状态",   # 当前情感/心境（如"恐惧"、"愤怒"、"平静"）
    "动机",       # 行为驱动力（如"追求艺术"、"保护师傅"、"自我保存"）
    "处境认知",   # 对当前处境的理解（如"知道被追捕"、"不知危险"）
    "内在冲突",   # 心理矛盾/张力（如"艺术 vs 生存"、"忠诚 vs 自保"）
    "健康状态",   # 身体状况（如"健康"、"受伤"、"疲惫"）
    "价值观",     # 哲学/道德框架（如"艺术高于一切"、"生命至上"）
    "行动能力",   # 能力/资源/自由度（如"被束缚"、"有逃跑工具"、"自由"）
]

# ── 关系维度（4）──────────────────────────────────────────
RELATIONSHIP_DIMENSIONS: List[str] = [
    "信任度",     # 对彼此的信任程度（如"完全信任"、"心存疑虑"）
    "情感倾向",   # 情感态度（如"敬仰"、"怨恨"、"依赖"、"冷淡"）
    "权力动态",   # 权力关系（如"师傅主导"、"势均力敌"、"君臣不可逾越"）
    "连接强度",   # 关系亲密度（如"极度亲密"、"渐行渐远"、"陌生"）
]

# ── 世界维度（6）──────────────────────────────────────────
WORLD_DIMENSIONS: List[str] = [
    "场景状态",   # 当前场景/地点的关键信息
    "时间状态",   # 时间节点/进程
    "物品状态",   # 关键道具的存在与状态（仅追踪影响行动的道具）
    "环境状态",   # 影响行动的环境因素（如"暴风雨阻路"，非纯氛围描写）
    "社会状态",   # 政治/社会动态（如"戒严中"、"民间动荡"）
    "资源状态",   # 可用资源（如"没有食物"、"有银两"）
]

# ── 全部维度集合（用于快速校验）──────────────────────────
ALL_DIMENSIONS: Set[str] = (
    set(CHARACTER_DIMENSIONS)
    | set(RELATIONSHIP_DIMENSIONS)
    | set(WORLD_DIMENSIONS)
)

# ── 旧维度 → 新维度映射（向后兼容）─────────────────────
OLD_TO_NEW_MAPPING: Dict[str, str] = {
    "世界观": "价值观",
    "道德观": "价值观",
    "情感状态": "情感倾向",   # relationship 维度重命名
    "关系类型": "",            # 已废弃，空字符串表示丢弃
    "世界设定": "",            # 已废弃
    "物品存在": "物品状态",
    "物品属性": "物品状态",
}


def get_dimension_list_for_prompt() -> str:
    """生成可直接嵌入 LLM prompt 的维度列表文本。"""
    char_dims = "、".join(CHARACTER_DIMENSIONS)
    rel_dims = "、".join(RELATIONSHIP_DIMENSIONS)
    world_dims = "、".join(WORLD_DIMENSIONS)
    return (
        f"- 人物(character)：{char_dims}\n"
        f"- 关系(relationship)：{rel_dims}\n"
        f"- 世界(world)：{world_dims}"
    )


WORLD_STATE_GUIDANCE = (
    "**世界状态变化的原则**：\n"
    "只追踪「影响角色行动自由」的世界变化（如：道具可用性、位置约束、时间限制）。\n"
    "场景氛围、环境描写、装饰细节不要写成状态变化，它们属于事件描述文字。\n"
    "判断标准：如果这个东西变了，会让某个角色做不了/能做某件事吗？是→追踪，否→不追踪。"
)


__all__ = [
    "CHARACTER_DIMENSIONS",
    "RELATIONSHIP_DIMENSIONS",
    "WORLD_DIMENSIONS",
    "ALL_DIMENSIONS",
    "OLD_TO_NEW_MAPPING",
    "get_dimension_list_for_prompt",
    "WORLD_STATE_GUIDANCE",
]
