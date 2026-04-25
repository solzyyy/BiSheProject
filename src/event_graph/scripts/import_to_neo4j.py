"""
将 extract_merged.json 转换为 Neo4j 知识图谱的导入脚本 🎨✨

这个脚本会：
1. 读取 extract_merged.json 文件
2. 创建事件节点（Event）- 人物信息作为事件属性存储
3. 建立事件之间的关系（因果、时间顺序、情感线索等）

这是一个纯事件-关系-事件图谱，人物信息存储在事件的 core.人物 属性中。
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any, Dict, List

from rich import print
from rich.progress import track

# 添加 src 目录到 Python 路径，以便导入其他模块
sys.path.insert(0, str(Path(__file__).parent.parent))

from core.neo4j_client import Neo4jClient


def load_extract_data(json_path: Path) -> Dict[str, Any]:
    """加载 extract_merged.json 文件 📖"""
    if not json_path.exists():
        raise FileNotFoundError(f"文件不存在: {json_path}")
    
    print(f"[cyan]正在加载 JSON 文件:[/cyan] {json_path}")
    try:
        with open(json_path, "r", encoding="utf-8") as f:
            data = json.load(f)
        print(f"[green]成功加载！包含 {len(data.get('new_events', []))} 个事件[/green]")
        return data
    except json.JSONDecodeError as e:
        raise ValueError(f"JSON 文件格式错误: {e}")


def convert_event_to_neo4j_format(event: Dict[str, Any], is_first: bool = False, is_last: bool = False) -> Dict[str, Any]:
    """
    将 JSON 中的事件转换为 Neo4j 客户端期望的格式 🔄
    
    Args:
        event: 原始事件数据
        is_first: 是否是第一个事件
        is_last: 是否是最后一个事件
    """
    event_id = event.get("event_id", "")
    event_data = event.get("事件", {})
    metrics_data = event.get("数值属性", {})
    
    # 转换为核心格式
    neo4j_event = {
        "id": event_id,
        "type": "Event",
        "core": {
            "人物": event_data.get("人物", []),
            "行动": event_data.get("行动", ""),
            "目标": event_data.get("目标", ""),
            "结果": event_data.get("结果", ""),
            "情绪": event_data.get("情绪", ""),  # 事件整体情绪
            "时间": event_data.get("时间", ""),
            "场景": event_data.get("场景", ""),
            "前提条件": event_data.get("前提条件", ""),
            "结果影响": event_data.get("结果影响", ""),
            "source_text": event_data.get("source_text", ""),
            "风格": event_data.get("风格", []),
            "文化元素": event_data.get("文化元素", []),
        },
        "metrics": {
            "emotion_value": metrics_data.get("emotion_value", 0),
            "conflict_value": metrics_data.get("conflict_value", 0),
            "impact_value": metrics_data.get("impact_value", 0),
            "说明": metrics_data.get("说明", ""),
        },
        "flags": {
            "is_start": is_first,
            "is_ending": is_last,
            "ending_id": None,
        },
    }
    
    # 添加对话信息（如果有）
    dialogue_info = event.get("对话信息", {})
    if dialogue_info.get("has_dialogue", False) and dialogue_info.get("dialogue_info"):
        neo4j_event["core"]["对话信息"] = dialogue_info["dialogue_info"]
    
    return neo4j_event


def import_to_neo4j(json_path: Path = Path("out/extract_chain.json"), clear_first: bool = False) -> None:
    """
    主函数：将 JSON 数据导入到 Neo4j 🚀
    
    Args:
        json_path: extract_chain.json 文件路径
        clear_first: 是否在导入前清空数据库（默认 False，保留旧数据）
    """
    print("[bold cyan]开始导入知识图谱！[/bold cyan]\n")
    
    # 1. 加载数据
    data = load_extract_data(json_path)
    events = data.get("new_events", [])
    relations = data.get("relations", [])
    
    if not events:
        print("[red]没有找到事件数据！[/red]")
        return
    
    # 2. 初始化 Neo4j 客户端
    print("\n[cyan]正在连接 Neo4j 数据库...[/cyan]")
    client = Neo4jClient()
    success_count = 0
    success_rel_count = 0
    relation_types = {}
    
    try:
        # 可选：清空旧数据
        if clear_first:
            print("\n[yellow]正在清空数据库中的旧数据...[/yellow]")
            client.clear_all_data()
        
        # 初始化约束
        client.init_constraints()
        print("[green]数据库约束已创建[/green]")
        
        # 3. 转换并导入事件
        print("\n[cyan]正在导入事件节点...[/cyan]")
        failed_events = []
        for idx, event in enumerate(track(events, description="导入事件")):
            try:
                is_first = idx == 0
                is_last = idx == len(events) - 1
                neo4j_event = convert_event_to_neo4j_format(event, is_first, is_last)
                
                # 验证 event_id 是否存在
                if not neo4j_event.get("id"):
                    print(f"[yellow]警告：事件 {idx+1} 缺少 event_id，跳过[/yellow]")
                    failed_events.append((idx+1, "缺少 event_id"))
                    continue
                
                client.upsert_event(neo4j_event)
            except Exception as e:
                event_id = event.get("event_id", f"未知({idx+1})")
                print(f"[yellow]警告：导入事件 {event_id} 时出错: {e}[/yellow]")
                failed_events.append((event_id, str(e)))
        
        success_count = len(events) - len(failed_events)
        print(f"[green]成功导入 {success_count}/{len(events)} 个事件节点[/green]")
        if failed_events:
            print(f"[yellow]失败的事件数: {len(failed_events)}[/yellow]")
        
        # 4. 导入事件之间的关系
        print("\n[cyan]正在导入事件关系...[/cyan]")
        failed_relations = []
        
        for relation in track(relations, description="导入关系"):
            try:
                from_id = relation.get("from", "")
                to_id = relation.get("to", "")
                rel_type = relation.get("类型", "时间顺序")
                
                # 验证关系数据
                if not from_id or not to_id:
                    failed_relations.append((from_id, to_id, "缺少 from 或 to"))
                    continue
                
                client.link_relation({
                    "from": from_id,
                    "to": to_id,
                    "type": rel_type,
                    "描述": relation.get("描述", ""),
                })
                
                # 统计关系类型
                relation_types[rel_type] = relation_types.get(rel_type, 0) + 1
            except Exception as e:
                from_id = relation.get("from", "未知")
                to_id = relation.get("to", "未知")
                failed_relations.append((from_id, to_id, str(e)))
        
        success_rel_count = len(relations) - len(failed_relations)
        print(f"[green]成功导入 {success_rel_count}/{len(relations)} 个关系[/green]")
        if failed_relations:
            print(f"[yellow]失败的关系数: {len(failed_relations)}[/yellow]")
        
        # 5. 统计信息
        print("\n[bold green]导入完成！[/bold green]")
        print(f"[cyan]统计信息：[/cyan]")
        print(f"  • 事件节点: {success_count}/{len(events)}")
        print(f"  • 事件关系: {success_rel_count}/{len(relations)}")
        
        # 统计有对话的事件
        events_with_dialogue = sum(1 for e in events 
                                   if e.get("对话信息", {}).get("has_dialogue", False))
        if events_with_dialogue > 0:
            print(f"  • 包含对话的事件: {events_with_dialogue}")
        
        # 统计关系类型
        if relation_types:
            print(f"  • 关系类型分布:")
            for rel_type, count in sorted(relation_types.items(), key=lambda x: x[1], reverse=True):
                print(f"    - {rel_type}: {count}")
        
    except Exception as e:
        print(f"[red]导入过程中出现错误:[/red] {e}")
        raise
    finally:
        client.close()
        print("\n[cyan]数据库连接已关闭[/cyan]")




