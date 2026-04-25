"""
Mention 表生成脚本 🚀✨

使用 LLM 从事件知识图谱中提取 Mention 记录。
"""

import json
import asyncio
import sys
from pathlib import Path
from typing import Any, Dict, List
from rich.console import Console

# 添加 src 目录到路径
src_dir = Path(__file__).parent.parent.parent
sys.path.insert(0, str(src_dir))

from character.extractors.mention_extractor import LLMMentionExtractor
from character.models.mention import Mention
from core.llm_client import AsyncLLMClient


def _ensure_utf8_stdio() -> None:
    """Windows 上控制台常为 GBK，Rich 输出 emoji 会 UnicodeEncodeError；子进程可配合 PYTHONIOENCODING=utf-8。"""
    if sys.platform != "win32":
        return
    for stream in (sys.stdout, sys.stderr):
        try:
            if hasattr(stream, "reconfigure"):
                stream.reconfigure(encoding="utf-8", errors="replace")
        except Exception:
            pass


def _make_console() -> Console:
    kw: dict = {}
    if sys.platform == "win32":
        kw["legacy_windows"] = False
    return Console(**kw)


_console = _make_console()


def load_events(json_path: Path) -> List[Dict[str, Any]]:
    """
    加载事件数据 📂
    
    根据 extract_chain.json 的结构加载事件数据。
    
    Args:
        json_path: JSON 文件路径
        
    Returns:
        事件列表
    """
    with open(json_path, "r", encoding="utf-8") as f:
        data = json.load(f)
    
    # extract_chain.json 使用 "new_events" 字段
    events = data.get("new_events", [])
 
    return events


async def main_async():
    """异步主函数 🌟"""
    global _console
    _ensure_utf8_stdio()
    _console = _make_console()

    # 默认路径（直接运行本脚本且无参数时）：与项目根下 ``out/`` 约定一致。
    # 流水线 / Streamlit 单步会通过 CLI 传入 ``<output_dir>/extract_chain.json`` 与 ``.../mentions.json``。
    default_input = Path("out/extract_chain.json")
    default_output = Path("out/mentions.json")
    
    # 从命令行参数获取路径（如果提供）
    if len(sys.argv) > 1:
        input_path = Path(sys.argv[1])
    else:
        input_path = default_input
    
    if len(sys.argv) > 2:
        output_path = Path(sys.argv[2])
    else:
        output_path = default_output
    
    _console.print("=" * 60)
    _console.print("👤 Mention 表生成工具 (LLM 版本)")
    _console.print("=" * 60)
    _console.print()
    
    if not input_path.exists():
        _console.print(f"[red]❌ 输入文件不存在: {input_path}[/red]")
        sys.exit(1)
    
    _console.print(f"[cyan]📂 加载数据: {input_path}[/cyan]")
    events = load_events(input_path)
    _console.print(f"[green]✓[/green] 加载了 {len(events)} 个事件")
    _console.print()
    
    # 初始化 LLM 客户端（使用 GPT-4o）
    _console.print("[cyan]🤖 初始化 LLM 客户端（GPT-4o）...[/cyan]")
    llm_client = AsyncLLMClient.create_default(model_name="gpt-4o")
    
    if llm_client.use_stub:
        _console.print("[yellow]⚠️  LLM 客户端为 Stub 模式，将无法提取 Mention[/yellow]")
        _console.print("[yellow]请设置 OPENAI_API_KEY 环境变量以启用 LLM 提取[/yellow]")
        sys.exit(1)
    
    _console.print("[green]✓[/green] LLM 客户端初始化成功")
    _console.print()
    
    # 初始化提取器
    extractor = LLMMentionExtractor(llm_client=llm_client, max_retries=3)
    
    # 批量提取（TTY 下为 Rich 进度条；管道/Streamlit 下为逐行 [mentions] i/n）
    _console.print(f"[cyan]🚀 开始处理 {len(events)} 个事件...[/cyan]")
    _console.print()
    results = await extractor.extract_from_events(events)
    
    # 收集所有 Mention
    all_mentions = []
    for result in results:
        all_mentions.extend(result.mentions)
    
    _console.print(f"\n[green]🎉 处理完成！共提取 {len(all_mentions)} 个 Mention[/green]")
    _console.print()
    
    # 统计信息
    unique_names = set(m.name for m in all_mentions)
    avg_confidence = sum(r.confidence for r in results) / len(results) if results else 0
    
    _console.print(f"[cyan]📊 统计信息：[/cyan]")
    _console.print(f"  • 成功处理事件数: {len(results)}")
    _console.print(f"  • 总 Mention 数: {len(all_mentions)}")
    _console.print(f"  • 唯一人物数: {len(unique_names)}")
    _console.print(f"  • 平均置信度: {avg_confidence:.2%}")
    _console.print()
    
    # 按人物统计
    name_counts = {}
    for mention in all_mentions:
        name_counts[mention.name] = name_counts.get(mention.name, 0) + 1
    
    _console.print(f"[cyan]📋 人物出现次数：[/cyan]")
    for name, count in sorted(name_counts.items(), key=lambda x: -x[1]):
        _console.print(f"  • {name}: {count} 次")
    _console.print()
    
    # 保存最终结果
    _console.print(f"[cyan]💾 保存结果到: {output_path}[/cyan]")
    output_data = {
        "mentions": [m.model_dump() for m in all_mentions],
        "total_count": len(all_mentions)
    }
    
    with open(output_path, 'w', encoding='utf-8') as f:
        json.dump(output_data, f, ensure_ascii=False, indent=2)
    
    _console.print(f"[green]✓[/green] Mention 表已保存到: {output_path}")
    _console.print(f"[green]✓[/green] 共生成 {len(all_mentions)} 条 Mention 记录")
    _console.print()
    
    _console.print("=" * 60)
    _console.print("🎉 Mention 表生成完成！")
    _console.print("=" * 60)


def main():
    """主函数入口 🚀"""
    asyncio.run(main_async())


if __name__ == "__main__":
    main()
