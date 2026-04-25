"""
Pipeline UI 工具函数。

供 pipeline_demo_app 使用，与 Streamlit 解耦，便于测试与复用。
"""
from __future__ import annotations

import hashlib
import importlib
import json
import os
import re
import shutil
import subprocess
import sys
from collections.abc import Callable
from copy import deepcopy
from pathlib import Path
from typing import Any


def _subprocess_ui_env(
    prompt_path_by_slot: dict[str, str] | None = None,
) -> dict[str, str]:
    """供 Streamlit 启动的子进程使用：无缓冲 stdout + UTF-8 stdio（避免 Windows GBK 下 Rich/emoji 崩）。"""
    from src.core.prompt_overrides import prompt_env_key

    env = dict(os.environ)
    env["PYTHONUNBUFFERED"] = "1"
    env["PIPELINE_UI_PROGRESS"] = "plain"
    env["PYTHONIOENCODING"] = "utf-8"
    if sys.platform == "win32":
        env["PYTHONUTF8"] = "1"
    if prompt_path_by_slot:
        for slot, abs_path in prompt_path_by_slot.items():
            if abs_path:
                env[prompt_env_key(slot)] = abs_path
    return env


def materialize_prompt_slots_to_temp_files(
    project_root: Path,
    run_id: str,
    session_state: Any,
    slot_ids: list[str],
) -> dict[str, str]:
    """
    将当前会话中各槽位编辑后的文本写入
    ``runs/ui/.prompt_override_files/<run_id>/<SLOT>.txt``，返回 slot -> 绝对路径。
    """
    from scripts.ui.prompt_registry import PROMPT_SLOTS

    rid = (run_id or "").strip() or "default"
    base_dir = project_root / "runs" / "ui" / ".prompt_override_files" / rid
    base_dir.mkdir(parents=True, exist_ok=True)
    out: dict[str, str] = {}
    for sid in slot_ids:
        meta = PROMPT_SLOTS.get(sid)
        if meta is None:
            continue
        wk = f"prompt_text_{sid}"
        raw = session_state.get(wk)
        if raw is None:
            src = project_root / meta.rel_path
            raw = src.read_text(encoding="utf-8") if src.is_file() else ""
        fp = base_dir / f"{sid}.txt"
        fp.write_text(str(raw), encoding="utf-8")
        out[sid] = str(fp.resolve())
    return out


def load_json(path: Path) -> dict[str, Any]:
    """加载 JSON 文件。"""
    return json.loads(path.read_text(encoding="utf-8"))


def save_json(path: Path, data: dict[str, Any]) -> None:
    """写入 JSON 文件（UTF-8，缩进 2）。"""
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


def resolve_input_path(project_root: Path, input_path: Path) -> Path:
    """解析输入路径（相对/绝对）。"""
    return input_path if input_path.is_absolute() else (project_root / input_path)


def strip_bracketed_content(text: str) -> str:
    """去掉中英文括号及其内部内容，支持多段。"""
    cleaned = text
    pattern = re.compile(r"\([^()]*\)|（[^（）]*）")
    while True:
        new_cleaned = pattern.sub("", cleaned)
        if new_cleaned == cleaned:
            break
        cleaned = new_cleaned
    return cleaned


def normalize_novel_title(text: str) -> str:
    """规范化小说标题（去括号、统一分隔符）。"""
    cleaned = strip_bracketed_content(text)
    cleaned = re.sub(r"[_\-]+", " ", cleaned)
    cleaned = re.sub(r"\s+", " ", cleaned).strip()
    return cleaned or "novel"


def to_pinyin_slug(text: str) -> str | None:
    """尝试将中文转拼音 slug；若依赖不可用或结果为空，返回 None。"""
    try:
        pypinyin = importlib.import_module("pypinyin")
    except ModuleNotFoundError:
        return None

    syllables = pypinyin.lazy_pinyin(text, errors="ignore")
    joined = "_".join(s for s in syllables if s).strip("_")
    if not joined:
        return None
    return re.sub(r"[^0-9A-Za-z_]+", "_", joined).strip("_") or None


def build_novel_identity(source_name: str) -> dict[str, str]:
    """
    统一小说名处理逻辑（用于 run_id、input_library 分桶目录名等）：
    1) 去掉括号及括号内容得到 title
    2) 转拼音 slug（无拼音库时降级为英文数字 slug）
    3) 生成默认 run_id（demo_<slug>）

    磁盘上的**文件名**请用 `normalize_filename_keep_ext`：保留中文展示名，仅清理非法字符。
    """
    stem = Path(source_name).stem
    title = normalize_novel_title(stem)
    pinyin = to_pinyin_slug(title)
    if pinyin:
        slug = pinyin.lower()
    else:
        slug = re.sub(r"[^0-9A-Za-z]+", "_", title).strip("_").lower() or "novel"
    return {
        "title": title,
        "slug": slug,
        "default_run_id": f"demo_{slug}",
    }


# Windows 文件名不能包含的字符；另需避免仅由保留设备名组成的 stem
_WIN_FILENAME_FORBIDDEN = re.compile(r'[<>:"/\\|?*\x00-\x1f]')
_WIN_RESERVED_STEMS = {
    "CON",
    "PRN",
    "AUX",
    "NUL",
    "COM0",
    "COM1",
    "COM2",
    "COM3",
    "COM4",
    "COM5",
    "COM6",
    "COM7",
    "COM8",
    "COM9",
    "LPT0",
    "LPT1",
    "LPT2",
    "LPT3",
    "LPT4",
    "LPT5",
    "LPT6",
    "LPT7",
    "LPT8",
    "LPT9",
}


def safe_filename_stem_for_storage(filename: str) -> str:
    """
    从原始文件名得到可写入磁盘的 stem：去括号、整理空白，**保留中文**，
    仅去掉 Windows 非法字符；若结果为空则退回拼音 slug。
    """
    stem = normalize_novel_title(Path(filename).stem)
    stem = _WIN_FILENAME_FORBIDDEN.sub("_", stem)
    stem = stem.rstrip(" .") or ""
    if not stem:
        stem = build_novel_identity(Path(filename).name)["slug"]
    if len(stem) > 200:
        stem = stem[:200].rstrip(" .")
    if stem.upper() in _WIN_RESERVED_STEMS:
        stem = f"{stem}_"
    return stem or "novel"


def normalize_filename_keep_ext(filename: str) -> str:
    """规范化文件名（保留扩展名），用于归档：中文书名 + 合法字符，不再强制改为拼音。"""
    p = Path(filename)
    stem = safe_filename_stem_for_storage(p.name)
    return f"{stem}{p.suffix.lower()}"


def sanitize_uploaded_filename(filename: str) -> str:
    """上传保存到 input_library 时的文件名：去括号、保留中文、清理非法字符。"""
    return normalize_filename_keep_ext(filename)


def derive_run_id(run_id_input: str, source_path: Path) -> str:
    """根据用户输入或源文件名推导 run_id。"""
    raw = (run_id_input or "").strip()
    if raw:
        return raw
    return build_novel_identity(source_path.name)["default_run_id"]


def infer_selected_input_path(
    *,
    input_source: str,
    selected_project_file: Path | None,
    uploaded_saved_path: Path | None,
) -> Path | None:
    """根据输入方式推断当前选中的输入文件路径。"""
    if input_source == "上传文件":
        return uploaded_saved_path
    if input_source == "从项目文件选择":
        return selected_project_file
    return None


def sha256_file(path: Path) -> str:
    """计算文件 SHA256。"""
    h = hashlib.sha256()
    with path.open("rb") as f:
        while True:
            chunk = f.read(8192)
            if not chunk:
                break
            h.update(chunk)
    return h.hexdigest()


def sha256_bytes(data: bytes) -> str:
    """计算字节串 SHA256。"""
    return hashlib.sha256(data).hexdigest()


def infer_type_bucket(source_path: Path) -> str:
    """根据扩展名推断输入类型（epub/txt/other）。"""
    ext = source_path.suffix.lower()
    if ext == ".epub":
        return "epub"
    if ext == ".txt":
        return "txt"
    return "other"


def list_candidate_files(
    *,
    project_root: Path,
    browse_root_raw: str,
    recursive: bool,
    suffixes: list[str],
) -> tuple[list[Path], str | None]:
    """列出目录下匹配后缀的文件，返回 (文件列表, 错误信息)。"""
    browse_root = Path(browse_root_raw)
    if not browse_root.is_absolute():
        browse_root = project_root / browse_root
    if not browse_root.exists() or not browse_root.is_dir():
        return [], f"目录不存在或不可访问: {browse_root}"

    normalized_suffixes = {s.lower() for s in suffixes if s}
    iterator = browse_root.rglob("*") if recursive else browse_root.glob("*")
    files = [p for p in iterator if p.is_file() and p.suffix.lower() in normalized_suffixes]
    files.sort(key=lambda p: str(p).lower())
    return files, None


