"""
按 decision_points_analysis.branch_points 预生成全局 player_choice（每个分叉点一次 LLM），
供 generate-content 注入，避免同分叉在多路径下重复生成、文案不一致。

数据来源：
- decision_points_analysis.json → branch_points
- canonical_branch_chronological.json → processed_events 按 event_id 取当前事件与上一事件
- branches.json → fork_event_id 过滤得到该点的所有分支行

输出 JSON 结构：
{
  "version": 1,
  "by_fork_event_id": {
    "E3": { "prompt": "...", "options": [ {"text": "...", "choice_id": "accept_invitation"}, ... ] }
  }
}
不含 jump_target；增强阶段按 all_paths_map 的 path_combination 填入。

generate-content 通过 ContentGenerator.build_player_choice_catalog 调用本模块时，使用与场景/对话相同的
文学创作客户端（默认 Gemini 2.5 Flash，环境变量 CONTENT_LITERARY_MODEL_NAME / CONTENT_LITERARY_MODEL）。
"""

from __future__ import annotations

import sys
from pathlib import Path

_src_dir = Path(__file__).resolve().parent.parent / "src"
if str(_src_dir) not in sys.path:
    sys.path.insert(0, str(_src_dir))

import asyncio
import copy
import json
import os
from typing import Any, Dict, List, Optional, Tuple

from core.llm_client import AsyncLLMClient


CATALOG_VERSION = 1


def load_branch_points(analysis_path: Path) -> List[str]:
    with open(analysis_path, "r", encoding="utf-8") as f:
        data = json.load(f)
    pts = data.get("branch_points")
    if not isinstance(pts, list):
        return []
    return [str(x).strip() for x in pts if str(x).strip()]


def _index_processed_events(processed: List[Dict[str, Any]]) -> Dict[str, int]:
    return {str(r.get("event_id") or "").strip(): i for i, r in enumerate(processed) if r.get("event_id")}


def _summarize_canonical_record(rec: Dict[str, Any]) -> str:
    if not rec:
        return "（无）"
    parts = [
        f"event_id: {rec.get('event_id', '')}",
        f"description: {str(rec.get('description', ''))}",
        f"decision_point: {rec.get('decision_point')}",
        f"canonical_choice（主线）: {rec.get('canonical_choice')}",
    ]
    return "\n".join(parts)


def _branches_for_fork(
    branches_data: List[Dict[str, Any]], fork_event_id: str
) -> List[Dict[str, Any]]:
    out = []
    for b in branches_data:
        if not isinstance(b, dict):
            continue
        if (b.get("fork_event_id") or "").strip() != fork_event_id:
            continue
        out.append(
            {
                "branch_choice": b.get("branch_choice"),
                "canonical_choice": b.get("canonical_choice"),
                "description": (b.get("description") or ""),
                "reasoning": (b.get("reasoning") or ""),
            }
        )
    return out


def _allowed_choice_ids(canonical_rec: Dict[str, Any], branch_rows: List[Dict[str, Any]]) -> List[str]:
    seen: List[str] = []
    canon = (canonical_rec.get("canonical_choice") or "").strip()
    if canon and canon not in seen:
        seen.append(canon)
    for b in branch_rows:
        cid = (b.get("branch_choice") or "").strip()
        if cid and cid not in seen:
            seen.append(cid)
    return seen


