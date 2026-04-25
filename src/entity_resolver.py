"""
实体解析：按 entities.json 将说者/称呼归并为同一人（canonical_name）。

用于：
- 生成 characters.rpy 时，extra_speakers 用 canonical 集合，避免「皇帝」「天子」各一个 define
- 生成对话行时，说者先 resolve 到 canonical，再查变量名，保证同一人用同一变量
- recognize-entities CLI：扫描路径下对话，列出已识别/未识别的说者
"""

import json
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple


def load_entities(entities_path: str) -> List[Dict[str, Any]]:
    """加载 entities.json，返回实体列表。"""
    path = Path(entities_path)
    if not path.exists():
        return []
    with open(path, "r", encoding="utf-8") as f:
        data = json.load(f)
    return data.get("entities", [])


def build_mention_to_entity(entities_path: str) -> Dict[str, Dict[str, Any]]:
    """
    构建 称呼 -> 实体 映射。
    每个实体的 canonical_name 与所有 aliases 都映射到该实体 dict。
    用于 get_canonical_name(speaker, mention_to_entity)。
    """
    entities = load_entities(entities_path)
    out: Dict[str, Dict[str, Any]] = {}
    for e in entities:
        canonical = (e.get("canonical_name") or "").strip()
        if canonical:
            out[canonical] = e
        for alias in e.get("aliases") or []:
            a = (alias or "").strip()
            if a:
                out[a] = e
    return out


def get_canonical_name(mention: str, mention_to_entity: Dict[str, Dict[str, Any]]) -> Optional[str]:
    """
    将说者/称呼解析为 canonical_name。
    若 mention 在 mention_to_entity 中（作为 canonical 或 alias），返回该实体的 canonical_name；否则返回 None。
    """
    if not mention:
        return None
    s = (mention or "").strip()
    if not s:
        return None
    entity = mention_to_entity.get(s)
    if not entity:
        return None
    return (entity.get("canonical_name") or "").strip() or None


def _collect_speakers_from_path_file(path: Path) -> List[str]:
    """从单条路径 JSON 中收集所有 dialogue 的 speaker。"""
    speakers: List[str] = []
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
    except Exception:
        return speakers
    for event in data.get("events") or []:
        for d in event.get("dialogue") or []:
            if isinstance(d, dict):
                sp = d.get("speaker", "")
                if sp and str(sp).strip():
                    speakers.append(str(sp).strip())
    return speakers


def collect_speakers_from_paths(paths_dir: str) -> Dict[str, int]:
    """扫描 paths_dir 下所有 .json，收集说者及其出现次数。返回 { speaker: count }。"""
    root = Path(paths_dir)
    if not root.is_dir():
        return {}
    counts: Dict[str, int] = {}
    for p in root.glob("*.json"):
        for s in _collect_speakers_from_path_file(p):
            counts[s] = counts.get(s, 0) + 1
    return counts


def report_speakers_entities(
    paths_dir: str,
    entities_path: str,
) -> Tuple[Dict[str, Dict[str, Any]], Dict[str, int]]:
    """
    扫描 paths_dir 下对话说者，按 entities 归类。
    返回:
        resolved: { speaker: { "canonical_name": str, "id": str } }
        unresolved: { speaker: count } 未在 entities 中出现的说者及出现次数
    """
    mention_to_entity = build_mention_to_entity(entities_path)
    speaker_counts = collect_speakers_from_paths(paths_dir)
    resolved: Dict[str, Dict[str, Any]] = {}
    unresolved: Dict[str, int] = {}
    for speaker, count in speaker_counts.items():
        canonical = get_canonical_name(speaker, mention_to_entity)
        if canonical:
            entity = mention_to_entity.get(canonical) or mention_to_entity.get(speaker)
            resolved[speaker] = {
                "canonical_name": canonical,
                "id": (entity or {}).get("id", ""),
            }
        else:
            unresolved[speaker] = count
    return resolved, unresolved


if __name__ == "__main__":
    import sys
    from pathlib import Path
    project_root = Path(__file__).resolve().parent.parent
    entities_path = project_root / "out" / "entities.json"
    paths_dir = project_root / "out" / "enhanced_paths"
    if len(sys.argv) >= 2:
        entities_path = Path(sys.argv[1])
    if len(sys.argv) >= 3:
        paths_dir = Path(sys.argv[2])
    entities = load_entities(str(entities_path))
    print("=== 实体列表 (entities.json) ===")
    print(f"共 {len(entities)} 个实体\n")
    for e in entities:
        name = e.get("canonical_name", "")
        eid = e.get("id", "")
        aliases = e.get("aliases") or []
        typ = e.get("entity_type", "")
        print(f"  {eid}  {name}  [{typ}]  别名: {aliases}")
    print("\n=== 对话说者识别结果 ===\n")
    resolved, unresolved = report_speakers_entities(str(paths_dir), str(entities_path))
    print("已识别（在 entities 中有对应）：")
    for speaker, info in sorted(resolved.items()):
        canonical = info.get("canonical_name", "")
        eid = info.get("id", "")
        print(f'  "{speaker}" -> 实体 {eid} ({canonical})')
    print("\n未识别（建议加入 entities 或作为某实体的别名）：")
    for speaker, count in sorted(unresolved.items(), key=lambda x: -x[1]):
        print(f'  "{speaker}"  出现 {count} 次')
