"""
🌸 状态基线初始化脚本 🌸

扫描所有 StateChange，按 target_type + dimension 聚类，生成 baseline 状态。
参考状态系统设计，使用 dataclass 定义状态类，为每个实体创建状态实例。
"""

import json
import sys
from pathlib import Path
from typing import Dict, List, Set, Any, Optional
from dataclasses import dataclass, field, asdict
from collections import defaultdict

# 处理 Windows 编码问题
if sys.platform == "win32":
    import io
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')
    sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding='utf-8', errors='replace')

from rich import print


# ==================== 状态类定义 ====================

@dataclass
class CharacterState:
    """
    角色状态类 🐱
    
    这个类管理单个角色的所有维度状态
    就像给每个角色准备了一个小档案袋～
    """
    character_id: str  # 角色ID
    
    # 使用字典存储所有维度，支持动态维度
    dimensions: Dict[str, Any] = field(default_factory=dict)
    metadata: Dict[str, Any] = field(default_factory=dict)
    
    def get_dimension(self, dimension: str, default: Any = None) -> Any:
        """获取维度值"""
        return self.dimensions.get(dimension, default)
    
    def set_dimension(self, dimension: str, value: Any):
        """设置维度值"""
        self.dimensions[dimension] = value
    
    def to_dict(self) -> Dict[str, Any]:
        """转换为字典格式 📋"""
        result = {
            "character_id": self.character_id,
            **self.dimensions,
        }
        if self.metadata:
            result["metadata"] = self.metadata.copy()
        return result


@dataclass
class RelationshipState:
    """
    关系状态类 💕
    
    管理两个角色之间的关系
    就像记录两个人的友谊日记～
    """
    character_a: str  # 角色A的ID
    character_b: str  # 角色B的ID
    
    # 使用字典存储所有维度，支持动态维度
    dimensions: Dict[str, Any] = field(default_factory=dict)
    metadata: Dict[str, Any] = field(default_factory=dict)
    
    def get_dimension(self, dimension: str, default: Any = None) -> Any:
        """获取维度值"""
        return self.dimensions.get(dimension, default)
    
    def set_dimension(self, dimension: str, value: Any):
        """设置维度值"""
        self.dimensions[dimension] = value
    
    def to_dict(self) -> Dict[str, Any]:
        """转换为字典 📋"""
        result = {
            "character_a": self.character_a,
            "character_b": self.character_b,
            **self.dimensions,
        }
        if self.metadata:
            result["metadata"] = self.metadata.copy()
        return result


@dataclass
class WorldState:
    """
    世界状态类 🌍
    
    管理游戏世界的各种状态
    就像游戏世界的记录本～
    """
    state_id: str  # 状态ID
    
    # 使用字典存储所有维度，支持动态维度
    dimensions: Dict[str, Any] = field(default_factory=dict)
    metadata: Dict[str, Any] = field(default_factory=dict)
    
    def get_dimension(self, dimension: str, default: Any = None) -> Any:
        """获取维度值"""
        return self.dimensions.get(dimension, default)
    
    def set_dimension(self, dimension: str, value: Any):
        """设置维度值"""
        self.dimensions[dimension] = value
    
    def to_dict(self) -> Dict[str, Any]:
        """转换为字典 📋"""
        result = {
            "state_id": self.state_id,
            **self.dimensions,
        }
        if self.metadata:
            result["metadata"] = self.metadata.copy()
        return result


# ==================== 状态管理器 ====================

class StateManager:
    """
    状态管理器 🎮
    
    统一管理所有角色、关系和世界状态
    就像游戏的大管家～
    """
    
    def __init__(self):
        self.characters: Dict[str, CharacterState] = {}
        self.relationships: Dict[str, RelationshipState] = {}
        self.world_states: Dict[str, WorldState] = {}
    
    def add_character(self, character_id: str, **kwargs) -> CharacterState:
        """添加角色 🐱"""
        char = CharacterState(character_id=character_id, **kwargs)
        self.characters[character_id] = char
        return char
    
    def add_relationship(self, char_a: str, char_b: str, **kwargs) -> RelationshipState:
        """添加关系 💕"""
        # 使用排序后的键来确保一致性
        rel_key = f"{char_a}|{char_b}" if char_a < char_b else f"{char_b}|{char_a}"
        rel = RelationshipState(character_a=char_a, character_b=char_b, **kwargs)
        self.relationships[rel_key] = rel
        return rel
    
    def add_world_state(self, state_id: str, **kwargs) -> WorldState:
        """添加世界状态 🌍"""
        world = WorldState(state_id=state_id, **kwargs)
        self.world_states[state_id] = world
        return world
    
    def get_state_snapshot(self) -> Dict[str, Any]:
        """获取当前所有状态的快照 📸"""
        return {
            "characters": {
                char_id: char.to_dict() 
                for char_id, char in self.characters.items()
            },
            "relationships": {
                rel_key: rel.to_dict()
                for rel_key, rel in self.relationships.items()
            },
            "world_states": {
                state_id: state.to_dict()
                for state_id, state in self.world_states.items()
            }
        }


