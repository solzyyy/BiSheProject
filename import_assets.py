# -*- coding: utf-8 -*-
"""将立绘、Fantasy GUI、Free Sky 背景、豆包场景导入到 wangfo 项目。请在项目根目录运行: python import_assets.py

所有第三方资源统一存放在 assets/ 下：
  assets/sprites/        立绘 PNG（*-立绘生成-remove-preview.png）
  assets/scenes/         豆包场景 PNG（xxx (n).png）
  assets/Fantasy_project/  GUI 主题
  assets/Free Sky Backgrounds/  天空背景
  assets/Japanese Music Pack/   游戏音乐

立绘（自动发现）：
  - 扫描 assets/sprites/ 中后缀为「立绘生成-remove-preview」的 PNG
  - 根据文件名前缀得到人物（前缀→变量名见 NAME_TO_VAR），复制为 game/images/{变量}_sprite.png
  - 生成 game/images_sprites.rpy 和 game/sprite_vars.txt
  - character_generator 读 sprite_vars.txt，在 characters.rpy 里为这些角色加上 image= 属性

豆包场景：
  - 扫描 assets/scenes/ 中「xxx (n).png」，复制为 bg_scene_01.png ... 并生成 images_doubao.rpy

事件→场景映射（本脚本为唯一数据源，并由本脚本写入剧本）：
  - 本脚本内定义事件 ID → 豆包场景的映射
  - 运行后写入 wangfo/game/event_scene_map.json，并触发 Ren'Py 脚本生成

游戏音乐：
  - 从 assets/Japanese Music Pack 中查找「Voice Of Evening」，复制到 wangfo/game/audio/
"""
from __future__ import print_function
import os
import re
import shutil
import json

ROOT = os.path.dirname(os.path.abspath(__file__))
# 默认目标工程（可被 main(game_dir=...) 覆盖）
WANGFO_GAME = os.path.join(ROOT, "wangfo", "game")
IMAGES = os.path.join(WANGFO_GAME, "images")
GUI_DST = os.path.join(WANGFO_GAME, "gui")
ASSETS = os.path.join(ROOT, "assets")
FANTASY_GUI = os.path.join(ASSETS, "Fantasy_project", "Fantasy_project", "game", "gui")
SKY_SRC = os.path.join(ASSETS, "Free Sky Backgrounds")
DOUBAO_SCENES_DIR = os.path.join(ASSETS, "scenes")
SPRITES_DIR = os.path.join(ASSETS, "sprites")
JAPANESE_MUSIC_PACK_DIR = os.path.join(ASSETS, "Japanese Music Pack")
AUDIO_DIR = os.path.join(WANGFO_GAME, "audio")


def _default_game_dir() -> str:
    """选择默认 Ren'Py game 目录：优先已有工程，其次回退到 ``wangfo/game``。"""
    wangfo_game = os.path.join(ROOT, "wangfo", "game")
    demo_game = os.path.join(ROOT, "demo", "game")

    # 保持历史兼容：若 wangfo/game 已存在，继续优先使用它。
    if os.path.isfile(os.path.join(wangfo_game, "script.rpy")):
        return wangfo_game
    # 常见 Demo 工程位置：demo/game。
    if os.path.isfile(os.path.join(demo_game, "script.rpy")):
        return demo_game
    return wangfo_game


def _set_game_paths(game_dir: str | None) -> None:
    """将资源写入目标 Ren'Py 工程的 ``game`` 目录（与脚本导入所选目录一致）。"""
    global WANGFO_GAME, IMAGES, GUI_DST, AUDIO_DIR
    if game_dir and str(game_dir).strip():
        WANGFO_GAME = os.path.normpath(os.path.abspath(str(game_dir).strip()))
    else:
        WANGFO_GAME = os.path.normpath(os.path.abspath(_default_game_dir()))
    IMAGES = os.path.join(WANGFO_GAME, "images")
    GUI_DST = os.path.join(WANGFO_GAME, "gui")
    AUDIO_DIR = os.path.join(WANGFO_GAME, "audio")


