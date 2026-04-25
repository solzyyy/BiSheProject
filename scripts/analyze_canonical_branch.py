"""
分析 canonical_branch.json，提取决策点信息 📊✨
"""

import json
from pathlib import Path

def analyze_canonical_branch(json_path: str = "out/canonical_branch.json"):
    """分析主线记录，提取决策点信息"""
    
    with open(json_path, 'r', encoding='utf-8') as f:
        data = json.load(f)
    
    processed_events = data.get('processed_events', [])
    
    print("=" * 70)
    print("Canonical Branch 分析结果")
    print("=" * 70)
    print()
    
    # 1. 统计信息
    print(f"总体统计：")
    print(f"  总事件数: {len(processed_events)}")
    print(f"  起始事件: {data.get('start_event_id')}")
    print()
    
    # 2. 提取决策点
    decision_points = []
    for event in processed_events:
        if event.get('decision_point') is not None:
            decision_points.append(event)
    
    print(f"决策点统计：")
    print(f"  找到 {len(decision_points)} 个决策点")
    print()
    
    if decision_points:
        print("决策点详情：")
        for i, event in enumerate(decision_points, 1):
            print(f"\n  [{i}] 事件 {event.get('event_id')}:")
            print(f"     决策点标识: {event.get('decision_point')}")
            print(f"     主线选择: {event.get('canonical_choice')}")
            print(f"     描述: {event.get('description', '')[:80]}...")
            print(f"     状态变化数: {len(event.get('state_changes', []))}")
            print(f"     应用的状态变化数: {len(event.get('selected_state_changes', []))}")
    else:
        print("  ⚠️  没有找到决策点（所有事件的 decision_point 都是 null）")
    
    print()
    print("=" * 70)
    
    return decision_points

if __name__ == "__main__":
    decision_points = analyze_canonical_branch()
    
    # 保存决策点列表
    if decision_points:
        output_path = Path("out/decision_points.json")
        output_path.parent.mkdir(parents=True, exist_ok=True)
        
        decision_points_data = {
            "total_count": len(decision_points),
            "decision_points": [
                {
                    "event_id": e.get('event_id'),
                    "decision_point": e.get('decision_point'),
                    "canonical_choice": e.get('canonical_choice'),
                    "description": e.get('description'),
                }
                for e in decision_points
            ]
        }
        
        with open(output_path, 'w', encoding='utf-8') as f:
            json.dump(decision_points_data, f, ensure_ascii=False, indent=2)
        
        print(f"\n决策点列表已保存到: {output_path}")

