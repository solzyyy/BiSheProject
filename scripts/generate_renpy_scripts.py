"""
Ren'Py 脚本生成主程序 🎮✨

管道：从指定目录加载 enhanced 路径 JSON → 写入已有 Ren'Py 工程里的 ``game`` 目录
（characters.rpy、paths/、endings/、script.rpy 等）。不调用 LLM；内容增强请用 ``generate-content``。

**不会**用本脚本替代「在 Ren'Py Launcher 里新建游戏工程」。标准做法是：

1. 打开 ``renpy.exe`` → **Create New Project**，按向导建一个可运行工程；
2. 记下工程路径（Launcher 里显示的项目文件夹），其下必有子目录 **``game/``**；
3. 运行本工具时把 **``--game-dir``** 指到该 **``game``** 文件夹（绝对路径或相对项目根均可），
   即把生成的剧本**导入/写入**到这个工程里。

若使用本仓库自带工程，可直接 **``--game-dir wangfo/game``**（已含 options.rpy / gui 等）。

用法：
    python scripts/generate_renpy_scripts.py
    python scripts/generate_renpy_scripts.py --enhanced-paths runs/ui/demo/out/enhanced_paths \\
        --game-dir D:/RenPyProjects/我的游戏/game
"""

import argparse
import json
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional, Set, Tuple

# 添加项目根目录到路径
project_root = Path(__file__).parent.parent
sys.path.insert(0, str(project_root))

from src.renpy.character_generator import CharacterGenerator, collect_speakers_from_path_data
from src.renpy.path_script_generator import PathScriptGenerator
from src.renpy.ending_script_generator import EndingScriptGenerator
from src.renpy.main_script_updater import MainScriptUpdater

try:
    from src.entity_resolver import build_mention_to_entity, get_canonical_name
except ImportError:
    build_mention_to_entity = None
    get_canonical_name = None


def _resolve_under_root(root: Path, p: Path | str) -> Path:
    path = Path(p)
    return path if path.is_absolute() else (root / path)


def ensure_renpy_game_layout(game_dir: Path) -> None:
    """
    仅确保 ``game`` 目录及本管道用到的子目录 ``paths/``、``endings/`` 存在（mkdir）。

    **不是**创建完整 Ren'Py 工程；完整工程请用 Launcher「创建工程」后再把 ``--game-dir`` 指到其 ``game/``。
    """
    game_dir.mkdir(parents=True, exist_ok=True)
    (game_dir / "paths").mkdir(parents=True, exist_ok=True)
    (game_dir / "endings").mkdir(parents=True, exist_ok=True)


def load_enhanced_paths(enhanced_dir: Path) -> Tuple[List[Dict[str, Any]], Dict[str, Dict[str, Any]]]:
    """
    从 enhanced_dir/*.json 加载路径数据（即增强后的完整内容）。
    - 仅扫描 *.json，跳过 index.json。
    - 每条路径的 renpy_label 取自 JSON 内 renpy_label 或文件名（stem）。
    - 顺序：canonical_path 在前，其余按 renpy_label 排序。
    """
    path_files = [f for f in enhanced_dir.glob("*.json") if f.name != "index.json"]
    loaded: List[Tuple[Dict[str, Any], str]] = []
    for path_file in path_files:
        try:
            with open(path_file, "r", encoding="utf-8") as f:
                path_data = json.load(f)
        except Exception as e:
            print(f"   跳过（无法读取）: {path_file.name} - {e}")
            continue
        renpy_label = path_data.get("renpy_label") or path_data.get("path_id") or path_file.stem
        path_data["renpy_label"] = renpy_label
        loaded.append((path_data, renpy_label))
    loaded.sort(key=lambda x: (0 if x[1] == "canonical_path" else 1, x[1]))
    content_result = [p[0] for p in loaded]
    path_map = {p[1]: p[0] for p in loaded}
    return content_result, path_map


