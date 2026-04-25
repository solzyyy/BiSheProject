"""
导入状态变化到 Neo4j 图谱 🎯✨

从 state_changes JSON 文件读取状态变化数据，写入 Neo4j 数据库。
支持 world_states.json, character_states.json, relationship_states.json
"""

import json
import sys
from pathlib import Path
from rich import print
from rich.progress import Progress, SpinnerColumn, TextColumn, BarColumn, TaskProgressColumn

# 添加 src 目录到路径
src_dir = Path(__file__).parent.parent.parent
sys.path.insert(0, str(src_dir))

from core.neo4j_client import Neo4jClient


def load_state_changes(json_path: Path) -> list:
    """加载状态变化数据 📂"""
    with open(json_path, "r", encoding="utf-8") as f:
        data = json.load(f)
    return data.get("state_changes", [])


def main():
    """主函数 🚀"""
    # 默认输入文件路径
    default_input = Path("out/world_states.json")
    
    # 从命令行参数获取路径（如果提供）
    if len(sys.argv) > 1:
        input_path = Path(sys.argv[1])
    else:
        input_path = default_input
    
    print("=" * 60)
    print("🎯 状态变化导入工具 (Neo4j)")
    print("=" * 60)
    print()
    
    if not input_path.exists():
        print(f"[red]❌ 状态变化文件不存在: {input_path}[/red]")
        sys.exit(1)
    
    print(f"[cyan]📂 加载状态变化数据: {input_path}[/cyan]")
    state_changes = load_state_changes(input_path)
    print(f"[green]✓[/green] 加载了 {len(state_changes)} 个状态变化")
    print()
    
    # 统计信息
    target_types = {}
    for sc in state_changes:
        target_type = sc.get("target_type", "未知")
        target_types[target_type] = target_types.get(target_type, 0) + 1
    
    print(f"[cyan]📊 状态变化类型分布：[/cyan]")
    for target_type, count in sorted(target_types.items()):
        print(f"  • {target_type}: {count} 个")
    print()
    
    # 初始化 Neo4j 客户端
    print("[cyan]🔌 连接 Neo4j 数据库...[/cyan]")
    client = Neo4jClient()
    
    try:
        # 初始化 StateChange 索引
        print("[cyan]🔧 初始化 StateChange 索引...[/cyan]")
        client.init_state_change_indexes()
        print()
        
        # 导入状态变化
        print(f"[cyan]📤 开始导入 {len(state_changes)} 个状态变化...[/cyan]")
        print()
        
        with Progress(
            SpinnerColumn(),
            TextColumn("[progress.description]{task.description}"),
            BarColumn(),
            TaskProgressColumn(),
            console=None
        ) as progress:
            task = progress.add_task("导入状态变化...", total=len(state_changes))
            
            for state_change in state_changes:
                source_event = state_change.get("source_event", "未知")
                target_type = state_change.get("target_type", "未知")
                dimension = state_change.get("dimension", "未知")
                
                progress.update(task, description=f"导入: {source_event} - {target_type} - {dimension}...")
                client.upsert_state_change(state_change)
                progress.advance(task)
        
        print()
        print(f"[green]🎉 成功导入 {len(state_changes)} 个状态变化！[/green]")
        print()
        
    finally:
        client.close()
    
    print("=" * 60)
    print("✅ 状态变化导入完成！")
    print("=" * 60)


if __name__ == "__main__":
    main()









