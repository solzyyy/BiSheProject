"""
all_paths 事件补全脚本 📌

在 generate-all-paths 得到 out/all_paths 之后运行。

逻辑（与旧版「只按 path_combination 补缺失 E」不同）：
1. 若文件含 ``branch_events``：按其中每条 ``event_id`` 解析前缀 ``E{n}``（如 ``E6_branch_...`` → 6），
   与 ``metadata.path_combination`` 里同 ``fork_event_id`` 的条目对齐；
2. 对每个涉及的 fork，按 fork 序号从小到大，在 ``events`` 里找到「该分叉及之前主线」之后的插入点，
   先插入 path_combination 对应的占位决策事件（若尚无同 ``event_id`` 的条目），
   再紧接插入将该 fork 下 ``branch_events`` 转成与 ``events`` 同形的故事事件；
3. 已从 ``events`` 里删掉、准备重插的 branch_events（按 ``event_id`` 去重）避免重复；
4. 最后再跑一轮「path_combination 里出现而 events 仍缺的纯 fork 事件」补全（与下游 complete 习惯兼容）。

用法（项目根目录）：
  python -m scripts.complete_all_paths_events
  python -m scripts.complete_all_paths_events --input out/all_paths --output out/all_paths_completed
"""

from __future__ import annotations

import argparse
import json
import re
from pathlib import Path
from typing import Any, Dict, List, Optional, Set, Tuple


def _pure_e_fork_number(fork_event_id: str) -> Optional[int]:
    m = re.fullmatch(r"E(\d+)", str(fork_event_id).strip(), re.I)
    return int(m.group(1)) if m else None


def _fork_prefix_num(event_id: str) -> Optional[int]:
    """从 E6_branch_...、E13_branch_..._ending 等解析分叉点序号 6、13。"""
    if not event_id:
        return None
    m = re.match(r"^E(\d+)", str(event_id).strip(), re.I)
    return int(m.group(1)) if m else None


def _build_event_from_choice(fork_event_id: str, choice: Dict[str, Any]) -> Dict[str, Any]:
    """用 path_combination 的一条拼占位决策点事件（非 extract 真实事件，不写 original_event_id）。"""
    desc = choice.get("description") or ""
    return {
        "event_id": fork_event_id,
        "type": "decision_point",
        "scene_description": desc,
        "source_text": desc,
        "dialogue": [],
        "player_choice": None,
        "state_changes": {},
        "snapshot": {"snapshot_ref": "通过 event_id 从 canonical_branch.json 查找完整 snapshot"},
    }


def _pick_path_combination_for_fork(
    path_combination: List[Dict[str, Any]], fork_event_id: str
) -> Optional[Dict[str, Any]]:
    """同一 fork 可能有多条（先分支后主线等）；优先非 canonical 的那条，否则取第一条。"""
    matches = [
        c
        for c in path_combination
        if (c.get("fork_event_id") or "").strip() == fork_event_id
    ]
    if not matches:
        return None
    non_canon = [c for c in matches if not c.get("is_canonical", False)]
    return non_canon[0] if non_canon else matches[0]


def _branch_record_to_story_event(rec: Dict[str, Any]) -> Dict[str, Any]:
    """把 LangGraph 的 branch_events 条目转成与 all_paths events 相近的 story 形状。"""
    eid = str(rec.get("event_id") or "").strip() or "unknown_branch_event"
    desc = (rec.get("description") or "").strip()
    is_ending = bool(rec.get("is_ending"))
    raw_sc = rec.get("state_changes")
    if isinstance(raw_sc, list):
        state_changes: Dict[str, Any] = {}
    elif isinstance(raw_sc, dict):
        state_changes = dict(raw_sc)
    else:
        state_changes = {}

    snap = rec.get("snapshot")
    if snap is not None and not isinstance(snap, dict):
        snap = None
    formatted_snapshot = (
        snap
        if snap
        else {"snapshot_ref": "通过 event_id 从 canonical_branch.json 查找完整 snapshot"}
    )

    out: Dict[str, Any] = {
        "event_id": eid,
        "type": "ending" if is_ending else "branch_event",
        "scene_description": desc,
        "source_text": desc if desc else None,
        "dialogue": rec.get("dialogue") if isinstance(rec.get("dialogue"), list) else [],
        "player_choice": None,
        "state_changes": state_changes,
        "snapshot": formatted_snapshot,
    }
    if is_ending:
        out["is_ending"] = True
        if rec.get("ending_type"):
            out["ending_type"] = rec["ending_type"]
        if rec.get("ending_reason"):
            out["ending_reason"] = rec["ending_reason"]
    return out


