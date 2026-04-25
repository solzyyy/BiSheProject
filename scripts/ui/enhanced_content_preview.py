"""
generate-content 产出（enhanced_paths）的 Streamlit 预览与就地编辑。

主线 / 支线分开展示；若事件含 content_blocks，则按块顺序编辑；否则按叙述 / dialogue JSON / player_choice JSON。
保存写回对应路径 JSON，并同步顶层字段与块一致。支持下载当前 JSON、打包目录、按块顺序导出 txt。
"""

from __future__ import annotations

import io
import json
import copy
import re
import zipfile
from pathlib import Path
from typing import Any

def resolve_enhanced_dir(project_root: Path, params_obj: dict[str, Any]) -> Path | None:
    """从 generate-content 合并后的 params 解析 enhanced 目录。"""
    for key in ("output", "output_dir"):
        raw = str(params_obj.get(key) or "").strip()
        if not raw:
            continue
        p = Path(raw)
        return p if p.is_absolute() else (project_root / p)
    return None


def _safe_key_stem(path_file: Path) -> str:
    s = path_file.stem
    s = re.sub(r"[^a-zA-Z0-9_]+", "_", s)
    return (s or "path")[:80]


def _event_has_editable_blocks(ev: dict) -> bool:
    blocks = ev.get("content_blocks")
    if not isinstance(blocks, list) or not blocks:
        return False
    return all(isinstance(b, dict) for b in blocks)


def _sync_event_top_level_from_blocks(ev: dict, blocks_out: list[dict]) -> None:
    """保存后让 detailed_scene_description / dialogue / player_choice 与块内容一致。"""
    ev["content_blocks"] = blocks_out
    narr = [
        str(b.get("text") or "")
        for b in blocks_out
        if (b.get("type") or "").lower() == "narrative"
    ]
    if narr:
        ev["detailed_scene_description"] = "\n\n".join(narr)
    dlist: list[dict[str, Any]] = []
    for b in blocks_out:
        if (b.get("type") or "").lower() != "dialogue":
            continue
        dlist.append({k: v for k, v in b.items() if k != "type"})
    ev["dialogue"] = dlist
    pcs = [b for b in blocks_out if (b.get("type") or "").lower() == "choice"]
    if pcs:
        first = {k: v for k, v in pcs[0].items() if k != "type"}
        ev["player_choice"] = first if first else None
    else:
        ev["player_choice"] = None


def _blocks_to_readable_text(events: list[Any]) -> str:
    """按 content_blocks 顺序导出纯文本（仅展示用）。"""
    lines: list[str] = []
    for i, ev in enumerate(events):
        if not isinstance(ev, dict):
            continue
        eid = str(ev.get("event_id") or f"#{i}")
        et = str(ev.get("type") or "")
        lines.append(f"## {eid} · {et}")
        blocks = ev.get("content_blocks")
        if isinstance(blocks, list) and blocks:
            for b in blocks:
                if not isinstance(b, dict):
                    continue
                t = (b.get("type") or "").lower()
                if t == "narrative":
                    lines.append(str(b.get("text") or "").strip())
                elif t == "dialogue":
                    sp = str(b.get("speaker") or "").strip()
                    tx = str(b.get("text") or "").strip()
                    lines.append(f"【{sp}】{tx}" if sp else tx)
                elif t == "choice":
                    lines.append(json.dumps(b, ensure_ascii=False, indent=2))
                else:
                    lines.append(json.dumps(b, ensure_ascii=False, indent=2))
                lines.append("")
            lines.append("")
            continue
        lines.append(str(ev.get("detailed_scene_description") or "").strip())
        dlg = ev.get("dialogue")
        if isinstance(dlg, list):
            for d in dlg:
                if isinstance(d, dict):
                    sp = str(d.get("speaker") or "").strip()
                    tx = str(d.get("text") or "").strip()
                    lines.append(f"【{sp}】{tx}" if sp else tx)
        pc = ev.get("player_choice")
        if pc is not None:
            lines.append(json.dumps(pc, ensure_ascii=False, indent=2))
        lines.append("")
    return "\n".join(lines).strip()


