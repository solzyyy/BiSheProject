"""
导入实体到 Neo4j 图谱 👤✨

从 entities.json 读取实体数据，写入 Neo4j 数据库。
"""

import json
import sys
from pathlib import Path
from rich import print

# 添加 src 目录到路径
src_dir = Path(__file__).parent.parent.parent
sys.path.insert(0, str(src_dir))

from core.neo4j_client import Neo4jClient


def load_entities(json_path: Path) -> list:
    """加载实体数据 📂"""
    with open(json_path, "r", encoding="utf-8") as f:
        data = json.load(f)
    return data.get("entities", [])


def main():
    """主函数 🚀"""
    # 默认输入文件路径
    default_input = Path("out/entities.json")
    
    # 从命令行参数获取路径（如果提供）
    if len(sys.argv) > 1:
        input_path = Path(sys.argv[1])
    else:
        input_path = default_input
    
    print("=" * 60)
    print("👤 实体导入工具 (Neo4j)")
    print("=" * 60)
    print()
    
    if not input_path.exists():
        print(f"[red]❌ 实体文件不存在: {input_path}[/red]")
        sys.exit(1)
    
    print(f"[cyan]📂 加载实体数据: {input_path}[/cyan]")
    entities = load_entities(input_path)
    print(f"[green]✓[/green] 加载了 {len(entities)} 个实体")
    print()
    
    # 初始化 Neo4j 客户端
    print("[cyan]🔌 连接 Neo4j 数据库...[/cyan]")
    client = Neo4jClient()
    
    try:
        # 初始化 Character 约束和索引
        print("[cyan]🔧 初始化 Character 约束和索引...[/cyan]")
        client.init_character_constraints_and_indexes()
        print()
        
        # 导入实体
        print(f"[cyan]📤 开始导入 {len(entities)} 个实体...[/cyan]")
        print()
        
        with client._driver.session() as session:
            for i, entity in enumerate(entities, 1):
                entity_id = entity.get("id", "未知")
                canonical_name = entity.get("canonical_name", "")
                print(f"[{i}/{len(entities)}] 导入: {entity_id} - {canonical_name}")
                client.upsert_character(entity)
        
        print()
        print(f"[green]🎉 成功导入 {len(entities)} 个实体！[/green]")
        print()
        
    finally:
        client.close()
    
    print("=" * 60)
    print("✅ 实体导入完成！")
    print("=" * 60)


if __name__ == "__main__":
    main()









