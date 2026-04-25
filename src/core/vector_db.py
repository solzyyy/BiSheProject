"""
向量数据库封装 🔍

负责：
- 初始化 Chroma 客户端
- 添加文档和向量
- 查询相似文档
- 管理集合（collections）
"""

from pathlib import Path
from typing import List, Dict, Any, Optional
import chromadb
from chromadb.config import Settings


class VectorDB:
    """向量数据库封装类"""
    
    def __init__(self, db_path: str = "./vector_db", collection_name: str = "text_chunks"):
        """
        初始化向量数据库
        
        Args:
            db_path: 数据库存储路径
            collection_name: 集合名称
        """
        self.db_path = Path(db_path)
        self.collection_name = collection_name
        
        # 创建持久化客户端
        self.client = chromadb.PersistentClient(
            path=str(self.db_path),
            settings=Settings(anonymized_telemetry=False)
        )
        
        # 获取或创建集合
        try:
            self.collection = self.client.get_collection(name=collection_name)
            print(f"[green]✓[/green] 已加载集合: {collection_name}")
        except Exception:
            self.collection = self.client.create_collection(
                name=collection_name,
                metadata={"hnsw:space": "cosine"}  # 使用余弦相似度
            )
            print(f"[green]✓[/green] 已创建新集合: {collection_name}")
    
    def add_documents(
        self,
        documents: List[str],
        ids: Optional[List[str]] = None,
        metadatas: Optional[List[Dict[str, Any]]] = None
    ):
        """
        添加文档到向量数据库
        
        Args:
            documents: 文档文本列表
            ids: 文档ID列表（可选，自动生成）
            metadatas: 文档元数据列表（可选）
        """
        if not documents:
            return
        
        # 如果没有提供ID，自动生成
        if ids is None:
            ids = [f"chunk_{i:06d}" for i in range(len(documents))]
        
        # 如果没有提供元数据，使用空字典
        if metadatas is None:
            metadatas = [{}] * len(documents)
        
        # 确保长度一致
        assert len(documents) == len(ids) == len(metadatas), \
            "documents, ids, metadatas 长度必须一致"
        
        # 批量添加（Chroma 会自动处理）
        self.collection.add(
            documents=documents,
            ids=ids,
            metadatas=metadatas
        )
        
        print(f"[green]✓[/green] 已添加 {len(documents)} 个文档到向量数据库")
    
    def query(
        self,
        query_texts: List[str],
        n_results: int = 5,
        where: Optional[Dict[str, Any]] = None
    ) -> Dict[str, Any]:
        """
        查询相似文档
        
        Args:
            query_texts: 查询文本列表
            n_results: 返回的结果数量
            where: 过滤条件（可选）
        
        Returns:
            查询结果字典，包含：
            - documents: 文档列表
            - distances: 距离列表（越小越相似）
            - ids: 文档ID列表
            - metadatas: 元数据列表
        """
        results = self.collection.query(
            query_texts=query_texts,
            n_results=n_results,
            where=where
        )
        
        return results
    
    def get_collection_info(self) -> Dict[str, Any]:
        """获取集合信息"""
        count = self.collection.count()
        return {
            "collection_name": self.collection_name,
            "document_count": count,
            "db_path": str(self.db_path)
        }
    
    def delete_collection(self):
        """删除集合（谨慎使用）"""
        self.client.delete_collection(name=self.collection_name)
        print(f"[yellow]⚠️  已删除集合: {self.collection_name}[/yellow]")