def _list_path_files(enhanced_dir: Path) -> tuple[list[Path], list[Path]]:
    """(主线 canonical_path.json 若存在, 其余支线文件列表按名排序)。"""
    files = sorted(
        [p for p in enhanced_dir.glob("*.json") if p.name != "index.json"],
        key=lambda p: p.name.lower(),
    )
    main: list[Path] = []
    branch: list[Path] = []
    for p in files:
        if p.stem == "canonical_path":
            main.append(p)
        else:
            branch.append(p)
    return main, branch


def render_in_streamlit(
    st,
    enhanced_dir: Path,
    *,
    project_root: Path | None = None,
    export_base_name: str | None = None,
) -> None:
    _ = project_root

    if not enhanced_dir.is_dir():
        st.warning(f"目录不存在或不是文件夹：`{enhanced_dir}`")
        return

    main_files, branch_files = _list_path_files(enhanced_dir)
    if not main_files and not branch_files:
        st.info("该目录下没有路径 JSON（已跳过 `index.json`）。")
        return

    with st.expander("这条数据是什么？怎么改？", expanded=False):
        st.markdown(
            """
- 数据来自 **内容生成** 写入的 **润色文本目录**。
- 界面按**块顺序**展示（叙述 / 对话 / 选项穿插）；保存时会写回各块，并同步。
- **保存**只写回当前选中的路径文件。
- 下载区提供“一键导出全部路径可读文本”：主线/支线会在同一个 zip 里分成不同目录。
            """.strip()
        )

    options: list[tuple[str, Path]] = []
    for p in main_files:
        options.append(("[主线] canonical_path", p))
    for p in sorted(branch_files, key=lambda x: x.name.lower()):
        options.append((f"[支线] {p.stem}", p))

    labels = [x[0] for x in options]
    choice_i = st.selectbox("选择路径", range(len(options)), format_func=lambda i: labels[i])
    path_file = options[choice_i][1]
    stem = _safe_key_stem(path_file)

    try:
        raw_text = path_file.read_text(encoding="utf-8")
        path_data = json.loads(raw_text)
    except Exception as exc:
        st.error(f"读取 JSON 失败：{exc}")
        return

    if not isinstance(path_data, dict):
        st.error("路径文件根节点应为 JSON 对象。")
        return

    events = path_data.get("events")
    if not isinstance(events, list):
        st.warning("本文件无 `events` 数组。")
        events = []

    # 前端只保留“事件数”这一项，让界面更干净
    st.metric("事件数", len(events))

    st.divider()
    # 下载区（按钮更靠右，更符合“操作区”的直觉）
    dl_left, dl_right = st.columns([3, 1])
    # 下载区：一键导出全部路径可读文本（zip，主线/支线分目录）
    def _apply_session_state_to_events_preview(
        events_in: list[dict[str, Any]],
    ) -> list[dict[str, Any]]:
        events_preview = copy.deepcopy(events_in)
        for j, ev in enumerate(events_preview):
            if not isinstance(ev, dict):
                continue
            # 1) content_blocks 模式：优先从块的编辑输入读取
            if _event_has_editable_blocks(ev):
                blocks = ev.get("content_blocks")
                if not isinstance(blocks, list):
                    continue
                for bi, block in enumerate(blocks):
                    if not isinstance(block, dict):
                        continue
                    t = (block.get("type") or "").lower()
                    if t == "narrative":
                        kn = f"enc_{stem}_{j}_b{bi}_n"
                        block["text"] = st.session_state.get(kn, block.get("text", ""))
                    elif t == "dialogue":
                        ksp = f"enc_{stem}_{j}_b{bi}_sp"
                        ktx = f"enc_{stem}_{j}_b{bi}_tx"
                        block["speaker"] = st.session_state.get(ksp, block.get("speaker", ""))
                        block["text"] = st.session_state.get(ktx, block.get("text", ""))
                    elif t == "choice":
                        kch = f"enc_{stem}_{j}_b{bi}_ch"
                        raw_ch = (st.session_state.get(kch) or "{}").strip()
                        try:
                            parsed = json.loads(raw_ch or "{}")
                            if isinstance(parsed, dict):
                                # 保留 type，其他字段以解析结果为准
                                block.update(parsed)
                                block["type"] = "choice"
                        except json.JSONDecodeError:
                            # JSON 无效时：不改变原块内容（避免导出出错）
                            pass
                ev["content_blocks"] = blocks
                # 同步顶层字段，保证导出“无 content_blocks”时也可读（虽然本页主要按块导出）
                _sync_event_top_level_from_blocks(ev, blocks)
            else:
                # 2) 无 content_blocks 模式：从叙述/对话/选项输入读取
                k_desc = f"enc_{stem}_{j}_desc"
                k_dlg = f"enc_{stem}_{j}_dlg"
                k_pc = f"enc_{stem}_{j}_pc"
                if k_desc in st.session_state:
                    ev["detailed_scene_description"] = st.session_state.get(k_desc, "")
                if k_dlg in st.session_state:
                    try:
                        parsed = json.loads(st.session_state.get(k_dlg) or "[]")
                        ev["dialogue"] = parsed if isinstance(parsed, list) else []
                    except json.JSONDecodeError:
                        pass
                if k_pc in st.session_state:
                    raw_pc = (st.session_state.get(k_pc) or "").strip()
                    if raw_pc in ("", "{}"):
                        ev["player_choice"] = None
                    else:
                        try:
                            ev["player_choice"] = json.loads(raw_pc)
                        except json.JSONDecodeError:
                            pass
        return events_preview

    events_preview = _apply_session_state_to_events_preview(
        events if isinstance(events, list) else []
    )
    txt_out = _blocks_to_readable_text(events_preview)
    ending = path_data.get("ending")
    if isinstance(ending, dict):
        ending_desc = str(
            ending.get("detailed_scene_description") or "",
        )
        # 如果用户正在编辑 ending.detailed_scene_description，也从 session_state 读取
        k_end = f"enc_{stem}_ending_desc"
        if k_end in st.session_state:
            ending_desc = st.session_state.get(k_end, ending_desc)
        if ending_desc.strip():
            txt_out = (txt_out + "\n\n" + "===== 结局 =====\n" + ending_desc).strip()

    # 生成 zip：主线/支线分目录；每条路径一个 txt
    all_json = sorted(
        [p for p in enhanced_dir.glob("*.json") if p.name != "index.json"],
        key=lambda p: p.name.lower(),
    )

    zip_buf = io.BytesIO()
    with zipfile.ZipFile(zip_buf, "w", zipfile.ZIP_DEFLATED) as zf:
        selected_abs = path_file.resolve()
        for jp in all_json:
            try:
                pdata = json.loads(jp.read_text(encoding="utf-8"))
            except Exception:
                continue

            is_selected = jp.resolve() == selected_abs
            if is_selected:
                text = txt_out
            else:
                raw_events = pdata.get("events")
                one_events = raw_events if isinstance(raw_events, list) else []
                text = _blocks_to_readable_text(one_events)
                ending_one = pdata.get("ending")
                if isinstance(ending_one, dict):
                    ending_desc_one = str(
                        ending_one.get("detailed_scene_description") or ""
                    ).strip()
                    if ending_desc_one:
                        text = (
                            text
                            + "\n\n"
                            + "===== 结局 =====\n"
                            + ending_desc_one
                        ).strip()

            group = "主线" if jp.stem == "canonical_path" else "支线"
            zf.writestr(f"{group}/{jp.stem}.txt", text.encode("utf-8"))

    def _safe_filename(name: str) -> str:
        # Windows 不允许: <>:"/\|?*
        cleaned = re.sub(r'[<>:"/\\\\|?*]+', "_", name).strip()
        cleaned = re.sub(r"\s+", " ", cleaned)
        return cleaned[:80] if len(cleaned) > 80 else cleaned

    base_name = (export_base_name or "").strip()
    if not base_name:
        base_name = enhanced_dir.name
    zip_name = f"{_safe_filename(base_name)}_可读文本.zip"

    with dl_left:
        st.caption("一键导出：主线/支线会分别放在 zip 的“主线 / 支线”目录下。")
    with dl_right:
        st.download_button(
            "导出全部路径可读文本",
            data=zip_buf.getvalue(),
            file_name=zip_name,
            mime="application/zip",
            key=f"enc_dl_all_txt_zip_{stem}",
            help="导出全部路径的可读文本（包含你在当前页面改动的这条路径）。",
            use_container_width=True,
        )

    with st.form(f"enc_edit_form_{stem}"):
        j = 0
        for i, ev in enumerate(events):
            if not isinstance(ev, dict):
                continue
            eid = str(ev.get("event_id") or f"#{i}")
            et = str(ev.get("type") or "")
            st.markdown(f"##### {eid} · `{et}`")
            if _event_has_editable_blocks(ev):
                st.caption("按 **content_blocks** 顺序（叙述 / 对话 / 选项穿插）")
                blocks = ev.get("content_blocks")
                assert isinstance(blocks, list)
                for bi, block in enumerate(blocks):
                    if not isinstance(block, dict):
                        continue
                    t = (block.get("type") or "").lower()
                    if t == "narrative":
                        st.markdown("**叙述**")
                        st.text_area(
                            "叙述文本",
                            value=str(block.get("text") or ""),
                            height=140,
                            key=f"enc_{stem}_{j}_b{bi}_n",
                        )
                    elif t == "dialogue":
                        st.markdown("**对话**")
                        c1, c2 = st.columns([1, 3])
                        with c1:
                            st.text_input(
                                "说话人",
                                value=str(block.get("speaker") or ""),
                                key=f"enc_{stem}_{j}_b{bi}_sp",
                            )
                        with c2:
                            st.text_area(
                                "台词",
                                value=str(block.get("text") or ""),
                                height=100,
                                key=f"enc_{stem}_{j}_b{bi}_tx",
                            )
                    elif t == "choice":
                        st.markdown("**选项**")
                        ch_obj = {k: v for k, v in block.items() if k != "type"}
                        st.text_area(
                            "选项（JSON：prompt（提示）/ options（选项）等）",
                            value=json.dumps(ch_obj, ensure_ascii=False, indent=2),
                            height=160,
                            key=f"enc_{stem}_{j}_b{bi}_ch",
                        )
                    else:
                        st.caption(f"未知块类型 `{t}`（保存时保留原样）")
                        st.json(block)
                    st.divider()
            else:
                st.text_area(
                    "叙述（detailed_scene_description）",
                    value=str(ev.get("detailed_scene_description") or ""),
                    height=220,
                    key=f"enc_{stem}_{j}_desc",
                )
                dlg = ev.get("dialogue")
                dlg_s = (
                    json.dumps(dlg, ensure_ascii=False, indent=2)
                    if isinstance(dlg, list)
                    else "[]"
                )
                st.text_area(
                    "对话（dialogue，JSON 数组；元素含 speaker（说话人）/ text（台词）/ tone（语气））",
                    value=dlg_s,
                    height=160,
                    key=f"enc_{stem}_{j}_dlg",
                )
                pc = ev.get("player_choice")
                pc_s = (
                    json.dumps(pc, ensure_ascii=False, indent=2)
                    if isinstance(pc, (dict, list))
                    else ("{}" if pc is None else json.dumps(pc, ensure_ascii=False, indent=2))
                )
                st.text_area(
                    "玩家选项（player_choice，JSON；无则 {}）",
                    value=pc_s,
                    height=140,
                    key=f"enc_{stem}_{j}_pc",
                )
                st.divider()
            j += 1

        ending = path_data.get("ending")
        ending_is_dict = isinstance(ending, dict)
        if ending_is_dict:
            st.markdown("##### 结局（ending）")
            st.text_area(
                "结局详细场景（ending.detailed_scene_description）",
                value=str(ending.get("detailed_scene_description") or ""),
                height=180,
                key=f"enc_{stem}_ending_desc",
            )

        submitted = st.form_submit_button("💾 保存到磁盘（覆盖本路径 JSON）")

    if not submitted:
        return

    try:
        path_data2 = json.loads(path_file.read_text(encoding="utf-8"))
    except Exception as exc:
        st.error(f"保存前重新读取失败：{exc}")
        return

    ev2 = path_data2.get("events")
    if not isinstance(ev2, list):
        st.error("文件结构已变：无 events 数组，取消保存。")
        return

    errs: list[str] = []
    j = 0
    for i, ev in enumerate(ev2):
        if not isinstance(ev, dict):
            continue
        eid = str(ev.get("event_id") or i)
        if _event_has_editable_blocks(ev):
            blocks = ev.get("content_blocks")
            assert isinstance(blocks, list)
            blocks_out: list[dict[str, Any]] = []
            block_err = False
            for bi, block in enumerate(blocks):
                if not isinstance(block, dict):
                    continue
                t = (block.get("type") or "").lower()
                if t == "narrative":
                    kn = f"enc_{stem}_{j}_b{bi}_n"
                    txt = st.session_state.get(kn, block.get("text", ""))
                    blocks_out.append({"type": "narrative", "text": txt})
                elif t == "dialogue":
                    ksp = f"enc_{stem}_{j}_b{bi}_sp"
                    ktx = f"enc_{stem}_{j}_b{bi}_tx"
                    extras = {k: v for k, v in block.items() if k not in ("type", "speaker", "text")}
                    sp = st.session_state.get(ksp, block.get("speaker", ""))
                    tx = st.session_state.get(ktx, block.get("text", ""))
                    blocks_out.append({"type": "dialogue", "speaker": sp, "text": tx, **extras})
                elif t == "choice":
                    kch = f"enc_{stem}_{j}_b{bi}_ch"
                    raw_ch = st.session_state.get(kch, "{}")
                    try:
                        parsed = json.loads(raw_ch or "{}")
                        if not isinstance(parsed, dict):
                            errs.append(f"{eid}: choice 块 #{bi} 须为 JSON 对象")
                            block_err = True
                        else:
                            blocks_out.append({"type": "choice", **parsed})
                    except json.JSONDecodeError as e:
                        errs.append(f"{eid}: choice 块 #{bi} JSON 无效 — {e}")
                        block_err = True
                else:
                    blocks_out.append(dict(block))
            if not block_err:
                _sync_event_top_level_from_blocks(ev, blocks_out)
        else:
            k_desc = f"enc_{stem}_{j}_desc"
            k_dlg = f"enc_{stem}_{j}_dlg"
            k_pc = f"enc_{stem}_{j}_pc"
            if k_desc in st.session_state:
                ev["detailed_scene_description"] = st.session_state[k_desc]
            if k_dlg in st.session_state:
                try:
                    parsed = json.loads(st.session_state[k_dlg] or "[]")
                    if not isinstance(parsed, list):
                        errs.append(f"{eid}: dialogue 须为 JSON 数组")
                    else:
                        ev["dialogue"] = parsed
                except json.JSONDecodeError as e:
                    errs.append(f"{eid}: dialogue JSON 无效 — {e}")
            if k_pc in st.session_state:
                raw_pc = (st.session_state[k_pc] or "").strip()
                if raw_pc in ("", "{}"):
                    ev["player_choice"] = None
                else:
                    try:
                        ev["player_choice"] = json.loads(raw_pc)
                    except json.JSONDecodeError as e:
                        errs.append(f"{eid}: player_choice JSON 无效 — {e}")
        j += 1

    if ending_is_dict and isinstance(path_data2.get("ending"), dict):
        k_end = f"enc_{stem}_ending_desc"
        if k_end in st.session_state:
            path_data2["ending"]["detailed_scene_description"] = st.session_state[k_end]

    if errs:
        st.error("未写入磁盘，请先修正：\n" + "\n".join(f"- {x}" for x in errs))
        return

    try:
        path_file.write_text(
            json.dumps(path_data2, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        st.success(f"已保存：`{path_file}`")
    except OSError as exc:
        st.error(f"写入失败：{exc}")


def render_dir_picker_and_preview(
    st,
    project_root: Path,
    *,
    default_relative_dir: str,
) -> None:
    """带目录输入框的入口（独立页面用）。"""
    raw = st.text_input(
        "增强结果目录（对应 generate-content 的 `--output`）",
        value=default_relative_dir,
        key="enhanced_preview_dir_input",
        help="相对路径相对项目根；也可填绝对路径。",
    )
    p = Path(raw.strip())
    ed = p if p.is_absolute() else (project_root / p)
    st.caption(f"解析为：`{ed}`")
    render_in_streamlit(st, ed, project_root=project_root)
