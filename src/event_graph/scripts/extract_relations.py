"""
事件关系提取器 🔗✨

从已提取的事件中分析并提取事件之间的关系。
支持因果、时间顺序、冲突、协作、情感线索等关系类型。
"""

import asyncio
import json
import os
import sys
from collections.abc import Callable
from pathlib import Path
from typing import Any, Dict, List, Tuple

from dotenv import load_dotenv
from rich.progress import Progress, SpinnerColumn, TextColumn, BarColumn, TaskProgressColumn

from core.llm_client import AsyncLLMClient
from core.prompt_overrides import SLOT_RELATION_EXTRACT, read_prompt_with_override

load_dotenv()


def _stdout_is_utf8() -> bool:
    """Windows 默认 GBK 控制台无法输出 emoji，Rich 会触发 UnicodeEncodeError。"""
    enc = (getattr(sys.stdout, "encoding", None) or "").lower().replace("-", "_")
    return enc in ("utf_8", "utf8")


def _rich_console_ok() -> bool:
    """是否可对当前 stdout 安全使用 Rich（彩色、进度条、含 emoji 的文案）。"""
    if sys.platform == "win32" and not _stdout_is_utf8():
        return False
    return True


def _log(msg: str) -> None:
    """仅 ASCII + 中文，避免 Windows GBK 控制台编码错误。"""
    print(msg, flush=True)

_REL_ROOT = Path(__file__).resolve().parents[3]
RELATION_PROMPT_PATH = _REL_ROOT / "prompts" / "relation_extract_prompt.txt"


def load_relation_prompt_template() -> str:
    """加载关系提取提示词模板 📄（支持环境变量覆盖）"""
    return read_prompt_with_override(RELATION_PROMPT_PATH, SLOT_RELATION_EXTRACT)


async def extract_relation_between_events(
    llm_client: AsyncLLMClient,
    event1: Dict[str, Any],
    event2: Dict[str, Any],
) -> Dict[str, Any] | None:
    """
    分析两个事件之间的关系 🔗
    
    Args:
        llm_client: 异步 LLM 客户端
        event1: 第一个事件
        event2: 第二个事件
        
    Returns:
        关系字典，如果没有关系则返回 None
    """
    template = load_relation_prompt_template()
    
    # 格式化事件信息
    event1_text = _format_event_for_prompt(event1)
    event2_text = _format_event_for_prompt(event2)
    
    prompt = (
        template
        .replace("{event1}", event1_text)
        .replace("{event2}", event2_text)
    )
    
    try:
        result = await llm_client.invoke(prompt, return_json=True)
        
        if result.get("has_relation", False):
            return {
                "type": result.get("type", "时间顺序"),
                "description": result.get("description", ""),
            }
        return None
    except Exception as e:
        _log(f"[extract-relations] 单对关系提取失败: {e}")
        return None


def _format_event_for_prompt(event: Dict[str, Any]) -> str:
    """格式化事件信息用于提示词"""
    event_data = event.get("事件", {})
    event_id = event.get("event_id", "未知")
    
    return f"""事件ID: {event_id}
人物: {', '.join(event_data.get('人物', []))}
行动: {event_data.get('行动', '')}
目标: {event_data.get('目标', '')}
结果: {event_data.get('结果', '')}
情绪: {event_data.get('情绪', '')}
时间: {event_data.get('时间', '')}
场景: {event_data.get('场景', '')}
前提条件: {event_data.get('前提条件', '')}
结果影响: {event_data.get('结果影响', '')}
"""


