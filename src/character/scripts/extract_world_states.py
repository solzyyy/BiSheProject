"""
世界/物品状态提取脚本 🌍✨

从事件图谱的"前提条件"中提取世界/物品状态变化（完全不依赖人物聚合）。
"""

import json
import asyncio
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional
from rich import print
from rich.progress import Progress, SpinnerColumn, TextColumn, BarColumn, TaskProgressColumn

# 添加 src 目录到路径
src_dir = Path(__file__).parent.parent.parent
sys.path.insert(0, str(src_dir))

from character.models.state_change import StateChange
from core.llm_client import AsyncLLMClient
from core.prompt_overrides import SLOT_WORLD_STATE_EXTRACT, read_prompt_with_override


# 提示词文件路径
WORLD_STATE_EXTRACT_PROMPT_PATH = (
    Path(__file__).resolve().parent.parent.parent.parent / "prompts" / "world_state_extract_prompt.txt"
)


def load_world_state_extract_template() -> str:
    """加载世界状态提取提示词模板 📄（支持环境变量覆盖）"""
    return read_prompt_with_override(WORLD_STATE_EXTRACT_PROMPT_PATH, SLOT_WORLD_STATE_EXTRACT)


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


def build_prompt(
    template: str,
    event: Dict[str, Any]
) -> str:
    """构建 LLM 提示词 📝"""
    event_data = event.get("事件", {})
    metrics = event.get("数值属性", {})
    
    # 构建数值属性说明
    metrics_note = metrics.get("说明", "")
    if not metrics_note:
        # 如果没有说明，至少提供数值信息
        emotion = metrics.get("emotion_value", 0)
        conflict = metrics.get("conflict_value", 0)
        impact = metrics.get("impact_value", 0)
        metrics_note = f"情绪值: {emotion}, 冲突值: {conflict}, 影响值: {impact}"
    
    return template.format(
        event_id=event.get("event_id", "未知"),
        precondition=event_data.get("前提条件", ""),
        scene=event_data.get("场景", ""),
        time=event_data.get("时间", ""),
        source_text=event_data.get("source_text", "")[:500],  # 限制长度
        metrics_note=metrics_note
    )


async def extract_world_states_from_event(
    llm_client: AsyncLLMClient,
    template: str,
    event: Dict[str, Any]
) -> List[StateChange]:
    """
    从单个事件中提取世界/物品状态变化 🌍
    
    Args:
        llm_client: LLM 客户端
        template: 提示词模板
        event: 事件数据
        
    Returns:
        状态变化列表
    """
    prompt = build_prompt(template, event)
    
    try:
        # 使用 invoke 方法，return_json=True 让客户端自动处理 JSON 提取
        result = await llm_client.invoke(prompt=prompt, return_json=True)
        
        # 提取状态变化
        state_changes_data = result.get("state_changes", [])
        state_changes = []
        
        for sc_data in state_changes_data:
            try:
                # 确保 source_event 正确
                sc_data["source_event"] = event.get("event_id", sc_data.get("source_event", "未知"))
                # 确保 target_type 为 "world"
                sc_data["target_type"] = "world"
                # 确保 change_type 为 "set"
                sc_data["change_type"] = "set"
                # condition 设为 None（如果不存在）
                if "condition" not in sc_data or sc_data["condition"] is None:
                    sc_data["condition"] = None
                
                # 处理 value 字段：统一转换为中文字符串
                if "value" in sc_data:
                    value = sc_data["value"]
                    if isinstance(value, list):
                        # 将列表转换为逗号分隔的中文字符串
                        sc_data["value"] = "、".join(str(item) for item in value)
                    elif isinstance(value, bool):
                        # 布尔值转换为中文
                        sc_data["value"] = "是" if value else "否"
                    elif isinstance(value, (int, float)):
                        # 数字转换为中文描述（如果需要，可以更具体）
                        sc_data["value"] = str(value)
                    elif not isinstance(value, str):
                        # 其他类型转换为字符串
                        sc_data["value"] = str(value)
                    # 如果已经是字符串，保持不变（应该是中文）
                
                # 确保 is_obstacle 存在（如果 LLM 没有提供，根据 logic_impact 推断）
                if "is_obstacle" not in sc_data:
                    # 如果 logic_impact 包含"阻止"、"禁止"、"无法"等关键词，设为 True
                    logic_impact = sc_data.get("logic_impact", "")
                    if any(keyword in logic_impact for keyword in ["阻止", "禁止", "无法", "不能", "限制"]):
                        sc_data["is_obstacle"] = True
                    else:
                        sc_data["is_obstacle"] = False
                
                # 确保 logic_impact 存在（如果 LLM 没有提供，设为默认值）
                if "logic_impact" not in sc_data or not sc_data.get("logic_impact"):
                    sc_data["logic_impact"] = "未明确说明逻辑影响"
                
                # 创建 StateChange 对象
                state_change = StateChange(**sc_data)
                state_changes.append(state_change)
            except Exception as e:
                print(f"[yellow]⚠️  跳过无效的状态变化: {e}[/yellow]")
                continue
        
        return state_changes
        
    except (json.JSONDecodeError, ValueError, KeyError) as e:
        event_id = event.get("event_id", "未知")
        print(f"[red]❌ 提取 {event_id} 失败: JSON 解析错误[/red]")
        print(f"[yellow]错误详情: {e}[/yellow]")
        return []
    except ValueError as e:
        event_id = event.get("event_id", "未知")
        print(f"[red]❌ 提取 {event_id} 失败: {e}[/red]")
        return []
    except Exception as e:
        event_id = event.get("event_id", "未知")
        print(f"[red]❌ 提取 {event_id} 失败: {e}[/red]")
        import traceback
        print(f"[yellow]详细错误: {traceback.format_exc()}[/yellow]")
        return []


