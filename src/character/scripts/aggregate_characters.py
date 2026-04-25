"""
人物聚合脚本 - 聚合所有人物节点得到完整画像 🎭✨
"""

import asyncio
import json
import sys
from pathlib import Path
from typing import Dict, List

from rich import print
from rich.progress import Progress, SpinnerColumn, TextColumn, BarColumn, TaskProgressColumn, TimeRemainingColumn

from core.llm_client import AsyncLLMClient
from ..aggregators.character_aggregator import CharacterAggregator
from ..models.character_profile import CharacterProfile


async def main_async():
    """主函数"""
    # 解析命令行参数
    args = sys.argv[1:]
    
    events_file = args[0] if len(args) > 0 else "out/extract_chain.json"
    character_states_file = args[1] if len(args) > 1 else "out/character_states.json"
    relationship_states_file = args[2] if len(args) > 2 else "out/relationship_states.json"
    entities_file = args[3] if len(args) > 3 else "out/entities.json"
    mentions_file = args[4] if len(args) > 4 else "out/mentions.json"
    output_file = args[5] if len(args) > 5 else "out/character_profiles.json"
    use_rules = "--rules" in args or "-r" in args  # 默认使用 LLM，可以用 --rules 切换回规则提取
    
    print(f"🎭 开始聚合人物节点...")
    print(f"  事件文件: {events_file}")
    print(f"  人物状态文件: {character_states_file}")
    print(f"  关系状态文件: {relationship_states_file}")
    print(f"  实体文件: {entities_file}")
    print(f"  Mentions 文件: {mentions_file}")
    print(f"  输出文件: {output_file}")
    print(f"  使用 LLM 提取行动词: {not use_rules}")
    print()
    
    # 初始化 LLM 客户端（默认使用 LLM）
    llm_client = None
    use_llm = not use_rules
    if use_llm:
        print("🤖 初始化 LLM 客户端...")
        llm_client = AsyncLLMClient()
    
    # 创建聚合器
    aggregator = CharacterAggregator(use_llm_for_actions=use_llm, llm_client=llm_client)
    
    # 加载数据
    print("📚 加载数据...")
    events = aggregator.load_events(events_file)
    state_changes = aggregator.load_state_changes(character_states_file, relationship_states_file)
    entities = aggregator.load_entities(entities_file)
    mentions_index = aggregator.load_mentions(mentions_file)
    
    print(f"  加载了 {len(events)} 个事件")
    print(f"  加载了 {sum(len(sc) for sc in state_changes.values())} 个状态变化")
    print(f"  加载了 {len(entities)} 个实体")
    print(f"  加载了 {len(mentions_index)} 个 mentions 索引")
    print()
    
    # 从事件和状态变化中提取人物信息（利用 entities.json 和 mentions.json）
    print("🔍 从事件和状态变化中提取人物信息（利用 entities.json 和 mentions.json）...")
    characters_info = aggregator.extract_characters_from_events_and_states(events, state_changes, entities, mentions_index)
    print(f"  提取了 {len(characters_info)} 个人物")
    print()
    
    # 构建所有人物行动词（用于计算 TF-IDF/PMI）
    print("🔍 构建所有人物行动词（用于计算 TF-IDF/PMI，从 mentions.json 提取）...")
    print(f"  共 {len(characters_info)} 个人物需要提取行动词")
    print()
    
    with Progress(
        SpinnerColumn(),
        TextColumn("[progress.description]{task.description}"),
        BarColumn(),
        TaskProgressColumn(),
        TimeRemainingColumn(),
        console=None,
    ) as progress:
        task = progress.add_task("提取行动词...", total=len(characters_info))
        try:
            all_characters_actions = await aggregator.build_all_characters_actions(
                characters_info, events, mentions_index, state_changes, progress=progress, task=task
            )
        except KeyboardInterrupt:
            print("\n⚠️ 用户中断")
            raise
        except Exception as e:
            print(f"\n❌ 提取行动词时出错: {e}")
            import traceback
            traceback.print_exc()
            raise
    print(f"  构建完成，共 {len(all_characters_actions)} 个人物的行动数据")
    print()
    
    # 聚合每个人物
    print("🎯 开始聚合每个人物...")
    profiles = []
    
    with Progress(
        SpinnerColumn(),
        TextColumn("[progress.description]{task.description}"),
        BarColumn(),
        TaskProgressColumn(),
        TimeRemainingColumn(),
        console=None,
    ) as progress:
        task = progress.add_task("聚合人物...", total=len(characters_info))
        
        for i, character_id in enumerate(characters_info.keys(), 1):
            character_info = characters_info[character_id]
            canonical_name = character_info.get("canonical_name", character_id)
            progress.update(task, description=f"聚合人物: {canonical_name}")
            
            try:
                profile = await aggregator.aggregate_character(
                    character_id=character_id,
                    character_info=character_info,
                    events=events,
                    state_changes=state_changes,
                    mentions_index=mentions_index,
                    all_characters_actions=all_characters_actions
                )
                profiles.append(profile)
                
                # 显示统计信息（在进度条下方）
                print(f"  ✓ {canonical_name}: {profile.total_events} 个事件, {len(profile.action_patterns)} 种行动模式, {len(profile.unique_behaviors)} 种独有行为")
            except Exception as e:
                print(f"  ❌ {canonical_name} 聚合失败: {e}")
                import traceback
                traceback.print_exc()
                continue
            finally:
                progress.update(task, advance=1)
    
    print()
    print(f"✅ 聚合完成！共生成 {len(profiles)} 个人物画像")
    
    # 保存结果
    print(f"💾 保存结果到: {output_file}")
    output_path = Path(output_file)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    
    result = {
        "profiles": [profile.to_dict() for profile in profiles],
        "statistics": {
            "total_characters": len(profiles),
            "total_events": sum(p.total_events for p in profiles),
            "total_actions": sum(p.total_actions for p in profiles),
            "characters_with_unique_behaviors": sum(1 for p in profiles if p.unique_behaviors)
        }
    }
    
    with open(output_path, 'w', encoding='utf-8') as f:
        json.dump(result, f, ensure_ascii=False, indent=2)
    
    print("🎉 完成！")


if __name__ == "__main__":
    asyncio.run(main_async())

