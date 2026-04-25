"""
状态应用主脚本 🚀✨

通过扫描事件知识图谱，应用状态变化，并管理生成的内容。
"""

import asyncio
import json
import sys
from pathlib import Path
from typing import Optional

from rich import print
from rich.progress import Progress, SpinnerColumn, TextColumn, BarColumn, TimeElapsedColumn

# 添加 src 目录到路径
src_dir = Path(__file__).parent.parent.parent.parent
sys.path.insert(0, str(src_dir))

from state_manager.state_applier import StateApplier
from state_manager.content_manager import ContentManager
from state_manager.models import GeneratedContent
from core.llm_client import AsyncLLMClient
from core.neo4j_client import Neo4jClient


async def main_async(
    event_ids: Optional[list[str]] = None,
    baseline_path: str = "out/state_baseline.json",
    personas_path: str = "out/character_personas.json",
    output_dir: str = "out",
    max_concurrent: int = 5,
):
    """
    主函数 - 应用状态变化并管理内容
    
    Args:
        event_ids: 要处理的事件ID列表（如果为 None，会处理所有事件）
        baseline_path: 状态基线文件路径
        personas_path: 静态人设文件路径
        output_dir: 输出目录
        max_concurrent: 最大并发数
    """
    print("[cyan]🚀 开始应用状态变化...[/cyan]")
    
    # 检查 LLM 客户端
    llm_client = AsyncLLMClient.create_default()
    if llm_client.use_stub:
        print("[red]❌ LLM 客户端未初始化（Stub 模式），无法使用 function calling[/red]")
        print("[yellow]请设置 LLM_API_KEY 环境变量[/yellow]")
        return
    
    # 初始化组件
    neo4j_client = Neo4jClient()
    content_manager = ContentManager(
        collection_name="generated_content",
    )
    
    state_applier = StateApplier(
        llm_client=llm_client,
        neo4j_client=neo4j_client,
        baseline_path=baseline_path,
        personas_path=personas_path,
        max_concurrent=max_concurrent,
    )
    
    try:
        # 获取事件列表
        if event_ids is None:
            from state_manager.state_applier import StateApplier
            # 临时创建一个实例来获取事件ID列表
            temp_applier = StateApplier(
                llm_client=llm_client,
                neo4j_client=neo4j_client,
                content_manager=content_manager,
            )
            event_ids = await temp_applier._get_all_event_ids()
            temp_applier.close()
        
        if not event_ids:
            print("[yellow]⚠️  没有找到事件[/yellow]")
            return
        
        # 逐个事件处理（状态变化是一个事件一个事件来的）
        print(f"[cyan]📊 正在处理 {len(event_ids)} 个事件的状态变化...[/cyan]")
        
        current_states = None
        all_persona_updates = []
        narrative_count = 0
        
        with Progress(
            SpinnerColumn(),
            TextColumn("[progress.description]{task.description}"),
            BarColumn(),
            TextColumn("[progress.percentage]{task.percentage:>3.0f}%"),
            TimeElapsedColumn(),
        ) as progress:
            task = progress.add_task("处理事件...", total=len(event_ids))
            
            for event_id in event_ids:
                try:
                    # 获取事件信息和所有状态变化
                    event_info = await state_applier.get_event_info(event_id)
                    if event_info is None:
                        print(f"⚠️  事件 {event_id} 不存在，跳过")
                        continue
                    
                    all_state_changes = await state_applier.get_event_state_changes(event_id)
                    
                    # 职责分离：选择需要 apply 的状态变化
                    # 这里使用简单策略：只选择世界事实型（world）的状态变化
                    # 注意：在实际使用中，应该由 Choice/Canonical Selector 来决定
                    selected_state_changes = [
                        sc for sc in all_state_changes
                        if sc.get("target_type") == "world"
                    ]
                    
                    # 应用已选中的状态变化
                    updated_states, persona_updates, snapshot = await state_applier.apply_state_change(
                        event_id=event_id,
                        selected_state_changes=selected_state_changes,
                        current_states=current_states,
                        event_info=event_info,
                    )
                    current_states = updated_states
                    all_persona_updates.extend(persona_updates)
                    
                    progress.update(task, advance=1)
                    
                    # 注意：这里不生成摘要，摘要应该在生成文游内容后调用 generate_summaries_if_needed()
                    
                except Exception as e:
                    print(f"❌ 处理事件 {event_id} 时出错: {e}")
                    import traceback
                    traceback.print_exc()
                    continue
        
        # 创建最终状态快照
        final_snapshot = state_applier._create_state_snapshot(current_states)
        
        # 保存状态快照（供后续剧情生成使用）
        output_path = Path(output_dir)
        output_path.mkdir(parents=True, exist_ok=True)
        
        snapshot_output = output_path / "state_snapshot.json"
        with open(snapshot_output, "w", encoding="utf-8") as f:
            snapshot_dict = {
                "character_states": final_snapshot.character_states,
                "relationship_states": final_snapshot.relationship_states,
                "world_state": {
                    "factual_layer": [
                        {
                            "target_id": sc.target_id,
                            "dimension": sc.dimension,
                            "value": sc.value,
                            "logic_impact": sc.logic_impact,
                        }
                        for sc in final_snapshot.world_state.factual_layer
                    ],
                    "logical_layer": [
                        {
                            "target_id": sc.target_id,
                            "dimension": sc.dimension,
                            "value": sc.value,
                            "is_obstacle": sc.is_obstacle,
                            "logic_impact": sc.logic_impact,
                        }
                        for sc in final_snapshot.world_state.logical_layer
                    ],
                    "narrative_layer": [
                        {
                            "target_id": sc.target_id,
                            "dimension": sc.dimension,
                            "value": sc.value,
                            "logic_impact": sc.logic_impact,
                        }
                        for sc in final_snapshot.world_state.narrative_layer
                    ],
                },
            }
            json.dump(snapshot_dict, f, ensure_ascii=False, indent=2)
        
        print(f"[green]✅ 已保存状态快照到: {snapshot_output}[/green]")
        print(f"[cyan]💡 状态快照可用于后续剧情生成，配合事件和上下文一起传递给LLM[/cyan]")
        
        # 保存人设更新建议
        if all_persona_updates:
            persona_updates_output = output_path / "persona_updates.json"
            with open(persona_updates_output, "w", encoding="utf-8") as f:
                json.dump(
                    {
                        "persona_updates": [
                            {
                                "character_id": update.character_id,
                                "should_update": update.should_update,
                                "update_reason": update.update_reason,
                                "updated_fields": update.updated_fields,
                            }
                            for update in all_persona_updates
                        ],
                        "total_count": len(all_persona_updates),
                    },
                    f,
                    ensure_ascii=False,
                    indent=2,
                )
            
            print(f"[green]✅ 已保存人设更新建议到: {persona_updates_output}[/green]")
            
            # 显示需要更新的人设
            updates_needed = [u for u in all_persona_updates if u.should_update]
            if updates_needed:
                print(f"\n[cyan]📝 需要更新的人设（{len(updates_needed)} 个）：[/cyan]")
                for update in updates_needed:
                    print(f"  - {update.character_id}: {update.update_reason}")
                    if update.updated_fields:
                        for field, value in update.updated_fields.items():
                            print(f"    {field}: {value}")
        
        # 保存内容索引
        content_manager.save_index(f"{output_dir}/content_index.json")
        
        print(f"\n[cyan]📊 处理完成：[/cyan]")
        print(f"  处理事件数: {len(event_ids)}")
        print(f"  人设更新建议: {len(all_persona_updates)}")
        print(f"\n[cyan]💡 提示：[/cyan]")
        print(f"  1. 状态快照已保存，可用于生成文游内容")
        print(f"  2. 生成文游内容后，调用 store_narrative_content() 存储")
        print(f"  3. 调用 get_context_for_narrative_generation() 获取上下文（包括摘要、大总结、检索结果）")
        print(f"  4. 根据文游内容数量，调用 generate_summaries_if_needed() 生成摘要")
        
    finally:
        state_applier.close()
        neo4j_client.close()


def main():
    """命令行入口"""
    import argparse
    
    parser = argparse.ArgumentParser(description="应用状态变化并管理内容")
    parser.add_argument(
        "--events",
        nargs="+",
        help="要处理的事件ID列表（如果不提供，会处理所有事件）",
    )
    parser.add_argument(
        "--baseline",
        default="out/state_baseline.json",
        help="状态基线文件路径",
    )
    parser.add_argument(
        "--personas",
        default="out/character_personas.json",
        help="静态人设文件路径",
    )
    parser.add_argument(
        "--output",
        default="out",
        help="输出目录",
    )
    parser.add_argument(
        "--max-concurrent",
        type=int,
        default=5,
        help="最大并发数",
    )
    
    args = parser.parse_args()
    
    asyncio.run(main_async(
        event_ids=args.events,
        baseline_path=args.baseline,
        personas_path=args.personas,
        output_dir=args.output,
        max_concurrent=args.max_concurrent,
    ))


if __name__ == "__main__":
    main()