def _find_insert_index_after_fork_context(working: List[Dict[str, Any]], fork_num: int) -> int:
    """
    插入点：紧接在「时间线上该分叉点及之前」已出现的最后一条事件之后。
    只用 **纯时间线编号** E{n} 判断；无匹配时落在列表末尾。

    为什么要“只认纯 E{n}”：
    - branch_event 的 event_id 常为 ``E6_branch_...``，它的前缀并不代表时间线进度，
      若参与比较会把“插入点”推到奇怪的位置，导致后续 fork（例如 E16）难以稳定插入。
    """
    last = -1
    for i, e in enumerate(working):
        eid = str(e.get("timeline_event_id") or e.get("event_id") or "")
        a = _pure_e_fork_number(eid)
        if a is None:
            continue
        if a <= fork_num:
            last = i
    return last + 1


def _first_ending_index(events: List[Dict[str, Any]]) -> Optional[int]:
    """返回首个 ending 事件下标（含提前结局）；无则 None。"""
    for i, e in enumerate(events or []):
        if not isinstance(e, dict):
            continue
        etype = str(e.get("type") or "").strip()
        if etype == "ending" or bool(e.get("is_ending")):
            return i
    return None


def _find_insert_index_for_fork_event_id(working: List[Dict[str, Any]], fork_event_id: str) -> int:
    """
    计算某个 fork_event_id（如 E16）的 decision_point 应插入的位置。

    规则（按你说的直觉）：
    - 只在「首个 ending 之前」寻找锚点与插入（ending 本身也应在 decision_point 之后）；
    - 优先在 events 中找 timeline_event_id == fork_event_id 的那条事件，插到它**之前**；
      （因为 fork_event_id 是“时间线锚点”，event_id 可能为了展示连续而被重编号）
    - 若找不到锚点（例如：在 E16 选择后立刻 ending，没有主线 E16 正文事件），则插在 ending 之前；
    - 再兜底：按时间线序号（纯 E{n}）插到 “<= fork_num 的最后一个时间线事件之后”。
    """
    fid = (fork_event_id or "").strip()
    if not fid:
        return len(working)

    end_idx = _first_ending_index(working)
    search_upto = end_idx if end_idx is not None else len(working)

    # 1) 锚点优先：timeline_event_id == fork_event_id
    for i in range(search_upto):
        e = working[i]
        if not isinstance(e, dict):
            continue
        if str(e.get("timeline_event_id") or "").strip().upper() == fid.upper():
            return i

    # 2) 次优：event_id == fork_event_id（部分数据源可能没 timeline_event_id）
    for i in range(search_upto):
        e = working[i]
        if not isinstance(e, dict):
            continue
        if str(e.get("event_id") or "").strip().upper() == fid.upper():
            return i

    # 3) 若存在 ending：直接插在 ending 之前（保证“选择点”出现在结局前）
    if end_idx is not None:
        return end_idx

    # 4) 兜底：按 fork_num 插入（只看纯时间线编号）
    fork_num = _pure_e_fork_number(fid)
    if fork_num is None:
        return len(working)
    return _find_insert_index_after_fork_context(working, fork_num)


def _event_ids_in_list(evlist: List[Dict[str, Any]]) -> Set[str]:
    return {str(e.get("event_id") or "").strip() for e in evlist if e.get("event_id")}


