"""
Neo4j 数据查询工具 🔍✨

提供常用的查询功能，方便查看和验证导入的数据。
"""

import sys
from pathlib import Path
from rich import print
from rich.table import Table
from rich.console import Console

# 添加 src 目录到路径
src_dir = Path(__file__).parent.parent.parent
sys.path.insert(0, str(src_dir))

from core.neo4j_client import Neo4jClient

console = Console()


def query_statistics():
    """查询基本统计信息 📊"""
    client = Neo4jClient()
    try:
        with client._driver.session() as session:
            # 查询节点统计
            result = session.run("""
                MATCH (e:Event)
                RETURN count(e) as event_count
            """)
            event_count = result.single()["event_count"]
            
            result = session.run("""
                MATCH (c:Character)
                RETURN count(c) as character_count
            """)
            character_count = result.single()["character_count"]
            
            result = session.run("""
                MATCH (s:StateChange)
                RETURN count(s) as state_change_count
            """)
            state_change_count = result.single()["state_change_count"]
            
            # 按类型统计 StateChange
            result = session.run("""
                MATCH (s:StateChange)
                RETURN s.target_type as target_type, count(s) as count
                ORDER BY count DESC
            """)
            state_by_type = {record["target_type"]: record["count"] for record in result}
            
            # 查询关系统计
            result = session.run("""
                MATCH ()-[r]->()
                RETURN type(r) as rel_type, count(r) as count
                ORDER BY count DESC
            """)
            relations = {record["rel_type"]: record["count"] for record in result}
            
            # 显示统计信息
            table = Table(title="📊 Neo4j 数据统计", show_header=True, header_style="bold magenta")
            table.add_column("类型", style="cyan")
            table.add_column("数量", justify="right", style="green")
            
            table.add_row("Event 节点", str(event_count))
            table.add_row("Character 节点", str(character_count))
            table.add_row("StateChange 节点", str(state_change_count))
            
            console.print(table)
            print()
            
            if state_by_type:
                table2 = Table(title="🎯 StateChange 按类型统计", show_header=True, header_style="bold magenta")
                table2.add_column("类型", style="cyan")
                table2.add_column("数量", justify="right", style="green")
                
                for target_type, count in state_by_type.items():
                    type_name = {
                        "character": "人物内在状态",
                        "relationship": "人物关系状态",
                        "world": "世界/物品状态"
                    }.get(target_type, target_type)
                    table2.add_row(type_name, str(count))
                
                console.print(table2)
                print()
            
            if relations:
                table3 = Table(title="🔗 关系统计", show_header=True, header_style="bold magenta")
                table3.add_column("关系类型", style="cyan")
                table3.add_column("数量", justify="right", style="green")
                
                for rel_type, count in relations.items():
                    table3.add_row(rel_type, str(count))
                
                console.print(table3)
                print()
                
    finally:
        client.close()


def query_characters(limit: int = 10):
    """查询 Character 节点 👤"""
    client = Neo4jClient()
    try:
        with client._driver.session() as session:
            result = session.run("""
                MATCH (c:Character)
                RETURN c.id as id, c.canonical_name as name, c.entity_type as type, c.mention_count as mentions
                ORDER BY c.id
                LIMIT $limit
            """, limit=limit)
            
            table = Table(title=f"👤 Character 节点（前 {limit} 个）", show_header=True, header_style="bold magenta")
            table.add_column("ID", style="cyan")
            table.add_column("名称", style="yellow")
            table.add_column("类型", style="green")
            table.add_column("提及次数", justify="right", style="blue")
            
            for record in result:
                table.add_row(
                    record["id"],
                    record["name"],
                    record["type"],
                    str(record["mentions"])
                )
            
            console.print(table)
            print()
                
    finally:
        client.close()


def query_state_changes(target_type: str = None, limit: int = 10):
    """查询 StateChange 节点 🎯"""
    client = Neo4jClient()
    try:
        if target_type:
            query = """
                MATCH (s:StateChange)
                WHERE s.target_type = $target_type
                RETURN s.source_event as event, s.target_type as type, s.target_id as target, 
                       s.dimension as dimension, s.value as value, s.plot_sensitive as sensitive
                ORDER BY s.source_event
                LIMIT $limit
            """
            params = {"target_type": target_type, "limit": limit}
            title = f"🎯 {target_type} 类型 StateChange（前 {limit} 个）"
        else:
            query = """
                MATCH (s:StateChange)
                RETURN s.source_event as event, s.target_type as type, s.target_id as target, 
                       s.dimension as dimension, s.value as value, s.plot_sensitive as sensitive
                ORDER BY s.source_event
                LIMIT $limit
            """
            params = {"limit": limit}
            title = f"🎯 StateChange 节点（前 {limit} 个）"
        
        with client._driver.session() as session:
            result = session.run(query, **params)
            
            table = Table(title=title, show_header=True, header_style="bold magenta")
            table.add_column("事件ID", style="cyan")
            table.add_column("类型", style="yellow")
            table.add_column("目标", style="green")
            table.add_column("维度", style="blue")
            table.add_column("值", style="magenta", max_width=30)
            table.add_column("敏感度", justify="right", style="red")
            
            for record in result:
                target = str(record["target"])[:20] if record["target"] else ""
                value = str(record["value"])[:30] if record["value"] else ""
                sensitive = f"{record['sensitive']:.2f}" if record["sensitive"] is not None else "N/A"
                
                table.add_row(
                    record["event"],
                    record["type"],
                    target,
                    record["dimension"],
                    value,
                    sensitive
                )
            
            console.print(table)
            print()
                
    finally:
        client.close()