def _build_fork_prompt(
    fork_event_id: str,
    current_rec: Dict[str, Any],
    prev_rec: Optional[Dict[str, Any]],
    branch_rows: List[Dict[str, Any]],
    analysis_blurb: str,
    allowed_ids: List[str],
) -> str:
    prev_text = _summarize_canonical_record(prev_rec) if prev_rec else "（无，此为第一个事件）"
    curr_text = _summarize_canonical_record(current_rec)
    branches_json = json.dumps(branch_rows, ensure_ascii=False, indent=2)
    ids_json = json.dumps(allowed_ids, ensure_ascii=False)

    return f"""你是一位文字冒险游戏的脚本助手。下面是一个决策分叉点，请生成**唯一一份**玩家选择界面用的文案（全游戏此分叉共用）。

**分叉事件 ID（fork_event_id）**：{fork_event_id}

**上一事件（主线时间线中紧邻的前一条，供衔接语气）**：
{prev_text}

**当前决策事件（主线 canonical 记录）**：
{curr_text}

**该点在 branches.json 中的备选分支摘要**（含 branch_choice、与主线对比等）：
{branches_json}

**决策点分析摘要（若有）**：
{analysis_blurb if analysis_blurb else "（无）"}

**你必须覆盖的选项 ID（choice_id 必须与下列字符串完全一致，不可增删 ID）**：
{ids_json}

**要求**：
1. 全文使用**简体中文**。
2. 生成一句「选择提示」prompt（不要出现英文），贴合当前场景，不要剧透未发生剧情。
3. 为每个 choice_id 生成**恰好一条**选项展示文案 `text`（适合菜单按钮，长短自然即可），顺序任意，但 `choice_id` 必须与给定列表一致、一一对应。
4. 不要输出 jump_target（由程序根据路径文件后填）。

**输出 JSON 格式（仅 JSON，不要其它说明）**：
{{
  "prompt": "……",
  "options": [
    {{ "text": "……", "choice_id": "accept_invitation" }}
  ]
}}
options 长度必须等于 {len(allowed_ids)}，且每个 choice_id 恰好在 allowed_ids 中出现一次。
"""


async def _generate_one_fork(
    fork_event_id: str,
    prompt: str,
    llm_client: AsyncLLMClient,
) -> Optional[Dict[str, Any]]:
    try:
        raw = await llm_client.invoke(prompt, return_json=True)
        if not isinstance(raw, dict):
            return None
        prompt_t = (raw.get("prompt") or "").strip()
        options = raw.get("options")
        if not prompt_t or not isinstance(options, list):
            return None
        return {"prompt": prompt_t, "options": options}
    except Exception as e:
        print(f"⚠️  player_choice 目录：分叉 {fork_event_id} LLM 失败：{e}")
        return None


def _validate_catalog_piece(
    piece: Dict[str, Any], allowed_ids: List[str]
) -> Optional[Dict[str, Any]]:
    """确保 options 与 allowed_ids 一一对应（按 allowed 顺序输出）。"""
    opts = piece.get("options")
    if not isinstance(opts, list):
        return None
    by_id: Dict[str, str] = {}
    for o in opts:
        if not isinstance(o, dict):
            continue
        cid = (o.get("choice_id") or "").strip()
        if cid:
            by_id[cid] = (o.get("text") or "").strip()
    out_opts: List[Dict[str, str]] = []
    for aid in allowed_ids:
        if aid not in by_id or not by_id[aid]:
            return None
        out_opts.append({"text": by_id[aid], "choice_id": aid})
    return {"prompt": piece["prompt"], "options": out_opts}


async def build_player_choice_catalog(
    decision_analysis_path: Path,
    canonical_path: Path,
    branches_path: Path,
    llm_client: AsyncLLMClient,
) -> Dict[str, Any]:
    branch_points = load_branch_points(decision_analysis_path)
    with open(canonical_path, "r", encoding="utf-8") as f:
        canonical = json.load(f)
    processed = canonical.get("processed_events") or []
    if not isinstance(processed, list):
        processed = []

    with open(branches_path, "r", encoding="utf-8") as f:
        branches_data = json.load(f)
    if not isinstance(branches_data, list):
        branches_data = []

    with open(decision_analysis_path, "r", encoding="utf-8") as f:
        analysis_root = json.load(f)
    analysis_map = analysis_root.get("analysis") or {}

    idx_map = _index_processed_events(processed)
    by_fork: Dict[str, Dict[str, Any]] = {}

    tasks: List[Tuple[str, str, List[str]]] = []
    for fork_id in branch_points:
        if fork_id not in idx_map:
            print(f"⚠️  player_choice 目录：branch_points 含 {fork_id}，但 canonical 中无此 event_id，跳过")
            continue
        i = idx_map[fork_id]
        cur = processed[i]
        prev = processed[i - 1] if i > 0 else None
        branch_rows = _branches_for_fork(branches_data, fork_id)
        allowed = _allowed_choice_ids(cur, branch_rows)
        if not allowed:
            print(f"⚠️  player_choice 目录：{fork_id} 无可用 choice_id，跳过")
            continue
        blurb = ""
        ent = analysis_map.get(fork_id)
        if isinstance(ent, dict):
            blurb = str(ent.get("reasoning") or "")
        prompt = _build_fork_prompt(fork_id, cur, prev, branch_rows, blurb, allowed)
        tasks.append((fork_id, prompt, allowed))

    async def run_one(
        fid: str, p: str, allowed: List[str]
    ) -> Tuple[str, Optional[Dict[str, Any]]]:
        raw = await _generate_one_fork(fid, p, llm_client)
        if not raw:
            return fid, None
        validated = _validate_catalog_piece(raw, allowed)
        if not validated:
            print(
                f"⚠️  player_choice 目录：分叉 {fid} LLM 返回的 choice_id/text 与预期不符，跳过该点"
            )
            return fid, None
        return fid, validated

    results = await asyncio.gather(*[run_one(fid, p, al) for fid, p, al in tasks])
    for fid, piece in results:
        if piece:
            by_fork[fid] = piece

    return {
        "version": CATALOG_VERSION,
        "by_fork_event_id": by_fork,
        "meta": {
            "decision_analysis": str(decision_analysis_path),
            "canonical": str(canonical_path),
            "branches": str(branches_path),
        },
    }