# ==================== 默认值映射 ====================

def get_default_value_for_dimension(dimension: str, target_type: str) -> Any:
    """
    为维度获取合理的默认初始值 🌸
    
    Args:
        dimension: 维度名称
        target_type: 目标类型（character, relationship, world）
    
    Returns:
        默认值（如果维度有默认值，否则返回 None）
    """
    # 人物维度默认值
    if target_type == "character":
        defaults = {
            "健康状态": "健康",
            "情绪状态": "",
            "处境认知": "",
            "动机": "",
            "世界观": "",
            "内在冲突": [],
        }
        return defaults.get(dimension, None)
    
    # 关系维度默认值
    elif target_type == "relationship":
        defaults = {
            "权力动态": "equal",  # equal, superior, subordinate
            "连接强度": 0,  # 0-100
            "情感状态": "",
            "关系类型": "",
        }
        return defaults.get(dimension, None)
    
    # 世界维度默认值
    elif target_type == "world":
        defaults = {
            "物品存在": "",
            "物品状态": "",
            "场景状态": "",
            "时间状态": "",
            "环境状态": "",
            "资源状态": "",
            "社会状态": "",
        }
        return defaults.get(dimension, None)
    
    return None


# ==================== 数据加载 ====================

def load_state_changes(file_path: Path) -> List[Dict[str, Any]]:
    """加载状态变化数据 📂"""
    if not file_path.exists():
        print(f"[yellow]⚠️  文件不存在: {file_path}[/yellow]")
        return []
    
    with open(file_path, 'r', encoding='utf-8') as f:
        data = json.load(f)
    
    return data.get("state_changes", [])


# ==================== 实体和维度提取 ====================

def extract_entities_and_dimensions(state_changes: List[Dict[str, Any]]) -> Dict[str, Any]:
    """
    提取所有实体（人物、关系、物品）和维度 🔍
    
    Returns:
        {
            "world_dimensions": {"场景状态", "环境状态", ...},
            "character_dimensions": {"情绪状态", "动机", ...},
            "relationship_dimensions": {"情感状态", "权力动态", ...},
            "characters": {"王佛", "林", ...},  # 所有人物ID
            "relationships": {"王佛|林", ...},  # 所有关系对（格式：char_a|char_b）
            "world_items": {"场景_汉王国大道", "物品_画笔", ...}  # 所有物品/世界变量ID
        }
    """
    result = {
        "world_dimensions": set(),
        "character_dimensions": set(),
        "relationship_dimensions": set(),
        "characters": set(),
        "relationships": set(),
        "world_items": set(),
    }
    
    for sc in state_changes:
        target_type = sc.get("target_type", "")
        dimension = sc.get("dimension", "")
        target_id = sc.get("target_id", "")
        
        if not target_type or not dimension:
            continue
        
        # 提取维度
        if target_type == "world":
            result["world_dimensions"].add(dimension)
            # 提取物品/世界变量ID
            if target_id:
                if isinstance(target_id, str):
                    result["world_items"].add(target_id)
                elif isinstance(target_id, dict):
                    item_id = target_id.get("id") or target_id.get("name") or str(target_id)
                    result["world_items"].add(item_id)
        elif target_type == "character":
            result["character_dimensions"].add(dimension)
            # 提取人物ID
            if target_id:
                if isinstance(target_id, str):
                    result["characters"].add(target_id)
                elif isinstance(target_id, dict):
                    char_id = target_id.get("id") or target_id.get("name") or str(target_id)
                    result["characters"].add(char_id)
        elif target_type == "relationship":
            result["relationship_dimensions"].add(dimension)
            # 提取关系对
            if target_id:
                if isinstance(target_id, list):
                    # target_id 是列表格式，如 ["C010", "C008"]
                    if len(target_id) >= 2:
                        char_a, char_b = target_id[0], target_id[1]
                        # 确保关系键的一致性（排序）
                        rel_key = f"{char_a}|{char_b}" if char_a < char_b else f"{char_b}|{char_a}"
                        result["relationships"].add(rel_key)
                elif isinstance(target_id, str):
                    # 可能是 "char_a|char_b" 格式
                    result["relationships"].add(target_id)
                elif isinstance(target_id, dict):
                    char_a = target_id.get("character_a") or target_id.get("char_a") or target_id.get("a")
                    char_b = target_id.get("character_b") or target_id.get("char_b") or target_id.get("b")
                    if char_a and char_b:
                        # 确保关系键的一致性（排序）
                        rel_key = f"{char_a}|{char_b}" if char_a < char_b else f"{char_b}|{char_a}"
                        result["relationships"].add(rel_key)
    
    return result


