"""
canonical_branch 按故事时间线重排脚本 📅

在 LangGraph / generate-all-paths 之前执行，将主线事件按故事内时间先后重排（处理倒叙/插叙），
输出写入指定文件，供后续路径生成使用。重排逻辑全部在本脚本内，不依赖 path_generation_functions。

用法（项目根目录）：
    python scripts/reorder_canonical_branch.py
    # 默认读 out/canonical_branch.json，写 out/canonical_branch_chronological.json
    python scripts/reorder_canonical_branch.py -o out/canonical_branch.json  # 指定输出路径
"""

import argparse
import asyncio
import json
import logging
import math
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional

project_root = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(project_root / "src"))
sys.path.insert(0, str(project_root))

from core.llm_client import AsyncLLMClient

logger = logging.getLogger(__name__)

# 事件数超过此值则分块排序再合并（单次 prompt 事件数上限）
REORDER_CHUNK_SIZE = 35


def _build_event_summary(record: Dict[str, Any]) -> str:
    """单条事件的极简摘要：event_id + 是否决策点 + 描述截断。"""
    eid = record.get("event_id", "")
    desc = (record.get("description") or "").strip()
    tag = " [决策点]" if (record.get("decision_point") or record.get("canonical_choice")) else ""
    return f"- {eid}{tag}: {desc}"


async def _invoke_llm(llm_client: AsyncLLMClient, events_text: str) -> Optional[List[str]]:
    """单次调用 LLM 返回按时间顺序的 event_id 列表。"""
    prompt = f"""你是一位熟悉叙事结构的助手。以下是一条故事主线的若干事件（可能包含倒叙、插叙）。
请仅根据**故事内发生的时间先后**（谁先发生、谁后发生），将这些事件重排为严格的时间顺序（从最早到最晚）。

事件列表：
{events_text}

要求：
1. 输出一个 JSON 数组，仅包含 event_id，按时间从早到晚排列。
2. 例如：["E2", "E1", "E3", "E4", ...] 表示 E2 最早、接着 E1、再 E3...
3. 不要输出任何解释，只输出一个合法 JSON 数组。"""
    try:
        response = await llm_client.invoke(prompt, return_json=True)
        if isinstance(response, list):
            return response
        if isinstance(response, dict):
            ordered_ids = response.get("order") or response.get("event_ids") or response.get("event_order")
            if not ordered_ids and response:
                ordered_ids = next((v for v in response.values() if isinstance(v, list)), None)
            return ordered_ids if isinstance(ordered_ids, list) else None
        return json.loads(response) if isinstance(response, str) else None
    except Exception as e:
        logger.warning("按时间线重排 LLM 调用失败: %s", e)
        return None


