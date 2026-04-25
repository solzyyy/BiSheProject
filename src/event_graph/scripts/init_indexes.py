"""
初始化 Neo4j 数据库索引和约束 🚀✨

这个脚本会创建所有必要的索引和约束，以优化数据库查询性能：
- Event 节点的唯一约束
- StateChange 节点的索引（定位、时间、维度、剧情敏感度）
- Character 节点的约束和索引（ID唯一、别名索引）

使用方法：
    python -m src.scripts.init_indexes
"""

import sys
from pathlib import Path

# 添加项目根目录到路径
project_root = Path(__file__).parent.parent.parent
sys.path.insert(0, str(project_root))

from core.neo4j_client import Neo4jClient


def main():
    """初始化所有索引和约束"""
    print("=" * 60)
    print("🔧 Neo4j 数据库索引和约束初始化工具")
    print("=" * 60)
    print()
    
    # 创建 Neo4j 客户端
    client = Neo4jClient()
    
    try:
        # 初始化所有索引和约束
        client.init_all_indexes()
        print()
        print("=" * 60)
        print("🎉 初始化完成！数据库已优化，查询性能将大幅提升！")
        print("=" * 60)
    except Exception as e:
        print(f"\n❌ 初始化过程中出现错误: {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)
    finally:
        # 关闭数据库连接
        client.close()
        print("\n🔌 数据库连接已关闭")


if __name__ == "__main__":
    main()