def archive_input_file(project_root: Path, source_path: Path) -> tuple[Path, bool, str]:
    """
    统一按文件类型 + 小说名归档到 experiments/input_library/<type>/<novel_slug>/ 下。
    同名同内容会复用，不重复复制。
    返回 (目标路径, 是否复用, 消息)。
    """
    bucket = infer_type_bucket(source_path)
    novel_slug = build_novel_identity(source_path.name)["slug"]
    archive_dir = project_root / "experiments" / "input_library" / bucket / novel_slug
    archive_dir.mkdir(parents=True, exist_ok=True)
    normalized_filename = normalize_filename_keep_ext(source_path.name)
    target_path = archive_dir / normalized_filename

    src_hash = sha256_file(source_path)
    if target_path.exists():
        if sha256_file(target_path) == src_hash:
            return target_path, True, "已存在同名同内容文件，直接复用"
        alt_name = f"{target_path.stem}__{src_hash[:8]}{target_path.suffix}"
        alt_path = archive_dir / alt_name
        if alt_path.exists() and sha256_file(alt_path) == src_hash:
            return alt_path, True, "检测到同内容文件（哈希匹配），直接复用"
        shutil.copy2(source_path, alt_path)
        return alt_path, False, "同名不同内容，已按哈希后缀另存"

    if source_path.resolve() != target_path.resolve():
        shutil.copy2(source_path, target_path)
        return target_path, False, "已复制到输入库"
    return target_path, True, "输入文件已在输入库中，直接使用"


def save_uploaded_file_to_library(project_root: Path, uploaded_file) -> tuple[Path, bool, str]:
    """
    将 Streamlit 上传文件保存到按类型 + 小说名分桶的输入库目录。
    epub 文件会自动转换为 txt 后存入 txt 桶。
    同名同内容不重复写入。
    返回 (目标路径, 是否复用, 消息)。
    """
    filename = sanitize_uploaded_filename(Path(uploaded_file.name).name)
    ext = Path(filename).suffix.lower()

    # 分桶目录仍用拼音 slug（路径 ASCII 友好）
    novel_slug = build_novel_identity(Path(uploaded_file.name).name)["slug"]
    # 文件名：去掉括号内容，只保留干净的标题
    clean_title = normalize_novel_title(Path(uploaded_file.name).stem)

    if ext == ".epub":
        # epub -> 先写临时文件，再转 txt，最终存入 txt 桶
        import tempfile
        from scripts.extract_epub_text import extract_epub_to_txt

        payload = uploaded_file.getvalue()
        # 写临时 epub 用于转换
        with tempfile.NamedTemporaryFile(suffix=".epub", delete=False) as tmp:
            tmp.write(payload)
            tmp_epub = Path(tmp.name)

        try:
            target_dir = project_root / "experiments" / "input_library" / "txt" / novel_slug
            target_dir.mkdir(parents=True, exist_ok=True)
            target_path = target_dir / f"{clean_title}.txt"

            # 如果已有同名文件，检查是否需要重新转换
            if target_path.exists():
                return target_path, True, "已存在同名 txt 文件，直接复用"

            extract_epub_to_txt(tmp_epub, target_path)
            return target_path, False, "epub 已转换为 txt 并写入输入库"
        finally:
            tmp_epub.unlink(missing_ok=True)
    else:
        bucket = "txt" if ext == ".txt" else "other"
        target_dir = project_root / "experiments" / "input_library" / bucket / novel_slug
        target_dir.mkdir(parents=True, exist_ok=True)
        target_path = target_dir / f"{clean_title}{ext}"

        payload = uploaded_file.getvalue()
        payload_hash = sha256_bytes(payload)
        if target_path.exists():
            if sha256_file(target_path) == payload_hash:
                return target_path, True, "已存在同名同内容文件，直接复用"
            alt_name = f"{clean_title}__{payload_hash[:8]}{ext}"
            alt_path = target_dir / alt_name
            if alt_path.exists() and sha256_file(alt_path) == payload_hash:
                return alt_path, True, "检测到同内容文件（哈希匹配），直接复用"
            alt_path.write_bytes(payload)
            return alt_path, False, "同名不同内容，已按哈希后缀另存"

        target_path.write_bytes(payload)
        return target_path, False, "上传成功并写入输入库"


def prepare_input_text(
    project_root: Path,
    input_path: Path,
    *,
    extract_epub_fn=None,
) -> tuple[str, str]:
    """
    将输入统一转成 txt（全文），返回 (相对项目根路径, novel_slug)。

    若为 epub，调用 extract_epub_fn(epub_path, txt_path) 转换；
    未提供时使用 scripts.extract_epub_text.extract_epub_to_txt。
    """
    resolved_input = resolve_input_path(project_root, input_path)
    if not resolved_input.exists():
        raise FileNotFoundError(f"找不到输入文件: {resolved_input}")

    archived_input, _, _ = archive_input_file(project_root, resolved_input)
    novel_slug = build_novel_identity(archived_input.name)["slug"]

    if archived_input.suffix.lower() == ".epub":
        target_txt = project_root / "experiments" / "text_variants" / novel_slug / f"{novel_slug}.txt"
        target_txt.parent.mkdir(parents=True, exist_ok=True)
        if extract_epub_fn is None:
            from scripts.extract_epub_text import extract_epub_to_txt
            extract_epub_fn = extract_epub_to_txt
        extract_epub_fn(archived_input, target_txt)
        source_txt = target_txt
    else:
        source_txt = archived_input

    content = source_txt.read_text(encoding="utf-8", errors="ignore")
    tag = "full"
    out_file = project_root / "experiments" / "text_variants" / novel_slug / f"{novel_slug}_{tag}.txt"
    out_file.parent.mkdir(parents=True, exist_ok=True)
    out_file.write_text(content, encoding="utf-8")
    return str(out_file.relative_to(project_root)).replace("\\", "/"), novel_slug


def build_single_run_config(
    *,
    project_root: Path,
    base_config_path: Path,
    input_path: Path,
    effective_run_id: str,
    output_root: str,
    step_overrides: dict[str, Any],
) -> tuple[dict[str, Any], str]:
    """构建单次运行的 pipeline 配置。"""
    base = load_json(base_config_path)
    cfg = deepcopy(base)

    input_text_rel, novel_slug = prepare_input_text(
        project_root=project_root,
        input_path=input_path,
    )
    cfg["run_id"] = effective_run_id
    cfg["input_text"] = input_text_rel
    cfg["output_root"] = output_root or f"runs/ui/{effective_run_id}"
    cfg["steps"] = step_overrides
    cfg["_ui_run"] = {
        "text_mode": "full",
        "input_bucket": infer_type_bucket(input_path),
        "novel_slug": novel_slug,
        "effective_run_id": effective_run_id,
    }
    return cfg, novel_slug


def run_pipeline_and_stream(
    project_root: Path,
    config_rel_path: str,
    log_placeholder,
    *,
    prompt_path_by_slot: dict[str, str] | None = None,
) -> int:
    """执行 pipeline 并实时输出日志到 log_placeholder。返回退出码。"""
    cmd = [sys.executable, "-m", "src.cli", "run-pipeline", "-c", config_rel_path]
    process = subprocess.Popen(
        cmd,
        cwd=str(project_root),
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        encoding="utf-8",
        errors="replace",
        env=_subprocess_ui_env(prompt_path_by_slot),
        bufsize=1,
    )
    lines: list[str] = []
    assert process.stdout is not None
    for line in process.stdout:
        lines.append(line.rstrip("\n"))
        log_placeholder.code("\n".join(lines[-120:]), language="text")
    return process.wait()


def resolve_extract_output_dir_for_run(
    *,
    template_data: dict[str, Any],
    session_ui_run_id: str | None,
    session_selected_input_path: str | None,
) -> str:
    """
    抽取结果目录：``runs/ui/<run_id>/out/extract_chain.json``。

    run_id 顺序：会话 ``ui_run_id`` → 按选中小说路径推导 → 模板顶层 ``run_id`` → ``default``。
    """
    run_id = (session_ui_run_id or "").strip()
    if not run_id and session_selected_input_path:
        try:
            run_id = derive_run_id("", Path(session_selected_input_path))
        except Exception:
            run_id = ""
    if not run_id:
        run_id = (template_data.get("run_id") or "").strip()
    if not run_id:
        run_id = "default"
    return f"runs/ui/{run_id}/out"


