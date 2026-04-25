"""
基于 LLM 的人物提及（Mention）提取器 🎯✨

使用 LLM 智能提取 Mention 信息，相比规则提取能更好地理解语境、推断隐含信息。
"""

import json
import asyncio
import os
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional
from pydantic import ValidationError
from rich.console import Console
from rich.progress import Progress, SpinnerColumn, TextColumn, BarColumn, TaskProgressColumn, TimeRemainingColumn

from character.models.mention import Mention, MentionExtractionResult
from core.llm_client import AsyncLLMClient
from core.prompt_overrides import SLOT_MENTION_EXTRACT, read_prompt_with_override


# 提示词文件路径
MENTION_EXTRACT_PROMPT_PATH = Path(__file__).resolve().parent.parent.parent.parent / "prompts" / "mention_extract_prompt.txt"


def load_mention_extract_template() -> str:
    """加载 Mention 提取提示词模板 📄（支持环境变量覆盖）"""
    return read_prompt_with_override(MENTION_EXTRACT_PROMPT_PATH, SLOT_MENTION_EXTRACT)


def _rich_progress_console() -> Console:
    """避免 Windows 旧版控制台 + GBK 下 Rich 写 emoji 触发 UnicodeEncodeError；管道模式下换行日志便于 UI 捕获。"""
    kw: dict[str, Any] = {}
    if sys.platform == "win32":
        kw["legacy_windows"] = False
    return Console(**kw)


class LLMMentionExtractor:
    """
    使用 LLM 提取 Mention 信息 🤖✨
    
    优势：
    - 理解复杂语境和隐含信息 🧠
    - 自动推断角色、关系 🎭
    - 处理歧义和省略 💫
    - 提取合适的上下文片段 📖
    """
    
    def __init__(
        self, 
        llm_client: AsyncLLMClient,
        max_retries: int = 3
    ):
        """
        初始化提取器 🌟
        
        Args:
            llm_client: LLM 客户端实例
            max_retries: 失败重试次数
        """
        self.llm_client = llm_client
        self.max_retries = max_retries
        self.system_prompt = load_mention_extract_template()
    
    def _build_extraction_prompt(self, event_data: Dict[str, Any]) -> str:
        """
        构建提取提示词 📝
        
        把事件数据格式化成清晰的提示词，帮助 LLM 更好地理解
        """
        event_info = event_data.get("事件", {})
        
        prompt = f"""请从以下事件中提取每个人物的 Mention 信息：

【事件ID】: {event_data.get('event_id', 'Unknown')}

【人物列表】: {', '.join(event_info.get('人物', []))}

【行动】: {event_info.get('行动', '')}

【前提条件】: {event_info.get('前提条件', '')}

【场景】: {event_info.get('场景', '')}

【情绪】: {event_info.get('情绪', '')}

【结果影响】: {event_info.get('结果影响', '')}

【原文片段】:
{event_info.get('source_text', '')[:500]}{'...' if len(event_info.get('source_text', '')) > 500 else ''}

请为每个人物提取 Mention 记录，以 JSON 格式返回。
"""
        return prompt
    
    async def extract_mentions_from_event_without_id(
        self, 
        event_data: Dict[str, Any]
    ) -> Optional[MentionExtractionResult]:
        """
        从单个事件中提取所有人物的 Mention（不分配 mention_id）🎯
        
        Args:
            event_data: 事件数据字典
            
        Returns:
            MentionExtractionResult 或 None（如果提取失败）
        """
        event_id = event_data.get("event_id", "Unknown")
        
        # 如果 LLM 客户端是 Stub 模式，跳过
        if self.llm_client.use_stub:
            return None
        
        for attempt in range(self.max_retries):
            try:
                # 构建提示词
                user_prompt = self._build_extraction_prompt(event_data)
                full_prompt = f"{self.system_prompt}\n\n{user_prompt}"
                
                # 调用 LLM
                result_dict = await self.llm_client.invoke(
                    prompt=full_prompt,
                    return_json=True
                )
                
                # 为每个 mention 添加 event_id（不分配 mention_id，稍后统一分配）
                mentions_list = []
                for mention_data in result_dict.get("mentions", []):
                    mention_data["event_id"] = event_id
                    # 临时设置一个占位符 ID，稍后会被替换
                    mention_data["mention_id"] = "TEMP_PLACEHOLDER"
                    
                    # 验证数据
                    try:
                        mention = Mention(**mention_data)
                        mentions_list.append(mention)
                    except ValidationError:
                        continue
                
                if not mentions_list:
                    return None
                
                # 构建结果
                return MentionExtractionResult(
                    event_id=event_id,
                    mentions=mentions_list,
                    confidence=result_dict.get("confidence", 0.9),
                    extraction_notes=result_dict.get("extraction_notes", "")
                )
                
            except (json.JSONDecodeError, ValidationError, KeyError, Exception) as e:
                if attempt < self.max_retries - 1:
                    await asyncio.sleep(2)
                else:
                    return None
        
        return None
    
    async def extract_from_events(
        self, 
        events: List[Dict[str, Any]]
    ) -> List[MentionExtractionResult]:
        """
        从事件列表中批量提取 Mentions 📚✨
        
        Args:
            events: 事件列表
        
        Returns:
            所有成功提取的结果列表
        """
        results: List[MentionExtractionResult] = []
        mention_counter = 1
        total = len(events)
        # UI/子进程（如 Streamlit Popen）会设 PIPELINE_UI_PROGRESS=plain；部分环境下 isatty 仍可能为 True
        use_tty_progress = sys.stdout.isatty() and (
            os.environ.get("PIPELINE_UI_PROGRESS", "").lower() != "plain"
        )

        async def handle_event(event: Dict[str, Any]) -> None:
            nonlocal mention_counter
            result = await self.extract_mentions_from_event_without_id(event)
            if result and result.mentions:
                updated_mentions = []
                for mention in result.mentions:
                    updated_mention = mention.model_copy(
                        update={"mention_id": f"M{mention_counter}"}
                    )
                    updated_mentions.append(updated_mention)
                    mention_counter += 1
                updated_result = result.model_copy(update={"mentions": updated_mentions})
                results.append(updated_result)

        if use_tty_progress:
            with Progress(
                SpinnerColumn(),
                TextColumn("[progress.description]{task.description}"),
                BarColumn(),
                TaskProgressColumn(),
                TimeRemainingColumn(),
                transient=False,
                console=_rich_progress_console(),
            ) as progress:
                task = progress.add_task(
                    f"[cyan]处理 {total} 个事件...[/cyan]",
                    total=total,
                )
                for event in events:
                    event_id = event.get("event_id", "Unknown")
                    progress.update(
                        task, description=f"[cyan]处理事件 {event_id}...[/cyan]"
                    )
                    await handle_event(event)
                    progress.advance(task)
        else:
            for i, event in enumerate(events, start=1):
                event_id = event.get("event_id", "Unknown")
                print(
                    f"[mentions] {i}/{total} event_id={event_id}",
                    flush=True,
                )
                await handle_event(event)

        return results


__all__ = ["LLMMentionExtractor"]
