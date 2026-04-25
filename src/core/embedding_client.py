"""
嵌入模型客户端 🧬

负责：
- 调用嵌入模型 API
- 将文本转换为向量
- 支持批量处理
- 缓存机制
"""

import os
from typing import List, Optional
from pathlib import Path
import json

try:
    from openai import OpenAI
    OPENAI_AVAILABLE = True
except ImportError:
    OPENAI_AVAILABLE = False

# sentence_transformers 延迟导入，避免 transformers 库导入问题
# 只有在实际使用 BGE 模型时才会导入
SENTENCE_TRANSFORMERS_AVAILABLE = None  # 延迟检查


class EmbeddingClient:
    """嵌入模型客户端"""
    
    def __init__(
        self,
        model_type: str = "openai",
        model_name: Optional[str] = None,
        api_key: Optional[str] = None,
        cache_dir: Optional[str] = None
    ):
        """
        初始化嵌入模型客户端
        
        Args:
            model_type: 模型类型 ("openai" 或 "bge")
            model_name: 模型名称
            api_key: API密钥（OpenAI需要）
            cache_dir: 缓存目录（BGE模型下载位置）
        """
        self.model_type = model_type.lower()
        self.cache_dir = cache_dir
        
        if self.model_type == "openai":
            if not OPENAI_AVAILABLE:
                raise ImportError("需要安装 openai 库: pip install openai")
            
            self.model_name = model_name or "text-embedding-3-small"
            self.api_key = api_key or os.getenv("OPENAI_API_KEY")
            
            if not self.api_key:
                raise ValueError("OpenAI API密钥未设置，请设置 OPENAI_API_KEY 环境变量")
            
            self.client = OpenAI(api_key=self.api_key)
            self.dimension = 1536  # text-embedding-3-small 的维度
            
        elif self.model_type == "bge":
            # 延迟导入 sentence_transformers，避免在不需要时触发 transformers 导入错误
            try:
                from sentence_transformers import SentenceTransformer
            except ImportError:
                raise ImportError("需要安装 sentence-transformers 库: pip install sentence-transformers")
            except Exception as e:
                # 捕获 transformers 库的文件缺失错误
                raise ImportError(
                    f"sentence-transformers 或其依赖 transformers 库安装不完整: {e}\n"
                    "请尝试重新安装: pip install --upgrade --force-reinstall sentence-transformers transformers"
                ) from e
            
            self.model_name = model_name or "BAAI/bge-small-zh-v1.5"
            
            # 加载模型
            cache_path = Path(cache_dir) if cache_dir else None
            self.model = SentenceTransformer(
                self.model_name,
                cache_folder=str(cache_path) if cache_path else None
            )
            self.dimension = self.model.get_sentence_embedding_dimension()
            
        else:
            raise ValueError(f"不支持的模型类型: {model_type}，支持的类型: openai, bge")
        
        print(f"[green]✓[/green] 已初始化嵌入模型: {self.model_name} (维度: {self.dimension})")
    
    def embed(self, texts: List[str], batch_size: int = 100) -> List[List[float]]:
        """
        将文本列表转换为向量列表
        
        Args:
            texts: 文本列表
            batch_size: 批处理大小（OpenAI会自动批处理，BGE需要手动批处理）
        
        Returns:
            向量列表，每个向量是一个浮点数列表
        """
        if not texts:
            return []
        
        if self.model_type == "openai":
            return self._embed_openai(texts)
        elif self.model_type == "bge":
            return self._embed_bge(texts, batch_size)
        else:
            raise ValueError(f"不支持的模型类型: {self.model_type}")
    
    def _embed_openai(self, texts: List[str]) -> List[List[float]]:
        """使用 OpenAI 模型生成嵌入"""
        try:
            response = self.client.embeddings.create(
                model=self.model_name,
                input=texts
            )
            return [item.embedding for item in response.data]
        except Exception as e:
            print(f"[red]❌ OpenAI 嵌入生成失败: {e}[/red]")
            raise
    
    def _embed_bge(self, texts: List[str], batch_size: int) -> List[List[float]]:
        """使用 BGE 模型生成嵌入"""
        try:
            # BGE 模型自动批处理
            embeddings = self.model.encode(
                texts,
                batch_size=batch_size,
                show_progress_bar=len(texts) > 100,
                normalize_embeddings=True  # 归一化，适合余弦相似度
            )
            return embeddings.tolist()
        except Exception as e:
            print(f"[red]❌ BGE 嵌入生成失败: {e}[/red]")
            raise
    
    def embed_single(self, text: str) -> List[float]:
        """
        将单个文本转换为向量
        
        Args:
            text: 文本字符串
        
        Returns:
            向量（浮点数列表）
        """
        return self.embed([text])[0]
    
    @property
    def embedding_dimension(self) -> int:
        """获取嵌入维度"""
        return self.dimension