def merge_step_params_for_ui(
    *,
    base_config_path: Path,
    step_name: str,
    step_overrides: dict[str, Any] | None,
    project_root: Path | None = None,
    session_selected_input_path: str | None = None,
    session_ui_run_id: str | None = None,
) -> dict[str, Any]:
    """
    与 `load_pipeline_config` 的合并规则一致：
    default_step_params + 模板里该步的 params；若 UI 已编辑过 step_overrides 再覆盖。

    extract-events：若 params 里尚未有 input / input_path，则按序填入
    1) 会话里「选择小说」的路径（尽量写成相对 project_root）
    2) 模板顶层 input_text
    3) 默认文件名（与 Typer 默认一致）

    extract-events：若 output_dir 未设或为空，则 ``runs/ui/<run_id>/out``（仅按 run_id）。

    extract-relations：与 extract-events 共用同一 ``output_dir``（按 run_id）；并补全
    ``json``（extract_chain）、``output``（extract_relations.json）。``output_dir`` 仅用于 UI 合并，
    传 CLI 前由单步页剔除。
    """
    data = load_json(base_config_path)
    default_step_params = data.get("default_step_params") or {}
    if not isinstance(default_step_params, dict):
        default_step_params = {}
    raw_steps = data.get("steps") or {}
    raw_step = raw_steps.get(step_name) or {}
    raw_params = raw_step.get("params") or {}
    if not isinstance(raw_params, dict):
        raw_params = {}
    merged = dict(default_step_params)
    merged.update(raw_params)
    if step_overrides and step_name in step_overrides:
        ov = step_overrides[step_name].get("params") or {}
        if isinstance(ov, dict):
            merged.update(ov)

    if step_name == "extract-events":
        raw_in = merged.get("input") if "input" in merged else None
        raw_ip = merged.get("input_path") if "input_path" in merged else None
        eff = raw_in if raw_in is not None else raw_ip
        missing = eff is None or (isinstance(eff, str) and not str(eff).strip())
        if missing:
            chosen: str | None = None
            if session_selected_input_path and project_root is not None:
                try:
                    abs_sel = Path(session_selected_input_path).resolve()
                    root = project_root.resolve()
                    chosen = str(abs_sel.relative_to(root)).replace("\\", "/")
                except ValueError:
                    chosen = session_selected_input_path
            if not chosen:
                it = data.get("input_text")
                if it:
                    chosen = str(it)
            merged["input"] = chosen or "王佛脱险记.txt"

        raw_od = merged.get("output_dir") if "output_dir" in merged else None
        od_missing = raw_od is None or (isinstance(raw_od, str) and not str(raw_od).strip())
        if od_missing:
            merged["output_dir"] = resolve_extract_output_dir_for_run(
                template_data=data,
                session_ui_run_id=session_ui_run_id,
                session_selected_input_path=session_selected_input_path,
            )

    # 与 extract-events 相同：合并输入路径与 runs/ui/<run_id>/out
    if step_name == "extract-events-and-relations":
        raw_in = merged.get("input") if "input" in merged else None
        raw_ip = merged.get("input_path") if "input_path" in merged else None
        eff = raw_in if raw_in is not None else raw_ip
        missing = eff is None or (isinstance(eff, str) and not str(eff).strip())
        if missing:
            chosen: str | None = None
            if session_selected_input_path and project_root is not None:
                try:
                    abs_sel = Path(session_selected_input_path).resolve()
                    root = project_root.resolve()
                    chosen = str(abs_sel.relative_to(root)).replace("\\", "/")
                except ValueError:
                    chosen = session_selected_input_path
            if not chosen:
                it = data.get("input_text")
                if it:
                    chosen = str(it)
            merged["input"] = chosen or "王佛脱险记.txt"

        raw_od = merged.get("output_dir") if "output_dir" in merged else None
        od_missing2 = raw_od is None or (isinstance(raw_od, str) and not str(raw_od).strip())
        if od_missing2:
            merged["output_dir"] = resolve_extract_output_dir_for_run(
                template_data=data,
                session_ui_run_id=session_ui_run_id,
                session_selected_input_path=session_selected_input_path,
            )

    if step_name == "extract-relations":
        raw_od_er = merged.get("output_dir") if "output_dir" in merged else None
        od_missing_er = raw_od_er is None or (
            isinstance(raw_od_er, str) and not str(raw_od_er).strip()
        )
        if od_missing_er:
            merged["output_dir"] = resolve_extract_output_dir_for_run(
                template_data=data,
                session_ui_run_id=session_ui_run_id,
                session_selected_input_path=session_selected_input_path,
            )
        base_er = str(merged.get("output_dir") or "").strip().rstrip("/\\")
        if base_er:
            j_er = merged.get("json") if "json" in merged else None
            if j_er is None or (isinstance(j_er, str) and not j_er.strip()):
                merged["json"] = f"{base_er}/extract_chain.json"
            o_er = merged.get("output") if "output" in merged else None
            if o_er is None or (isinstance(o_er, str) and not o_er.strip()):
                merged["output"] = f"{base_er}/extract_relations.json"

    # 与 extract 同一 output_dir：读 extract_chain，写 mentions.json（Typer 为 --input / --output）
    if step_name == "generate-mentions":
        raw_od = merged.get("output_dir") if "output_dir" in merged else None
        od_missing = raw_od is None or (isinstance(raw_od, str) and not str(raw_od).strip())
        if od_missing:
            merged["output_dir"] = resolve_extract_output_dir_for_run(
                template_data=data,
                session_ui_run_id=session_ui_run_id,
                session_selected_input_path=session_selected_input_path,
            )
        base = str(merged.get("output_dir") or "").strip().rstrip("/\\")
        if base:
            inp = merged.get("input") if "input" in merged else None
            if inp is None or (isinstance(inp, str) and not inp.strip()):
                merged["input"] = f"{base}/extract_chain.json"
            out = merged.get("output") if "output" in merged else None
            if out is None or (isinstance(out, str) and not out.strip()):
                merged["output"] = f"{base}/mentions.json"

    if step_name == "generate-entities":
        raw_od = merged.get("output_dir") if "output_dir" in merged else None
        od_missing_e = raw_od is None or (isinstance(raw_od, str) and not str(raw_od).strip())
        if od_missing_e:
            merged["output_dir"] = resolve_extract_output_dir_for_run(
                template_data=data,
                session_ui_run_id=session_ui_run_id,
                session_selected_input_path=session_selected_input_path,
            )
        base = str(merged.get("output_dir") or "").strip().rstrip("/\\")
        if base:
            inp = merged.get("input") if "input" in merged else None
            if inp is None or (isinstance(inp, str) and not inp.strip()):
                merged["input"] = f"{base}/mentions.json"
            out = merged.get("output") if "output" in merged else None
            if out is None or (isinstance(out, str) and not out.strip()):
                merged["output"] = f"{base}/entities.json"

    # 合并步：与 generate-mentions 共用 output_dir + extract_chain 输入
    if step_name == "generate-mentions-and-entities":
        raw_od = merged.get("output_dir") if "output_dir" in merged else None
        od_missing = raw_od is None or (isinstance(raw_od, str) and not str(raw_od).strip())
        if od_missing:
            merged["output_dir"] = resolve_extract_output_dir_for_run(
                template_data=data,
                session_ui_run_id=session_ui_run_id,
                session_selected_input_path=session_selected_input_path,
            )
        base = str(merged.get("output_dir") or "").strip().rstrip("/\\")
        if base:
            inp = merged.get("input") if "input" in merged else None
            if inp is None or (isinstance(inp, str) and not inp.strip()):
                merged["input"] = f"{base}/extract_chain.json"

    # 合并步：四类状态 + baseline，均落在同一 output_dir（runs/ui/<run_id>/out）
    if step_name == "extract-states-and-baseline":
        raw_od = merged.get("output_dir") if "output_dir" in merged else None
        od_missing = raw_od is None or (isinstance(raw_od, str) and not str(raw_od).strip())
        if od_missing:
            merged["output_dir"] = resolve_extract_output_dir_for_run(
                template_data=data,
                session_ui_run_id=session_ui_run_id,
                session_selected_input_path=session_selected_input_path,
            )
        base = str(merged.get("output_dir") or "").strip().rstrip("/\\")
        if base:
            ev = merged.get("events") if "events" in merged else None
            if ev is None or (isinstance(ev, str) and not ev.strip()):
                merged["events"] = f"{base}/extract_chain.json"
            men = merged.get("mentions") if "mentions" in merged else None
            if men is None or (isinstance(men, str) and not men.strip()):
                merged["mentions"] = f"{base}/mentions.json"
            ent = merged.get("entities") if "entities" in merged else None
            if ent is None or (isinstance(ent, str) and not ent.strip()):
                merged["entities"] = f"{base}/entities.json"

    def _fill_output_dir_and_chain_for_state_steps(
        merged_local: dict[str, Any],
    ) -> str:
        raw = merged_local.get("output_dir") if "output_dir" in merged_local else None
        if raw is None or (isinstance(raw, str) and not str(raw).strip()):
            merged_local["output_dir"] = resolve_extract_output_dir_for_run(
                template_data=data,
                session_ui_run_id=session_ui_run_id,
                session_selected_input_path=session_selected_input_path,
            )
        return str(merged_local.get("output_dir") or "").strip().rstrip("/\\")

    if step_name == "extract-world-states":
        b = _fill_output_dir_and_chain_for_state_steps(merged)
        if b:
            ev = merged.get("events") if "events" in merged else None
            if ev is None or (isinstance(ev, str) and not ev.strip()):
                merged["events"] = f"{b}/extract_chain.json"
            out = merged.get("output") if "output" in merged else None
            if out is None or (isinstance(out, str) and not out.strip()):
                merged["output"] = f"{b}/world_states.json"

    if step_name in ("extract-character-states", "extract-relationship-states"):
        b = _fill_output_dir_and_chain_for_state_steps(merged)
        if b:
            ev = merged.get("events") if "events" in merged else None
            if ev is None or (isinstance(ev, str) and not ev.strip()):
                merged["events"] = f"{b}/extract_chain.json"
            men = merged.get("mentions") if "mentions" in merged else None
            if men is None or (isinstance(men, str) and not men.strip()):
                merged["mentions"] = f"{b}/mentions.json"
            ent = merged.get("entities") if "entities" in merged else None
            if ent is None or (isinstance(ent, str) and not ent.strip()):
                merged["entities"] = f"{b}/entities.json"
            out = merged.get("output") if "output" in merged else None
            if out is None or (isinstance(out, str) and not out.strip()):
                fname = (
                    "character_states.json"
                    if step_name == "extract-character-states"
                    else "relationship_states.json"
                )
                merged["output"] = f"{b}/{fname}"

    if step_name == "init-state-baseline":
        b = _fill_output_dir_and_chain_for_state_steps(merged)
        if b:
            ws = merged.get("world_states") if "world_states" in merged else None
            if ws is None or (isinstance(ws, str) and not ws.strip()):
                merged["world_states"] = f"{b}/world_states.json"
            cs = merged.get("character_states") if "character_states" in merged else None
            if cs is None or (isinstance(cs, str) and not cs.strip()):
                merged["character_states"] = f"{b}/character_states.json"
            rs = merged.get("relationship_states") if "relationship_states" in merged else None
            if rs is None or (isinstance(rs, str) and not rs.strip()):
                merged["relationship_states"] = f"{b}/relationship_states.json"
            out = merged.get("output") if "output" in merged else None
            if out is None or (isinstance(out, str) and not out.strip()):
                merged["output"] = f"{b}/state_baseline.json"

    if step_name == "aggregate-characters-and-personas":
        raw_od = merged.get("output_dir") if "output_dir" in merged else None
        od_missing = raw_od is None or (isinstance(raw_od, str) and not str(raw_od).strip())
        if od_missing:
            merged["output_dir"] = resolve_extract_output_dir_for_run(
                template_data=data,
                session_ui_run_id=session_ui_run_id,
                session_selected_input_path=session_selected_input_path,
            )
        b = str(merged.get("output_dir") or "").strip().rstrip("/\\")
        if b:
            if merged.get("events") is None or (
                isinstance(merged.get("events"), str) and not str(merged.get("events")).strip()
            ):
                merged["events"] = f"{b}/extract_chain.json"
            if merged.get("char_states") is None or (
                isinstance(merged.get("char_states"), str)
                and not str(merged.get("char_states")).strip()
            ):
                merged["char_states"] = f"{b}/character_states.json"
            if merged.get("rel_states") is None or (
                isinstance(merged.get("rel_states"), str)
                and not str(merged.get("rel_states")).strip()
            ):
                merged["rel_states"] = f"{b}/relationship_states.json"
            if merged.get("entities") is None or (
                isinstance(merged.get("entities"), str) and not str(merged.get("entities")).strip()
            ):
                merged["entities"] = f"{b}/entities.json"
            if merged.get("mentions") is None or (
                isinstance(merged.get("mentions"), str) and not str(merged.get("mentions")).strip()
            ):
                merged["mentions"] = f"{b}/mentions.json"
            pr = merged.get("profiles") if "profiles" in merged else None
            if pr is None or (isinstance(pr, str) and not pr.strip()):
                merged["profiles"] = f"{b}/character_profiles.json"
            po = merged.get("personas_output") if "personas_output" in merged else None
            if po is None or (isinstance(po, str) and not po.strip()):
                merged["personas_output"] = f"{b}/character_personas.json"

    if step_name in ("import-neo4j", "import-entities", "import-state-changes"):
        b = _fill_output_dir_and_chain_for_state_steps(merged)
        if b:
            if step_name == "import-neo4j":
                j = merged.get("json") if "json" in merged else None
                if j is None or (isinstance(j, str) and not j.strip()):
                    # merged JSON（必须包含 relations），否则导入时不会创建事件关系边。
                    merged["json"] = f"{b}/extract_merged.json"
            elif step_name == "import-entities":
                e = merged.get("entities") if "entities" in merged else None
                if e is None or (isinstance(e, str) and not e.strip()):
                    merged["entities"] = f"{b}/entities.json"
            else:
                s0 = merged.get("states") if "states" in merged else None
                if s0 is None or (isinstance(s0, str) and not s0.strip()):
                    merged["states"] = f"{b}/world_states.json"
    if step_name == "import-neo4j-all":
        b = _fill_output_dir_and_chain_for_state_steps(merged)
        if b:
            j = merged.get("json") if "json" in merged else None
            if j is None or (isinstance(j, str) and not j.strip()):
                # merged JSON（必须包含 relations），否则导入时不会创建事件关系边。
                merged["json"] = f"{b}/extract_merged.json"
            e = merged.get("entities") if "entities" in merged else None
            if e is None or (isinstance(e, str) and not e.strip()):
                merged["entities"] = f"{b}/entities.json"
            ws = merged.get("world_states") if "world_states" in merged else None
            if ws is None or (isinstance(ws, str) and not ws.strip()):
                merged["world_states"] = f"{b}/world_states.json"
            cs = (
                merged.get("character_states")
                if "character_states" in merged
                else None
            )
            if cs is None or (isinstance(cs, str) and not cs.strip()):
                merged["character_states"] = f"{b}/character_states.json"
            rs = (
                merged.get("relationship_states")
                if "relationship_states" in merged
                else None
            )
            if rs is None or (isinstance(rs, str) and not rs.strip()):
                merged["relationship_states"] = f"{b}/relationship_states.json"
        # UI：让 clear 能在前端被勾选改变（默认 true）。
        if "clear" not in merged:
            merged["clear"] = True

    if step_name == "aggregate-characters":
        b = _fill_output_dir_and_chain_for_state_steps(merged)
        if b:
            if merged.get("events") is None or (
                isinstance(merged.get("events"), str) and not str(merged.get("events")).strip()
            ):
                merged["events"] = f"{b}/extract_chain.json"
            if merged.get("char_states") is None or (
                isinstance(merged.get("char_states"), str)
                and not str(merged.get("char_states")).strip()
            ):
                merged["char_states"] = f"{b}/character_states.json"
            if merged.get("rel_states") is None or (
                isinstance(merged.get("rel_states"), str)
                and not str(merged.get("rel_states")).strip()
            ):
                merged["rel_states"] = f"{b}/relationship_states.json"
            if merged.get("entities") is None or (
                isinstance(merged.get("entities"), str) and not str(merged.get("entities")).strip()
            ):
                merged["entities"] = f"{b}/entities.json"
            if merged.get("mentions") is None or (
                isinstance(merged.get("mentions"), str) and not str(merged.get("mentions")).strip()
            ):
                merged["mentions"] = f"{b}/mentions.json"
            out = merged.get("output") if "output" in merged else None
            if out is None or (isinstance(out, str) and not out.strip()):
                merged["output"] = f"{b}/character_profiles.json"

    if step_name == "aggregate-personas":
        b = _fill_output_dir_and_chain_for_state_steps(merged)
        if b:
            pr = merged.get("profiles") if "profiles" in merged else None
            if pr is None or (isinstance(pr, str) and not pr.strip()):
                merged["profiles"] = f"{b}/character_profiles.json"
            out = merged.get("output") if "output" in merged else None
            if out is None or (isinstance(out, str) and not out.strip()):
                merged["output"] = f"{b}/character_personas.json"

    if step_name == "generate-canonical-branch":
        # CLI flag: --output（变量名 output_path）；流水线 JSON 键用 output 对齐。
        base = resolve_extract_output_dir_for_run(
            template_data=data,
            session_ui_run_id=session_ui_run_id,
            session_selected_input_path=session_selected_input_path,
        )
        out = merged.get("output") if "output" in merged else None
        if out is None or (isinstance(out, str) and not out.strip()):
            merged["output"] = f"{base}/canonical_branch.json"

    if step_name == "analyze-decision-points":
        # analyzer 使用 canonical_branch_* 里的 processed_events；前端按你实际产物做自动回退。
        base = resolve_extract_output_dir_for_run(
            template_data=data,
            session_ui_run_id=session_ui_run_id,
            session_selected_input_path=session_selected_input_path,
        )

        base_abs = Path(base)
        if project_root is not None and not base_abs.is_absolute():
            base_abs = project_root / base_abs

        canonical_ch = base_abs / "canonical_branch_chronological.json"
        canonical_rel = (
            f"{base}/canonical_branch_chronological.json"
            if canonical_ch.is_file()
            else f"{base}/canonical_branch.json"
        )

        # CLI: analyze-decision-points uses Typer flags: --canonical / -c
        cb = merged.get("canonical") if "canonical" in merged else None
        if cb is None or (isinstance(cb, str) and not cb.strip()):
            merged["canonical"] = canonical_rel

        # 避免把错误的 key 传给 params_to_cli_args（会生成 --canonical-branch-path）
        merged.pop("canonical_branch_path", None)

        # CLI: analyze-decision-points uses Typer flags: --output / -o
        out = merged.get("output") if "output" in merged else None
        if out is None or (isinstance(out, str) and not out.strip()):
            merged["output"] = f"{base}/decision_points_analysis.json"

        merged.pop("output_path", None)

        # 与 Typer analyze-decision-points 一致：限制 branch_points 数量（留空=不限制）
        if "target_branch_count" not in merged:
            merged["target_branch_count"] = None
        # 不在前端展示；未传时 CLI Typer 默认 --max-concurrent 5
        merged.pop("max_concurrent", None)

    if step_name == "determine-ending-candidates":
        # CLI flags：
        # - branches: --branches / -b
        # - canonical: --canonical / -c
        # - output: --output / -o
        # - branch-id: --branch-id / -i（由 UI 透传）
        base = resolve_extract_output_dir_for_run(
            template_data=data,
            session_ui_run_id=session_ui_run_id,
            session_selected_input_path=session_selected_input_path,
        )

        base_abs = Path(base)
        if project_root is not None and not base_abs.is_absolute():
            base_abs = project_root / base_abs

        canonical_ch = base_abs / "canonical_branch_chronological.json"
        canonical_rel = (
            f"{base}/canonical_branch_chronological.json"
            if canonical_ch.is_file()
            else f"{base}/canonical_branch.json"
        )

        br = merged.get("branches") if "branches" in merged else None
        if br is None or (isinstance(br, str) and not br.strip()):
            merged["branches"] = f"{base}/branches.json"

        cb = merged.get("canonical") if "canonical" in merged else None
        if cb is None or (isinstance(cb, str) and not cb.strip()):
            merged["canonical"] = canonical_rel

        out = merged.get("output") if "output" in merged else None
        if out is None or (isinstance(out, str) and not out.strip()):
            merged["output"] = f"{base}/ending_candidates.json"

        # 避免把错误 key 传给 params_to_cli_args
        merged.pop("branches_path", None)
        merged.pop("canonical_branch_path", None)
        merged.pop("output_path", None)

    if step_name == "generate-branch":
        # CLI flags：
        # - canonical: --canonical / -c
        # - decision_analysis: --decision-analysis / -d
        # - output: --output / -o
        base = resolve_extract_output_dir_for_run(
            template_data=data,
            session_ui_run_id=session_ui_run_id,
            session_selected_input_path=session_selected_input_path,
        )

        base_abs = Path(base)
        if project_root is not None and not base_abs.is_absolute():
            base_abs = project_root / base_abs

        canonical_ch = base_abs / "canonical_branch_chronological.json"
        canonical_rel = (
            f"{base}/canonical_branch_chronological.json"
            if canonical_ch.is_file()
            else f"{base}/canonical_branch.json"
        )

        cb = merged.get("canonical") if "canonical" in merged else None
        if cb is None or (isinstance(cb, str) and not cb.strip()):
            merged["canonical"] = canonical_rel

        da = merged.get("decision_analysis") if "decision_analysis" in merged else None
        if da is None or (isinstance(da, str) and not da.strip()):
            merged["decision_analysis"] = f"{base}/decision_points_analysis.json"

        out = merged.get("output") if "output" in merged else None
        if out is None or (isinstance(out, str) and not out.strip()):
            merged["output"] = f"{base}/branches.json"

        # 与 Typer generate-branch 一致：每点最多支线数、分支点密度（未指定 fork 时按 analysis 的 branch_points 截取）
        if "max_branches_per_point" not in merged:
            merged["max_branches_per_point"] = 2
        if "decision_density" not in merged or (
            isinstance(merged.get("decision_density"), str)
            and not str(merged.get("decision_density") or "").strip()
        ):
            merged["decision_density"] = "high"
        if "decision_density_ratio" not in merged:
            merged["decision_density_ratio"] = None
        if "fork_event_id" not in merged:
            merged["fork_event_id"] = None

        merged.pop("canonical_branch_path", None)
        merged.pop("decision_analysis_path", None)
        merged.pop("output_path", None)

    if step_name == "generate-all-paths":
        # CLI flags：
        # - canonical: --canonical / -c
        # - ending_candidates: --ending-candidates / -e
        # - decision_analysis: --decision-analysis / -d
        # - output: --output / -o
        base = resolve_extract_output_dir_for_run(
            template_data=data,
            session_ui_run_id=session_ui_run_id,
            session_selected_input_path=session_selected_input_path,
        )

        base_abs = Path(base)
        if project_root is not None and not base_abs.is_absolute():
            base_abs = project_root / base_abs

        def _is_stale_ui_run_path(v: Any) -> bool:
            if not isinstance(v, str):
                return False
            sv = v.strip().replace("\\", "/")
            if not sv.startswith("runs/ui/"):
                return False
            return not sv.startswith(f"{base}/")

        canonical_ch = base_abs / "canonical_branch_chronological.json"
        canonical_rel = (
            f"{base}/canonical_branch_chronological.json"
            if canonical_ch.is_file()
            else f"{base}/canonical_branch.json"
        )

        cb = merged.get("canonical") if "canonical" in merged else None
        cb_norm = str(cb).strip().replace("\\", "/") if isinstance(cb, str) else ""
        if (
            cb is None
            or (isinstance(cb, str) and not cb.strip())
            or (isinstance(cb, str) and str(cb).strip().startswith("out/"))
            or _is_stale_ui_run_path(cb)
            or cb_norm.endswith("/canonical_branch.json")
            or cb_norm == "canonical_branch.json"
        ):
            merged["canonical"] = canonical_rel

        ec = merged.get("ending_candidates") if "ending_candidates" in merged else None
        if (
            ec is None
            or (isinstance(ec, str) and not ec.strip())
            or (isinstance(ec, str) and str(ec).strip().startswith("out/"))
            or _is_stale_ui_run_path(ec)
        ):
            merged["ending_candidates"] = f"{base}/ending_candidates.json"

        da = merged.get("decision_analysis") if "decision_analysis" in merged else None
        if (
            da is None
            or (isinstance(da, str) and not da.strip())
            or (isinstance(da, str) and str(da).strip().startswith("out/"))
            or _is_stale_ui_run_path(da)
        ):
            merged["decision_analysis"] = f"{base}/decision_points_analysis.json"

        out = merged.get("output") if "output" in merged else None
        if (
            out is None
            or (isinstance(out, str) and not out.strip())
            or (isinstance(out, str) and str(out).strip().startswith("out/"))
            or _is_stale_ui_run_path(out)
        ):
            merged["output"] = f"{base}/all_paths"

        # 避免把错误 key 传给 params_to_cli_args
        merged.pop("ending_candidates_path", None)
        merged.pop("canonical_branch_path", None)
        merged.pop("decision_analysis_path", None)
        merged.pop("output_path", None)

    if step_name == "complete-all-path-events":
        base = resolve_extract_output_dir_for_run(
            template_data=data,
            session_ui_run_id=session_ui_run_id,
            session_selected_input_path=session_selected_input_path,
        )
        base_abs = Path(base)
        if project_root is not None and not base_abs.is_absolute():
            base_abs = project_root / base_abs

        def _is_stale_ui_run_path(v: Any) -> bool:
            if not isinstance(v, str):
                return False
            sv = v.strip().replace("\\", "/")
            if not sv.startswith("runs/ui/"):
                return False
            return not sv.startswith(f"{base}/")

        raw_in = merged.get("input_dir") if "input_dir" in merged else None
        alt_in = merged.get("input") if "input" in merged else None
        eff_in = raw_in if raw_in is not None else alt_in
        if (
            eff_in is None
            or (isinstance(eff_in, str) and not str(eff_in).strip())
            or (isinstance(eff_in, str) and str(eff_in).strip().startswith("out/"))
            or (isinstance(eff_in, str) and _is_stale_ui_run_path(str(eff_in)))
        ):
            merged["input_dir"] = f"{base}/all_paths"
        elif isinstance(eff_in, str) and eff_in.strip():
            merged["input_dir"] = eff_in.strip()
        merged.pop("input", None)

        raw_out = merged.get("output_dir") if "output_dir" in merged else None
        alt_out = merged.get("output") if "output" in merged else None
        eff_out = raw_out if raw_out is not None else alt_out
        if (
            eff_out is None
            or (isinstance(eff_out, str) and not str(eff_out).strip())
            or (isinstance(eff_out, str) and str(eff_out).strip().startswith("out/"))
            or (isinstance(eff_out, str) and _is_stale_ui_run_path(str(eff_out)))
        ):
            merged["output_dir"] = f"{base}/all_paths_completed"
        elif isinstance(eff_out, str) and eff_out.strip():
            merged["output_dir"] = eff_out.strip()
        merged.pop("output", None)
        merged.pop("output_path", None)

    if step_name == "generate-content":
        # CLI flags：
        # - input: --input / -i
        # - output: --output / -o
        # - personas: --personas / -p
        # 历史模板可能写成 input_dir/output_dir，这里统一归一化为 input/output。
        base = resolve_extract_output_dir_for_run(
            template_data=data,
            session_ui_run_id=session_ui_run_id,
            session_selected_input_path=session_selected_input_path,
        )

        def _is_stale_ui_run_path(v: Any) -> bool:
            if not isinstance(v, str):
                return False
            sv = v.strip().replace("\\", "/")
            if not sv.startswith("runs/ui/"):
                return False
            return not sv.startswith(f"{base}/")

        in_dir = merged.get("input_dir") if "input_dir" in merged else None
        if in_dir is not None and "input" not in merged:
            merged["input"] = in_dir
        out_dir = merged.get("output_dir") if "output_dir" in merged else None
        if out_dir is not None and "output" not in merged:
            merged["output"] = out_dir

        inp = merged.get("input") if "input" in merged else None
        if (
            inp is None
            or (isinstance(inp, str) and not inp.strip())
            or (isinstance(inp, str) and str(inp).strip().startswith("out/"))
            or _is_stale_ui_run_path(inp)
        ):
            merged["input"] = f"{base}/all_paths_completed"

        out = merged.get("output") if "output" in merged else None
        if (
            out is None
            or (isinstance(out, str) and not out.strip())
            or (isinstance(out, str) and str(out).strip().startswith("out/"))
            or _is_stale_ui_run_path(out)
        ):
            merged["output"] = f"{base}/enhanced_paths"

        personas = merged.get("personas") if "personas" in merged else None
        if (
            personas is None
            or (isinstance(personas, str) and not personas.strip())
            or (isinstance(personas, str) and str(personas).strip().startswith("out/"))
            or _is_stale_ui_run_path(personas)
        ):
            merged["personas"] = f"{base}/character_personas.json"

        # 避免误传成 --input-dir/--output-dir（CLI 无此长选项）
        merged.pop("input_dir", None)
        merged.pop("output_dir", None)

    if step_name == "generate-renpy-scripts":
        base = resolve_extract_output_dir_for_run(
            template_data=data,
            session_ui_run_id=session_ui_run_id,
            session_selected_input_path=session_selected_input_path,
        )

        def _is_stale_ui_run_path(v: Any) -> bool:
            if not isinstance(v, str):
                return False
            sv = v.strip().replace("\\", "/")
            if not sv.startswith("runs/ui/"):
                return False
            return not sv.startswith(f"{base}/")

        raw_ep = merged.get("enhanced_paths")
        if raw_ep is None:
            raw_ep = merged.get("paths")
        if raw_ep is None:
            raw_ep = merged.get("input")
        if (
            raw_ep is None
            or (isinstance(raw_ep, str) and not str(raw_ep).strip())
            or (isinstance(raw_ep, str) and str(raw_ep).strip().startswith("out/"))
            or (isinstance(raw_ep, str) and _is_stale_ui_run_path(str(raw_ep)))
        ):
            merged["enhanced_paths"] = f"{base}/enhanced_paths"
        elif isinstance(raw_ep, str) and raw_ep.strip():
            merged["enhanced_paths"] = raw_ep.strip()
        merged.pop("paths", None)
        merged.pop("input", None)

        personas = merged.get("personas") if "personas" in merged else None
        if (
            personas is None
            or (isinstance(personas, str) and not str(personas).strip())
            or (isinstance(personas, str) and str(personas).strip().startswith("out/"))
            or (isinstance(personas, str) and _is_stale_ui_run_path(str(personas)))
        ):
            merged["personas"] = f"{base}/character_personas.json"
        elif isinstance(personas, str) and personas.strip():
            merged["personas"] = personas.strip()

        ent = merged.get("entities") if "entities" in merged else None
        if (
            ent is None
            or (isinstance(ent, str) and not str(ent).strip())
            or (isinstance(ent, str) and str(ent).strip().startswith("out/"))
            or (isinstance(ent, str) and _is_stale_ui_run_path(str(ent)))
        ):
            merged["entities"] = f"{base}/entities.json"
        elif isinstance(ent, str) and ent.strip():
            merged["entities"] = ent.strip()

        gd = merged.get("game_dir")
        if isinstance(gd, str) and _is_stale_ui_run_path(gd):
            merged.pop("game_dir", None)

    if step_name == "import-assets":
        base = resolve_extract_output_dir_for_run(
            template_data=data,
            session_ui_run_id=session_ui_run_id,
            session_selected_input_path=session_selected_input_path,
        )

        def _is_stale_ui_run_path_import(v: Any) -> bool:
            if not isinstance(v, str):
                return False
            sv = v.strip().replace("\\", "/")
            if not sv.startswith("runs/ui/"):
                return False
            return not sv.startswith(f"{base}/")

        gd_ia = merged.get("game_dir")
        if isinstance(gd_ia, str) and _is_stale_ui_run_path_import(gd_ia):
            merged.pop("game_dir", None)

        # 与 generate-renpy-scripts 一致：资源导入末尾会再次调用 generate_content_and_scripts，
        # 若仍用 out/enhanced_paths 会覆盖成错误剧情。
        def _is_stale_ui_run_path_ia(v: Any) -> bool:
            if not isinstance(v, str):
                return False
            sv = v.strip().replace("\\", "/")
            if not sv.startswith("runs/ui/"):
                return False
            return not sv.startswith(f"{base}/")

        raw_ep = merged.get("enhanced_paths")
        if raw_ep is None:
            raw_ep = merged.get("paths")
        if raw_ep is None:
            raw_ep = merged.get("input")
        if (
            raw_ep is None
            or (isinstance(raw_ep, str) and not str(raw_ep).strip())
            or (isinstance(raw_ep, str) and str(raw_ep).strip().startswith("out/"))
            or (isinstance(raw_ep, str) and _is_stale_ui_run_path_ia(str(raw_ep)))
        ):
            merged["enhanced_paths"] = f"{base}/enhanced_paths"
        elif isinstance(raw_ep, str) and raw_ep.strip():
            merged["enhanced_paths"] = raw_ep.strip()
        merged.pop("paths", None)
        merged.pop("input", None)

        personas = merged.get("personas") if "personas" in merged else None
        if (
            personas is None
            or (isinstance(personas, str) and not str(personas).strip())
            or (isinstance(personas, str) and str(personas).strip().startswith("out/"))
            or (isinstance(personas, str) and _is_stale_ui_run_path_ia(str(personas)))
        ):
            merged["personas"] = f"{base}/character_personas.json"
        elif isinstance(personas, str) and personas.strip():
            merged["personas"] = personas.strip()

        ent = merged.get("entities") if "entities" in merged else None
        if (
            ent is None
            or (isinstance(ent, str) and not str(ent).strip())
            or (isinstance(ent, str) and str(ent).strip().startswith("out/"))
            or (isinstance(ent, str) and _is_stale_ui_run_path_ia(str(ent)))
        ):
            merged["entities"] = f"{base}/entities.json"
        elif isinstance(ent, str) and ent.strip():
            merged["entities"] = ent.strip()

    return merged


