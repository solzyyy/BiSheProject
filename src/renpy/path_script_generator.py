"""
路径脚本生成器 📜✨

职责：
- 从 enhanced_paths（或 all_paths）JSON 生成路径脚本（paths/*.rpy）
- 将事件转换为 Ren'Py 脚本语句
- 为非决策点事件添加无伤大雅的 flavor 选项（均进入下一事件）

数据约定（与 enhanced_paths 的 event 结构一致）：
- 描述：优先 event["detailed_scene_description"]，否则 event["scene_description"]
- 对话：event["dialogue"]（{ speaker, text, tone }）
- 分支选择：event["player_choice"] 的 options[].jump_target 存**路径 renpy_label**（与 metadata 一致）；
  生成 .rpy 时会对决策点解析为 ``jump {label}_after_{fork_event_id}``，与主线同路径则 ``pass``。

事件内顺序（可配置）：
- 默认：描述与对话**交错**（按段落与对话条交替），最后再出选择（先选才会进入下一事件）。
- 若 event 含 "content_blocks"：按 blocks 顺序输出（narrative / dialogue / choice 可任意穿插）。
"""

import json
import re
from pathlib import Path
from typing import Dict, List, Any, Optional, Tuple, Union

# 无伤大雅的 flavor 选项：不改变剧情，选任意一项都进入下一事件（用于非决策点）
FLAVOR_CHOICES: List[str] = [
    "继续",
    "沉默片刻",
    "再听一会儿",
]
# 仅每隔 FLAVOR_EVERY_N 个事件、且非首事件时加 flavor，避免过多
FLAVOR_EVERY_N: int = 4

# 事件 ID → 豆包场景（仅从 import_assets.py 生成的 event_scene_map.json 读取；请先运行 python import_assets.py）
def _load_event_scene_maps() -> Tuple[Dict[str, str], Dict[str, str]]:
    try:
        root = Path(__file__).resolve().parent.parent.parent
        json_path = root / "wangfo" / "game" / "event_scene_map.json"
        if json_path.is_file():
            with open(json_path, "r", encoding="utf-8") as f:
                data = json.load(f)
            return (
                data.get("event") or {},
                data.get("ending") or {},
            )
    except Exception as e:
        import warnings
        warnings.warn("无法读取 event_scene_map.json，请先运行: python import_assets.py — %s" % e)
    return {}, {}


EVENT_SCENE_MAP, ENDING_SCENE_MAP = _load_event_scene_maps()