def generate_content_and_scripts(
    *,
    project_root: Optional[Path] = None,
    enhanced_dir: Path | str | None = None,
    personas_file: Path | str | None = None,
    wangfo_game_dir: Path | str | None = None,
    entities_file: Path | str | None = None,
) -> None:
    """
    从 enhanced 目录加载内容并生成 Ren'Py 脚本（不调用 LLM）。

    Args:
        project_root: 项目根；默认本仓库根目录。
        enhanced_dir: 增强路径 JSON 目录（与 generate-content 的 --output 一致）。
        personas_file: character_personas.json。
        wangfo_game_dir: 已在 Launcher 中建好的工程里的 **``game``** 目录；写入 characters.rpy、paths/ 等。
            若目录不存在仍会 mkdir 并建 ``paths/``、``endings/``，但缺少 options.rpy 时无法直接运行游戏。
        entities_file: 可选；显式指定 entities.json。为 None 时若存在 ``<project_root>/out/entities.json`` 则使用。
    """
    root = project_root or Path(__file__).resolve().parent.parent
    ed = _resolve_under_root(root, enhanced_dir or (root / "out" / "enhanced_paths"))
    pf = _resolve_under_root(root, personas_file or (root / "out" / "character_personas.json"))
    wg = _resolve_under_root(root, wangfo_game_dir or (root / "wangfo" / "game"))

    characters_file = wg / "characters.rpy"
    paths_dir = wg / "paths"
    endings_dir = wg / "endings"
    script_file = wg / "script.rpy"

    entities_path: str | None = None
    if entities_file is not None:
        ef = _resolve_under_root(root, entities_file)
        if ef.is_file():
            entities_path = str(ef)
    else:
        default_e = root / "out" / "entities.json"
        if default_e.is_file():
            entities_path = str(default_e)

    print(f"[Step 0] 从增强目录加载路径数据：{ed}")
    if not ed.exists():
        print(f"   错误：目录不存在 {ed}")
        return

    ensure_renpy_game_layout(wg)
    if not (wg / "options.rpy").is_file():
        print(
            "   ━━ Ren'Py 工程提示 ━━\n"
            f"   当前 --game-dir 指向：{wg}\n"
            "   此处没有检测到 options.rpy，说明多半还不是「Launcher 里建好的完整工程」。\n"
            "   建议：① 在 Ren'Py Launcher 中选 Create New Project 新建游戏；\n"
            "        ② 将本命令的 --game-dir 改为「该工程文件夹下的 game 子目录」再运行（即把剧本导入该工程）。\n"
            "   或使用本仓库已有工程：--game-dir wangfo/game 。\n"
            "   下方仍会写入 paths/、endings/ 等，但若缺少官方模板文件，Launcher 里可能无法正常启动游戏。"
        )

    content_result, path_map = load_enhanced_paths(ed)
    print(f"   已加载 {len(content_result)} 条路径\n")

    narrative_perspective = None
    index_file = ed / "index.json"
    if index_file.exists():
        try:
            with open(index_file, "r", encoding="utf-8") as f:
                index_data = json.load(f)
            narrative_perspective = index_data.get("narrative_perspective") or None
        except Exception:
            pass
    if not narrative_perspective:
        for content_context_file in (
            ed.parent / "content_context.json",
            root / "out" / "content_context.json",
        ):
            if content_context_file.exists():
                try:
                    with open(content_context_file, "r", encoding="utf-8") as f:
                        content_context = json.load(f)
                    narrative_perspective = content_context.get("narrative_perspective") or None
                    if narrative_perspective:
                        break
                except Exception:
                    pass
    if narrative_perspective:
        print(f"   叙述人称：{narrative_perspective}\n")

    print("[Step 1] 生成角色定义...")
    if not pf.is_file():
        print(f"   错误：人设文件不存在 {pf}")
        return
    raw_speakers = collect_speakers_from_path_data(content_result)
    extra_speakers = set(raw_speakers)
    if build_mention_to_entity and get_canonical_name and entities_path:
        entities_p = Path(entities_path)
        if entities_p.is_file():
            mention_to_entity = build_mention_to_entity(entities_path=str(entities_p))
            extra_speakers = set()
            for s in raw_speakers:
                canonical = get_canonical_name(s, mention_to_entity) or s
                extra_speakers.add(canonical)
    char_generator = CharacterGenerator(
        personas_file=str(pf),
        output_file=str(characters_file),
        extra_speakers=extra_speakers,
        sprite_vars_file=str(wg / "sprite_vars.txt"),
    )
    char_generator.save()
    print("")

    print("[Step 2] 生成路径脚本...")
    path_script_generator = PathScriptGenerator(char_generator, path_map, entities_path=entities_path)
    for path_data in content_result:
        renpy_label = path_data.get("renpy_label") or path_data.get("path_id", "unknown").replace("/", "_").replace("\\", "_")
        path_script_generator.save_path_script(path_data, paths_dir / f"{renpy_label}.rpy")
    print("")

    print("[Step 3] 生成结局脚本...")
    ending_generator = EndingScriptGenerator(char_generator)
    endings_seen: Set[str] = set()
    for path_data in content_result:
        ending = path_data.get("ending")
        if not ending:
            continue
        ending_id = ending.get("id")
        if ending_id and ending_id not in endings_seen:
            endings_seen.add(ending_id)
            ending_generator.save_ending_script(ending, endings_dir / f"ending_{ending_id}.rpy")
    print("")

    print("[Step 4] 更新主脚本...")
    MainScriptUpdater(script_file).update_script(content_result)
    print("")

    print("所有脚本生成完成！")
    print("")
    print("生成的文件：")
    print(f"   - {characters_file}")
    print(f"   - {paths_dir}/*.rpy ({len(content_result)} 个文件)")
    print(f"   - {endings_dir}/*.rpy ({len(endings_seen)} 个文件)")
    print(f"   - {script_file}")


def main() -> None:
    parser = argparse.ArgumentParser(
        description=(
            "从 enhanced_paths 生成 Ren'Py 剧本并写入「已有工程」的 game 目录。"
            "请先在 Ren'Py Launcher 创建工程，再把 --game-dir 指到该工程下的 game 文件夹。"
        )
    )
    parser.add_argument(
        "--enhanced-paths",
        "-i",
        type=str,
        default=None,
        help="增强路径 JSON 目录（默认 <项目根>/out/enhanced_paths）",
    )
    parser.add_argument(
        "--personas",
        "-p",
        type=str,
        default=None,
        help="character_personas.json（默认 <项目根>/out/character_personas.json）",
    )
    parser.add_argument(
        "--game-dir",
        "-g",
        type=str,
        default=None,
        help="Ren'Py 工程内的 game 目录（默认 <项目根>/wangfo/game；新项目请指 Launcher 所建工程下的 game）",
    )
    parser.add_argument(
        "--entities",
        "-e",
        type=str,
        default=None,
        help="entities.json（可选；默认尝试 <项目根>/out/entities.json）",
    )
    args = parser.parse_args()
    root = Path(__file__).resolve().parent.parent
    generate_content_and_scripts(
        project_root=root,
        enhanced_dir=args.enhanced_paths,
        personas_file=args.personas,
        wangfo_game_dir=args.game_dir,
        entities_file=args.entities,
    )


if __name__ == "__main__":
    print("开始生成 Ren'Py 脚本...")
    print("")
    main()