def ordered_pipeline_step_names(base_config_path: Path) -> list[str]:
    """按模板 JSON 中 steps 的键顺序列出内置步骤，其余 BUILTIN 步骤按名字排在后面。"""
    from src.pipeline.builtin_steps import BUILTIN_STEP_SPECS

    data = load_json(base_config_path)
    keys = list((data.get("steps") or {}).keys())
    builtin = set(BUILTIN_STEP_SPECS.keys())
    ordered = [k for k in keys if k in builtin]
    tail = sorted(builtin - set(ordered))
    return ordered + tail


def run_extract_events_for_ui(
    project_root: Path,
    params: dict[str, Any],
    *,
    on_progress: Callable[[int, int], None] | None = None,
    prompt_path_by_slot: dict[str, str] | None = None,
) -> int:
    """
    在项目根目录下进程内执行 extract-events（与 CLI 行为一致），便于前端进度回调。

    params：input / input_path、chunk_size、chunk_overlap、max_chunks；
    可选 output_dir（相对项目根或绝对路径的目录），默认 ``out``，产物为其中的 extract_chain.json。
    若 input 指向 epub，会先经 prepare_input_text 转成 text_variants 下的 txt 再抽事件。
    """
    src_dir = str(project_root / "src")
    if src_dir not in sys.path:
        sys.path.insert(0, src_dir)

    from event_graph.scripts.extract_events import run_extract

    prev_cwd = os.getcwd()
    os.chdir(project_root)

    raw_in = params.get("input") or params.get("input_path") or "王佛脱险记.txt"
    inp = resolve_input_path(project_root, Path(str(raw_in)))

    if inp.suffix.lower() == ".epub":
        rel_txt, _slug = prepare_input_text(project_root, inp)
        inp = resolve_input_path(project_root, Path(rel_txt))

    od_raw = params.get("output_dir")
    if od_raw is not None and str(od_raw).strip():
        out_dir = resolve_input_path(project_root, Path(str(od_raw).strip()))
    else:
        out_dir = project_root / "out"

    chunk_size = int(params.get("chunk_size", 800))
    chunk_overlap = int(params.get("chunk_overlap", 160))
    mc = params.get("max_chunks")
    max_chunks: int | None
    if mc is None:
        max_chunks = None
    else:
        max_chunks = int(mc)

    from src.core.prompt_overrides import use_prompt_path_overrides

    with use_prompt_path_overrides(prompt_path_by_slot):
        try:
            _chained, _errors = run_extract(
                inp,
                chunk_size=chunk_size,
                chunk_overlap=chunk_overlap,
                max_chunks=max_chunks,
                out_dir=out_dir,
                progress_callback=on_progress,
            )
        finally:
            os.chdir(prev_cwd)
    return 0


