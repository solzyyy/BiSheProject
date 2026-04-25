"""
生成器模块 - 主线、支线和路径生成 🌳🌿✨
"""

try:
    from src.branch.generation.canonical_branch import CanonicalBranchGenerator  # type: ignore
    from src.branch.generation.branch_generator import BranchGenerator  # type: ignore
except Exception:  # pragma: no cover
    from branch.generation.canonical_branch import CanonicalBranchGenerator  # type: ignore
    from branch.generation.branch_generator import BranchGenerator  # type: ignore

__all__ = [
    "CanonicalBranchGenerator",
    "BranchGenerator",
    "PathGenerationFunctions",
]

def __getattr__(name: str):
    """
    懒加载 PathGenerationFunctions，避免在仅用 generate-branch 等步骤时
    提前导入 path_generation_functions 引发 LangGraph 相关提示/副作用。
    """
    if name == "PathGenerationFunctions":
        try:
            from src.branch.generation.path_generation_functions import (  # type: ignore
                PathGenerationFunctions,
            )
        except Exception:  # pragma: no cover
            from branch.generation.path_generation_functions import (  # type: ignore
                PathGenerationFunctions,
            )
        return PathGenerationFunctions
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")