def _write_event_scene_map(path: str) -> None:
    """写入事件/结局到背景映射 JSON。"""
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump({"event": EVENT_SCENE_MAP, "ending": ENDING_SCENE_MAP}, f, ensure_ascii=False, indent=2)


def _ensure_canonical_entry_scene() -> bool:
    """兜底确保 canonical_path 入口在第一条可执行语句前设置背景。"""
    canonical_path = os.path.join(WANGFO_GAME, "paths", "canonical_path.rpy")
    if not os.path.isfile(canonical_path):
        return False

    with open(canonical_path, "r", encoding="utf-8") as f:
        lines = f.readlines()

    label_idx = None
    for i, line in enumerate(lines):
        if line.strip() == "label canonical_path:":
            label_idx = i
            break
    if label_idx is None:
        return False

    first_stmt_idx = None
    for i in range(label_idx + 1, len(lines)):
        s = lines[i].strip()
        if not s or s.startswith("#"):
            continue
        first_stmt_idx = i
        break

    if first_stmt_idx is not None and lines[first_stmt_idx].lstrip().startswith("scene "):
        return False

    scene_name = EVENT_SCENE_MAP.get("E1", "bg_scene_06")
    insertion = [f"    scene {scene_name}\n", "\n"]
    insert_at = label_idx + 1
    lines[insert_at:insert_at] = insertion

    with open(canonical_path, "w", encoding="utf-8") as f:
        f.writelines(lines)
    return True


def _ensure_sideimage_behind_textbox() -> bool:
    """修补 say 屏幕绘制顺序：先绘制 SideImage，再绘制 window。"""
    screens_path = os.path.join(WANGFO_GAME, "screens.rpy")
    if not os.path.isfile(screens_path):
        return False

    with open(screens_path, "r", encoding="utf-8") as f:
        lines = f.readlines()

    marker = "# import_assets: sideimage layered under textbox"
    if any(marker in ln for ln in lines):
        return False

    say_start = None
    for i, ln in enumerate(lines):
        if ln.lstrip().startswith("screen say("):
            say_start = i
            break
    if say_start is None:
        return False

    say_end = len(lines)
    for i in range(say_start + 1, len(lines)):
        s = lines[i].strip()
        if s.startswith("screen ") and not lines[i].startswith((" ", "\t")):
            say_end = i
            break

    window_i = None
    for i in range(say_start + 1, say_end):
        if lines[i].strip() == "window:":
            window_i = i
            break
    if window_i is None:
        return False

    side_i = None
    for i in range(say_start + 1, say_end):
        if "add SideImage()" in lines[i]:
            side_i = i
            break
    if side_i is None:
        return False

    # 找到包含 SideImage 的 if 代码块起止（通常是 if not renpy.variant("small")）
    block_start = side_i
    for i in range(side_i - 1, say_start, -1):
        if lines[i].strip().startswith("if "):
            if_indent = lines[i][: len(lines[i]) - len(lines[i].lstrip())]
            line_indent = lines[side_i][: len(lines[side_i]) - len(lines[side_i].lstrip())]
            if len(line_indent) > len(if_indent):
                block_start = i
            break

    base_indent = lines[block_start][: len(lines[block_start]) - len(lines[block_start].lstrip())]
    block_end = say_end
    for i in range(block_start + 1, say_end):
        stripped = lines[i].strip()
        if not stripped:
            continue
        indent = lines[i][: len(lines[i]) - len(lines[i].lstrip())]
        if len(indent) <= len(base_indent):
            block_end = i
            break

    block = lines[block_start:block_end]
    del lines[block_start:block_end]

    # 删除后索引会变化，重新定位 window
    say_end2 = len(lines)
    for i in range(say_start + 1, len(lines)):
        s = lines[i].strip()
        if s.startswith("screen ") and not lines[i].startswith((" ", "\t")):
            say_end2 = i
            break
    window_i2 = None
    for i in range(say_start + 1, say_end2):
        if lines[i].strip() == "window:":
            window_i2 = i
            break
    if window_i2 is None:
        return False

    patched_block = [base_indent + marker + "\n"] + block + ["\n"]
    lines[window_i2:window_i2] = patched_block

    with open(screens_path, "w", encoding="utf-8") as f:
        f.writelines(lines)
    return True
