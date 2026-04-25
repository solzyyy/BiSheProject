"""
聚合人物静态人设脚本 🎭✨

从 character_profiles.json 聚合得到人物的静态人设。
"""

import json
import asyncio
import sys
from pathlib import Path
from collections import Counter
from rich import print
from rich.progress import Progress, SpinnerColumn, TextColumn, BarColumn, TaskProgressColumn

# 添加 src 目录到路径
src_dir = Path(__file__).parent.parent.parent
sys.path.insert(0, str(src_dir))

from character.aggregators.persona_aggregator import PersonaAggregator
from character.models.character_persona import CharacterPersona
from core.llm_client import AsyncLLMClient


async def main_async():
    """异步主函数 🌟"""
    # 默认输入文件路径
    default_profiles = Path("out/character_profiles.json")
    default_output = Path("out/character_personas.json")
    
    # 从命令行参数获取路径（如果提供）
    if len(sys.argv) > 1:
        profiles_path = Path(sys.argv[1])
    else:
        profiles_path = default_profiles
    
    if len(sys.argv) > 2:
        output_path = Path(sys.argv[2])
    else:
        output_path = default_output
    
    print("=" * 60)
    print("🎭 人物静态人设聚合工具")
    print("=" * 60)
    print()
    
    if not profiles_path.exists():
        print(f"[red]❌ 人物画像文件不存在: {profiles_path}[/red]")
        sys.exit(1)
    
    print(f"[cyan]📂 加载人物画像: {profiles_path}...[/cyan]")
    aggregator = PersonaAggregator()
    profiles = aggregator.load_profiles(str(profiles_path))
    print(f"[green]✓[/green] 加载了 {len(profiles)} 个人物画像")
    print()
    
    # 初始化 LLM 客户端（所有提取逻辑都需要 LLM）
    print("[cyan]🤖 初始化 LLM 客户端...[/cyan]")
    llm_client = AsyncLLMClient.create_default()
    
    if llm_client.use_stub:
        print("[red]❌ LLM 客户端为 Stub 模式，无法进行人设提取[/red]")
        print("[yellow]⚠️  请设置相应的 API Key 环境变量以启用 LLM 提取[/yellow]")
        print("[yellow]   例如：OPENAI_API_KEY, ANTHROPIC_API_KEY 等[/yellow]")
        sys.exit(1)
    
    print("[green]✓[/green] LLM 客户端初始化成功")
    print()
    
    aggregator.llm_client = llm_client
    # 设置最大并发数（可以根据 API 限制调整）
    max_concurrent = 5  # 可以根据 API 限制调整
    aggregator.semaphore = asyncio.Semaphore(max_concurrent)
    aggregator.max_retries = 3  # 设置重试次数
    
    print(f"[cyan]📊 并发配置：[/cyan]")
    print(f"  • 最大并发数：{max_concurrent}")
    print(f"  • 最大重试次数：{aggregator.max_retries}")
    print()
    
    # 聚合所有人物的静态人设（并发处理）
    print(f"[cyan]🚀 开始聚合人物静态人设（并发处理）...[/cyan]")
    print()
    
    personas = []
    with Progress(
        SpinnerColumn(),
        TextColumn("[progress.description]{task.description}"),
        BarColumn(),
        TaskProgressColumn(),
        console=None
    ) as progress:
        task = progress.add_task("聚合静态人设...", total=len(profiles))
        
        # 使用并发处理
        personas = await aggregator.aggregate_all_personas(profiles, progress=progress, task=task)
    
    print(f"\n[green]🎉 处理完成！共聚合 {len(personas)} 个人物静态人设[/green]")
    print()
    
    # 保存结果
    print(f"[cyan]💾 保存结果到: {output_path}[/cyan]")
    output_data = {
        "personas": [p.model_dump(mode='json') for p in personas],
        "total_count": len(personas),
        "metadata": {
            "source_profiles_file": str(profiles_path),
            "generated_by_llm": True
        }
    }
    
    # 确保输出目录存在
    output_path.parent.mkdir(parents=True, exist_ok=True)
    
    with open(output_path, 'w', encoding='utf-8') as f:
        json.dump(output_data, f, ensure_ascii=False, indent=2)
    
    print(f"[green]✓[/green] 人物静态人设已保存到: {output_path}")
    print(f"[green]✓[/green] 共生成 {len(personas)} 个静态人设")
    print()
    
    # 显示统计信息
    print(f"[cyan]📊 统计信息：[/cyan]")
    roles = Counter(p.role for p in personas)
    print(f"  • 角色分布：")
    for role, count in roles.most_common():
        print(f"    - {role}: {count} 人")
    print()
    
    print("=" * 60)
    print("🎉 人物静态人设聚合完成！")
    print("=" * 60)


def main():
    """主函数入口 🚀"""
    asyncio.run(main_async())


if __name__ == "__main__":
    main()