async def _reorder_by_chunks(
    events: List[Dict[str, Any]],
    llm_client: AsyncLLMClient,
) -> Optional[List[str]]:
    """事件过多时：分块排序，再对块边界做一次排序得到块顺序，拼接。"""
    n = len(events)
    chunk_size = REORDER_CHUNK_SIZE
    num_chunks = math.ceil(n / chunk_size)
    chunks: List[List[str]] = []
    for i in range(num_chunks):
        start = i * chunk_size
        sub = events[start : start + chunk_size]
        text = "\n".join(_build_event_summary(r) for r in sub)
        ids = await _invoke_llm(llm_client, text)
        if not ids:
            return None
        chunks.append(ids)
    if num_chunks == 1:
        return chunks[0]
    boundary_summaries = []
    for i, chunk_ids in enumerate(chunks):
        first_id = chunk_ids[0] if chunk_ids else ""
        last_id = chunk_ids[-1] if chunk_ids else ""
        first_rec = next((r for r in events if r.get("event_id") == first_id), None)
        last_rec = next((r for r in events if r.get("event_id") == last_id), None)
        f = _build_event_summary(first_rec) if first_rec else f"- {first_id}"
        l = _build_event_summary(last_rec) if last_rec else f"- {last_id}"
        boundary_summaries.append(f"块{i+1} 首: {f}\n块{i+1} 尾: {l}")
    boundary_text = "\n\n".join(boundary_summaries)
    boundary_prompt = f"""以下是把一条主线事件分成 {num_chunks} 块后，每块的「首事件」和「尾事件」。
请按故事时间先后，对这 {num_chunks} 块排序（只输出块的顺序，从最早到最晚）。
输出一个 JSON 数组，仅包含块编号：["块1", "块2", ...] 或 [1, 2, ...] 表示第1块最早、第2块次之。

{boundary_text}"""
    try:
        resp = await llm_client.invoke(boundary_prompt, return_json=True)
        if isinstance(resp, list):
            order = resp
        elif isinstance(resp, dict):
            order = resp.get("order") or resp.get("chunk_order") or list(resp.values())[0]
        else:
            order = json.loads(resp) if isinstance(resp, str) else []
        if not order or len(order) != num_chunks:
            return None
        idx_list = []
        for x in order:
            if isinstance(x, int) and 0 <= x <= num_chunks:
                idx_list.append(x - 1 if x > 0 else 0)
            elif isinstance(x, str):
                if x.startswith("块"):
                    try:
                        idx_list.append(int(x.replace("块", "").strip()) - 1)
                    except ValueError:
                        idx_list.append(len(idx_list))
                else:
                    try:
                        idx_list.append(int(x) - 1)
                    except ValueError:
                        idx_list.append(len(idx_list))
            else:
                idx_list.append(len(idx_list))
        seen = set()
        chunk_order = []
        for i in idx_list:
            i = max(0, min(i, num_chunks - 1))
            if i not in seen:
                seen.add(i)
                chunk_order.append(i)
        for i in range(num_chunks):
            if i not in seen:
                chunk_order.append(i)
        result = []
        for i in chunk_order:
            if 0 <= i < len(chunks):
                result.extend(chunks[i])
        return result
    except Exception as e:
        logger.warning("分块重排合并失败: %s", e)
        return None


def _load_event_source_texts(extract_chain_path: Optional[Path]) -> Dict[str, str]:
    """从 extract_chain.json 加载 event_id -> 原文(source_text) 映射。"""
    if not extract_chain_path or not extract_chain_path.exists():
        return {}
    try:
        with open(extract_chain_path, "r", encoding="utf-8") as f:
            data = json.load(f)
    except Exception as e:
        logger.warning("无法加载 extract_chain 原文映射: %s", e)
        return {}
    out = {}
    for item in data.get("new_events") or []:
        eid = (item.get("event_id") or "").strip()
        if not eid:
            continue
        ev = item.get("事件") or item.get("event") or {}
        text = (ev.get("source_text") or ev.get("原文") or "").strip()
        if text:
            out[eid] = text
    return out