GAME_MUSIC_DST_NAME = "voice_of_evening.mp3"

# 立绘文件名后缀（自动发现）
# 新：前缀为人名，如 林-立绘生成-remove-preview.png
# 旧：前缀为人名+「的」，如 林的立绘生成-removebg-preview.png
SPRITE_SUFFIX = "-立绘生成-remove-preview.png"
SPRITE_SUFFIX_LEGACY = "的立绘生成-removebg-preview.png"
# 人名（文件名前缀）→ Ren'Py 变量名，与 character_generator 的 name_mapping 一致
NAME_TO_VAR = {
    "王佛": "wangfo",
    "林": "lin",
    "皇帝": "emperor",
    "天子": "emperor",
    "僧道": "sengdao",
    "卫兵": "weibing",
    "林的妻子": "lin_wife",
    "老百姓": "laobaixing",
    "达官贵人": "daguan_guiren",
    "过路人": "guoluren",
}

# 事件 ID → 豆包场景（与 images_doubao 生成的 bg_scene_xx 对应）
EVENT_SCENE_MAP = {
    "E1": "bg_scene_06",   # 有梅花的庭院
    "E2": "bg_scene_11",   # 酒馆
    "E3": "bg_scene_03",   # 庭院夜色
    "E4": "bg_scene_06",   # 梅花庭院
    "E5": "bg_scene_06",   # 宅院/离开
    "E6": "bg_scene_04",   # 旅途道路
    "E7": "bg_scene_13",   # 庙宇
    "E8": "bg_scene_12",   # 破庙暮色
    "E9": "bg_scene_12",   # 破庙/旅舍
    "E10": "bg_scene_10",  # 押解囚犯
    "E11": "bg_scene_02",  # 宫廷
    "E12": "bg_scene_14",  # 龙椅/大殿
    "E13": "bg_scene_14",
    "E14": "bg_scene_14",
    "E15": "bg_scene_14",
    "E16": "bg_scene_14",
    "E17": "bg_scene_14",
    "E18": "bg_scene_14",
    "E19": "bg_scene_14",
    "E20": "bg_scene_07",  # 画中海/海水
    "E21": "bg_scene_07",
    "E22": "bg_scene_07",
    "E23": "bg_scene_07",
    "E24": "bg_scene_07",
    "E25": "bg_scene_07",
    "E26": "bg_scene_07",
}
ENDING_SCENE_MAP = {
    "ending_symbiotic_descent": "bg_scene_07",
    "ending_violent_extinction": "bg_scene_05",
    "ending_artistic_detente": "bg_scene_05",
    "ending_accuse_painter": "bg_scene_05",
    "ending_shattered_trust_solitary_survival": "bg_scene_05",
    "ending_premature_silencing": "bg_scene_05",
    "ending_canonical_true": "bg_scene_07",
}

# 豆包场景：匹配「xxx (数字).png」，支持 (1)、(2) 等后缀
SCENE_PATTERN = re.compile(r"^(.+)\s+\((\d+)\)\.png$", re.UNICODE)



def discover_doubao_scenes():
    """扫描 assets/scenes 文件夹，返回所有豆包场景 PNG（排除立绘/removebg）。"""
    seen = set()
    scenes = []
    if not os.path.isdir(DOUBAO_SCENES_DIR):
        return scenes
    for f in os.listdir(DOUBAO_SCENES_DIR):
        if not f.endswith(".png"):
            continue
        if "立绘" in f or "removebg" in f.lower():
            continue
        if f in seen:
            continue
        m = SCENE_PATTERN.match(f)
        if m:
            seen.add(f)
            path = os.path.join(DOUBAO_SCENES_DIR, f)
            if os.path.isfile(path):
                scenes.append(path)
    return sorted(scenes)


