"""
测试结局候选池确定器

使用 branches.json 中的分支选择来测试 determine_ending_candidates 功能
"""

import asyncio
import json
import sys
import io
from pathlib import Path

# 修复 Windows 控制台编码问题
if sys.platform == "win32":
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8')
    sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding='utf-8')

# 添加项目根目录到路径
project_root = Path(__file__).parent.parent
if str(project_root) not in sys.path:
    sys.path.insert(0, str(project_root))

from src.branch.analysis.ending_candidate_determiner import EndingCandidateDeterminer


async def main():
    """测试结局候选池确定器"""
    print("🎯 开始测试结局候选池确定器...\n")
    
    # 加载分支数据
    branches_path = Path("out/branches.json")
    if not branches_path.exists():
        print(f"❌ 找不到分支文件: {branches_path}")
        return
    
    with open(branches_path, "r", encoding="utf-8") as f:
        branches = json.load(f)
    
    if not branches:
        print("❌ 分支文件为空")
        return
    
    # 使用第一个分支进行测试
    branch_record = branches[0]
    print(f"📝 测试分支: {branch_record.get('branch_id')}")
    print(f"   分叉事件: {branch_record.get('fork_event_id')}")
    print(f"   分支选择: {branch_record.get('branch_choice')}")
    print(f"   主线选择: {branch_record.get('canonical_choice')}")
    print()
    
    # 创建确定器
    determiner = EndingCandidateDeterminer()
    
    # 准备分支选择信息
    branch_choice = {
        "choice_id": branch_record.get("branch_choice"),
        "description": branch_record.get("description", ""),
        "reasoning": branch_record.get("reasoning", ""),
    }
    
    # 调用确定结局候选池
    print("🔍 正在分析分支选择，确定结局候选池...\n")
    result = await determiner.determine_ending_candidates(
        branch_choice=branch_choice,
        canonical_choice=branch_record.get("canonical_choice"),
        fork_event_id=branch_record.get("fork_event_id"),
    )
    
    # 输出结果
    print("\n" + "="*60)
    print("📊 测试结果:")
    print("="*60)
    print(f"✅ 结局候选池: {result.get('candidates')}")
    print(f"⚠️  立即触发: {result.get('is_immediate_trigger')}")
    print(f"📈 偏离度: {result.get('deviation_level')}")
    print(f"🔄 合流压力: {result.get('merge_pressure')}")
    print(f"💭 倾向性分析: {result.get('tendency_analysis')}")
    print(f"📝 理由: {result.get('reasoning')}")
    print("="*60)


if __name__ == "__main__":
    asyncio.run(main())