def run_extract_relations_for_ui(
    project_root: Path,
    params: dict[str, Any],
    *,
    on_progress: Callable[[int, int], None] | None = None,
    prompt_path_by_slot: dict[str, str] | None = None,
) -> int:
    """
    在项目根目录下进程内执行 extract-relations（与 CLI 的 ``--json`` / ``--output`` 一致），
    便于 Streamlit 进度条。

    params：``json`` 事件链路径、``output`` 关系写出路径（与 Typer 选项一致；亦可用
    ``json_path`` / ``output_path`` 别名）。
    """
    src_dir = str(project_root / "src")
    if src_dir not in sys.path:
        sys.path.insert(0, src_dir)

    from core.llm_client import AsyncLLMClient
    from event_graph.scripts.extract_relations import extract_relations

    prev_cwd = os.getcwd()
    os.chdir(project_root)

    raw_json = (
        params.get("json")
        or params.get("json_path")
        or "out/extract_chain.json"
    )
    json_path = resolve_input_path(project_root, Path(str(raw_json)))

    raw_out = params.get("output") or params.get("output_path")
    if raw_out is not None and str(raw_out).strip():
        output_path = resolve_input_path(project_root, Path(str(raw_out).strip()))
    else:
        output_path = resolve_input_path(project_root, Path("out") / "extract_relations.json")

    from src.core.prompt_overrides import use_prompt_path_overrides

    with use_prompt_path_overrides(prompt_path_by_slot):
        try:
            if not json_path.exists():
                raise FileNotFoundError(f"未找到事件链 JSON: {json_path}")

            data = json.loads(json_path.read_text(encoding="utf-8"))
            events = data.get("new_events", [])
            if not events:
                raise ValueError("事件链 JSON 中 new_events 为空，请先运行 extract-events")

            llm_client = AsyncLLMClient.create_default()
            relations = extract_relations(
                events,
                llm_client,
                progress_callback=on_progress,
            )

            output_data = {"relations": relations}
            output_path.parent.mkdir(parents=True, exist_ok=True)
            output_path.write_text(
                json.dumps(output_data, ensure_ascii=False, indent=2) + "\n",
                encoding="utf-8",
            )
        finally:
            os.chdir(prev_cwd)
    return 0