async def reorder_canonical_branch_by_chronology(
    canonical_branch: Dict[str, Any],
    llm_client: AsyncLLMClient,
    extract_chain_path: Optional[Path] = None,
) -> Dict[str, Any]:
    """
    将 canonical_branch 的 processed_events 按故事时间先后重排（LLM 判断），
    并按新顺序为每个事件挂上 extract_chain 中的原文 source_text。
    返回新的 canonical_branch（不修改入参）。
    """
    events = canonical_branch.get("processed_events", [])
    if not events:
        return canonical_branch

    event_source_texts = _load_event_source_texts(extract_chain_path)
    if event_source_texts:
        logger.info("已加载 %d 条事件的原文（extract_chain）", len(event_source_texts))

    if len(events) <= REORDER_CHUNK_SIZE:
        items = [_build_event_summary(r) for r in events]
        events_text = "\n".join(items)
        ordered_ids = await _invoke_llm(llm_client, events_text)
    else:
        ordered_ids = await _reorder_by_chunks(events, llm_client)

    if not ordered_ids:
        logger.warning("未得到有效顺序，保持原顺序")
        return canonical_branch

    by_id = {r.get("event_id"): r for r in events if r.get("event_id")}
    ordered = [by_id[eid] for eid in ordered_ids if eid in by_id]
    for r in events:
        eid = r.get("event_id")
        if eid and eid not in ordered_ids and r not in ordered:
            ordered.append(r)

    # 重排后把 event_id 改为顺序 E1, E2, E3...，原 id 保留到 original_event_id；并建立 old_id -> new_id 映射
    renumbered = []
    old_to_new: Dict[str, str] = {}
    for i, ev in enumerate(ordered):
        ev = dict(ev)
        old_id = ev.get("event_id", "")
        new_id = f"E{i + 1}"
        old_to_new[old_id] = new_id
        ev["original_event_id"] = old_id
        ev["event_id"] = new_id
        renumbered.append(ev)

    # 按**旧的**顺序挂上原文：按输入 events 的原始顺序遍历，给 renumbered 里对应 original_event_id 的事件挂上该事件的原文
    if event_source_texts:
        renumbered_by_old_id = {ev["original_event_id"]: ev for ev in renumbered}
        for ev_old in events:
            old_id = ev_old.get("event_id", "")
            text = event_source_texts.get(old_id, ev_old.get("source_text") or "")
            if old_id in renumbered_by_old_id:
                renumbered_by_old_id[old_id]["source_text"] = text

    # 所有 state_changes 的 source_event 按映射统一改为新 id（包括指向其它事件的引用）
    for ev in renumbered:
        for sc in ev.get("state_changes") or []:
            if isinstance(sc, dict) and sc.get("source_event"):
                old_src = sc["source_event"]
                if old_src in old_to_new:
                    sc["source_event"] = old_to_new[old_src]

    out = dict(canonical_branch)
    out["processed_events"] = renumbered
    return out


async def main_async(
    input_path: Path,
    output_path: Path,
    extract_chain_path: Optional[Path] = None,
) -> None:
    if not input_path.exists():
        print(f"[ERROR] 输入文件不存在: {input_path}")
        sys.exit(1)
    with open(input_path, "r", encoding="utf-8") as f:
        canonical_branch = json.load(f)
    events = canonical_branch.get("processed_events", [])
    if not events:
        print("[WARN] processed_events 为空，无需重排")
        output_path.parent.mkdir(parents=True, exist_ok=True)
        with open(output_path, "w", encoding="utf-8") as f:
            json.dump(canonical_branch, f, ensure_ascii=False, indent=2)
        return
    print(f"[INFO] 按故事时间线重排 canonical_branch（共 {len(events)} 个事件）...")
    llm_client = AsyncLLMClient.create_default()
    reordered = await reorder_canonical_branch_by_chronology(
        canonical_branch, llm_client, extract_chain_path=extract_chain_path
    )
    output_path.parent.mkdir(parents=True, exist_ok=True)
    with open(output_path, "w", encoding="utf-8") as f:
        json.dump(reordered, f, ensure_ascii=False, indent=2)
    print(f"[INFO] 已写入: {output_path}")


def main() -> None:
    parser = argparse.ArgumentParser(description="将 canonical_branch 按故事时间先后重排（LLM 判断）")
    parser.add_argument(
        "--input", "-i",
        default="out/canonical_branch.json",
        help="输入的 canonical_branch.json 路径",
    )
    parser.add_argument(
        "--output", "-o",
        default="out/canonical_branch_chronological.json",
        help="输出路径（默认 out/canonical_branch_chronological.json，不覆盖原文件）",
    )
    parser.add_argument(
        "--extract-chain", "-e",
        default="out/extract_chain.json",
        help="extract_chain.json 路径，用于按 event_id 挂上事件原文 source_text（默认 out/extract_chain.json）",
    )
    args = parser.parse_args()
    input_path = project_root / args.input
    output_path = project_root / args.output
    extract_chain_path = project_root / args.extract_chain
    asyncio.run(main_async(input_path, output_path, extract_chain_path=extract_chain_path))


if __name__ == "__main__":
    main()