def _decision_point_event_ids_in_list(evlist: List[Dict[str, Any]]) -> Set[str]:
    """仅统计 type == decision_point 的 fork event_id。"""
    out: Set[str] = set()
    for e in evlist:
        if not isinstance(e, dict):
            continue
        if e.get("type") != "decision_point":
            continue
        eid = str(e.get("event_id") or "").strip()
        if eid:
            out.add(eid)
    return out


def _find_event_index_by_event_id(evlist: List[Dict[str, Any]], event_id: str) -> Optional[int]:
    """返回第一个 event_id 匹配项的下标；不存在返回 None。"""
    if not event_id:
        return None
    want = str(event_id).strip()
    for i, e in enumerate(evlist):
        if not isinstance(e, dict):
            continue
        if str(e.get("event_id") or "").strip() == want:
            return i
    return None


def _unique_fork_event_ids(path_combination: List[Dict[str, Any]]) -> List[str]:
    """从 path_combination 里取 fork_event_id（去重+按数字升序）。"""
    seen: Set[str] = set()
    out: List[str] = []
    for c in path_combination or []:
        if not isinstance(c, dict):
            continue
        fid = (c.get("fork_event_id") or "").strip()
        if not fid or fid in seen:
            continue
        if _pure_e_fork_number(fid) is None:
            continue
        seen.add(fid)
        out.append(fid)
    out.sort(key=lambda x: _pure_e_fork_number(x) or 0)
    return out


def _max_reached_fork_number(events: List[Dict[str, Any]]) -> Optional[int]:
    """
    根据时间线中「第一个结局之前」的事件，估算本路径实际走到的最大 E 序号。

    规则：
    - 从 events 开头向后扫描，遇到第一个 type==ending / is_ending==True 就停止（该 ending 也计入经历范围）；
    - 对每个事件优先取 timeline_event_id（稳定时间线锚点），其次取 event_id（展示编号），再兜底 original_event_id；
    - 优先使用纯时间线编号：严格匹配 ``E{n}``（fullmatch），避免从 ``E6_branch_...`` 这类字符串里“偷 n”而误判；
    - 但对 **结局事件** 例外：若 ending 的 id 形如 ``E16_branch_..._ending``，仍认为路径实际到达了 fork=16，
      否则会把「在 E16 选择后立即提前结局」的路径误判为只到 E15，进而跳过插入 E16 的 decision_point。
    - 返回扫描段里最大的 n。

    用途：
    - 提前结局路径不应再插入后续 fork 的 decision_point（避免“坟头分叉”）。
    """
    max_n: Optional[int] = None
    for e in events or []:
        if not isinstance(e, dict):
            continue

        etype = str(e.get("type") or "").strip()
        is_ending = bool(e.get("is_ending")) or etype == "ending"

        # 注意：
        # - original_event_id 的来源是路径格式化/重排阶段，用于 extract_chain 对齐，可能不是「时间线编号」；
        # - branch_event 的 event_id 常形如 E6_branch_...，若用前缀解析会把它误算进“走到的时间线”。
        # 因此这里仅认可严格的 E{n}（fullmatch）。
        raw_id = (
            str(e.get("timeline_event_id") or "").strip()
            or str(e.get("event_id") or "").strip()
            or str(e.get("original_event_id") or "").strip()
        )
        n = _pure_e_fork_number(raw_id) if raw_id else None
        # ending 兜底：允许从 E16_branch_..._ending 解析 16
        if n is None and is_ending and raw_id:
            n = _fork_prefix_num(raw_id)
        if n is not None and ((max_n is None) or (n > max_n)):
            max_n = n

        if is_ending:
            break
    return max_n


