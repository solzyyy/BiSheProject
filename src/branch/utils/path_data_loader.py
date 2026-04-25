"""
路径数据加载器 - 统一管理所有数据文件的加载 📚✨

职责：
1. 加载 ending_candidates.json
2. 加载 branches.json
3. 加载 decision_points_analysis.json
4. 加载 extract_chain.json 并建立事件映射

这个类封装了所有数据加载逻辑，避免代码重复。
"""

import json
from typing import Dict, List, Any, Optional
from pathlib import Path


class PathDataLoader:
    """
    路径数据加载器 📚
    
    统一管理所有数据文件的加载和缓存
    """
    
    def __init__(
        self,
        ending_candidates_path: str = "out/ending_candidates.json",
        branches_path: str = "out/branches.json",
        decision_analysis_path: str = "out/decision_points_analysis.json",
        extract_chain_path: str = "out/extract_chain.json",
    ):
        """
        初始化数据加载器
        
        Args:
            ending_candidates_path: 结局候选文件路径
            branches_path: 分支决策文件路径
            decision_analysis_path: 决策点分析文件路径
            extract_chain_path: 提取链文件路径
        """
        self.ending_candidates_path = Path(ending_candidates_path)
        self.branches_path = Path(branches_path)
        self.decision_analysis_path = Path(decision_analysis_path)
        self.extract_chain_path = Path(extract_chain_path)
        
        # 数据缓存
        self.ending_candidates: List[Dict[str, Any]] = []
        self.branches_data: List[Dict[str, Any]] = []
        self.decision_analysis: Optional[Dict[str, Any]] = None
        self.extract_chain_events: Dict[str, Dict[str, Any]] = {}  # event_id -> event
        
        # 加载所有数据
        self.load_all()
    
    def load_all(self) -> None:
        """加载所有数据文件"""
        self.load_ending_candidates()
        self.load_branches_data()
        self.load_decision_analysis()
        self.load_extract_chain()
    
    def load_ending_candidates(self) -> None:
        """加载结局候选文件 📚"""
        if self.ending_candidates_path.exists():
            try:
                with open(self.ending_candidates_path, "r", encoding="utf-8") as f:
                    raw = json.load(f)

                # 向后兼容：
                # 1) 旧格式：直接是 list[dict]
                # 2) 新格式：dict，主列表在 path_results
                if isinstance(raw, list):
                    self.ending_candidates = raw
                elif isinstance(raw, dict):
                    if isinstance(raw.get("path_results"), list):
                        self.ending_candidates = raw["path_results"]
                    elif isinstance(raw.get("ending_candidates"), list):
                        self.ending_candidates = raw["ending_candidates"]
                    elif len(raw) == 1:
                        only_val = next(iter(raw.values()))
                        self.ending_candidates = only_val if isinstance(only_val, list) else []
                    else:
                        #兜底：把所有 list 值扁平化（例如 { "default_endings": [...], ... }）
                        flattened: list[Any] = []
                        for v in raw.values():
                            if isinstance(v, list):
                                flattened.extend(v)
                        self.ending_candidates = flattened
                else:
                    self.ending_candidates = []

                print(
                    f"  ✅ 已加载 {len(self.ending_candidates)} 个路径的结局候选"
                    f"（from {self.ending_candidates_path.name}）"
                )
            except Exception as e:
                print(f"  ⚠️  加载结局候选文件失败: {e}")
                self.ending_candidates = []
        else:
            # 第一次运行 determine-ending-candidates 时文件本来就不存在；
            # 这里把提示降级为信息，避免误导用户以为流程出错。
            print(f"  ℹ️  尚无结局候选文件（首次生成属正常）：{self.ending_candidates_path}")
            self.ending_candidates = []
    
    def load_branches_data(self) -> None:
        """加载分支决策数据（优化：提前加载，避免重复读取）✨"""
        if self.branches_path.exists():
            try:
                with open(self.branches_path, "r", encoding="utf-8") as f:
                    self.branches_data = json.load(f)
                print(f"  ✅ 已加载 {len(self.branches_data)} 个分支决策")
            except Exception as e:
                print(f"  ⚠️  加载分支决策失败: {e}")
                self.branches_data = []
        else:
            self.branches_data = []
    
    def load_decision_analysis(self) -> None:
        """加载决策点分析结果（优化：提前加载，用于优化合流判断）✨"""
        if self.decision_analysis_path.exists():
            try:
                with open(self.decision_analysis_path, "r", encoding="utf-8") as f:
                    self.decision_analysis = json.load(f)
                print(f"  ✅ 已加载决策点分析结果")
            except Exception as e:
                print(f"  ⚠️  加载决策点分析失败: {e}")
                self.decision_analysis = None
        else:
            self.decision_analysis = None
    
    def load_extract_chain(self) -> None:
        """加载 extract_chain.json 并建立事件映射（用于提取 source_text 和 dialogue）✨"""
        if self.extract_chain_path.exists():
            try:
                with open(self.extract_chain_path, "r", encoding="utf-8") as f:
                    data = json.load(f)
                events = data.get("new_events", [])
                # 建立 event_id -> event 的映射
                for event in events:
                    event_id = event.get("event_id", "")
                    if event_id:
                        self.extract_chain_events[event_id] = event
                print(f"  ✅ 已加载 {len(self.extract_chain_events)} 个事件到映射表")
            except Exception as e:
                print(f"  ⚠️  加载 extract_chain.json 失败: {e}")
                self.extract_chain_events = {}
        else:
            print(f"  ⚠️  extract_chain.json 不存在: {self.extract_chain_path}")
            self.extract_chain_events = {}
    
    def get_branch_data(
        self,
        fork_event_id: str,
        branch_choice_id: str,
    ) -> Optional[Dict[str, Any]]:
        """
        从 branches.json 中获取分支决策信息（优化：直接使用已有数据）✨
        
        Args:
            fork_event_id: 分叉事件ID
            branch_choice_id: 分支选择ID
            
        Returns:
            分支决策信息，如果没找到返回 None
        """
        for branch in self.branches_data:
            if (branch.get("fork_event_id") == fork_event_id and 
                branch.get("branch_choice") == branch_choice_id):
                return branch
        return None
    
    def get_extract_event(self, event_id: str) -> Optional[Dict[str, Any]]:
        """
        从 extract_chain.json 中获取事件信息
        
        Args:
            event_id: 事件ID
            
        Returns:
            事件信息，如果没找到返回 None
        """
        return self.extract_chain_events.get(event_id)


__all__ = ["PathDataLoader"]