# ==================== Baseline 创建 ====================

def create_baseline_state(
    world_dimensions: Set[str],
    character_dimensions: Set[str],
    relationship_dimensions: Set[str],
    characters: Set[str],
    relationships: Set[str],
    world_items: Set[str],
) -> Dict[str, Any]:
    """
    创建 baseline 状态 - 使用 StateManager 管理所有状态 🌸
    
    Returns:
        {
            "world_baseline": {
                "场景_汉王国大道": {
                    "场景状态": "",
                    "环境状态": "",
                    ...
                },
                ...
            },
            "character_baseline": {
                "王佛": {
                    "情绪状态": "",
                    "动机": "",
                    "健康状态": "健康",
                    ...
                },
                ...
            },
            "relationship_baseline": {
                "王佛|林": {
                    "情感状态": "",
                    "权力动态": "equal",
                    "连接强度": 0,
                    ...
                },
                ...
            }
        }
    """
    manager = StateManager()
    
    # 为每个物品/世界变量创建状态
    for item_id in sorted(world_items):
        world_state = manager.add_world_state(item_id)
        for dim in sorted(world_dimensions):
            default_value = get_default_value_for_dimension(dim, "world")
            value = default_value if default_value is not None else ""
            world_state.set_dimension(dim, value)
    
    # 为每个人物创建状态
    for char_id in sorted(characters):
        char_state = manager.add_character(char_id)
        for dim in sorted(character_dimensions):
            default_value = get_default_value_for_dimension(dim, "character")
            # 对于列表类型，使用空列表
            if dim == "内在冲突" or dim.endswith("列表") or dim.endswith("_list"):
                value = [] if default_value is None else default_value
            else:
                value = default_value if default_value is not None else ""
            char_state.set_dimension(dim, value)
    
    # 为每对关系创建状态
    for rel_key in sorted(relationships):
        # 解析关系对
        parts = rel_key.split("|")
        if len(parts) == 2:
            char_a, char_b = parts
            rel_state = manager.add_relationship(char_a, char_b)
            for dim in sorted(relationship_dimensions):
                default_value = get_default_value_for_dimension(dim, "relationship")
                # 对于数值类型，使用数字
                if dim == "连接强度" or dim.endswith("强度") or dim.endswith("_strength"):
                    value = 0 if default_value is None else default_value
                else:
                    value = default_value if default_value is not None else ""
                rel_state.set_dimension(dim, value)
    
    # 转换为字典格式
    snapshot = manager.get_state_snapshot()
    
    # 重新组织格式以匹配 baseline 结构
    baseline = {
        "world_baseline": {},
        "character_baseline": {},
        "relationship_baseline": {},
    }
    
    # 转换世界状态
    for state_id, state_dict in snapshot["world_states"].items():
        # 移除 state_id，只保留维度
        world_dict = {k: v for k, v in state_dict.items() if k != "state_id" and k != "metadata"}
        baseline["world_baseline"][state_id] = world_dict
    
    # 转换人物状态
    for char_id, char_dict in snapshot["characters"].items():
        # 移除 character_id，只保留维度
        char_dict_clean = {k: v for k, v in char_dict.items() if k != "character_id" and k != "metadata"}
        baseline["character_baseline"][char_id] = char_dict_clean
    
    # 转换关系状态
    for rel_key, rel_dict in snapshot["relationships"].items():
        # 移除 character_a 和 character_b，只保留维度
        rel_dict_clean = {k: v for k, v in rel_dict.items() if k not in ["character_a", "character_b", "metadata"]}
        baseline["relationship_baseline"][rel_key] = rel_dict_clean
    
    return baseline


# ==================== 主函数 ====================

