"""
状态管理模块 🎯✨

负责：
- 扫描事件知识图谱中的 StateChange 节点
- 使用 LLM function calling 应用状态更新
- 管理已生成内容的向量化和检索
- 判断并更新静态人设
"""

from .state_applier import StateApplier
from .content_manager import ContentManager
from .models import UpdatedState, PersonaUpdate
from .state_snapshot import StateSnapshot

__all__ = [
    "StateApplier",
    "ContentManager",
    "UpdatedState",
    "PersonaUpdate",
    "StateSnapshot",
]