async def extract_all_relations_async(
    events: List[Dict[str, Any]],
    llm_client: AsyncLLMClient | None = None,
    max_concurrent: int = 10,
    *,
    progress_callback: Callable[[int, int], None] | None = None,
) -> List[Dict[str, Any]]:
    """
    提取所有事件之间的关系 🔗
    
    线性结构策略：
    1. 使用 LLM 分析事件对之间的关系
    2. 只保留 LLM 识别出的关系，保持线性结构
    3. 不自动添加时间顺序关系，让 LLM 自己判断
    
    Args:
        events: 事件列表（已按顺序排列，ID为E1, E2, E3...）
        llm_client: 异步 LLM 客户端
        max_concurrent: 最大并发数
        
    Returns:
        关系列表
    """
    if llm_client is None:
        llm_client = AsyncLLMClient.create_default()
    
    relations: List[Dict[str, Any]] = []
    
    _log(f"[extract-relations] 开始提取事件关系（线性结构），事件数: {len(events)}")

    # 使用 LLM 分析所有事件对之间的关系（保持线性结构）
    if llm_client.use_stub:
        _log("[extract-relations] LLM 为 Stub 模式，跳过关系提取")
        if progress_callback is not None:
            progress_callback(1, 1)
        return relations
    
    _log("[extract-relations] 使用 LLM 分析相邻事件对关系（线性结构）")
    
    # 只分析相邻事件对（i, i+1），保持线性结构
    pairs_to_analyze: List[Tuple[int, int]] = []
    for i in range(len(events) - 1):
        # 只分析相邻的事件对
        pairs_to_analyze.append((i, i + 1))
    
    if not pairs_to_analyze:
        if progress_callback is not None:
            progress_callback(1, 1)
        return relations
    
    _log(f"[extract-relations] 待分析相邻对数: {len(pairs_to_analyze)}")

    async def analyze_pair_with_index(i: int, j: int) -> Tuple[int, int, Dict[str, Any] | None]:
        """分析事件对，返回 (i, j, relation)"""
        try:
            relation = await extract_relation_between_events(
                llm_client,
                events[i],
                events[j],
            )
            return (i, j, relation)
        except Exception:
            return (i, j, None)

    tasks = [
        asyncio.create_task(analyze_pair_with_index(i, j))
        for i, j in pairs_to_analyze
    ]

    relation_type_count: Dict[str, int] = {}
    total_pairs = len(pairs_to_analyze)

    def _consume_pair_result(i: int, j: int, relation: Dict[str, Any] | None) -> None:
        if relation:
            event1_id = events[i].get("event_id", f"E{i+1}")
            event2_id = events[j].get("event_id", f"E{j+1}")
            rel_type = relation["type"]
            existing = any(
                r.get("from") == event1_id and r.get("to") == event2_id
                for r in relations
            )
            if not existing:
                relations.append({
                    "from": event1_id,
                    "to": event2_id,
                    "类型": rel_type,
                    "描述": relation["description"],
                })
                relation_type_count[rel_type] = relation_type_count.get(rel_type, 0) + 1

    if progress_callback is not None:
        done = 0
        for coro in asyncio.as_completed(tasks):
            i, j, relation = await coro
            _consume_pair_result(i, j, relation)
            done += 1
            progress_callback(done, total_pairs)
    elif _rich_console_ok():
        with Progress(
            SpinnerColumn(),
            TextColumn("[progress.description]{task.description}"),
            BarColumn(),
            TaskProgressColumn(),
            transient=False,
        ) as progress:
            task = progress.add_task("分析关系", total=total_pairs)
            for coro in asyncio.as_completed(tasks):
                i, j, relation = await coro
                _consume_pair_result(i, j, relation)
                progress.update(task, advance=1)
    else:
        done_plain = 0
        step = max(1, total_pairs // 10)
        for coro in asyncio.as_completed(tasks):
            i, j, relation = await coro
            _consume_pair_result(i, j, relation)
            done_plain += 1
            if done_plain in (1, total_pairs) or done_plain % step == 0:
                _log(f"[extract-relations] 进度 {done_plain}/{total_pairs}")

    _log(f"[extract-relations] 共提取 {len(relations)} 个关系")
    if relation_type_count:
        _log("[extract-relations] 关系类型统计:")
        for rel_type, count in sorted(relation_type_count.items()):
            _log(f"  - {rel_type}: {count}")
    return relations


def extract_relations(
    events: List[Dict[str, Any]],
    llm_client: AsyncLLMClient | None = None,
    *,
    progress_callback: Callable[[int, int], None] | None = None,
) -> List[Dict[str, Any]]:
    """
    提取所有事件之间的关系（同步包装器）🔗

    Args:
        events: 事件列表
        llm_client: 异步 LLM 客户端
        progress_callback: 可选；每完成一对 (done, total)，供 UI 进度条使用

    Returns:
        关系列表
    """
    return asyncio.run(
        extract_all_relations_async(
            events, llm_client, progress_callback=progress_callback
        )
    )


def main() -> None:
    """主函数 🎯"""
    # 1. 读取事件数据
    json_path = Path(os.getenv("EXTRACT_CHAIN_JSON", "out/extract_chain.json"))
    
    if not json_path.exists():
        _log(f"[extract-relations] 错误: 未找到文件 {json_path}")
        _log("[extract-relations] 请先运行事件提取生成 extract_chain.json")
        return

    _log(f"[extract-relations] 读取事件数据: {json_path}")
    data = json.loads(json_path.read_text(encoding="utf-8"))
    events = data.get("new_events", [])
    
    if not events:
        _log("[extract-relations] 错误: JSON 中无 new_events")
        return

    _log(f"[extract-relations] 已加载 {len(events)} 个事件")
    
    # 2. 提取关系
    llm_client = AsyncLLMClient.create_default()
    relations = extract_relations(events, llm_client)
    
    # 3. 保存结果
    output_data = {
        "relations": relations,
    }
    
    out_file = os.getenv("EXTRACT_RELATIONS_OUTPUT", "").strip()
    if out_file:
        output_path = Path(out_file)
    else:
        out_dir = Path("out")
        out_dir.mkdir(parents=True, exist_ok=True)
        output_path = out_dir / "extract_relations.json"

    output_path.parent.mkdir(parents=True, exist_ok=True)

    output_path.write_text(
        json.dumps(output_data, ensure_ascii=False, indent=2),
        encoding="utf-8"
    )
    
    _log(f"[extract-relations] 结果已保存: {output_path}")
    _log(f"[extract-relations] 事件数: {len(events)}  关系数: {len(relations)}")

    # 统计关系类型
    relation_types = {}
    for rel in relations:
        rel_type = rel.get("类型", "未知")
        relation_types[rel_type] = relation_types.get(rel_type, 0) + 1

    _log("[extract-relations] 关系类型统计:")
    for rel_type, count in sorted(relation_types.items()):
        _log(f"  - {rel_type}: {count}")


if __name__ == "__main__":
    main()

