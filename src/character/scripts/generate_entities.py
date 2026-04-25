"""
实体生成脚本 🌸✨

从 Mention 表中生成 Character Entity，进行别名归并和指代消歧。
"""

import json
import asyncio
import os
import sys
from pathlib import Path
from typing import Any, Dict, List
from rich import print

# 添加 src 目录到路径
src_dir = Path(__file__).parent.parent.parent
sys.path.insert(0, str(src_dir))

from character.models.mention import Mention
from character.normalizers.alias_mapper import AliasMapper
from core.llm_client import AsyncLLMClient


def load_mentions(json_path: Path) -> List[Mention]:
    """
    加载 Mention 数据 📂
    
    Args:
        json_path: JSON 文件路径
        
    Returns:
        Mention 列表
    """
    with open(json_path, "r", encoding="utf-8") as f:
        data = json.load(f)
    
    mentions_data = data.get("mentions", [])
    mentions = [Mention(**m) for m in mentions_data]
    
    return mentions


async def main_async():
    """异步主函数 🌟"""
    # 默认输入文件路径
    default_input = Path("out/mentions.json")
    
    # 默认输出文件路径
    default_output = Path("out/entities.json")
    
    # 从命令行参数获取路径（如果提供）
    if len(sys.argv) > 1:
        input_path = Path(sys.argv[1])
    else:
        input_path = default_input
    
    if len(sys.argv) > 2:
        output_path = Path(sys.argv[2])
    else:
        output_path = default_output
    
    print("=" * 70)
    print("🌸 智能别名映射系统 🌸")
    print("=" * 70)
    print()
    
    if not input_path.exists():
        print(f"[red]❌ 输入文件不存在: {input_path}[/red]")
        sys.exit(1)
    
    print(f"[cyan]📂 加载 Mention 数据: {input_path}[/cyan]")
    mentions = load_mentions(input_path)
    print(f"[green]✓[/green] 加载了 {len(mentions)} 个 Mention")
    
    # 统计唯一名字
    unique_names = set(m.name for m in mentions)
    print(f"[green]✓[/green] 共 {len(unique_names)} 个唯一名字")
    print()
    
    # 初始化 LLM 客户端（使用 GPT-4o）
    print("[cyan]🤖 初始化 LLM 客户端（GPT-4o Turbo）...[/cyan]")
    llm_client = AsyncLLMClient.create_default(model_name="gpt-4o")
    
    if llm_client.use_stub:
        print("[yellow]⚠️  LLM 客户端为 Stub 模式，将无法确认别名[/yellow]")
        print("[yellow]请设置 OPENAI_API_KEY 环境变量以启用 LLM 确认[/yellow]")
        sys.exit(1)
    
    print("[green]✓[/green] LLM 客户端初始化成功")
    print()
    
    # 初始化别名映射器
    mapper = AliasMapper(
        llm_client=llm_client,
        mentions=mentions,
        threshold=0.7
    )
    
    # 智能筛选候选
    print("[cyan]🎯 智能筛选候选别名对...[/cyan]")
    candidates = mapper.smart_filter_candidates()
    print(f"[green]✓[/green] 筛选出 {len(candidates)} 对候选")
    _plain = os.environ.get("PIPELINE_UI_PROGRESS", "").lower() == "plain" or (
        not sys.stdout.isatty()
    )
    if _plain and len(candidates) == 0:
        print("[entities] 0/0", flush=True)
    
    if candidates:
        print()
        print("[cyan]候选列表：[/cyan]")
        for idx, (name1, name2, reason) in enumerate(candidates[:10], 1):
            print(f"  {idx}. {name1} ←→ {name2} ({reason})")
        if len(candidates) > 10:
            print(f"  ... 还有 {len(candidates) - 10} 对")
        print()
    
    # LLM 确认
    if candidates:
        print(f"[cyan]🤖 使用 LLM 确认候选对（阈值: 0.7）...[/cyan]")
        confirmed_pairs = await mapper.confirm_with_llm(candidates)
        print(f"[green]✓[/green] 确认了 {len(confirmed_pairs)} 对别名关系")
        
        if confirmed_pairs:
            print()
            print("[cyan]确认的别名对：[/cyan]")
            for pair in confirmed_pairs:
                conf = pair.get('confidence', 0)
                reasoning = pair.get('reasoning', '')
                print(f"  • {pair['name1']} ↔ {pair['name2']} (置信度: {conf:.2f}, {reasoning})")
            print()
    else:
        confirmed_pairs = []
        print("[yellow]没有需要确认的候选，所有名字都是独立实体[/yellow]")
        print()
    
    # 合并别名并生成实体
    print("[cyan]🔗 合并别名组并生成实体表...[/cyan]")
    if confirmed_pairs:
        canonical_groups = mapper.merge_aliases(confirmed_pairs)
    else:
        # 没有确认的别名，所有名字都是独立实体
        canonical_groups = {name: [name] for name in mapper.name_groups.keys()}
    
    entities = mapper.generate_entities(canonical_groups)
    print(f"[green]✓[/green] 生成了 {len(entities)} 个实体")
    print()
    
    # 统计信息
    total_with_aliases = sum(1 for e in entities if len(e.get('aliases', [])) > 0)
    avg_mentions = sum(e['mention_count'] for e in entities) / len(entities) if entities else 0
    
    # 按 entity_type 统计
    entity_type_counts = {}
    for e in entities:
        entity_type = e.get('entity_type', '未知')
        entity_type_counts[entity_type] = entity_type_counts.get(entity_type, 0) + 1
    
    # 统计有 relation_hints 的实体
    total_with_relations = sum(1 for e in entities if len(e.get('relation_hints', [])) > 0)
    
    print(f"[cyan]📊 统计信息：[/cyan]")
    print(f"  • 总实体数: {len(entities)}")
    print(f"  • 有别名的实体: {total_with_aliases}")
    print(f"  • 平均 Mention 数: {avg_mentions:.1f}")
    print(f"  • 角色分类:")
    for entity_type, count in sorted(entity_type_counts.items()):
        print(f"    - {entity_type}: {count} 个")
    print(f"  • 有关联线索的实体: {total_with_relations}")
    print()
    
    # 显示部分实体
    print("[cyan]🌟 实体示例（前10个）：[/cyan]")
    for entity in entities[:10]:
        alias_info = f"({len(entity.get('aliases', []))} 别名)" if len(entity.get('aliases', [])) > 0 else ""
        entity_type = entity.get('entity_type', '未知')
        relation_info = f"[{len(entity.get('relation_hints', []))} 关联]" if len(entity.get('relation_hints', [])) > 0 else ""
        print(f"  {entity['id']} | {entity['canonical_name']:15s} | {entity_type:4s} | {alias_info:12s} | {relation_info:12s} | {entity['mention_count']:2d} 次")
    if len(entities) > 10:
        print(f"  ... 还有 {len(entities) - 10} 个实体")
    print()
    
    # 保存结果
    print(f"[cyan]💾 保存结果到: {output_path}[/cyan]")
    output_data = {
        "entities": entities,
        "total_count": len(entities),
        "metadata": {
            "method": "智能筛选 + LLM确认",
            "confirmed_pairs_count": len(confirmed_pairs),
            "candidates_count": len(candidates)
        }
    }
    
    with open(output_path, 'w', encoding='utf-8') as f:
        json.dump(output_data, f, ensure_ascii=False, indent=2)
    
    print(f"[green]✓[/green] 实体表已保存到: {output_path}")
    print(f"[green]✓[/green] 共生成 {len(entities)} 个实体")
    print()
    
    print("=" * 70)
    print("🎉 实体生成完成！")
    print("=" * 70)


def main():
    """主函数入口 🚀"""
    asyncio.run(main_async())


if __name__ == "__main__":
    main()