def _prefix_to_var(prefix):
    """文件名前缀（人物名）→ 变量名。未在 NAME_TO_VAR 的用简单规范化。"""
    prefix = (prefix or "").strip()
    if prefix in NAME_TO_VAR:
        return NAME_TO_VAR[prefix]
    var = prefix.replace(" ", "_").replace("的", "_").lower()
    var = "".join(c if (c.isalnum() or c == "_") else "_" for c in var).strip("_") or "sprite"
    return var


def discover_sprite_pngs():
    """扫描 assets/sprites 目录，返回 (源路径, 人物变量名) 列表。"""
    results = []
    seen_var = set()
    if not os.path.isdir(SPRITES_DIR):
        return results
    for f in os.listdir(SPRITES_DIR):
        if not f.endswith(".png"):
            continue
        prefix = None
        if f.endswith(SPRITE_SUFFIX):
            prefix = f[: -len(SPRITE_SUFFIX)].strip()
        elif f.endswith(SPRITE_SUFFIX_LEGACY):
            prefix = f[: -len(SPRITE_SUFFIX_LEGACY)].strip()
        if not prefix:
            continue
        path = os.path.join(SPRITES_DIR, f)
        if not os.path.isfile(path):
            continue
        var = _prefix_to_var(prefix)
        if var in seen_var:
            continue
        seen_var.add(var)
        results.append((path, var))
    return results


def _inject_bgm_into_script(audio_rel_path: str) -> None:
    """在 script.rpy 的 label start: 块首行注入 play music 语句（幂等）。"""
    script_path = os.path.join(WANGFO_GAME, "script.rpy")
    if not os.path.isfile(script_path):
        print("script.rpy not found, skip BGM injection")
        return
    with open(script_path, "r", encoding="utf-8") as f:
        content = f.read()
    play_stmt = 'play music "%s" fadein 1.0' % audio_rel_path
    # 已经注入过则跳过（幂等）
    if play_stmt in content:
        print("BGM already injected in script.rpy, skip")
        return
    # 在 "label start:" 之后的第一个空白缩进行前插入
    import re
    pattern = r'(label start:\s*\n)'
    replacement = r'\1    %s\n' % play_stmt
    new_content, count = re.subn(pattern, replacement, content, count=1)
    if count == 0:
        print("Could not find 'label start:' in script.rpy, skip BGM injection")
        return
    with open(script_path, "w", encoding="utf-8") as f:
        f.write(new_content)
    print("Injected BGM into script.rpy: %s" % play_stmt)


def import_voice_of_evening():
    """从已解压的 Japanese Music Pack 文件夹中复制 Voice Of Evening 曲目到 game/audio/。"""
    if not os.path.isdir(JAPANESE_MUSIC_PACK_DIR):
        # 音频包不存在时，若文件已经就位则仍注入 BGM 指令
        dst_path = os.path.join(AUDIO_DIR, GAME_MUSIC_DST_NAME)
        if os.path.isfile(dst_path):
            _inject_bgm_into_script("audio/%s" % GAME_MUSIC_DST_NAME)
        else:
            print("Japanese Music Pack folder not found, skip game music import")
        return
    os.makedirs(AUDIO_DIR, exist_ok=True)
    dst_path = os.path.join(AUDIO_DIR, GAME_MUSIC_DST_NAME)
    # 在文件夹内递归查找文件名同时含 voice 与 evening 的音频
    candidates = []
    for root, _, files in os.walk(JAPANESE_MUSIC_PACK_DIR):
        for f in files:
            if "voice" in f.lower() and "evening" in f.lower():
                if f.lower().endswith((".mp3", ".ogg", ".wav")):
                    candidates.append(os.path.join(root, f))
    if not candidates:
        print("No 'Voice Of Evening' audio found in Japanese Music Pack, skip")
        return
    src_path = candidates[0]
    try:
        shutil.copy2(src_path, dst_path)
        print("Copied game music: %s -> game/audio/%s" % (os.path.basename(src_path), GAME_MUSIC_DST_NAME))
        _inject_bgm_into_script("audio/%s" % GAME_MUSIC_DST_NAME)
    except Exception as e:
        print("Failed to import Voice Of Evening: %s" % e)


