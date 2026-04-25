"""
合并事件和关系数据，然后导入到 Neo4j 🚀
"""

import json
from pathlib import Path
from rich import print


def merge_data(
    chain_path: Path = Path("out/extract_chain.json"),
    relations_path: Path = Path("out/extract_relations.json"),
    output_path: Path = Path("out/extract_merged.json"),
) -> Path:
    """
    合并事件和关系数据
    
    Args:
        chain_path: extract_chain.json 文件路径
        relations_path: extract_relations.json 文件路径
        output_path: 合并后的输出文件路径
        
    Returns:
        合并后的文件路径
    """
    # 读取 extract_chain.json（包含重新编号的事件 E1, E2, E3...）
    if not chain_path.exists():
        raise FileNotFoundError(f"文件不存在: {chain_path}")
    
    chain_data = json.loads(chain_path.read_text(encoding="utf-8"))
    events = chain_data.get("new_events", [])
    
    print(f"extract_chain.json: {len(events)} 个事件")
    
    # 读取 extract_relations.json（包含关系数据）
    if relations_path.exists():
        relations_data = json.loads(relations_path.read_text(encoding="utf-8"))
        relations = relations_data.get("relations", [])
        print(f"extract_relations.json: {len(relations)} 个关系")
        
        print("\n合并关系数据...")
        chain_data["relations"] = relations
        print(f"[green]已合并 {len(relations)} 个关系[/green]")
    else:
        print("[yellow]警告：extract_relations.json 不存在，使用 extract_chain.json 中的关系[/yellow]")
    
    # 保存合并后的数据
    output_path.write_text(
        json.dumps(chain_data, ensure_ascii=False, indent=2),
        encoding="utf-8"
    )
    print(f"[green]合并后的数据已保存到: {output_path}[/green]")
    print(f"事件数: {len(events)}")
    print(f"关系数: {len(chain_data.get('relations', []))}")
    
    return output_path


def main(clear_first: bool = False) -> None:
    """主函数：合并数据并导入到 Neo4j"""
    # 合并数据
    merged_path = merge_data()
    
    # 导入到 Neo4j
    print("\n[cyan]开始导入到 Neo4j...[/cyan]")
    import sys
    sys.path.insert(0, str(Path(__file__).parent.parent))
    from scripts.import_to_neo4j import import_to_neo4j
    import_to_neo4j(merged_path, clear_first=clear_first)


if __name__ == "__main__":
    main(clear_first=True)

