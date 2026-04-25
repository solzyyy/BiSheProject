"""
运行 Canonical Branch 生成器的简单脚本 🚀✨
"""

import asyncio
import json
from pathlib import Path

from src.branch.canonical_branch import CanonicalBranchGenerator
from src.core.llm_client import AsyncLLMClient


async def main():
    """主函数"""
    print("📜 开始生成 Canonical Branch（主线）...")
    print()
    
    # 初始化 LLM 客户端
    llm_client = AsyncLLMClient.create_default("deepseek")
    
    output_file = Path("out/canonical_branch.json")
    progress_file = output_file.parent / "canonical_branch_progress.json"

    # 创建生成器（让 progress_file 与 output_file 同目录，避免串进度）
    generator = CanonicalBranchGenerator(
        llm_client=llm_client,
        progress_file=progress_file,
    )
    
    try:
        # 生成主线记录
        # 可以修改这些参数：
        # - start_event_id: 起始事件ID（None = 自动查找第一个事件）
        # - max_events: 最大处理事件数（None = 处理所有事件）
        # - resume: 是否从上次进度恢复（True = 是）
        result = await generator.generate_canonical_branch(
            start_event_id=None,  # 自动查找第一个事件
            max_events=None,  # 处理所有事件（可以改为数字，如 5，只处理前5个）
            resume=True,  # 从上次进度恢复
            save_progress_interval=1,  # 每处理1个事件保存一次进度
        )
        
        # 保存结果
        output_file.parent.mkdir(parents=True, exist_ok=True)
        
        with open(output_file, 'w', encoding='utf-8') as f:
            json.dump(result, f, ensure_ascii=False, indent=2)
        
        print()
        print(f"✅ 生成完成！结果已保存到: {output_file}")
        print()
        print("📊 生成统计：")
        print(f"  起始事件ID: {result.get('start_event_id')}")
        print(f"  处理事件数: {result.get('total_events', 0)}")
        print(f"  主线记录数: {len(result.get('processed_events', []))}")
        
        # 显示第一个主线记录示例
        if result.get('processed_events'):
            first_record = result['processed_events'][0]
            print()
            print("📝 第一个主线记录示例：")
            print(f"  事件ID: {first_record.get('event_id')}")
            print(f"  描述: {first_record.get('description', '')[:100]}...")
            if first_record.get('decision_point'):
                print(f"  选择节点: {first_record.get('decision_point')}")
                print(f"  主线选择: {first_record.get('canonical_choice')}")
            print(f"  状态变化数: {len(first_record.get('state_changes', []))}")
            print(f"  应用的状态变化数: {len(first_record.get('selected_state_changes', []))}")
    except Exception as e:
        print(f"❌ 生成失败: {e}")
        import traceback
        traceback.print_exc()
    finally:
        generator.close()


if __name__ == "__main__":
    asyncio.run(main())