def ensure_decision_point_forks_present(
    events: List[Dict[str, Any]],
    path_combination: List[Dict[str, Any]],
) -> Tuple[List[Dict[str, Any]], bool]:
    """
    对齐 metadata.path_combination：
    - 若 events 中没有同 event_id 且 type == decision_point，则补齐；
    - 若 events 中已有同 event_id 但 type != decision_point，则“替换成决策点”。
    """
    if not path_combination:
        return events, False

    working = list(events)
    changed = False
    max_reached = _max_reached_fork_number(working)
    existing_decision_ids = _decision_point_event_ids_in_list(working)
    fork_ids = _unique_fork_event_ids(path_combination)

    for fork_event_id in fork_ids:
        if fork_event_id in existing_decision_ids:
            continue

        fork_num = _pure_e_fork_number(fork_event_id)
        if fork_num is None:
            continue
        # 若路径实际只走到 E6，就不再为后续 E16 等 fork 补决策点（避免提前结局后“坟头分叉”）。
        if max_reached is not None and fork_num > max_reached:
            continue

        # 同一 fork 可能有 canonical 与 branch 两条；这里复用“优先非 canonical”的选择规则
        chosen = _pick_path_combination_for_fork(path_combination, fork_event_id)
        if chosen is None:
            continue

        placeholder = _build_event_from_choice(fork_event_id, chosen)
        existing_idx = _find_event_index_by_event_id(working, fork_event_id)
        if existing_idx is not None:
            existing_event = working[existing_idx]
            # 替换为 decision_point，但尽量保留现有的非空 state_changes / snapshot，
            # 避免把上游已写好的内容“抹掉”。
            for key in ("state_changes", "snapshot"):
                if existing_event.get(key):
                    placeholder[key] = existing_event[key]

            if working[existing_idx].get("type") != "decision_point":
                working[existing_idx] = placeholder
                changed = True
        else:
            insert_at = _find_insert_index_for_fork_event_id(working, fork_event_id)
            working.insert(insert_at, placeholder)
            changed = True

        existing_decision_ids.add(fork_event_id)

    return working, changed


def ensure_branch_events_present(
    events: List[Dict[str, Any]],
    branch_events: List[Dict[str, Any]],
) -> Tuple[List[Dict[str, Any]], bool]:
    """
    将 top-level branch_events 中缺失的事件补进 events：
    - 逐个按 event_id 判断是否已经存在（不再用“有没有出现过 branch_event 就跳过”的粗判断）
    - 按 fork_num 分组，插入点放在对应 fork 上下文之后
    """
    if not branch_events:
        return events, False

    working = list(events)
    changed = False
    present_ids = _event_ids_in_list(working)

    by_fork: Dict[int, List[Dict[str, Any]]] = {}
    for be in branch_events:
        if not isinstance(be, dict):
            continue
        eid = str(be.get("event_id") or "").strip()
        if not eid or eid in present_ids:
            continue
        n = _fork_prefix_num(eid)
        if n is None:
            continue
        by_fork.setdefault(n, []).append(be)

    for fork_num in sorted(by_fork.keys()):
        insert_at = _find_insert_index_after_fork_context(working, fork_num)
        block: List[Dict[str, Any]] = []
        for be in by_fork[fork_num]:
            block.append(_branch_record_to_story_event(be))
        for i, item in enumerate(block):
            working.insert(insert_at + i, item)
            item_eid = str(item.get("event_id") or "").strip()
            if item_eid:
                present_ids.add(item_eid)
        if block:
            changed = True

    return working, changed


