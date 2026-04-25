"""
分支模块 - 主线与支线生成系统 🌳🌿✨

功能：
1. 主线生成：确定不可跳过的关键事件，确保事件有因果关系
2. 支线生成：只影响人物状态、情绪、关系等，不影响主线关键事件
3. 合流点机制：支线通过合流点回到主线轨道
4. 多结局系统：通过关键决策点、成长数值、关系权重判定结局
5. 分支深度控制：确保每个分支点不会引发过多复杂选择

模块结构：
- generation/: 生成器相关（主线、支线、路径生成）
- state/: 状态管理（状态变化、快照）
- utils/: 工具类（数据加载、事件提取、格式化）
- analysis/: 分析器（决策点分析、结局候选确定）
"""

# 从子模块导入主要类，保持向后兼容
# 兼容两种 sys.path 形态：
# - sys.path 含项目根：可 import src.branch...
# - sys.path 含 src/：可 import branch...
try:
    from src.branch.generation import (  # type: ignore
        CanonicalBranchGenerator,
        BranchGenerator,
        PathGenerationFunctions,
    )
    from src.branch.state import (  # type: ignore
        StateChangeGenerator,
    )
    from src.branch.utils import (  # type: ignore
        PathDataLoader,
        PathEventExtractor,
        StoryContentFormatter,
    )
    from src.branch.analysis import (  # type: ignore
        DecisionPointAnalyzer,
        EndingCandidateDeterminer,
    )
except Exception:  # pragma: no cover
    from branch.generation import (  # type: ignore
        CanonicalBranchGenerator,
        BranchGenerator,
        PathGenerationFunctions,
    )
    from branch.state import (  # type: ignore
        StateChangeGenerator,
    )
    from branch.utils import (  # type: ignore
        PathDataLoader,
        PathEventExtractor,
        StoryContentFormatter,
    )
    from branch.analysis import (  # type: ignore
        DecisionPointAnalyzer,
        EndingCandidateDeterminer,
    )

# 注意：以下类可能已不存在或已重命名
# - BranchEventGenerator: 功能已整合到 PathGenerationFunctions
# - EndingSystem: 功能已整合到其他模块

__all__ = [
    # 生成器
    "CanonicalBranchGenerator",
    "BranchGenerator",
    "PathGenerationFunctions",
    # 状态管理
    "StateChangeGenerator",
    # 工具类
    "PathDataLoader",
    "PathEventExtractor",
    "StoryContentFormatter",
    # 分析器
    "DecisionPointAnalyzer",
    "EndingCandidateDeterminer",
]
