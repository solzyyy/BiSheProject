"""
人物节点初始化脚本 🎭✨

从 entities.json、mentions.json、extract_chain.json 中提取信息，
使用 LLM 生成完整的人物节点属性。
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

from character.models.mention import Mention
from core.llm_client import AsyncLLMClient
from core.prompt_overrides import SLOT_CHARACTER_INIT, read_prompt_with_override


# 提示词文件路径
CHARACTER_INIT_PROMPT_PATH = (
    Path(__file__).resolve().parent.parent.parent.parent / "prompts" / "character_init_prompt.txt"
)


def load_character_init_template() -> str:
    """加载人物初始化提示词模板 📄（支持环境变量覆盖）"""
    return read_prompt_with_override(CHARACTER_INIT_PROMPT_PATH, SLOT_CHARACTER_INIT)


def load_entities(json_path: Path) -> List[Dict[str, Any]]:
    """加载实体数据 📂"""
    with open(json_path, "r", encoding="utf-8") as f:
        data = json.load(f)
    return data.get("entities", [])


def load_mentions(json_path: Path) -> List[Mention]:
    """加载 Mention 数据 📂"""
    with open(json_path, "r", encoding="utf-8") as f:
        data = json.load(f)
    mentions_data = data.get("mentions", [])
    return [Mention(**m) for m in mentions_data]


def load_events(json_path: Path) -> Dict[str, Dict[str, Any]]:
    """加载事件数据，返回 event_id -> event_data 的字典 📂"""
    with open(json_path, "r", encoding="utf-8") as f:
        data = json.load(f)
    events_list = data.get("new_events", [])
    return {event["event_id"]: event for event in events_list}


def find_first_mention(
    entity_name: str,
    aliases: List[str],
    mentions: List[Mention]
) -> Optional[Mention]:
    """
    找到实体的"第一案发现场"（初次提及）🔍
    
    查找逻辑：
    1. 先找 canonical_name 匹配的 mention
    2. 再找 aliases 匹配的 mention
    3. 返回 mention_id 最小的（即最早出现的）
    """
    matching_mentions = []
    
    # 收集所有匹配的 mentions
    all_names = [entity_name] + aliases
    for mention in mentions:
        if mention.name in all_names:
            matching_mentions.append(mention)
    
    if not matching_mentions:
        return None
    
    # 按 mention_id 排序（M1, M2, M3...），返回最早的
    matching_mentions.sort(key=lambda m: int(m.mention_id[1:]))
    return matching_mentions[0]


def calculate_relation_cooccurrence(
    entity_name: str,
    entity_aliases: List[str],
    entity_type: str,
    mentions: List[Mention],
    entities: List[Dict[str, Any]]
) -> Dict[str, Dict[str, Any]]:
    """
    计算重要人物之间的关系 🔍
    
    只记录：两个都是重要人物（主角或配角），且他们在同一个事件里同框过
    
    返回格式：
    {
        "target_entity_id": {
            "first_event_id": "E1",  # 第一次共现的事件ID
            "relation_types": ["师徒", "师徒"]
        }
    }
    """
    # 重要人物类型
    important_types = {"主角", "配角"}
    
    # 如果当前实体不是重要人物，直接返回空
    if entity_type not in important_types:
        return {}
    
    # 构建实体名称到ID和类型的映射
    name_to_id = {}
    id_to_type = {}
    for entity in entities:
        canonical = entity["canonical_name"]
        aliases = entity.get("aliases", [])
        entity_id = entity["id"]
        entity_type_value = entity.get("entity_type", "路人")
        name_to_id[canonical] = entity_id
        id_to_type[entity_id] = entity_type_value
        for alias in aliases:
            name_to_id[alias] = entity_id
    
    # 收集该实体的所有 mentions
    entity_names = [entity_name] + entity_aliases
    entity_mentions = [m for m in mentions if m.name in entity_names]
    
    # 统计每个事件中与该实体共现的其他实体
    event_cooccurrence = {}  # event_id -> set of other entity names
    
    for mention in entity_mentions:
        event_id = mention.event_id
        if event_id not in event_cooccurrence:
            event_cooccurrence[event_id] = set()
        
        # 找到同事件中的其他 mentions
        for other_mention in mentions:
            if (other_mention.event_id == event_id and 
                other_mention.name not in entity_names):
                event_cooccurrence[event_id].add(other_mention.name)
    
    # 统计每个目标实体的首次共现事件
    target_first_events = {}  # target_entity_id -> first_event_id (最早的事件)
    target_relation_types = {}  # target_entity_id -> list of relation_types
    target_event_ids = {}  # target_entity_id -> list of event_ids (用于找最早的事件)
    
    for event_id, other_names in event_cooccurrence.items():
        for other_name in other_names:
            if other_name in name_to_id:
                target_id = name_to_id[other_name]
                target_type = id_to_type.get(target_id, "路人")
                
                # 只有当目标也是重要人物时，才记录关系
                if target_type in important_types:
                    if target_id not in target_event_ids:
                        target_event_ids[target_id] = []
                        target_relation_types[target_id] = []
                    
                    target_event_ids[target_id].append(event_id)
                    
                    # 收集关系类型（从该实体的 mention 中）
                    for mention in entity_mentions:
                        if mention.event_id == event_id and mention.relation_hint:
                            if mention.relation_hint not in target_relation_types[target_id]:
                                target_relation_types[target_id].append(mention.relation_hint)
    
    # 找到每个目标实体的最早事件（按 event_id 排序）
    for target_id, event_ids in target_event_ids.items():
        # 提取事件编号并排序（E1, E2, E3...）
        sorted_events = sorted(event_ids, key=lambda e: int(e[1:]) if e[1:].isdigit() else 999)
        target_first_events[target_id] = sorted_events[0]
    
    # 构建结果
    result = {}
    for target_id, first_event_id in target_first_events.items():
        result[target_id] = {
            "first_event_id": first_event_id,
            "relation_types": target_relation_types.get(target_id, [])
        }
    
    return result


def build_prompt(
    template: str,
    entity: Dict[str, Any],
    first_mention: Mention,
    first_event: Dict[str, Any],
    frequent_relations: Dict[str, Dict[str, Any]],
    events: Dict[str, Dict[str, Any]]
) -> str:
    """构建 LLM 提示词 📝"""
    event_data = first_event.get("事件", {})
    
    # 处理 aliases JSON 格式
    aliases_json = json.dumps(entity.get("aliases", []), ensure_ascii=False)
    
    # 构建重要关系及其首次共现事件信息
    relation_events_info = []
    for target_id, rel_info in frequent_relations.items():
        first_cooccur_event_id = rel_info["first_event_id"]
        cooccur_event = events.get(first_cooccur_event_id)
        
        if cooccur_event:
            cooccur_event_data = cooccur_event.get("事件", {})
            relation_events_info.append(
                f"\n**关系对象 {target_id}**:\n"
                f"- 首次共现事件ID: {first_cooccur_event_id}\n"
                f"- 事件人物: {', '.join(cooccur_event_data.get('人物', []))}\n"
                f"- 事件行动: {cooccur_event_data.get('行动', '')}\n"
                f"- 事件前提条件: {cooccur_event_data.get('前提条件', '')}\n"
                f"- 事件场景: {cooccur_event_data.get('场景', '')}\n"
                f"- 事件情绪: {cooccur_event_data.get('情绪', '')}\n"
                f"- 事件原文: {cooccur_event_data.get('source_text', '')[:300]}\n"
            )
    
    relation_events_text = "\n".join(relation_events_info) if relation_events_info else "无重要关系"
    
    return template.format(
        character_id=entity["id"],
        canonical_name=entity["canonical_name"],
        aliases=aliases_json,  # 用于显示
        aliases_json=aliases_json,  # 用于 JSON 输出格式
        entity_type=entity.get("entity_type", "未知"),
        first_event_id=first_mention.event_id,
        event_characters=", ".join(event_data.get("人物", [])),
        event_action=event_data.get("行动", ""),
        event_precondition=event_data.get("前提条件", ""),
        event_scene=event_data.get("场景", ""),
        event_emotion=event_data.get("情绪", ""),
        event_impact=event_data.get("结果影响", ""),
        event_source_text=event_data.get("source_text", "")[:500],  # 限制长度
        role_hint=first_mention.role_hint or "无",
        action_hint=first_mention.action_hint or "无",
        relation_hint=first_mention.relation_hint or "无",
        context_snippet=first_mention.context_snippet or "无",
        relation_events=relation_events_text
    )


async def extract_character_node(
    llm_client: AsyncLLMClient,
    template: str,
    entity: Dict[str, Any],
    first_mention: Mention,
    first_event: Dict[str, Any],
    frequent_relations: Dict[str, Dict[str, Any]],
    events: Dict[str, Dict[str, Any]]
) -> Optional[Dict[str, Any]]:
    """使用 LLM 提取人物节点信息 🤖"""
    if llm_client.use_stub:
        # Stub 模式，返回基础结构
        return {
            "id": entity["id"],
            "canonical_name": entity["canonical_name"],
            "aliases": entity.get("aliases", []),
            "entity_type": entity.get("entity_type", "未知"),
            "source_event_id": first_mention.event_id
        }
    
    try:
        prompt = build_prompt(template, entity, first_mention, first_event, frequent_relations, events)
        result = await llm_client.invoke(prompt=prompt, return_json=True)
        
        # 后处理：为 relations 添加 source_event_id
        if result and "relations" in result:
            for relation in result["relations"]:
                target_id = relation.get("target_id")
                if target_id and target_id in frequent_relations:
                    relation["source_event_id"] = frequent_relations[target_id]["first_event_id"]
        
        return result
    except Exception as e:
        print(f"[red]❌ 提取 {entity['canonical_name']} 失败: {e}[/red]")
        return None


async def main_async():
    """异步主函数 🌟"""
    # 默认文件路径
    entities_path = Path("out/entities.json")
    mentions_path = Path("out/mentions.json")
    events_path = Path("out/extract_chain.json")
    output_path = Path("out/characters.json")
    
    # 从命令行参数获取路径（如果提供）
    if len(sys.argv) > 1:
        entities_path = Path(sys.argv[1])
    if len(sys.argv) > 2:
        mentions_path = Path(sys.argv[2])
    if len(sys.argv) > 3:
        events_path = Path(sys.argv[3])
    if len(sys.argv) > 4:
        output_path = Path(sys.argv[4])
    
    print("=" * 70)
    print("🎭 人物节点初始化系统 🎭")
    print("=" * 70)
    print()
    
    # 加载数据
    print("[cyan]📂 加载数据文件...[/cyan]")
    if not entities_path.exists():
        print(f"[red]❌ 实体文件不存在: {entities_path}[/red]")
        sys.exit(1)
    if not mentions_path.exists():
        print(f"[red]❌ Mention 文件不存在: {mentions_path}[/red]")
        sys.exit(1)
    if not events_path.exists():
        print(f"[red]❌ 事件文件不存在: {events_path}[/red]")
        sys.exit(1)
    
    entities = load_entities(entities_path)
    mentions = load_mentions(mentions_path)
    events = load_events(events_path)
    
    print(f"[green]✓[/green] 加载了 {len(entities)} 个实体")
    print(f"[green]✓[/green] 加载了 {len(mentions)} 个 Mention")
    print(f"[green]✓[/green] 加载了 {len(events)} 个事件")
    print()
    
    # 初始化 LLM 客户端
    print("[cyan]🤖 初始化 LLM 客户端（DeepSeek）...[/cyan]")
    llm_client = AsyncLLMClient.create_default()
    
    if llm_client.use_stub:
        print("[yellow]⚠️  LLM 客户端为 Stub 模式，将生成基础结构[/yellow]")
        print("[yellow]请设置 OPENAI_API_KEY 环境变量以启用 LLM 提取[/yellow]")
    
    print("[green]✓[/green] LLM 客户端初始化成功")
    print()
    
    # 加载提示词模板
    template = load_character_init_template()
    
    # 提取人物节点
    print("[cyan]🎯 开始提取人物节点信息...[/cyan]")
    character_nodes = []
    
    with Progress(
        SpinnerColumn(),
        TextColumn("[progress.description]{task.description}"),
        BarColumn(),
        TaskProgressColumn(),
    ) as progress:
        task = progress.add_task(
            "[cyan]处理实体...[/cyan]",
            total=len(entities)
        )
        
        for entity in entities:
            canonical_name = entity["canonical_name"]
            aliases = entity.get("aliases", [])
            
            progress.update(task, description=f"[cyan]处理: {canonical_name}...[/cyan]")
            
            # 1. 找到"第一案发现场"
            first_mention = find_first_mention(canonical_name, aliases, mentions)
            
            if not first_mention:
                print(f"[yellow]⚠️  {canonical_name} 未找到初次提及，跳过[/yellow]")
                progress.advance(task)
                continue
            
            # 2. 获取首次出现的事件
            first_event = events.get(first_mention.event_id)
            if not first_event:
                print(f"[yellow]⚠️  {canonical_name} 的事件 {first_mention.event_id} 未找到，跳过[/yellow]")
                progress.advance(task)
                continue
            
            # 3. 计算重要人物之间的关系
            entity_type = entity.get("entity_type", "路人")
            frequent_relations = calculate_relation_cooccurrence(
                canonical_name, aliases, entity_type, mentions, entities
            )
            
            if frequent_relations:
                print(f"[cyan]  {canonical_name} 有 {len(frequent_relations)} 个重要关系[/cyan]")
            
            # 4. 使用 LLM 提取人物节点信息
            node = await extract_character_node(
                llm_client, template, entity, first_mention, first_event, frequent_relations, events
            )
            
            if node:
                character_nodes.append(node)
            
            progress.advance(task)
    
    print()
    print(f"[green]✓[/green] 成功提取 {len(character_nodes)} 个人物节点")
    print()
    
    # 保存结果
    print(f"[cyan]💾 保存结果到: {output_path}[/cyan]")
    output_data = {
        "characters": character_nodes,
        "total_count": len(character_nodes),
        "metadata": {
            "source_entities": str(entities_path),
            "source_mentions": str(mentions_path),
            "source_events": str(events_path)
        }
    }
    
    output_path.parent.mkdir(parents=True, exist_ok=True)
    with open(output_path, 'w', encoding='utf-8') as f:
        json.dump(output_data, f, ensure_ascii=False, indent=2)
    
    print(f"[green]✓[/green] 人物节点已保存到: {output_path}")
    print()
    
    print("=" * 70)
    print("🎉 人物节点初始化完成！")
    print("=" * 70)


def main():
    """主函数入口 🚀"""
    asyncio.run(main_async())


if __name__ == "__main__":
    main()