def sort_events_by_event_id_numeric(events: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """
    按 event_id 的 E{n} 数值做稳定排序（n 小的在前）。

    注意：仅取「首个 E 数字」会把 ``E6`` 与 ``E6_branch_..._ending`` 都归到 6，
    若上游先插入了结局再补决策点，稳定排序会保持「结局在 E6 前」的错误顺序。
    因此：
    - ``event_id`` 精确为 ``E{n}``（如决策点）的排在同数字前缀的 ``E{n}_branch_...`` 之前；
    - ``type == ending`` 且 ``event_id`` 形如 ``E{n}_branch_...`` 的分支结局，排在整条时间线末尾
      （在 E7、E16 等之后），避免插在 E5 与 E6 决策点之间。
    """
    indexed: List[Tuple[int, Dict[str, Any]]] = list(enumerate(events))

    def _key(item: Tuple[int, Dict[str, Any]]) -> Tuple[int, int, int]:
        idx, e = item
        eid = str((e or {}).get("event_id") or "").strip()
        etype = str((e or {}).get("type") or "").strip()
        if not eid:
            return (1, 10**9, idx)

        # 分支结局：E6_branch_..._ending，必须放在该路径其它事件之后
        if etype == "ending" and re.match(r"^E\d+_branch_", eid, re.I):
            m = re.match(r"^E(\d+)", eid, re.I)
            fork_n = int(m.group(1)) if m else 0
            return (0, 1_000_000 + fork_n, idx)

        # 精确 E{n}（无下划线后缀），如 decision_point E6
        m_exact = re.fullmatch(r"E(\d+)", eid, re.I)
        if m_exact:
            return (0, int(m_exact.group(1)), idx)

        n = _fork_prefix_num(eid)
        if n is None:
            return (1, 10**9, idx)
        return (0, n, idx)

    indexed.sort(key=_key)
    return [e for _, e in indexed]


def merge_branch_events_into_timeline(
    events: List[Dict[str, Any]],
    path_combination: List[Dict[str, Any]],
    branch_events: List[Dict[str, Any]],
) -> List[Dict[str, Any]]:
    """
    按 branch_events 的 E{n} 前缀分组，依次在 events 中插入：
    [可选：path_combination 占位] + [该 fork 的 branch_events 转换行]。
    """
    if not branch_events:
        return list(events)

    by_fork: Dict[int, List[Dict[str, Any]]] = {}
    for be in branch_events:
        if not isinstance(be, dict):
            continue
        n = _fork_prefix_num(str(be.get("event_id") or ""))
        if n is None:
            continue
        by_fork.setdefault(n, []).append(be)

    remove_ids: Set[str] = {
        str(be.get("event_id") or "").strip()
        for be in branch_events
        if isinstance(be, dict) and be.get("event_id")
    }
    working = [e for e in events if str(e.get("event_id") or "").strip() not in remove_ids]

    for fork_num in sorted(by_fork.keys()):
        fork_event_id = f"E{fork_num}"
        choice = _pick_path_combination_for_fork(path_combination, fork_event_id)
        insert_at = _find_insert_index_after_fork_context(working, fork_num)

        block: List[Dict[str, Any]] = []
        existing_ids = _event_ids_in_list(working)
        if fork_event_id not in existing_ids and choice is not None:
            block.append(_build_event_from_choice(fork_event_id, choice))
        elif fork_event_id not in existing_ids and choice is None:
            block.append(
                _build_event_from_choice(
                    fork_event_id,
                    {"description": f"（path_combination 中未找到 {fork_event_id}，占位）"},
                )
            )

        for be in by_fork[fork_num]:
            block.append(_branch_record_to_story_event(be))

        for i, item in enumerate(block):
            working.insert(insert_at + i, item)

    return working


def fill_missing_path_combination_forks(
    events: List[Dict[str, Any]],
    path_combination: List[Dict[str, Any]],
) -> List[Dict[str, Any]]:
    """
    path_combination 里出现的 fork_event_id，若 events 中仍无同 event_id，则在时间线合适位置插入占位事件
   （按 fork 序号找插入点，不做全局排序，避免打乱 merge_branch 后的顺序）。
    """
    if not path_combination:
        return events
    working = list(events)
    have_ids = _event_ids_in_list(working)
    max_reached = _max_reached_fork_number(working)

    missing: List[Tuple[str, Dict[str, Any]]] = []
    seen_forks: Set[str] = set()
    for c in path_combination:
        fid = (c.get("fork_event_id") or "").strip()
        if not fid or fid in have_ids or fid in seen_forks:
            continue
        if _pure_e_fork_number(fid) is None:
            continue
        seen_forks.add(fid)
        missing.append((fid, c))

    for fid, c in sorted(
        missing,
        key=lambda x: _pure_e_fork_number(x[0]) or 0,
    ):
        fork_num = _pure_e_fork_number(fid)
        if fork_num is None:
            continue
        # 同 ensure_decision_point_forks_present：若路径已在某个 E{n} 前提前结束，则不再补更大的 fork。
        if max_reached is not None and fork_num > max_reached:
            continue
        insert_at = _find_insert_index_after_fork_context(working, fork_num)
        working.insert(insert_at, _build_event_from_choice(fid, c))
        have_ids.add(fid)

    return working


def complete_path_data(data: Dict[str, Any]) -> Tuple[List[Dict[str, Any]], bool]:
    """
    返回 (新 events, 是否相对原始 events 有改动)。
    """
    events = data.get("events")
    if not events or not isinstance(events, list):
        return events if isinstance(events, list) else [], False

    meta = data.get("metadata") or {}
    path_combination = meta.get("path_combination")
    if not path_combination or not isinstance(path_combination, list):
        return list(events), False

    branch_events = data.get("branch_events")
    if not isinstance(branch_events, list):
        branch_events = []

    original = list(events)

    # 1) 先补齐/替换决策点（decision_point），严格对齐 metadata.path_combination
    working, changed_dp = ensure_decision_point_forks_present(
        list(events), path_combination
    )

    # 2) 再补齐缺失的分支/结局事件（branch_events），严格按 event_id 判断缺失
    working2, changed_be = ensure_branch_events_present(working, branch_events)

    # 3) 最后按 event_id 数值顺序整理时间线（稳定排序）
    sorted_events = sort_events_by_event_id_numeric(working2)

    changed = changed_dp or changed_be or (sorted_events != original)
    return sorted_events, changed


def run(
    input_dir: str = "out/all_paths",
    output_dir: str = "out/all_paths_completed",
    project_root: Optional[Path] = None,
) -> None:
    root = project_root or Path(__file__).resolve().parent.parent
    in_p = Path(input_dir)
    out_p = Path(output_dir)
    input_path = in_p if in_p.is_absolute() else (root / input_dir)
    output_path = out_p if out_p.is_absolute() else (root / output_dir)
    output_path.mkdir(parents=True, exist_ok=True)
    if not input_path.is_dir():
        print(f"[错误] 目录不存在: {input_path}")
        return

    for meta_name in ("index.json", "canonical_path.json"):
        meta_src = input_path / meta_name
        if meta_src.exists():
            with open(meta_src, "r", encoding="utf-8") as f:
                meta_data = json.load(f)
            with open(output_path / meta_name, "w", encoding="utf-8") as f:
                json.dump(meta_data, f, ensure_ascii=False, indent=2)
            print(f"  已复制 {meta_name} 到 {output_path}")

    updated = 0
    skipped = 0
    for path_file in sorted(input_path.glob("*.json")):
        if path_file.name in ("index.json", "canonical_path.json"):
            continue
        try:
            with open(path_file, "r", encoding="utf-8") as f:
                data = json.load(f)
        except Exception as e:
            print(f"[跳过] {path_file.name}: 读取失败 - {e}")
            skipped += 1
            continue

        events_in = data.get("events") or []
        has_ending_in_input = bool(data.get("ending")) or any(
            isinstance(e, dict) and (e.get("type") == "ending" or e.get("is_ending"))
            for e in events_in
        )

        new_events, changed = complete_path_data(data)
        # 若没有任何改动，且输入里也本来就没有 ending，则跳过写出
        if not changed and not has_ending_in_input:
            skipped += 1
            continue

        data["events"] = new_events
        out_file = output_path / path_file.name
        with open(out_file, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
        print(
            f"  [ok] 已补全: {path_file.name}（共 {len(new_events)} 个 events，branch_events 已并入时间线并补缺 fork）"
        )
        updated += 1

    print(f"\n完成：已补全 {updated} 个文件，跳过 {skipped} 个。")


def main() -> None:
    parser = argparse.ArgumentParser(
        description="按 branch_events + path_combination 合并时间线并补缺 fork 事件"
    )
    parser.add_argument("--input", "-i", default="out/all_paths", help="all_paths 目录")
    parser.add_argument("--output", "-o", default="out/all_paths_completed", help="输出目录")
    args = parser.parse_args()
    run(input_dir=args.input, output_dir=args.output)


if __name__ == "__main__":
    main()