def query_character_states(character_id: str = None, limit: int = 10):
    """查询特定角色的状态变化 👤🎯"""
    client = Neo4jClient()
    try:
        if character_id:
            query = """
                MATCH (c:Character {id: $char_id})-[:HAS_STATE]->(s:StateChange)
                RETURN s.source_event as event, s.dimension as dimension, s.value as value, 
                       s.is_obstacle as obstacle, s.plot_sensitive as sensitive
                ORDER BY s.source_event
                LIMIT $limit
            """
            params = {"char_id": character_id, "limit": limit}
            title = f"👤 {character_id} 的状态变化（前 {limit} 个）"
        else:
            query = """
                MATCH (c:Character)-[:HAS_STATE]->(s:StateChange)
                RETURN c.id as char_id, s.source_event as event, s.dimension as dimension, 
                       s.value as value, s.plot_sensitive as sensitive
                ORDER BY c.id, s.source_event
                LIMIT $limit
            """
            params = {"limit": limit}
            title = f"👤 角色状态变化（前 {limit} 个）"
        
        with client._driver.session() as session:
            result = session.run(query, **params)
            
            table = Table(title=title, show_header=True, header_style="bold magenta")
            if not character_id:
                table.add_column("角色ID", style="cyan")
            table.add_column("事件ID", style="yellow")
            table.add_column("维度", style="green")
            table.add_column("值", style="blue", max_width=30)
            table.add_column("阻碍", justify="center", style="red")
            table.add_column("敏感度", justify="right", style="magenta")
            
            for record in result:
                row = []
                if not character_id:
                    row.append(record["char_id"])
                row.extend([
                    record["event"],
                    record["dimension"],
                    str(record["value"])[:30] if record["value"] else "",
                    "是" if record.get("obstacle") else "否",
                    f"{record['sensitive']:.2f}" if record["sensitive"] is not None else "N/A"
                ])
                table.add_row(*row)
            
            console.print(table)
            print()
                
    finally:
        client.close()


def query_event_states(event_id: str):
    """查询特定事件触发的所有状态变化 📅🎯"""
    client = Neo4jClient()
    try:
        with client._driver.session() as session:
            result = session.run("""
                MATCH (e:Event {id: $event_id})-[:TRIGGERS]->(s:StateChange)
                RETURN s.target_type as type, s.target_id as target, s.dimension as dimension, 
                       s.value as value, s.is_obstacle as obstacle, s.plot_sensitive as sensitive
                ORDER BY s.target_type, s.dimension
            """, event_id=event_id)
            
            table = Table(title=f"📅 事件 {event_id} 触发的状态变化", show_header=True, header_style="bold magenta")
            table.add_column("类型", style="cyan")
            table.add_column("目标", style="yellow")
            table.add_column("维度", style="green")
            table.add_column("值", style="blue", max_width=30)
            table.add_column("阻碍", justify="center", style="red")
            table.add_column("敏感度", justify="right", style="magenta")
            
            for record in result:
                table.add_row(
                    record["type"],
                    str(record["target"])[:20] if record["target"] else "",
                    record["dimension"],
                    str(record["value"])[:30] if record["value"] else "",
                    "是" if record["obstacle"] else "否",
                    f"{record['sensitive']:.2f}" if record["sensitive"] is not None else "N/A"
                )
            
            console.print(table)
            print()
                
    finally:
        client.close()


def main():
    """主函数 🚀"""
    import sys
    
    if len(sys.argv) < 2:
        print("=" * 60)
        print("🔍 Neo4j 数据查询工具")
        print("=" * 60)
        print()
        print("[cyan]可用命令：[/cyan]")
        print("  [green]stats[/green]              - 显示基本统计信息")
        print("  [green]characters[/green]         - 查询 Character 节点")
        print("  [green]states[/green]             - 查询 StateChange 节点")
        print("  [green]states --type character[/green]  - 查询特定类型的状态变化")
        print("  [green]states --type relationship[/green]")
        print("  [green]states --type world[/green]")
        print("  [green]char-states[/green]        - 查询角色状态变化")
        print("  [green]char-states --id C010[/green]  - 查询特定角色的状态变化")
        print("  [green]event-states E1[/green]    - 查询特定事件触发的状态变化")
        print()
        return
    
    command = sys.argv[1]
    
    if command == "stats":
        query_statistics()
    elif command == "characters":
        limit = int(sys.argv[2]) if len(sys.argv) > 2 else 10
        query_characters(limit)
    elif command == "states":
        target_type = None
        limit = 10
        if "--type" in sys.argv:
            idx = sys.argv.index("--type")
            if idx + 1 < len(sys.argv):
                target_type = sys.argv[idx + 1]
        if len(sys.argv) > 2 and sys.argv[2].isdigit():
            limit = int(sys.argv[2])
        query_state_changes(target_type, limit)
    elif command == "char-states":
        character_id = None
        limit = 10
        if "--id" in sys.argv:
            idx = sys.argv.index("--id")
            if idx + 1 < len(sys.argv):
                character_id = sys.argv[idx + 1]
        if len(sys.argv) > 2 and sys.argv[2].isdigit():
            limit = int(sys.argv[2])
        query_character_states(character_id, limit)
    elif command == "event-states":
        if len(sys.argv) < 3:
            print("[red]❌ 请提供事件ID，例如: event-states E1[/red]")
            return
        event_id = sys.argv[2]
        query_event_states(event_id)
    else:
        print(f"[red]❌ 未知命令: {command}[/red]")
        print("使用不带参数运行查看帮助")


if __name__ == "__main__":
    main()