def _copy_if_missing(src_path: str, dst_path: str, note: str) -> bool:
    """仅在目标不存在时复制文件，返回是否执行了复制。"""
    if os.path.isfile(dst_path):
        return False
    if not os.path.isfile(src_path):
        return False
    os.makedirs(os.path.dirname(dst_path), exist_ok=True)
    shutil.copy2(src_path, dst_path)
    print("GUI fallback: %s" % note)
    return True


def _ensure_button_foreground_set(button_dir: str, family: str) -> int:
    """
    为 radio/check 等按钮补齐最常见状态图：
      - {family}_foreground.png
      - {family}_selected_foreground.png
      - {family}_insensitive_foreground.png
    """
    os.makedirs(button_dir, exist_ok=True)
    fixed = 0
    base = os.path.join(button_dir, f"{family}_foreground.png")
    selected = os.path.join(button_dir, f"{family}_selected_foreground.png")
    insensitive = os.path.join(button_dir, f"{family}_insensitive_foreground.png")

    # 如果 selected 缺失，先用 base 兜底；反过来 base 缺失则用 selected 兜底。
    if not os.path.isfile(selected) and os.path.isfile(base):
        shutil.copy2(base, selected)
        print("GUI fallback: create %s_selected_foreground.png from base" % family)
        fixed += 1
    if not os.path.isfile(base) and os.path.isfile(selected):
        shutil.copy2(selected, base)
        print("GUI fallback: create %s_foreground.png from selected" % family)
        fixed += 1

    # insensitive 常常是遗漏项，默认由 base 兜底。
    if not os.path.isfile(insensitive) and os.path.isfile(base):
        shutil.copy2(base, insensitive)
        print("GUI fallback: create %s_insensitive_foreground.png from base" % family)
        fixed += 1
    return fixed


def ensure_gui_button_fallbacks() -> None:
    """
    导入 GUI 后的安全兜底：
      1) 若 gui/button 缺少 radio/check 前景图，则从 gui/phone/button 回填。
      2) 为两套目录都补齐 insensitive 状态图，避免 DynamicImage 运行时报错。
    """
    button_dir = os.path.join(GUI_DST, "button")
    phone_button_dir = os.path.join(GUI_DST, "phone", "button")

    for family in ("radio", "check"):
        # 从 phone/button 回填到 button（兼容仍引用 gui/button 的旧样式）
        _copy_if_missing(
            os.path.join(phone_button_dir, f"{family}_foreground.png"),
            os.path.join(button_dir, f"{family}_foreground.png"),
            f"copy {family}_foreground.png phone->desktop",
        )
        _copy_if_missing(
            os.path.join(phone_button_dir, f"{family}_selected_foreground.png"),
            os.path.join(button_dir, f"{family}_selected_foreground.png"),
            f"copy {family}_selected_foreground.png phone->desktop",
        )

        # 两个目录都补齐缺失状态，避免 [prefix_] 解析失败。
        _ensure_button_foreground_set(button_dir, family)
        _ensure_button_foreground_set(phone_button_dir, family)


