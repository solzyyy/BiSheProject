"""
事件提取器 📝✨

从文本中提取事件和关系的核心逻辑。
支持并发处理，保持结果有序。
"""

import asyncio
import json
import os
from pathlib import Path
from collections.abc import Callable
from typing import Any, Dict, List, Tuple

from dotenv import load_dotenv
from rich import print
from rich.progress import Progress, SpinnerColumn, TextColumn, BarColumn, TaskProgressColumn

from core.llm_client import AsyncLLMClient
from core.prompt_overrides import SLOT_EXTRACT_EVENTS, read_prompt_with_override
from core.text_processor import split_text_to_paragraphs

load_dotenv()

# 提示词文件路径（相对项目根）
_PROJECT_ROOT = Path(__file__).resolve().parents[3]
PROMPT_PATH = _PROJECT_ROOT / "prompts" / "extract_prompt.txt"


def load_prompt_template() -> str:
    """加载提示词模板 📄（支持环境变量覆盖，见 core.prompt_overrides）"""
    return read_prompt_with_override(PROMPT_PATH, SLOT_EXTRACT_EVENTS)


async def extract_events_from_paragraph(
    llm_client: AsyncLLMClient,
    paragraph_text: str,
    chunk_index: int,
    total_chunks: int,
) -> Dict[str, Any]:
    """
    从单个段落中异步提取事件 📝
    
    使用异步 LLM 客户端调用，提取事件。
    
    Args:
        llm_client: 异步 LLM 客户端实例
        paragraph_text: 当前段落文本
        chunk_index: 段落序号（从 1 开始）
        total_chunks: 总段落数
        
    Returns:
        包含 new_events 的字典，以及 chunk_index 用于排序
    """
    template = load_prompt_template()
    
    # 在 prompt 中添加序号信息
    prompt = (
        template.replace("{paragraph_text}", paragraph_text)
    )
    
    # 添加序号信息到 prompt
    numbering_hint = (
        f"\n\n【片段信息】"
        f"这是第 {chunk_index}/{total_chunks} 个文本片段。"
        f"请为本片段中的事件编号，编号格式为 E{chunk_index}-1, E{chunk_index}-2, ...（事件序号）。"
        f"若无事件可抽取，请返回空列表。"
    )
    prompt = f"{prompt}{numbering_hint}"
    
    result = await llm_client.invoke(prompt, return_json=True)
    
    # 返回结果时带上序号，方便后续排序
    return {
        "chunk_index": chunk_index,
        "result": result,
    }


async def process_text_async(
    text: str,
    llm_client: AsyncLLMClient | None = None,
    chunk_size: int = 800,
    chunk_overlap: int = 160,
    max_chunks: int | None = None,
    progress_callback: Callable[[int, int], None] | None = None,
) -> Tuple[Dict[str, Any], List[str]]:
    """
    异步并发处理文本，提取事件 📚
    
    Args:
        text: 输入文本
        llm_client: 异步 LLM 客户端（如果为 None，则使用默认客户端）
        
    Returns:
        (提取结果字典, 错误列表)
    """
    # 创建异步 LLM 客户端
    if llm_client is None:
        llm_client = AsyncLLMClient.create_default()
    
    paragraphs = split_text_to_paragraphs(
        text,
        chunk_size=chunk_size,
        chunk_overlap=chunk_overlap,
    )
    if max_chunks is not None and max_chunks > 0:
        paragraphs = paragraphs[:max_chunks]
    total_chunks = len(paragraphs)

    if total_chunks == 0:
        if progress_callback is not None:
            progress_callback(0, 0)
        return {"new_events": []}, []

    engine = "Stub" if llm_client.use_stub else "DeepSeek"
    print(f"[cyan]Total chunks:[/cyan] {total_chunks}  |  [cyan]Engine:[/cyan] {engine}")

    # 创建进度条
    with Progress(
        SpinnerColumn(),
        TextColumn("[progress.description]{task.description}"),
        BarColumn(),
        TaskProgressColumn(),
        transient=False,
    ) as progress:
        task = progress.add_task("Extracting events", total=total_chunks)
        
        # 创建包装函数，确保即使出错也能追踪到 chunk_index
        async def extract_with_index(idx: int, para: str) -> Tuple[int, Dict[str, Any] | None, Exception | None]:
            """提取事件，返回 (chunk_index, result, error)"""
            try:
                result = await extract_events_from_paragraph(
                    llm_client,
                    para,
                    chunk_index=idx,
                    total_chunks=total_chunks,
                )
                return (idx, result, None)
            except Exception as e:
                return (idx, None, e)
        
        # 创建所有任务（并发执行）
        tasks = [
            asyncio.create_task(extract_with_index(idx, para))
            for idx, para in enumerate(paragraphs, 1)
        ]
        
        # 等待所有任务完成（不管返回顺序）
        results_dict: Dict[int, Dict[str, Any]] = {}
        errors: List[str] = []
        
        # 使用 asyncio.as_completed 处理完成的任务
        completed = 0
        for coro in asyncio.as_completed(tasks):
            chunk_index, result, error = await coro
            if error:
                error_msg = f"chunk {chunk_index} error: {error}"
                errors.append(error_msg)
                print(f"[red]Failed[/red] {error_msg}")
            else:
                results_dict[chunk_index] = result["result"]
            progress.update(task, advance=1)
            completed += 1
            if progress_callback is not None:
                progress_callback(completed, total_chunks)
        
        # 按片段序号顺序整理事件，并重新编号为完全递增（E1, E2, E3...）
        all_events: List[Dict[str, Any]] = []
        event_counter = 1  # 全局事件编号，从 1 开始
        
        for idx in range(1, total_chunks + 1):
            if idx in results_dict:
                result = results_dict[idx]
                new_events = result.get("new_events", [])
                # 按片段顺序添加该片段的所有事件，并重新编号
                for ev in new_events:
                    # 重新编号为完全递增的格式（E1, E2, E3...）
                    ev["event_id"] = f"E{event_counter}"
                    all_events.append(ev)
                    event_counter += 1
            else:
                # 如果某个片段处理失败，记录错误
                errors.append(f"chunk {idx} missing from results")

    chained = {
        "new_events": all_events,
    }
    return chained, errors


def process_text(
    text: str,
    llm_client: AsyncLLMClient | None = None,
    chunk_size: int = 800,
    chunk_overlap: int = 160,
    max_chunks: int | None = None,
    progress_callback: Callable[[int, int], None] | None = None,
) -> Tuple[Dict[str, Any], List[str]]:
    """
    处理文本，提取事件 📚（同步包装器）
    
    这是同步接口，内部调用异步版本。
    
    Args:
        text: 输入文本
        llm_client: 异步 LLM 客户端（如果为 None，则使用默认客户端）
        
    Returns:
        (提取结果字典, 错误列表)
    """
    return asyncio.run(
        process_text_async(
            text,
            llm_client,
            chunk_size=chunk_size,
            chunk_overlap=chunk_overlap,
            max_chunks=max_chunks,
            progress_callback=progress_callback,
        )
    )