def run_extract_events_and_relations_for_ui(
    project_root: Path,
    params: dict[str, Any],
    *,
    on_events_progress: Callable[[int, int], None] | None = None,
    on_relations_progress: Callable[[int, int], None] | None = None,
    prompt_path_by_slot: dict[str, str] | None = None,
) -> int:
    """
    先执行 extract-events，再在同一 ``output_dir`` 下对 ``extract_chain.json`` 执行 extract-relations。

    ``params`` 与单步 ``extract-events`` 一致（含 ``input`` / ``output_dir`` / chunk 等）；
    关系结果路径固定为 ``<output_dir>/extract_relations.json``。
    """
    from src.core.prompt_overrides import use_prompt_path_overrides

    with use_prompt_path_overrides(prompt_path_by_slot):
        p = dict(params)
        run_extract_events_for_ui(
            project_root, p, on_progress=on_events_progress, prompt_path_by_slot=None
        )
        od_raw = p.get("output_dir")
        if not od_raw or not str(od_raw).strip():
            od_raw = "out"
        base = str(Path(str(od_raw).strip())).strip().rstrip("/\\")
        rel_params: dict[str, Any] = {
            "json": f"{base}/extract_chain.json",
            "output": f"{base}/extract_relations.json",
        }
        return run_extract_relations_for_ui(
            project_root,
            rel_params,
            on_progress=on_relations_progress,
            prompt_path_by_slot=None,
        )


