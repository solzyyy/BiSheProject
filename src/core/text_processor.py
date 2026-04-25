"""
文本处理工具 📝✨

负责文本的分割、预处理等操作。
"""

from typing import List
from langchain_text_splitters import RecursiveCharacterTextSplitter


def split_text_to_paragraphs(
    text: str,
    chunk_size: int = 800,
    chunk_overlap: int = 160
) -> List[str]:
    """
    将文本分割为段落 📄
    
    使用递归字符分割器，优先按段落、句子等自然边界分割。
    
    Args:
        text: 输入文本
        chunk_size: 每个段落的目标长度（字符数）
        chunk_overlap: 段落之间的重叠长度（字符数）
        
    Returns:
        段落列表
    """
    separators = ["\n\n", "。\n", "。", "；", ";", "，", ",", "\n", " "]
    splitter = RecursiveCharacterTextSplitter(
        separators=separators,
        chunk_size=chunk_size,
        chunk_overlap=chunk_overlap,
        length_function=lambda s: len(s),
    )
    return splitter.split_text(text)