async def main_async():
    """异步主函数 🌟"""
    # 默认输入文件路径
    default_input = Path("out/extract_chain.json")
    
    # 默认输出文件路径
    default_output = Path("out/world_states.json")
    
    # 从命令行参数获取路径（如果提供）
    if len(sys.argv) > 1:
        input_path = Path(sys.argv[1])
    else:
        input_path = default_input
    
    if len(sys.argv) > 2:
        output_path = Path(sys.argv[2])
    else:
        output_path = default_output
    
    print("=" * 60)
    print("🌍 世界/物品状态提取工具 (LLM 版本)")
    print("=" * 60)
    print()
    
    if not input_path.exists():
        print(f"[red]❌ 输入文件不存在: {input_path}[/red]")
        sys.exit(1)
    
    print(f"[cyan]📂 加载数据: {input_path}[/cyan]")
    events = load_events(input_path)
    print(f"[green]✓[/green] 加载了 {len(events)} 个事件")
    print()
    
    # 初始化 LLM 客户端（使用默认模型）
    print("[cyan]🤖 初始化 LLM 客户端...[/cyan]")
    llm_client = AsyncLLMClient.create_default()
    
    if llm_client.use_stub:
        print("[yellow]⚠️  LLM 客户端为 Stub 模式，将无法提取状态变化[/yellow]")
        print("[yellow]请设置相应的 API Key 环境变量以启用 LLM 提取[/yellow]")
        sys.exit(1)
    
    print("[green]✓[/green] LLM 客户端初始化成功")
    print()
    
    # 加载提示词模板
    print("[cyan]📄 加载提示词模板...[/cyan]")
    template = load_world_state_extract_template()
    print("[green]✓[/green] 提示词模板加载成功")
    print()
    
    # 批量提取（带进度条和并发控制）
    print(f"[cyan]🚀 开始处理 {len(events)} 个事件（并发处理）...[/cyan]")
    print()
    
    # 创建 Semaphore 控制并发数（最多同时处理 5 个事件）
    semaphore = asyncio.Semaphore(5)
    
    async def process_event_with_semaphore(event: Dict[str, Any], progress, task) -> tuple:
        """在 Semaphore 保护下处理单个事件"""
        async with semaphore:
            event_id = event.get("event_id", "未知")
            progress.update(task, description=f"处理: {event_id}...")
            
            state_changes = await extract_world_states_from_event(
                llm_client, template, event
            )
            
            progress.advance(task)
            
            # 检查前提条件是否存在
            precondition = event.get("事件", {}).get("前提条件", "")
            failed = False
            if not state_changes and precondition:
                failed = True
            
            return state_changes, event_id, failed
    
    all_state_changes = []
    failed_events = []
    
    with Progress(
        SpinnerColumn(),
        TextColumn("[progress.description]{task.description}"),
        BarColumn(),
        TaskProgressColumn(),
        console=None
    ) as progress:
        task = progress.add_task("提取世界状态...", total=len(events))
        
        # 并发处理所有事件
        tasks = [
            process_event_with_semaphore(event, progress, task)
            for event in events
        ]
        
        results = await asyncio.gather(*tasks, return_exceptions=True)
        
        for result in results:
            if isinstance(result, Exception):
                print(f"[red]❌ 处理事件时发生错误: {result}[/red]")
                continue
            
            state_changes, event_id, failed = result
            if state_changes:
                all_state_changes.extend(state_changes)
            if failed:
                failed_events.append(event_id)
    
    print(f"\n[green]🎉 处理完成！共提取 {len(all_state_changes)} 个世界/物品状态变化[/green]")
    print()
    
    # 统计信息
    unique_targets = set(sc.target_id for sc in all_state_changes)
    unique_dimensions = set(sc.dimension for sc in all_state_changes)
    
    print(f"[cyan]📊 统计信息：[/cyan]")
    print(f"  • 成功处理事件数: {len(events) - len(failed_events)}")
    print(f"  • 总状态变化数: {len(all_state_changes)}")
    print(f"  • 唯一世界变量数: {len(unique_targets)}")
    print(f"  • 唯一维度数: {len(unique_dimensions)}")
    if failed_events:
        print(f"  • 失败事件数: {len(failed_events)}")
    print()
    
    # 按维度统计
    dimension_counts = {}
    for sc in all_state_changes:
        dimension_counts[sc.dimension] = dimension_counts.get(sc.dimension, 0) + 1
    
    print(f"[cyan]📋 状态维度分布：[/cyan]")
    for dimension, count in sorted(dimension_counts.items(), key=lambda x: -x[1]):
        print(f"  • {dimension}: {count} 次")
    print()
    
    # 按 source_event 排序（自然排序：E1, E2, E10 而不是 E1, E10, E2）
    def extract_event_number(event_id: str) -> int:
        """提取事件ID中的数字部分用于排序"""
        import re
        match = re.search(r'E(\d+)', event_id)
        if match:
            return int(match.group(1))
        return 0
    
    all_state_changes.sort(key=lambda sc: extract_event_number(sc.source_event))
    
    # 保存最终结果
    print(f"[cyan]💾 保存结果到: {output_path}[/cyan]")
    
    # 使用 model_dump(mode='json') 来应用 json_encoders（包括 datetime 序列化）
    output_data = {
        "state_changes": [sc.model_dump(mode='json') for sc in all_state_changes],
        "total_count": len(all_state_changes),
        "metadata": {
            "source_file": str(input_path),
            "unique_targets": len(unique_targets),
            "unique_dimensions": len(unique_dimensions),
            "failed_events": failed_events
        }
    }
    
    # 确保输出目录存在
    output_path.parent.mkdir(parents=True, exist_ok=True)
    
    with open(output_path, 'w', encoding='utf-8') as f:
        json.dump(output_data, f, ensure_ascii=False, indent=2)
    
    print(f"[green]✓[/green] 世界/物品状态已保存到: {output_path}")
    print(f"[green]✓[/green] 共生成 {len(all_state_changes)} 条状态变化记录")
    print()
    
    print("=" * 60)
    print("🎉 世界/物品状态提取完成！")
    print("=" * 60)


def main():
    """主函数入口 🚀"""
    asyncio.run(main_async())


if __name__ == "__main__":
    main()

