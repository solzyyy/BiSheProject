"""
内容管理器 - 管理已生成内容的向量化存储和检索 📝✨

负责：
- 将已生成的内容存储到向量数据库
- 基础的向量检索功能
- 内容索引管理
"""

import json
import sys
from typing import Dict, List, Any, Optional
from pathlib import Path
from datetime import datetime

# 添加 src 目录到路径
src_dir = Path(__file__).parent.parent.parent
if str(src_dir) not in sys.path:
    sys.path.insert(0, str(src_dir))

from core.vector_db import VectorDB
from state_manager.models import GeneratedContent


class ContentManager:
    """
    内容管理器 - 管理已生成内容的存储和检索 🔍
    
    功能：
    - 向量化存储：将内容存储到向量数据库，支持语义检索
    - 元数据管理：记录内容的关联信息（事件ID、时间戳等）
    """
    
    def __init__(
        self,
        vector_db: Optional[VectorDB] = None,
        collection_name: str = "generated_content",
    ):
        """
        初始化内容管理器
        
        Args:
            vector_db: 向量数据库实例（如果为 None，会创建新实例）
            collection_name: 向量数据库集合名称
        """
        self.vector_db = vector_db or VectorDB(collection_name=collection_name)
        
        # 内容索引（内存中，用于快速查找）
        self.content_index: Dict[str, GeneratedContent] = {}
    
    async def add_content(
        self,
        content: GeneratedContent,
    ) -> str:
        """
        添加内容到管理器 📝
        
        Args:
            content: 生成的内容对象
            
        Returns:
            内容ID
        """
        # 存储到向量数据库
        self.vector_db.add_documents(
            documents=[content.content_text],
            ids=[content.content_id],
            metadatas=[{
                "event_id": content.event_id,
                "content_type": content.content_type,
                "timestamp": datetime.now().isoformat(),
                **content.metadata,
            }]
        )
        
        # 更新索引
        self.content_index[content.content_id] = content
        
        return content.content_id
    
    async def search_content(
        self,
        query: str,
        n_results: int = 5,
        content_type: Optional[str] = None,
    ) -> List[Dict[str, Any]]:
        """
        搜索相关内容 🔍
        
        Args:
            query: 查询文本
            n_results: 返回结果数量
            content_type: 内容类型过滤（可选）
            
        Returns:
            搜索结果列表，每个结果包含：content_id, content_text, metadata, distance
        """
        # 构建过滤条件
        where = None
        if content_type:
            where = {"content_type": content_type}
        
        # 查询向量数据库
        results = self.vector_db.query(
            query_texts=[query],
            n_results=n_results,
            where=where,
        )
        
        # 格式化结果
        search_results = []
        if results.get("ids") and results["ids"][0]:
            for i, content_id in enumerate(results["ids"][0]):
                content_text = results["documents"][0][i] if results.get("documents") else ""
                metadata = results["metadatas"][0][i] if results.get("metadatas") else {}
                distance = results["distances"][0][i] if results.get("distances") else 0.0
                
                # 获取完整内容对象
                content = self.content_index.get(content_id)
                
                search_results.append({
                    "content_id": content_id,
                    "content_text": content_text,
                    "metadata": metadata,
                    "distance": distance,
                    "content": content,
                })
        
        return search_results
    
    def save_index(self, index_path: str = "out/content_index.json"):
        """保存内容索引到文件"""
        index_file = Path(index_path)
        index_file.parent.mkdir(parents=True, exist_ok=True)
        
        index_data = {
            "contents": [
                {
                    "content_id": content.content_id,
                    "event_id": content.event_id,
                    "content_type": content.content_type,
                    "content_text": content.content_text,
                    "metadata": content.metadata,
                }
                for content in self.content_index.values()
            ],
            "total_count": len(self.content_index),
        }
        
        with open(index_file, "w", encoding="utf-8") as f:
            json.dump(index_data, f, ensure_ascii=False, indent=2)
    
    def load_index(self, index_path: str = "out/content_index.json"):
        """从文件加载内容索引"""
        index_file = Path(index_path)
        if not index_file.exists():
            return
        
        with open(index_file, "r", encoding="utf-8") as f:
            index_data = json.load(f)
        
        for content_data in index_data.get("contents", []):
            content = GeneratedContent(**content_data)
            self.content_index[content.content_id] = content


__all__ = ["ContentManager"]