def save_player_choice_catalog(path: Path, catalog: Dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(catalog, f, ensure_ascii=False, indent=2)


def load_player_choice_catalog(path: Path) -> Dict[str, Any]:
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def collect_path_combination_choices(
    event_id: str,
    all_paths_map: Dict[str, Dict[str, Any]],
) -> List[Dict[str, Any]]:
    """与 content_generator.generate_decision_point_choice 相同的 all_choices 结构。"""
    all_choices: List[Dict[str, Any]] = []
    choices_seen = set()
    for renpy_label, other_path_data in all_paths_map.items():
        other_combos = other_path_data.get("metadata", {}).get("path_combination", [])
        if not isinstance(other_combos, list):
            continue
        for combo in other_combos:
            if not isinstance(combo, dict):
                continue
            if combo.get("fork_event_id") != event_id:
                continue
            choice_key = (event_id, combo.get("branch_choice", ""))
            if choice_key not in choices_seen:
                choices_seen.add(choice_key)
                all_choices.append({"choice": combo, "target_path": renpy_label})
    return all_choices


def attach_jump_targets(
    player_choice_template: Dict[str, Any],
    all_choices: List[Dict[str, Any]],
) -> Dict[str, Any]:
    """按 branch_choice / canonical_choice 把目标路径的 renpy_label 写入各 option.jump_target。

    约定：JSON 里存的是路径名（与 path_data.renpy_label 一致），不是 Ren'Py 最终 label。
    导出 .rpy 时由 PathScriptGenerator 转为 ``jump {label}_after_{fork_event_id}``，与当前路径相同则 pass。
    """
    out = copy.deepcopy(player_choice_template)
    by_cid: Dict[str, str] = {}
    for ac in all_choices:
        combo = ac.get("choice") or {}
        cid = (combo.get("branch_choice") or combo.get("canonical_choice") or "").strip()
        if cid and cid not in by_cid:
            by_cid[cid] = ac.get("target_path") or ""
    for opt in out.get("options") or []:
        if not isinstance(opt, dict):
            continue
        cid = (opt.get("choice_id") or "").strip()
        if cid in by_cid:
            opt["jump_target"] = by_cid[cid]
    return out


def main_sync(
    decision_analysis: Path,
    canonical: Path,
    branches: Path,
    out_file: Path,
) -> None:
    async def _run() -> None:
        # 与 ContentGenerator._get_literary_client 一致，默认 gemini-2.5-flash
        literary_name = os.getenv("CONTENT_LITERARY_MODEL_NAME", "gemini")
        literary_model = os.getenv("CONTENT_LITERARY_MODEL", "gemini-2.5-flash")
        client = AsyncLLMClient(model_name=literary_name, model=literary_model)
        cat = await build_player_choice_catalog(decision_analysis, canonical, branches, client)
        save_player_choice_catalog(out_file, cat)
        n = len(cat.get("by_fork_event_id") or {})
        print(f"已写入 player_choice 目录：{out_file}（{n} 个分叉点）")

    asyncio.run(_run())


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="预生成 player_choice_catalog.json")
    parser.add_argument("--decision-analysis", type=Path, required=True)
    parser.add_argument("--canonical", type=Path, required=True)
    parser.add_argument("--branches", type=Path, required=True)
    parser.add_argument("--output", "-o", type=Path, required=True)
    args = parser.parse_args()
    main_sync(args.decision_analysis, args.canonical, args.branches, args.output)