def main(
    game_dir=None,
    enhanced_dir=None,
    personas_file=None,
    entities_file=None,
):
    """
    导入资源到 Ren'Py 工程的 ``game`` 目录。

    Args:
        game_dir: 目标 ``game`` 文件夹路径；默认 ``<项目根>/wangfo/game``。
        enhanced_dir: 与 ``generate-content`` / ``generate-renpy-scripts`` 一致的增强 JSON 目录；
            省略则默认 ``<项目根>/out/enhanced_paths``（易与 UI run 不一致，建议由 CLI/UI 传入）。
        personas_file: ``character_personas.json``；省略则默认 ``<项目根>/out/character_personas.json``。
        entities_file: 可选 ``entities.json``；省略时行为与 ``generate_renpy_scripts.generate_content_and_scripts`` 一致。
    """
    _set_game_paths(game_dir)
    print("Import target game dir: %s" % WANGFO_GAME)
    os.makedirs(IMAGES, exist_ok=True)
    os.makedirs(os.path.join(IMAGES, "backgrounds"), exist_ok=True)

    # 1. 立绘：识别 *-立绘生成-remove-preview.png，按前缀得到人物，复制并生成 side image 定义
    sprite_list = discover_sprite_pngs()
    sprite_vars = []
    for src_path, var in sprite_list:
        dst_name = "%s_sprite.png" % var
        dst = os.path.join(IMAGES, dst_name)
        shutil.copy2(src_path, dst)
        sprite_vars.append(var)
        print("Copied sprite: %s -> %s" % (os.path.basename(src_path), dst_name))
    if sprite_vars:
        rpy_path = os.path.join(WANGFO_GAME, "images_sprites.rpy")
        with open(rpy_path, "w", encoding="utf-8") as f:
            f.write("# 立绘（由 import_assets.py 根据 *-立绘生成-remove-preview.png 自动生成）\n")
            f.write("# 说话时显示立绘需在 characters.rpy 中对应角色设置 image=，由 character_generator 读 sprite_vars.txt 生成\n\n")
            for var in sprite_vars:
                fname = "%s_sprite.png" % var
                f.write('image side %s = "images/%s"\n' % (var, fname))
                f.write('image side %s default = "images/%s"\n' % (var, fname))
                f.write('image %s_sprite = "images/%s"\n' % (var, fname))
                f.write("\n")
        print("Wrote %d sprite definitions to game/images_sprites.rpy" % len(sprite_vars))
        sprite_vars_path = os.path.join(WANGFO_GAME, "sprite_vars.txt")
        with open(sprite_vars_path, "w", encoding="utf-8") as f:
            for v in sprite_vars:
                f.write(v + "\n")
        print("Wrote sprite_vars.txt for character_generator (image= attribute)")
        if _ensure_sideimage_behind_textbox():
            print("Patched screens.rpy: SideImage is now under textbox layer")
    else:
        # 无立绘时也写入空文件，避免保留旧的 sprite_vars 导致错误关联
        sprite_vars_path = os.path.join(WANGFO_GAME, "sprite_vars.txt")
        with open(sprite_vars_path, "w", encoding="utf-8") as f:
            f.write("# 有立绘的角色变量名（每行一个），由 import_assets.py 根据 *-立绘生成-remove-preview.png 自动生成\n")
        rpy_path = os.path.join(WANGFO_GAME, "images_sprites.rpy")
        with open(rpy_path, "w", encoding="utf-8") as f:
            f.write("# 立绘（由 import_assets.py 根据 *-立绘生成-remove-preview.png 自动生成）\n")
            f.write("# 未发现立绘文件时本文件为空；放入对应 PNG 后重新运行 import_assets.py 即可。\n")
        print("No *-立绘生成-remove-preview.png found in assets/sprites/ (wrote empty sprite_vars.txt and images_sprites.rpy)")

    # 1b. 事件→场景映射：写入 JSON，供 path_script_generator 使用
    event_scene_path = os.path.join(WANGFO_GAME, "event_scene_map.json")
    _write_event_scene_map(event_scene_path)
    print("Wrote event_scene_map.json (event/ending -> bg_scene)")

    # 1c. 游戏音乐：从 Japanese Music Pack.zip 提取 Voice Of Evening 到 game/audio/
    import_voice_of_evening()

    # 2. 豆包场景背景：自动发现「xxx (n).png」，复制为 bg_scene_01.png ... 并生成 images_doubao.rpy
    bg_dir = os.path.join(IMAGES, "backgrounds")
    scene_paths = discover_doubao_scenes()
    doubao_defs = []
    for i, src_path in enumerate(scene_paths, start=1):
        src_name = os.path.basename(src_path)
        dst_name = "bg_scene_%02d.png" % i
        dst = os.path.join(bg_dir, dst_name)
        if os.path.isfile(src_path):
            shutil.copy2(src_path, dst)
            tag = "bg_scene_%02d" % i
            doubao_defs.append((tag, dst_name))
            print("Copied scene: %s -> %s" % (src_name, dst_name))
    if doubao_defs:
        rpy_path = os.path.join(WANGFO_GAME, "images_doubao.rpy")
        with open(rpy_path, "w", encoding="utf-8") as f:
            f.write("# 豆包场景背景（由 import_assets.py 自动生成，请勿手改）\n")
            f.write("# 剧本中可用: scene bg_scene_01 / scene bg_scene_02 ...\n\n")
            for tag, fname in doubao_defs:
                f.write('image %s = "images/backgrounds/%s"\n' % (tag, fname))
        print("Wrote %d scene definitions to game/images_doubao.rpy" % len(doubao_defs))

    # 2. Fantasy GUI
    if os.path.isdir(FANTASY_GUI):
        for name in os.listdir(FANTASY_GUI):
            src = os.path.join(FANTASY_GUI, name)
            dst = os.path.join(GUI_DST, name)
            if os.path.isfile(src):
                shutil.copy2(src, dst)
                print("GUI file: %s" % name)
            elif os.path.isdir(src):
                if os.path.isdir(dst):
                    shutil.rmtree(dst)
                shutil.copytree(src, dst)
                print("GUI dir:  %s" % name)
        overlay_dir = os.path.join(GUI_DST, "overlay")
        main_menu_png = os.path.join(overlay_dir, "main_menu.png")
        game_menu_png = os.path.join(overlay_dir, "game_menu.png")
        if not os.path.isfile(main_menu_png) and os.path.isfile(game_menu_png):
            shutil.copy2(game_menu_png, main_menu_png)
            print("Created gui/overlay/main_menu.png from game_menu.png")
        ensure_gui_button_fallbacks()
        print("Fantasy GUI copied to wangfo/game/gui/")
    else:
        print("Fantasy GUI not found at: %s" % FANTASY_GUI)

    # 3. Free Sky Backgrounds
    if os.path.isdir(SKY_SRC):
        count = 0
        for name in sorted(os.listdir(SKY_SRC)):
            if name.lower().endswith(".png"):
                src = os.path.join(SKY_SRC, name)
                dst = os.path.join(IMAGES, "backgrounds", name)
                shutil.copy2(src, dst)
                count += 1
        print("Copied %d sky backgrounds to wangfo/game/images/backgrounds/" % count)
    else:
        print("Free Sky Backgrounds not found at: %s" % SKY_SRC)

    print("Done. Run the game and use images from %s" % os.path.join(WANGFO_GAME, "images.rpy"))

    # 根据场景映射把 scene 写入剧本：触发 Ren'Py 脚本生成（会读 event_scene_map.json 并写入 paths/*.rpy、endings/*.rpy）
    try:
        import sys
        sys.path.insert(0, ROOT)
        from pathlib import Path
        from scripts.generate_renpy_scripts import generate_content_and_scripts
        print("")
        print("--- 根据场景映射写入 paths/endings 剧本中的 scene 语句 ---")
        generate_content_and_scripts(
            project_root=Path(ROOT),
            enhanced_dir=enhanced_dir,
            personas_file=personas_file,
            wangfo_game_dir=Path(WANGFO_GAME),
            entities_file=entities_file,
        )
        if _ensure_canonical_entry_scene():
            print("Patched canonical_path entry scene fallback")
    except Exception as e:
        print("")
        print("⚠️  跳过脚本生成（需 out/enhanced_paths 等）: %s" % e)
        if _ensure_canonical_entry_scene():
            print("Patched canonical_path entry scene fallback (without regeneration)")

if __name__ == "__main__":
    main()