def main():
    """主函数 🚀"""
    # 默认文件路径
    world_states_path = Path("out/world_states.json")
    character_states_path = Path("out/character_states.json")
    relationship_states_path = Path("out/relationship_states.json")
    output_path = Path("out/state_baseline.json")
    
    # 从命令行参数获取路径（如果提供）
    if len(sys.argv) > 1:
        world_states_path = Path(sys.argv[1])
    if len(sys.argv) > 2:
        character_states_path = Path(sys.argv[2])
    if len(sys.argv) > 3:
        relationship_states_path = Path(sys.argv[3])
    if len(sys.argv) > 4:
        output_path = Path(sys.argv[4])
    
    print("=" * 70)
    print("🌸 状态变量初始化系统 🌸")
    print("=" * 70)
    print()
    
    # 1️⃣ 扫描所有 StateChange
    print("[cyan]📂 扫描所有 StateChange...[/cyan]")
    
    all_state_changes = []
    
    # 加载世界状态
    world_states = load_state_changes(world_states_path)
    print(f"[green]✓[/green] 从 {world_states_path.name} 加载了 {len(world_states)} 个世界状态变化")
    all_state_changes.extend(world_states)
    
    # 加载人物状态
    character_states = load_state_changes(character_states_path)
    print(f"[green]✓[/green] 从 {character_states_path.name} 加载了 {len(character_states)} 个人物状态变化")
    all_state_changes.extend(character_states)
    
    # 加载关系状态
    relationship_states = load_state_changes(relationship_states_path)
    print(f"[green]✓[/green] 从 {relationship_states_path.name} 加载了 {len(relationship_states)} 个关系状态变化")
    all_state_changes.extend(relationship_states)
    
    print(f"[green]✓[/green] 总共扫描了 {len(all_state_changes)} 个状态变化")
    print()
    
    # 2️⃣ 提取所有实体和维度
    print("[cyan]🔍 提取所有实体（人物、关系、物品）和维度...[/cyan]")
    entities_data = extract_entities_and_dimensions(all_state_changes)
    
    world_dimensions = entities_data["world_dimensions"]
    character_dimensions = entities_data["character_dimensions"]
    relationship_dimensions = entities_data["relationship_dimensions"]
    characters = entities_data["characters"]
    relationships = entities_data["relationships"]
    world_items = entities_data["world_items"]
    
    print(f"[green]✓[/green] 世界维度集合: {len(world_dimensions)} 个维度")
    for dim in sorted(world_dimensions):
        print(f"    - {dim}")
    
    print(f"[green]✓[/green] 人物内在维度集合: {len(character_dimensions)} 个维度")
    for dim in sorted(character_dimensions):
        print(f"    - {dim}")
    
    print(f"[green]✓[/green] 关系维度集合: {len(relationship_dimensions)} 个维度")
    for dim in sorted(relationship_dimensions):
        print(f"    - {dim}")
    print()
    
    print(f"[green]✓[/green] 发现 {len(characters)} 个人物")
    for char_id in sorted(characters):
        print(f"    - {char_id}")
    
    print(f"[green]✓[/green] 发现 {len(relationships)} 对关系")
    for rel_key in sorted(relationships):
        print(f"    - {rel_key}")
    
    print(f"[green]✓[/green] 发现 {len(world_items)} 个物品/世界变量")
    for item_id in sorted(world_items):
        print(f"    - {item_id}")
    print()
    
    # 3️⃣ 创建 baseline（使用 StateManager）
    print("[cyan]📝 创建 baseline 状态（使用 StateManager 管理状态）...[/cyan]")
    baseline = create_baseline_state(
        world_dimensions,
        character_dimensions,
        relationship_dimensions,
        characters,
        relationships,
        world_items,
    )
    
    print(f"[green]✓[/green] 为 {len(characters)} 个人物创建了 baseline")
    print(f"[green]✓[/green] 为 {len(relationships)} 对关系创建了 baseline")
    print(f"[green]✓[/green] 为 {len(world_items)} 个物品/世界变量创建了 baseline")
    print()
    
    # 4️⃣ 保存结果
    print(f"[cyan]💾 保存结果到: {output_path}[/cyan]")
    output_data = {
        "baseline": baseline,
        "dimension_summary": {
            "world_dimensions": sorted(world_dimensions),
            "character_dimensions": sorted(character_dimensions),
            "relationship_dimensions": sorted(relationship_dimensions),
            "total_dimensions": len(world_dimensions) + len(character_dimensions) + len(relationship_dimensions)
        },
        "entity_summary": {
            "characters": sorted(characters),
            "relationships": sorted(relationships),
            "world_items": sorted(world_items),
            "total_characters": len(characters),
            "total_relationships": len(relationships),
            "total_world_items": len(world_items),
        },
        "metadata": {
            "source_files": {
                "world_states": str(world_states_path),
                "character_states": str(character_states_path),
                "relationship_states": str(relationship_states_path)
            },
            "total_state_changes_scanned": len(all_state_changes),
            "note": "Baseline 使用 StateManager 和 dataclass 状态类管理，使用合理默认值（如健康状态='健康'，连接强度=0）。"
        }
    }
    
    output_path.parent.mkdir(parents=True, exist_ok=True)
    with open(output_path, 'w', encoding='utf-8') as f:
        json.dump(output_data, f, ensure_ascii=False, indent=2)
    
    print(f"[green]✓[/green] Baseline 状态已保存到: {output_path}")
    print()
    
    print("=" * 70)
    print("🎉 状态变量初始化完成！")
    print("=" * 70)


if __name__ == "__main__":
    main()

