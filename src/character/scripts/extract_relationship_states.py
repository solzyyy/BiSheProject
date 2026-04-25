"""
人物关系状态提取脚本 💕✨

从事件图谱中提取人物关系状态变化（target_type="relationship"）。
通过 mentions.json 找到人物共同经历的事件，提取关系状态变化。
"""

import json
import asyncio
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple
from collections import defaultdict
from itertools import combinations
from rich import print
from rich.progress import Progress, SpinnerColumn, TextColumn, BarColumn, TaskProgressColumn

# 添加 src 目录到路径
src_dir = Path(__file__).parent.parent.parent
sys.path.insert(0, str(src_dir))

from character.models.state_change import StateChange
from core.llm_client import AsyncLLMClient
from core.prompt_overrides import SLOT_RELATIONSHIP_STATE_EXTRACT, read_prompt_with_override


# 提示词文件路径
RELATIONSHIP_STATE_EXTRACT_PROMPT_PATH = (
    Path(__file__).resolve().parent.parent.parent.parent / "prompts" / "relationship_state_extract_prompt.txt"
)


def load_relationship_state_extract_template() -> str:
    """加载人物关系状态提取提示词模板 📄（支持环境变量覆盖）"""
    return read_prompt_with_override(RELATIONSHIP_STATE_EXTRACT_PROMPT_PATH, SLOT_RELATIONSHIP_STATE_EXTRACT)


def load_events(json_path: Path) -> Dict[str, Dict[str, Any]]:
    """加载事件数据，返回 event_id -> event_data 的字典 📂"""
    with open(json_path, "r", encoding="utf-8") as f:
        data = json.load(f)
    
    events = data.get("new_events", [])
    return {event.get("event_id"): event for event in events}


def load_mentions(json_path: Path) -> List[Dict[str, Any]]:
    """加载 Mention 数据 📂"""
    with open(json_path, "r", encoding="utf-8") as f:
        data = json.load(f)
    return data.get("mentions", [])


def load_entities(json_path: Path) -> tuple[Dict[str, Dict[str, Any]], Dict[str, str]]:
    """
    加载实体数据 📂
    
    Returns:
        (canonical_name -> entity 字典, mention_name -> canonical_name 映射)
    """
    with open(json_path, "r", encoding="utf-8") as f:
        data = json.load(f)
    
    entities = data.get("entities", [])
    entities_dict = {entity.get("canonical_name"): entity for entity in entities}
    
    # 建立别名映射：mention_name (可能是别名或规范名) -> canonical_name
    name_to_canonical = {}
    for entity in entities:
        canonical_name = entity.get("canonical_name")
        aliases = entity.get("aliases", [])
        
        # 规范名称本身也映射到自己
        name_to_canonical[canonical_name] = canonical_name
        
        # 所有别名也映射到规范名称
        for alias in aliases:
            if alias:  # 确保别名不为空
                name_to_canonical[alias] = canonical_name
    
    return entities_dict, name_to_canonical