def run_generate_mentions_and_entities_for_ui(
    project_root: Path,
    params: dict[str, Any],
    *,
    prompt_path_by_slot: dict[str, str] | None = None,
) -> int:
    """
    串行执行 ``generate-mentions`` → ``generate-entities``（同一 ``output_dir``）。
    与 ``python -m src.cli generate-mentions-and-entities`` 行为一致。
    """
    from src.pipeline.builtin_steps import params_to_cli_args

    p = dict(params)
    od_raw = p.get("output_dir")
    if not od_raw or not str(od_raw).strip():
        od_raw = "out"
    base = str(Path(str(od_raw).strip())).rstrip("/\\")

    inp = p.get("input")
    if inp is None or (isinstance(inp, str) and not str(inp).strip()):
        inp = f"{base}/extract_chain.json"
    else:
        inp = str(inp).strip()

    mentions_out = f"{base}/mentions.json"
    entities_out = f"{base}/entities.json"

    m_params: dict[str, Any] = {"input": inp, "output": mentions_out}
    cmd_m = [
        sys.executable,
        "-m",
        "src.cli",
        "generate-mentions",
        *params_to_cli_args(m_params),
    ]
    env_m = _subprocess_ui_env(prompt_path_by_slot)
    r1 = subprocess.run(cmd_m, cwd=str(project_root), env=env_m)
    if r1.returncode != 0:
        return r1.returncode

    e_params: dict[str, Any] = {"input": mentions_out, "output": entities_out}
    cmd_e = [
        sys.executable,
        "-m",
        "src.cli",
        "generate-entities",
        *params_to_cli_args(e_params),
    ]
    r2 = subprocess.run(cmd_e, cwd=str(project_root), env=_subprocess_ui_env())
    return r2.returncode