class PathScriptGenerator:
    """路径脚本生成器（对话说者可按 entities 合并同人异名，如 皇帝/天子 → 同一变量）"""
    
    def __init__(
        self,
        character_generator: Any,
        all_paths_map: Dict[str, Dict[str, Any]] = None,
        entities_path: Optional[str] = None,
    ):
        """
        初始化路径脚本生成器
        
        Args:
            character_generator: CharacterGenerator 实例，用于角色名映射
            all_paths_map: 所有路径的映射 {renpy_label: path_data}
            entities_path: entities.json 路径；若提供，对话中的说者先按实体归并为 canonical 再查变量名
        """
        self.character_generator = character_generator
        self.all_paths_map = all_paths_map or {}
        self._mention_to_entity: Dict[str, Dict[str, Any]] = {}
        if entities_path:
            try:
                from src.entity_resolver import build_mention_to_entity
                self._mention_to_entity = build_mention_to_entity(entities_path=entities_path)
            except Exception:
                pass
    
    def escape_renpy_text(self, text: str) -> str:
        """
        转义 Ren'Py 文本中的特殊字符
        
        Ren'Py 特殊字符：
        - { 开启文本标签，需要转义为 {{
        - [ 开启替换，需要转义为 [[
        - " 需要转义为 \"
        """
        if not text:
            return ""
        
        # 转义双引号
        text = text.replace('"', '\\"')
        # 转义花括号（文本标签）
        text = text.replace('{', '{{')
        # 转义方括号（替换）
        text = text.replace('[', '[[')
        
        return text
    
    def get_character_var_name(self, speaker: str) -> str:
        """获取角色变量名"""
        return self.character_generator.generate_character_name(speaker)
    
    def _interleave_narrative_and_dialogue(self, event: Dict[str, Any]) -> List[Tuple[str, Any]]:
        """
        把描述（按段落）与对话交错成有序块列表，便于「描述与对话在叙述中不定时发生」。
        顺序：段0 → 对话0 → 段1 → 对话1 → …，某一方用完后接剩余另一方。
        """
        scene_desc = event.get("detailed_scene_description") or event.get("scene_description", "") or ""
        dialogue = event.get("dialogue", []) or []
        paragraphs = [p.strip() for p in scene_desc.split("\n\n") if p.strip()]
        blocks: List[Tuple[str, Any]] = []
        i, j = 0, 0
        while i < len(paragraphs) or j < len(dialogue):
            if i < len(paragraphs):
                blocks.append(("narrative", paragraphs[i]))
                i += 1
            if j < len(dialogue):
                blocks.append(("dialogue", dialogue[j]))
                j += 1
        return blocks

    def _content_blocks_from_event(self, event: Dict[str, Any]) -> List[Tuple[str, Any]]:
        """
        得到事件正文块序列：若有 content_blocks 则按其中 narrative/dialogue/choice 顺序，
        否则用「描述与对话交错」的默认顺序（无 choice 在中间）。
        """
        raw = event.get("content_blocks")
        if raw and isinstance(raw, list):
            out: List[Tuple[str, Any]] = []
            for b in raw:
                if not isinstance(b, dict):
                    continue
                t = (b.get("type") or "").strip().lower()
                if t == "narrative":
                    text = b.get("text", "").strip()
                    if text:
                        out.append(("narrative", text))
                elif t == "dialogue":
                    speaker = b.get("speaker", "")
                    text = (b.get("text") or "").strip()
                    if text:
                        out.append(("dialogue", {"speaker": speaker, "text": text}))
                elif t == "choice":
                    out.append(("choice", b))
            if out:
                return out
        return self._interleave_narrative_and_dialogue(event)

    def _render_narrative(self, text: str) -> List[str]:
        """单段叙述 → Ren'Py 旁白行。"""
        if not text:
            return []
        escaped = self.escape_renpy_text(text)
        return [f'    "{escaped}"']

    def _render_dialogue_line(self, d: Union[Dict[str, Any], Any]) -> List[str]:
        """
        单条对话 → Ren'Py 行。
        若配置了 entities，先按 canonical 合并（如 天子→皇帝），再查变量名；未定义则用字符串 "speaker"。
        """
        if isinstance(d, dict):
            speaker = d.get("speaker", "")
            text = (d.get("text") or "").strip()
        else:
            speaker, text = "", str(d).strip()
        if not text:
            return []
        escaped = self.escape_renpy_text(text)
        if speaker and speaker != "叙述者（描述）":
            # 同人异名合并：天子 → 皇帝，再用皇帝查 var_name
            display_name = speaker
            if self._mention_to_entity:
                try:
                    from src.entity_resolver import get_canonical_name
                    canonical = get_canonical_name(speaker, self._mention_to_entity)
                    if canonical:
                        display_name = canonical
                except Exception:
                    pass
            var_name = self.get_character_var_name(display_name)
            defined = self.character_generator.get_defined_var_names()
            if var_name in defined:
                return [f'    {var_name} "{escaped}"']
            escaped_speaker = self.escape_renpy_text(speaker)
            return [f'    "{escaped_speaker}" "{escaped}"']
        return [f'    "{escaped}"']

    def generate_scene_description(self, event: Dict[str, Any]) -> List[str]:
        """生成整段场景描述（旁白）。用于未使用交错时的兼容。"""
        scene_desc = event.get("detailed_scene_description") or event.get("scene_description", "")
        return self._render_narrative(scene_desc or "") if scene_desc else []

    def generate_dialogue(self, event: Dict[str, Any]) -> List[str]:
        """生成整段对话。用于未使用交错时的兼容。"""
        lines = []
        for d in event.get("dialogue", []) or []:
            lines.extend(self._render_dialogue_line(d))
        if lines:
            lines.append("")
        return lines
    
    def _collect_decision_choices(self, event_id: str) -> List[Dict[str, Any]]:
        """
        从所有路径的 path_combination 收集该决策点的**完整**选项列表，
        保证主线/任意路径的 menu 都能列出所有分支。
        返回 [ {"branch_choice", "target_path", "description"}, ... ]。
        """
        all_choices = []
        choices_seen = set()
        for renpy_label, other_path_data in (self.all_paths_map or {}).items():
            for combo in other_path_data.get("metadata", {}).get("path_combination", []):
                if combo.get("fork_event_id") != event_id:
                    continue
                branch_choice = (combo.get("branch_choice") or "").strip()
                choice_key = (event_id, branch_choice)
                if choice_key in choices_seen:
                    continue
                choices_seen.add(choice_key)
                desc = (combo.get("description") or "").strip()
                all_choices.append({
                    "branch_choice": branch_choice,
                    "target_path": (renpy_label or "").strip(),
                    "description": desc or branch_choice,
                })
        return all_choices

    def _collect_decision_option_texts(self, event_id: str) -> Tuple[Dict[str, str], Dict[str, str], str]:
        """
        从**所有路径**里该 event_id 的 player_choice 合并选项文案与 prompt，
        使同一决策点在每条路径导出的 menu 文案一致（不因当前路径不同而不同）。
        返回 (text_by_target, text_by_branch, prompt)。
        同一 key 有多条时优先保留较短文案（适合菜单）。
        """
        text_by_target: Dict[str, str] = {}
        text_by_branch: Dict[str, str] = {}
        prompt = ""
        for _path_label, path_data in (self.all_paths_map or {}).items():
            for ev in path_data.get("events") or []:
                if ev.get("event_id") != event_id:
                    continue
                pc = ev.get("player_choice")
                if not pc:
                    continue
                if not prompt and (pc.get("prompt") or "").strip():
                    prompt = (pc.get("prompt") or "").strip()
                for opt in pc.get("options") or []:
                    text = (opt.get("text") or "").strip()
                    if not text:
                        continue
                    t = (opt.get("jump_target") or "").strip()
                    if t and (t not in text_by_target or len(text) < len(text_by_target[t])):
                        text_by_target[t] = text
                    bid = (opt.get("choice_id") or "").strip()
                    if bid and (bid not in text_by_branch or len(text) < len(text_by_branch[bid])):
                        text_by_branch[bid] = text
                break
        return text_by_target, text_by_branch, prompt

    def generate_decision_point(self, event: Dict[str, Any], path_data: Dict[str, Any]) -> List[str]:
        """
        生成决策点（menu）。仅 canonical_path 作为入口时输出完整分支 menu；
        其他路径仅为 label，在决策点不提供跳转选项，只输出「继续」-> pass，避免混淆。
        - canonical_path：完整 menu（选项列表来自 _collect_decision_choices，选后 pass 或 jump 到分支）
        - 非 canonical：仅「继续」-> pass
        """
        lines = []
        event_id = event.get("event_id", "")
        current_label = (path_data.get("renpy_label") or "").strip()

        # 仅主线（canonical）作为入口，在此设置分支 menu；其他路线只有 label，不设分支 menu
        if current_label != "canonical_path":
            lines.append("    menu:")
            lines.append('        "继续":')
            lines.append("            pass")
            lines.append("")
            return lines

        all_choices = self._collect_decision_choices(event_id)
        if not all_choices:
            return lines

        text_by_target, text_by_branch, prompt = self._collect_decision_option_texts(event_id)
        if not prompt:
            prompt = (event.get("scene_description") or "你应该如何选择？").strip()
            if len(prompt) > 60:
                prompt = "你应该如何选择？"

        lines.append("    menu:")
        lines.append(f'        "{self.escape_renpy_text(prompt)}"')
        lines.append("")

        for c in all_choices:
            target_path = c.get("target_path") or ""
            branch_choice = c.get("branch_choice") or ""
            option_text = (
                text_by_target.get(target_path)
                or text_by_branch.get(branch_choice)
                or c.get("description")
                or branch_choice
            )
            if not option_text:
                continue
            escaped = self.escape_renpy_text(option_text)
            lines.append(f'        "{escaped}":')
            if target_path and target_path != current_label:
                # 跳到目标路径「该决策点后缀」入口，避免从 E1 重播
                entry_label = f"{target_path}_{event_id}"
                lines.append(f"            jump {entry_label}")
            else:
                lines.append("            pass")
        lines.append("")
        return lines

    @staticmethod
    def _resolve_decision_jump_renpy_label(
        jump_target: str,
        current_renpy_label: str,
        fork_event_id: Optional[str],
    ) -> Optional[str]:
        """
        将 JSON 中的 jump_target（路径 renpy_label）转为 Ren'Py 实际 jump 的 label。
        - 空 / __next__：None → 生成 pass
        - 与当前路径相同（走主线）：None → pass
        - 其余：``{target}_{fork_event_id}``，与 generate_path_script 打的入口 label 一致
        若无 fork_event_id（非决策点穿插块），无法拼后缀，非空 target 则原样返回（兼容旧数据）。
        """
        jt = (jump_target or "").strip()
        cur = (current_renpy_label or "").strip()
        if not jt or jt == "__next__":
            return None
        if jt == cur:
            return None
        if fork_event_id:
            return f"{jt}_{fork_event_id}"
        return jt
    
    def _render_choice_block(
        self,
        block: Dict[str, Any],
        path_data: Optional[Dict[str, Any]] = None,
        fork_event_id: Optional[str] = None,
    ) -> List[str]:
        """
        渲染 content_blocks 中的 choice 块（穿插在叙述中的选择）。
        若选项均为 __next__ 则全部 pass；否则对决策点把 jump_target 解析为 ``label_after_E*``。
        """
        lines = []
        prompt = (block.get("prompt") or "").strip() or "接下来呢？"
        options = block.get("options") or []
        if not options:
            return lines
        all_next = all(
            (opt.get("jump_target") or "").strip() in ("", "__next__")
            for opt in options
        )
        lines.append("    menu:")
        lines.append(f'        "{self.escape_renpy_text(prompt)}"')
        lines.append("")
        current_label = (path_data.get("renpy_label") or "").strip() if path_data else ""
        if all_next:
            for opt in options:
                text = (opt.get("text") or "").strip()
                if text:
                    lines.append(f'        "{self.escape_renpy_text(text)}":')
                    lines.append("            pass")
        else:
            for opt in options:
                text = (opt.get("text") or "").strip()
                if not text:
                    continue
                jt = (opt.get("jump_target") or "").strip()
                resolved = self._resolve_decision_jump_renpy_label(
                    jt, current_label, fork_event_id
                )
                lines.append(f'        "{self.escape_renpy_text(text)}":')
                if resolved:
                    lines.append(f"            jump {resolved}")
                else:
                    lines.append("            pass")
        lines.append("")
        return lines

    def generate_flavor_menu(self, event: Dict[str, Any]) -> List[str]:
        """
        为非决策点事件生成「无伤大雅」的 flavor 选项菜单：
        若干选项均不跳转，选后进入下一事件，仅增加一点参与感。
        """
        lines = []
        choices = event.get("player_choice", {}).get("options") if event.get("player_choice") else None
        if choices:
            # 已有 player_choice 且选项均为「进入下一事件」（jump_target 为空或 __next__）
            prompt = (event.get("player_choice") or {}).get("prompt", "接下来呢？")
            all_next = all(
                (opt.get("jump_target") or "").strip() in ("", "__next__")
                for opt in choices
            )
            if all_next and choices:
                lines.append("    menu:")
                if prompt:
                    lines.append(f'        "{self.escape_renpy_text(prompt)}"')
                    lines.append("")
                for opt in choices:
                    text = opt.get("text", "").strip()
                    if text:
                        lines.append(f'        "{self.escape_renpy_text(text)}":')
                        lines.append("            pass")
                lines.append("")
            return lines
        # 使用默认 flavor 选项
        lines.append("    menu:")
        lines.append('        "接下来呢？"')
        lines.append("")
        for text in FLAVOR_CHOICES:
            lines.append(f'        "{self.escape_renpy_text(text)}":')
            lines.append("            pass")
        lines.append("")
        return lines
    
    def _generate_jump_target(self, branch_id: str, path_data: Dict[str, Any], choice: Dict[str, Any]) -> str:
        """
        生成跳转目标 label 名
        
        策略：
        1. 如果 branch_id 以 "canonical_" 开头或 is_canonical=True，跳转到 canonical_path
        2. 如果 branch_id 以 "branch_" 开头，查找对应的路径文件
           - 根据 fork_event_id 和 branch_choice 查找匹配的路径
           - 路径的 path_combination 中包含相同 fork_event_id 和 branch_choice 的路径
        3. 否则，生成一个基于 branch_id 的 label 名
        """
        # 情况1：主线选择，跳转到 canonical_path
        if branch_id.startswith("canonical_") or choice.get("is_canonical", False):
            return "canonical_path"
        
        # 情况2：分支选择，需要查找对应的路径
        if branch_id.startswith("branch_"):
            # 提取关键信息
            fork_event_id = choice.get("fork_event_id", "")
            branch_choice = choice.get("branch_choice", "")
            
            # 遍历所有路径，查找匹配的
            # 匹配条件：路径的 path_combination 中包含相同的 fork_event_id 和 branch_choice
            for renpy_label, other_path_data in self.all_paths_map.items():
                other_combos = other_path_data.get("metadata", {}).get("path_combination", [])
                for combo in other_combos:
                    # 检查是否匹配：相同的 fork_event_id 和 branch_choice
                    if (combo.get("fork_event_id") == fork_event_id and 
                        combo.get("branch_choice") == branch_choice):
                        # 确保这个选择在当前路径中（避免匹配到错误的路径）
                        # 实际上，只要 fork_event_id 和 branch_choice 匹配，就应该跳转到该路径
                        return renpy_label
        
        # 情况3：无法找到，生成一个基于 branch_id 的 label 名
        # 移除 "branch_" 前缀，转换为 label 格式
        label_name = branch_id.replace("branch_", "").lower().replace(" ", "_").replace("-", "_")
        label_name = ''.join(c if c.isalnum() or c == '_' else '_' for c in label_name)
        return label_name
    
    def _should_add_flavor(self, event_index: int, event_type: str) -> bool:
        """仅在「合适」的时机加 flavor：少而精，非首事件且每隔 FLAVOR_EVERY_N 个事件。"""
        if event_type in ("decision_point", "ending"):
            return False
        if event_index <= 0:
            return False
        return (event_index + 1) % FLAVOR_EVERY_N == 0

    def generate_event_content(
        self, event: Dict[str, Any], path_data: Dict[str, Any], event_index: int = 0
    ) -> List[str]:
        """
        生成单个事件的脚本内容。
        顺序：先按 content_blocks 或「描述与对话交错」输出正文，最后再出选择（先选才会进入下一事件）。
        """
        lines = []
        event_id = event.get("event_id", "")
        event_type = event.get("type", "")

        lines.append(f"    # 事件 {event_id} ({event_type})")

        # 根据事件 ID 自动插入场景（豆包场景），无需在剧本里手写 scene
        scene_bg = EVENT_SCENE_MAP.get(event_id)
        if scene_bg:
            lines.append(f"    scene {scene_bg}")
            lines.append("")

        # 正文：按 content_blocks 或默认交错顺序输出；其中可含穿插的 choice 块（由 LLM 决定位置）
        blocks = self._content_blocks_from_event(event)
        choice_rendered_in_blocks = False
        decision_branch_menu_in_blocks = False
        for kind, payload in blocks:
            if kind == "narrative":
                lines.extend(self._render_narrative(payload))
            elif kind == "dialogue":
                lines.extend(self._render_dialogue_line(payload))
            elif kind == "choice":
                lines.extend(
                    self._render_choice_block(
                        payload,
                        path_data=path_data,
                        fork_event_id=event_id if event_type == "decision_point" else None,
                    )
                )
                choice_rendered_in_blocks = True
                if event_type == "decision_point":
                    opts = payload.get("options") or []
                    if opts and not all(
                        (o.get("jump_target") or "").strip() in ("", "__next__")
                        for o in opts
                    ):
                        decision_branch_menu_in_blocks = True
            lines.append("")

        # 决策点：若已在 content_blocks 里输出分支 menu（与 generate_decision_point 等价），勿再追加第二套 menu
        if event_type == "decision_point" and not decision_branch_menu_in_blocks:
            menu_lines = self.generate_decision_point(event, path_data)
            lines.extend(menu_lines)
        elif (
            event_type != "ending"
            and self._should_add_flavor(event_index, event_type)
            and not choice_rendered_in_blocks
        ):
            has_content = (
                event.get("detailed_scene_description")
                or event.get("scene_description")
                or event.get("dialogue")
            )
            if has_content:
                flavor_lines = self.generate_flavor_menu(event)
                lines.extend(flavor_lines)

        if event_type == "ending":
            # jump 由 generate_path_script 末尾统一添加，此处不重复
            pass

        return lines
    
    def generate_path_script(self, path_data: Dict[str, Any]) -> str:
        """
        生成路径脚本内容
        
        Args:
            path_data: 路径 JSON 数据
            
        Returns:
            Ren'Py 脚本内容
        """
        def _event_num(eid: str) -> Optional[int]:
            # 不要用 \b 约束数字后边界：Ren'Py/数据里常见 E6_branch_...，下划线在 Python \w 内会导致 \b 匹配失败。
            m = re.search(r"E(\d+)", (eid or "").upper())
            return int(m.group(1)) if m else None

        lines: List[str] = []
        renpy_label = (path_data.get("renpy_label") or "").strip()
        events = path_data.get("events", []) or []
        ending = path_data.get("ending", {}) or {}

        # path_combination 里会出现多个 fork_event_id（用于标识「E6 选法 + E16 选法」等组合）。
        # 只有时间线上确实存在「该 fork 之后」的内容时，才需要生成 {path}_{fork}；
        # 否则（例如仅有 E6 后立刻结局、没有任何 E16+ 事件）不应为 E16 再打一个与 after_E6 重叠的空 label。
        combos = (path_data.get("metadata") or {}).get("path_combination") or []
        required_after_ids: List[str] = []
        for c in combos:
            if not isinstance(c, dict):
                continue
            fid = (c.get("fork_event_id") or "").strip()
            if fid and fid not in required_after_ids:
                required_after_ids.append(fid)

        # 为每个事件 index 预先计算需要插入的入口 labels（一个事件前可能插多个 label）。
        labels_before_event: Dict[int, List[str]] = {}
        generated_after_ids: set[str] = set()

        # 规则 A（已弃用）：
        # 早期实现会在「decision_point 的下一事件之前」插入 after_{fid} label。
        # 但现在 decision_point 自身会显式生成 label {path}_after_{event_id}，
        # 若再保留这条规则会造成重复 label（如 canonical_path_after_E5 出现两次）。

        # 规则 B：对 metadata.path_combination 里出现过的 fork_event_id，
        # 若规则 A 没生成该 after 标签，则把入口落到「该 fork 之后」的第一段内容处：
        # 选第一个 event_num >= fork_num 的事件（跳过与本 fork 同位的 decision_point）。
        # 若时间线上没有任何事件的 event_num >= fork_num，则不生成该 after（避免与更靠前的 after 重叠、产生连续空 label）。
        for fid in required_after_ids:
            if fid in generated_after_ids:
                continue
            # 若 events 中本身就存在该 fid 的事件（通常就是 decision_point），则 after_{fid}
            # 会在该事件自身的生成逻辑中产出；此处不再额外插一遍，避免重复 label。
            if any(
                isinstance(ev, dict)
                and str((ev.get("event_id") or "")).strip().upper() == str(fid).strip().upper()
                for ev in (events or [])
            ):
                generated_after_ids.add(fid)
                continue
            fnum = _event_num(fid)
            target_index: Optional[int] = None
            if fnum is not None:
                for i, ev in enumerate(events):
                    if not isinstance(ev, dict):
                        continue
                    eid = (ev.get("event_id") or "").strip()
                    etype = (ev.get("type") or "").strip()
                    enum = _event_num(eid)
                    if enum is None:
                        continue
                    # 若正好是本 fork 的 decision_point，本入口不应落在决策点本身（避免重复 menu）；
                    # 但该情况通常会由规则 A 处理，所以这里直接跳过。
                    if enum == fnum and etype == "decision_point" and (eid or "").strip().upper() == fid.upper():
                        continue
                    if enum >= fnum:
                        target_index = i
                        break
            if target_index is None:
                continue
            lst = labels_before_event.setdefault(target_index, [])
            if fid not in lst:
                lst.append(fid)
            generated_after_ids.add(fid)

        # 生成 label（路径入口，用于 start 或从其他决策点跳入「本路径该决策点之后」）
        lines.append(f"label {renpy_label}:")
        lines.append("")

        for event_index, event in enumerate(events):
            for after_id in labels_before_event.get(event_index, []):
                lines.append(f"label {renpy_label}_{after_id}:")
                lines.append("")

            # decision_point：允许 menu 与叙述/对话按 content_blocks 混排，
            # 同时保证“选了分支不会跳过 menu 之后的正文块”。
            if isinstance(event, dict) and (event.get("type") == "decision_point"):
                eid = (event.get("event_id") or "").strip()
                blocks = self._content_blocks_from_event(event)

                # 找到第一个 choice 块，认为它就是“分支 menu”（其选项 jump_target 指向其它路径）
                choice_idx: Optional[int] = None
                choice_payload: Optional[Dict[str, Any]] = None
                for i, (kind, payload) in enumerate(blocks):
                    if kind == "choice" and isinstance(payload, dict):
                        choice_idx = i
                        choice_payload = payload
                        break

                if choice_idx is None or choice_payload is None or not eid:
                    # 没有 choice/menu：退回统一渲染
                    event_lines = self.generate_event_content(event, path_data, event_index)
                    lines.extend(event_lines)
                    continue

                # 1) menu 之前的块：按 blocks 顺序输出（这些内容只在“真正走到此事件”时出现一次）
                pre_blocks = blocks[:choice_idx]
                for kind, payload in pre_blocks:
                    if kind == "narrative":
                        lines.extend(self._render_narrative(payload))
                    elif kind == "dialogue":
                        lines.extend(self._render_dialogue_line(payload))
                    elif kind == "choice":
                        # 极少见：menu 前还有 choice（多为 __next__），照常渲染
                        lines.extend(
                            self._render_choice_block(payload, path_data=path_data, fork_event_id=None)
                        )
                    lines.append("")

                # 2) 输出 menu（choice block）；其跳转目标解析为 {target}_{Eid}
                #    - 若选项跳回当前路径（jump_target==当前 renpy_label 或空），则 pass 并顺序进入 label {path}_{Eid}
                lines.extend(
                    self._render_choice_block(choice_payload, path_data=path_data, fork_event_id=eid)
                )

                # 3) 入口 label：默认应在事件开头；但若事件含 menu，则放在 menu 之后（你选 B：跳入这里直接进正文）
                lines.append(f"label {renpy_label}_{eid}:")
                lines.append("")

                # 4) menu 之后的正文块：从 choice 后继续输出（jump 进来会从这里开始）
                lines.append(f"    # 事件 {eid} (decision_point)")
                scene_bg = EVENT_SCENE_MAP.get(eid)
                if scene_bg:
                    lines.append(f"    scene {scene_bg}")
                    lines.append("")
                post_blocks = blocks[choice_idx + 1 :]
                for kind, payload in post_blocks:
                    if kind == "narrative":
                        lines.extend(self._render_narrative(payload))
                    elif kind == "dialogue":
                        lines.extend(self._render_dialogue_line(payload))
                    elif kind == "choice":
                        # menu 后的穿插 choice（通常为 __next__ 的 flavor）照常渲染
                        lines.extend(
                            self._render_choice_block(payload, path_data=path_data, fork_event_id=None)
                        )
                    lines.append("")
            else:
                event_lines = self.generate_event_content(event, path_data, event_index)
                lines.extend(event_lines)
        
        # 如果路径有结局：不再 jump 到 ending_xxx，路径内已有完整结局叙述，直接 return 并标记解锁
        if ending:
            ending_id = ending.get("id", "")
            if ending_id:
                scene_bg = ENDING_SCENE_MAP.get(ending_id)
                if scene_bg:
                    lines.append(f"    scene {scene_bg}")
                    lines.append("")
                lines.append("    # 标记结局已解锁")
                lines.append(f"    $ persistent.{ending_id} = True")
                lines.append("")
            lines.append("    return")
        else:
            lines.append("    return")
        
        lines.append("")
        
        return "\n".join(lines)
    
    def save_path_script(self, path_data: Dict[str, Any], output_file: Path):
        """
        保存路径脚本
        
        Args:
            path_data: 路径 JSON 数据
            output_file: 输出文件路径
        """
        content = self.generate_path_script(path_data)
        
        # 确保输出目录存在
        output_file.parent.mkdir(parents=True, exist_ok=True)
        
        with open(output_file, 'w', encoding='utf-8') as f:
            f.write(content)
        
        renpy_label = path_data.get("renpy_label", "")
        print(f"已生成路径脚本：{output_file} (label: {renpy_label})")

