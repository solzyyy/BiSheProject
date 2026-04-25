"""
运行决策点分析的脚本 🎯✨

与流水线/前端等价入口：**优先使用**（支持自定义路径、target_branch_count）：

    python -m src.cli analyze-decision-points --help

本脚本为从项目根一键跑的便捷版，默认读 ``out/canonical_branch_chronological.json``、
写出 ``out/decision_points_analysis.json``；未传 ``target_branch_count`` 时不限制分支点数量。
"""

import asyncio
import sys
from pathlib import Path

# 添加项目根目录到路径
project_root = Path(__file__).parent.parent
if str(project_root) not in sys.path:
    sys.path.insert(0, str(project_root))

from src.branch.analysis.decision_point_analyzer import DecisionPointAnalyzer


async def main():
    """主函数"""
    print("=" * 70)
    print("决策点分析器")
    print("=" * 70)
    print()
    
    analyzer = DecisionPointAnalyzer()
    
    try:
        # 1. 加载所有决策点
        print("加载决策点...")
        decision_points = analyzer.load_decision_points()
        print(f"   找到 {len(decision_points)} 个决策点")
        print()
        
        if not decision_points:
            print("没有找到决策点，请先运行主线生成器")
            return
        
        # 2. 分析决策点
        print("开始分析决策点（使用 LLM 判断哪些适合做分支、哪些适合做合流）...")
        print()
        
        analysis_result = await analyzer.analyze_decision_points(
            decision_points,
            target_branch_count=None,
        )

        # 3. 显示分析结果
        print("=" * 70)
        print("分析结果")
        print("=" * 70)
        print()

        critical_events = analysis_result.get("critical_events") or []
        branch_points = analysis_result["branch_points"]
        skip_points = analysis_result["skip_points"]
        analysis = analysis_result["analysis"]

        print("分类统计：")
        print(f"  关键事件（最小骨架）: {len(critical_events)} 个")
        print(f"  适合做分支: {len(branch_points)} 个")
        print(f"  不适合做分支: {len(skip_points)} 个")
        print()

        if branch_points:
            print("适合做分支的决策点：")
            for event_id in branch_points:
                result = analysis.get(event_id, {})
                print(f"  - {event_id}: {result.get('reasoning', '')[:80]}...")
                print(f"    置信度: {result.get('confidence', 0.0):.2f}")
            print()

        if skip_points:
            print("不适合做分支的决策点：")
            for event_id in skip_points[:5]:  # 只显示前5个
                result = analysis.get(event_id, {})
                print(f"  - {event_id}: {result.get('reasoning', '')[:80]}...")
            if len(skip_points) > 5:
                print(f"  ... 还有 {len(skip_points) - 5} 个")
            print()
        
        # 4. 保存分析结果
        analyzer.save_analysis_result(analysis_result)
        
        print("=" * 70)
        print("分析完成！")
        print("=" * 70)
        print()
        print("下一步建议：")
        print("  1. 使用分析结果中的 branch_points 生成支线（generate-branch）")
        print("  2. 合流点由最小骨架中的下一关键事件体现，无需单独 merge_points 列表")
        print("  3. skip_points 可视为不建议单独开分支的决策点")
        
    except Exception as e:
        print(f"分析失败: {e}")
        import traceback
        traceback.print_exc()
    finally:
        analyzer.close()


if __name__ == "__main__":
    asyncio.run(main())