def run_extract_states_and_baseline_for_ui(
    project_root: Path,
    params: dict[str, Any],
    *,
    log_placeholder: Any = None,
    on_substep: Callable[[int, int, str], None] | None = None,
    prompt_path_by_slot: dict[str, str] | None = None,
) -> int:
    """
    顺序：world_states → character_states → relationship_states → state_baseline；
    输入/产出均在同一 ``output_dir`` 下（与 ``runs/.../out`` 约定一致）。

    ``on_substep(i, n, title)``：启动第 ``i+1`` 个子进程前调用（``i`` 从 0 到 ``n-1``），全部成功后
    再调用 ``(n, n, "完成")``。若传入 ``log_placeholder``，子进程标准输出会流式写入其中。
    """
    p = dict(params)
    od_raw = p.get("output_dir")
    if not od_raw or not str(od_raw).strip():
        od_raw = "out"
    base = str(Path(str(od_raw).strip())).rstrip("/\\")

    def _opt(key: str, default: str) -> str:
        v = p.get(key)
        if v is None or (isinstance(v, str) and not str(v).strip()):
            return default
        return str(v).strip()

    chain = _opt("events", f"{base}/extract_chain.json")
    mentions = _opt("mentions", f"{base}/mentions.json")
    entities = _opt("entities", f"{base}/entities.json")
    world_out = f"{base}/world_states.json"
    char_out = f"{base}/character_states.json"
    rel_out = f"{base}/relationship_states.json"
    baseline_out = f"{base}/state_baseline.json"

    steps: list[list[str]] = [
        [
            sys.executable,
            "-m",
            "src.cli",
            "extract-world-states",
            "--events",
            chain,
            "--output",
            world_out,
        ],
        [
            sys.executable,
            "-m",
            "src.cli",
            "extract-character-states",
            "--events",
            chain,
            "--mentions",
            mentions,
            "--entities",
            entities,
            "--output",
            char_out,
        ],
        [
            sys.executable,
            "-m",
            "src.cli",
            "extract-relationship-states",
            "--events",
            chain,
            "--mentions",
            mentions,
            "--entities",
            entities,
            "--output",
            rel_out,
        ],
        [
            sys.executable,
            "-m",
            "src.cli",
            "init-state-baseline",
            "--world-states",
            world_out,
            "--character-states",
            char_out,
            "--relationship-states",
            rel_out,
            "--output",
            baseline_out,
        ],
    ]
    sub_labels = ("世界状态", "人物状态", "关系状态", "state baseline")
    lines_acc: list[str] | None = [] if log_placeholder is not None else None

    for i, cmd in enumerate(steps):
        if on_substep is not None:
            on_substep(i, len(steps), sub_labels[i])
        if lines_acc is not None and log_placeholder is not None:
            lines_acc.append(f"--- {sub_labels[i]} ---")
            log_placeholder.code("\n".join(lines_acc[-120:]), language="text")
        if log_placeholder is not None and lines_acc is not None:
            rc = run_subprocess_stream_to_log(
                project_root,
                cmd,
                log_placeholder,
                lines_acc,
                on_stdout_line=None,
                tail=120,
                prompt_path_by_slot=prompt_path_by_slot,
            )
        else:
            r = subprocess.run(
                cmd,
                cwd=str(project_root),
                env=_subprocess_ui_env(prompt_path_by_slot),
            )
            rc = r.returncode
        if rc != 0:
            return rc
    if on_substep is not None:
        on_substep(len(steps), len(steps), "完成")
    return 0


def run_subprocess_stream_to_log(
    project_root: Path,
    cmd: list[str],
    log_placeholder: Any,
    lines_acc: list[str],
    *,
    on_stdout_line: Callable[[str], None] | None = None,
    tail: int = 120,
    prompt_path_by_slot: dict[str, str] | None = None,
) -> int:
    """运行子进程，将 stdout/stderr 逐行追加到 ``lines_acc``，并刷新 ``log_placeholder``（保留尾部 ``tail`` 行）。"""
    process = subprocess.Popen(
        cmd,
        cwd=str(project_root),
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        encoding="utf-8",
        errors="replace",
        env=_subprocess_ui_env(prompt_path_by_slot),
        bufsize=1,
    )
    assert process.stdout is not None
    for line in process.stdout:
        raw = line.rstrip("\n")
        lines_acc.append(raw)
        if on_stdout_line is not None:
            on_stdout_line(raw)
        log_placeholder.code("\n".join(lines_acc[-tail:]), language="text")
    return process.wait()


def run_aggregate_characters_and_personas_for_ui(
    project_root: Path,
    params: dict[str, Any],
    *,
    log_placeholder: Any = None,
    on_substep: Callable[[int, int, str], None] | None = None,
) -> int:
    """
    先 ``aggregate-characters`` 再 ``aggregate-personas``；输入/产出默认均在同一 ``output_dir`` 下。
    """
    p = dict(params)
    od_raw = p.get("output_dir")
    if not od_raw or not str(od_raw).strip():
        od_raw = "out"
    base = str(Path(str(od_raw).strip())).rstrip("/\\")

    def _g(key: str, default: str) -> str:
        v = p.get(key)
        if v is None or (isinstance(v, str) and not str(v).strip()):
            return default
        return str(v).strip()

    events = _g("events", f"{base}/extract_chain.json")
    char_states = _g("char_states", f"{base}/character_states.json")
    rel_states = _g("rel_states", f"{base}/relationship_states.json")
    entities = _g("entities", f"{base}/entities.json")
    mentions = _g("mentions", f"{base}/mentions.json")
    profiles_out = _g("profiles", f"{base}/character_profiles.json")
    personas_out = _g("personas_output", f"{base}/character_personas.json")
    use_rules = bool(p.get("use_rules") or p.get("rules"))

    cmd_char: list[str] = [
        sys.executable,
        "-m",
        "src.cli",
        "aggregate-characters",
        "--events",
        events,
        "--char-states",
        char_states,
        "--rel-states",
        rel_states,
        "--entities",
        entities,
        "--mentions",
        mentions,
        "--output",
        profiles_out,
    ]
    if use_rules:
        cmd_char.append("--rules")
    cmd_per = [
        sys.executable,
        "-m",
        "src.cli",
        "aggregate-personas",
        "--profiles",
        profiles_out,
        "--output",
        personas_out,
    ]
    steps_cmds = [cmd_char, cmd_per]
    sub_labels = ("人物画像", "静态人设")
    lines_acc: list[str] | None = [] if log_placeholder is not None else None

    for i, cmd in enumerate(steps_cmds):
        if on_substep is not None:
            on_substep(i, len(steps_cmds), sub_labels[i])
        if lines_acc is not None and log_placeholder is not None:
            lines_acc.append(f"--- {sub_labels[i]} ---")
            log_placeholder.code("\n".join(lines_acc[-120:]), language="text")
        if log_placeholder is not None and lines_acc is not None:
            rc = run_subprocess_stream_to_log(
                project_root,
                cmd,
                log_placeholder,
                lines_acc,
                on_stdout_line=None,
                tail=120,
                prompt_path_by_slot=None,
            )
        else:
            r = subprocess.run(cmd, cwd=str(project_root), env=_subprocess_ui_env())
            rc = r.returncode
        if rc != 0:
            return rc
    if on_substep is not None:
        on_substep(len(steps_cmds), len(steps_cmds), "完成")
    return 0


def run_single_cli_step(
    project_root: Path,
    command_name: str,
    params: dict[str, Any],
    log_placeholder,
    *,
    on_stdout_line: Callable[[str], None] | None = None,
    prompt_path_by_slot: dict[str, str] | None = None,
) -> int:
    """执行单条 `python -m src.cli <command_name> ...`，日志写入 log_placeholder。返回退出码。

    ``on_stdout_line``：每收到一行子进程输出时回调（用于解析 ``[mentions] i/n`` 等驱动 Streamlit 进度条）。
    """
    from src.pipeline.builtin_steps import params_to_cli_args

    cmd = [
        sys.executable,
        "-m",
        "src.cli",
        command_name,
        *params_to_cli_args(params, command_name=command_name),
    ]
    lines: list[str] = []
    return run_subprocess_stream_to_log(
        project_root,
        cmd,
        log_placeholder,
        lines,
        on_stdout_line=on_stdout_line,
        tail=120,
        prompt_path_by_slot=prompt_path_by_slot,
    )


def format_cli_command_preview(
    command_name: str,
    params: dict[str, Any],
) -> str:
    """生成可复制的命令行预览（不含 python -m 前缀时可自行加）。"""
    from src.pipeline.builtin_steps import params_to_cli_args
    import shlex

    parts = ["python", "-m", "src.cli", command_name] + params_to_cli_args(
        params, command_name=command_name
    )
    return " ".join(shlex.quote(str(x)) for x in parts)