def get_event_mentions(event_id: str, mentions: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """获取某个事件的所有 mentions 📋"""
    return [m for m in mentions if m.get("event_id") == event_id]


def build_prompt(
    template: str,
    event: Dict[str, Any],
    char_a_name: str,
    char_a_id: str,
    char_b_name: str,
    char_b_id: str,
    mentions_info: List[Dict[str, Any]]
) -> str:
    """构建 LLM 提示词 📝"""
    event_data = event.get("事件", {})
    metrics = event.get("数值属性", {})
    
    # 构建数值属性说明
    metrics_note = metrics.get("说明", "")
    if not metrics_note:
        emotion = metrics.get("emotion_value", 0)
        conflict = metrics.get("conflict_value", 0)
        impact = metrics.get("impact_value", 0)
        metrics_note = f"情绪值: {emotion}, 冲突值: {conflict}, 影响值: {impact}"
    
    # 构建 mentions 信息（mentions_info 已经过滤过，包含这两个实体的所有 mentions，包括别名）
    mentions_text = "\n".join([
        f"- {m.get('name', '未知')}: {m.get('action_hint', '')} | {m.get('relation_hint', '')} | {m.get('context_snippet', '')[:100]}"
        for m in mentions_info
    ])
    if not mentions_text:
        mentions_text = "无相关信息"
    
    return template.format(
        event_id=event.get("event_id", "未知"),
        char_a_id=char_a_id,
        char_a_name=char_a_name,
        char_b_id=char_b_id,
        char_b_name=char_b_name,
        event_characters=", ".join(event_data.get("人物", [])),
        event_action=event_data.get("行动", ""),
        event_result=event_data.get("结果", ""),
        event_emotion=event_data.get("情绪", ""),
        event_impact=event_data.get("结果影响", ""),
        mentions_info=mentions_text,
        metrics_note=metrics_note
    )


async def extract_relationship_states_from_event(
    llm_client: AsyncLLMClient,
    template: str,
    event: Dict[str, Any],
    char_a_name: str,
    char_a_id: str,
    char_b_name: str,
    char_b_id: str,
    mentions_info: List[Dict[str, Any]]
) -> List[StateChange]:
    """
    从单个事件中提取两个人物的关系状态变化 💕
    
    Args:
        llm_client: LLM 客户端
        template: 提示词模板
        event: 事件数据
        char_a_name: 人物A的名称
        char_a_id: 人物A的ID
        char_b_name: 人物B的名称
        char_b_id: 人物B的ID
        mentions_info: 该事件中这两个人物的 mentions 信息
        
    Returns:
        状态变化列表
    """
    prompt = build_prompt(template, event, char_a_name, char_a_id, char_b_name, char_b_id, mentions_info)
    
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
                # 确保 target_type 为 "relationship"
                sc_data["target_type"] = "relationship"
                
                # 处理 target_id：关系是双向的，可能是 [A, B] 或 [B, A]
                if "target_id" not in sc_data:
                    # 如果没有提供，默认使用 A 对 B
                    sc_data["target_id"] = [char_a_id, char_b_id]
                elif not isinstance(sc_data["target_id"], list):
                    # 如果不是列表，转换为列表（默认 A 对 B）
                    sc_data["target_id"] = [char_a_id, char_b_id]
                else:
                    # 验证 target_id 列表中的ID是否正确（必须是 A 或 B 的ID）
                    valid_ids = {char_a_id, char_b_id}
                    if len(sc_data["target_id"]) != 2:
                        # 如果不是两个ID，修正为 A 对 B
                        sc_data["target_id"] = [char_a_id, char_b_id]
                    elif not all(tid in valid_ids for tid in sc_data["target_id"]):
                        # 如果包含无效ID，修正为 A 对 B
                        sc_data["target_id"] = [char_a_id, char_b_id]
                    # 如果 target_id 是正确的（[A, B] 或 [B, A]），保持不变
                # condition 设为 None（如果不存在）
                if "condition" not in sc_data or sc_data["condition"] is None:
                    sc_data["condition"] = None
                
                # 处理 value 字段：统一转换为中文字符串
                if "value" in sc_data:
                    value = sc_data["value"]
                    if isinstance(value, list):
                        sc_data["value"] = "、".join(str(item) for item in value)
                    elif isinstance(value, bool):
                        sc_data["value"] = "是" if value else "否"
                    elif isinstance(value, (int, float)):
                        sc_data["value"] = str(value)
                    elif not isinstance(value, str):
                        sc_data["value"] = str(value)
                
                # 确保 is_obstacle 存在（如果 LLM 没有提供，根据 logic_impact 推断）
                if "is_obstacle" not in sc_data:
                    logic_impact = sc_data.get("logic_impact", "")
                    if any(keyword in logic_impact for keyword in ["阻止", "禁止", "无法", "不能", "限制"]):
                        sc_data["is_obstacle"] = True
                    else:
                        sc_data["is_obstacle"] = False
                
                # 确保 logic_impact 存在（如果 LLM 没有提供，设为默认值）
                if "logic_impact" not in sc_data or not sc_data.get("logic_impact"):
                    sc_data["logic_impact"] = "未明确说明逻辑影响"
                
                # 验证：检查 logic_impact 是否真的描述的是这两个人物的关系
                # 如果 logic_impact 明确描述了其他人物（而不是这两个人物）的关系，可能是 LLM 提取错误
                logic_impact = sc_data.get("logic_impact", "")
                value = sc_data.get("value", "")
                
                # 检查 logic_impact 中是否明确描述了其他人物（而不是这两个人物）的关系
                # 这里使用简单的启发式检查：如果 logic_impact 中提到了其他人物名称，可能是错误的
                # 但不过滤，因为可能是正确的（例如："王佛对林的关系"描述的是王佛-林的关系，但提到了"王佛"和"林"）
                # 所以这里只做警告，不强制过滤
                
                # 创建 StateChange 对象
                state_change = StateChange(**sc_data)
                state_changes.append(state_change)
            except Exception as e:
                print(f"[yellow]⚠️  跳过无效的状态变化: {e}[/yellow]")
                continue
        
        return state_changes
        
    except (json.JSONDecodeError, ValueError, KeyError) as e:
        event_id = event.get("event_id", "未知")
        print(f"[red]❌ 提取 {event_id} 中 {char_a_name}-{char_b_name} 的关系失败: JSON 解析错误[/red]")
        print(f"[yellow]错误详情: {e}[/yellow]")
        return []
    except Exception as e:
        event_id = event.get("event_id", "未知")
        print(f"[red]❌ 提取 {event_id} 中 {char_a_name}-{char_b_name} 的关系失败: {e}[/red]")
        import traceback
        print(f"[yellow]详细错误: {traceback.format_exc()}[/yellow]")
        return []


async def main_async():
    """异步主函数 🌟"""
    # 默认输入文件路径
    default_events = Path("out/extract_chain.json")
    default_mentions = Path("out/mentions.json")
    default_entities = Path("out/entities.json")
    default_output = Path("out/relationship_states.json")
    
    # 从命令行参数获取路径（如果提供）
    if len(sys.argv) > 1:
        events_path = Path(sys.argv[1])
    else:
        events_path = default_events
    
    if len(sys.argv) > 2:
        mentions_path = Path(sys.argv[2])
    else:
        mentions_path = default_mentions
    
    if len(sys.argv) > 3:
        entities_path = Path(sys.argv[3])
    else:
        entities_path = default_entities
    
    if len(sys.argv) > 4:
        output_path = Path(sys.argv[4])
    else:
        output_path = default_output
    
    print("=" * 60)
    print("💕 人物关系状态提取工具 (LLM 版本)")
    print("=" * 60)
    print()
    
    if not events_path.exists():
        print(f"[red]❌ 事件文件不存在: {events_path}[/red]")
        sys.exit(1)
    if not mentions_path.exists():
        print(f"[red]❌ Mentions 文件不存在: {mentions_path}[/red]")
        sys.exit(1)
    if not entities_path.exists():
        print(f"[red]❌ 实体文件不存在: {entities_path}[/red]")
        sys.exit(1)
    
    print(f"[cyan]📂 加载数据...[/cyan]")
    events = load_events(events_path)
    mentions = load_mentions(mentions_path)
    entities, name_to_canonical = load_entities(entities_path)
    print(f"[green]✓[/green] 加载了 {len(events)} 个事件、{len(mentions)} 个 mentions、{len(entities)} 个实体")
    print(f"[green]✓[/green] 建立了 {len(name_to_canonical)} 个名称映射（包含别名）")
    print()
    
    # 初始化 LLM 客户端
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
    template = load_relationship_state_extract_template()
    print("[green]✓[/green] 提示词模板加载成功")
    print()
    
    # 按事件组织 mentions（处理别名映射）
    event_mentions_map = defaultdict(list)
    mention_name_to_canonical = {}  # mention中的name -> 规范名称（用于后续查找）
    
    for mention in mentions:
        event_id = mention.get("event_id")
        mention_name = mention.get("name")  # mentions中的name可能是别名
        
        if event_id:
            event_mentions_map[event_id].append(mention)
            # 记录mention中的name到规范名称的映射
            if mention_name:
                canonical_name = name_to_canonical.get(mention_name)
                if canonical_name:
                    mention_name_to_canonical[mention_name] = canonical_name
    
    # 批量提取（带进度条和并发控制）
    print(f"[cyan]🚀 开始处理事件中的人物关系状态变化（并发处理）...[/cyan]")
    print()
    
    # 创建 Semaphore 控制并发数（最多同时处理 5 个任务）
    semaphore = asyncio.Semaphore(5)
    
    # 准备所有任务
    all_tasks = []
    for event_id, event in events.items():
        event_data = event.get("事件", {})
        characters = event_data.get("人物", [])
        mentions_info = event_mentions_map.get(event_id, [])
        
        # 只处理在 entities 中存在的人物（通过别名映射找到规范名称）
        valid_chars = []
        for char_name in characters:
            canonical_name = name_to_canonical.get(char_name)
            if canonical_name and canonical_name in entities:
                valid_chars.append(canonical_name)
        
        # 去重（可能有多个别名映射到同一个规范名称）
        valid_chars = list(set(valid_chars))
        
        # 处理所有人物对
        for char_a_name, char_b_name in combinations(valid_chars, 2):
            char_a_entity = entities[char_a_name]
            char_b_entity = entities[char_b_name]
            char_a_id = char_a_entity.get("id")
            char_b_id = char_b_entity.get("id")
            
            if not char_a_id or not char_b_id:
                continue
            
            # 获取该事件中这两个实体的所有mentions（包括别名）
            mentions_info = []
            for m in event_mentions_map.get(event_id, []):
                mention_name = m.get("name")
                # 检查mention中的name是否映射到这两个规范名称之一
                mention_canonical = mention_name_to_canonical.get(mention_name)
                if mention_canonical in [char_a_name, char_b_name]:
                    mentions_info.append(m)
            
            all_tasks.append((event_id, event, char_a_name, char_a_id, char_b_name, char_b_id, mentions_info))
    
    total_tasks = len(all_tasks)
    
    async def process_task_with_semaphore(task_data, progress, task) -> tuple:
        """在 Semaphore 保护下处理单个任务"""
        async with semaphore:
            event_id, event, char_a_name, char_a_id, char_b_name, char_b_id, mentions_info = task_data
            progress.update(task, description=f"处理: {event_id} - {char_a_name}-{char_b_name}...")
            
            state_changes = await extract_relationship_states_from_event(
                llm_client, template, event, char_a_name, char_a_id, char_b_name, char_b_id, mentions_info
            )
            
            progress.advance(task)
            
            failed = not state_changes
            return state_changes, f"{event_id}-{char_a_name}-{char_b_name}", failed
    
    all_state_changes = []
    failed_extractions = []
    
    with Progress(
        SpinnerColumn(),
        TextColumn("[progress.description]{task.description}"),
        BarColumn(),
        TaskProgressColumn(),
        console=None
    ) as progress:
        task = progress.add_task("提取关系状态...", total=total_tasks)
        
        # 并发处理所有任务
        tasks = [
            process_task_with_semaphore(task_data, progress, task)
            for task_data in all_tasks
        ]
        
        results = await asyncio.gather(*tasks, return_exceptions=True)
        
        for result in results:
            if isinstance(result, Exception):
                print(f"[red]❌ 处理任务时发生错误: {result}[/red]")
                continue
            
            state_changes, task_id, failed = result
            if state_changes:
                all_state_changes.extend(state_changes)
            if failed:
                failed_extractions.append(task_id)
    
    print(f"\n[green]🎉 处理完成！共提取 {len(all_state_changes)} 个人物关系状态变化[/green]")
    print()
    
    # 统计信息
    unique_relationships = set(
        tuple(sorted(sc.target_id)) if isinstance(sc.target_id, list) else (sc.target_id,)
        for sc in all_state_changes
    )
    unique_dimensions = set(sc.dimension for sc in all_state_changes)
    
    print(f"[cyan]📊 统计信息：[/cyan]")
    print(f"  • 成功处理事件-人物对数: {total_tasks - len(failed_extractions)}")
    print(f"  • 总状态变化数: {len(all_state_changes)}")
    print(f"  • 涉及关系数: {len(unique_relationships)}")
    print(f"  • 唯一维度数: {len(unique_dimensions)}")
    if failed_extractions:
        print(f"  • 失败提取数: {len(failed_extractions)}")
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
    output_data = {
        "state_changes": [sc.model_dump(mode='json') for sc in all_state_changes],
        "total_count": len(all_state_changes),
        "metadata": {
            "source_events_file": str(events_path),
            "source_mentions_file": str(mentions_path),
            "source_entities_file": str(entities_path),
            "unique_relationships": len(unique_relationships),
            "unique_dimensions": len(unique_dimensions),
            "failed_extractions": failed_extractions
        }
    }
    
    # 确保输出目录存在
    output_path.parent.mkdir(parents=True, exist_ok=True)
    
    with open(output_path, 'w', encoding='utf-8') as f:
        json.dump(output_data, f, ensure_ascii=False, indent=2)
    
    print(f"[green]✓[/green] 人物关系状态已保存到: {output_path}")
    print(f"[green]✓[/green] 共生成 {len(all_state_changes)} 条状态变化记录")
    print()
    
    print("=" * 60)
    print("🎉 人物关系状态提取完成！")
    print("=" * 60)


def main():
    """主函数入口 🚀"""
    asyncio.run(main_async())


if __name__ == "__main__":
    main()

