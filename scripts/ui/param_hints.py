"""
单步执行等界面：为常用 CLI 参数提供「备注 + 预设档位」元数据。

像表格一行：左侧选预设或自定义，右侧说明推荐范围与用途。
"""

from __future__ import annotations

from typing import Any, TypedDict


class ParamHint(TypedDict, total=False):
    """单参数的 UI 提示。"""

    # 单步/参数页展示用中文名（JSON 键名不变）
    ui_label: str
    note: str
    # 长说明：默认折叠展开区（避免挤占右侧整列）
    expand_detail: str
    expand_detail_title: str
    # 预设：(数值, 短标签)，用于一键选择；仍可选「自定义」再填。
    presets: list[tuple[int, str]]
    # 若为 True，表示该参数本质是「可选整数」（None=不传），第一项预设应配合「不传」类选项在 UI 里处理。
    optional_int: bool
    # optional_int 时第一项下拉文案（默认「不传（全书）」，用于 extract max_chunks 等）
    optional_int_none_label: str
    # 选第一项时右侧说明（默认提及「处理整书」）
    optional_int_none_caption: str
    # 选中间预设时说明，须含占位符 ``{n}``（默认「将只处理前 **{n}** 块。」）
    optional_int_preset_caption: str
    # 为 True 时不渲染下拉下方的说明 caption（仅影响 hinted 行）
    suppress_preset_row_captions: bool
    # 预设下拉的可见标签（label_visibility 多为 collapsed，仍影响无障碍/占位）
    preset_select_label: str


# step_name 与 cli 子命令名一致（如 extract-events）
STEP_PARAM_HINTS: dict[str, dict[str, ParamHint]] = {
    "extract-events": {
        "chunk_size": {
            "ui_label": "每个事件的字符数",
            "presets": [
                (600, "偏小"),
                (800, "默认"),
                (1200, "偏大"),
            ],
        },
        "chunk_overlap": {
            "ui_label": "相邻事件重叠字符数",
            "presets": [
                (80, "小重叠"),
                (160, "默认"),
                (200, "大重叠"),
            ],
        },
    },
    "extract-relations": {
        "json": {
            "note": "Typer `--json` / `-j`：事件链 `extract_chain.json`（默认与 `output_dir` 同目录）。",
        },
        "output": {
            "note": "Typer `--output` / `-o`：关系结果写出路径（默认 `output_dir/extract_relations.json`）。",
        },
        "output_dir": {
            "note": "与 extract-events 同 run 的目录；用于自动填 json/output，**不会**作为 CLI 参数传递。",
        },
    },
    "generate-content": {
        "refresh_player_choice_only": {
            "ui_label": "仅刷新分叉点选项（不重写润色内容）",
            "note": "配合单步页按钮使用：尽量保留已润色的叙述/对话/内容块。",
        },
        "force_regenerate": {
            "ui_label": "强制重新生成（不复用缓存）",
            "note": "勾选后将忽略已有结果，重新生成并覆盖写回。",
        },
    },
}


def hint_for(step_name: str, param_name: str) -> ParamHint | None:
    h = STEP_PARAM_HINTS.get(step_name, {}).get(param_name)
    if h is not None:
        return h
    if step_name == "extract-events-and-relations":
        return STEP_PARAM_HINTS.get("extract-events", {}).get(param_name)
    if step_name == "generate-mentions-and-entities":
        if param_name == "input":
            return {
                "note": "事件链 ``extract_chain.json``；默认 ``<output_dir>/extract_chain.json``。",
            }
        if param_name == "output_dir":
            return {
                "note": "同一目录下生成 ``mentions.json`` 与 ``entities.json``（Typer：``--output-dir``）。",
            }
    if step_name == "extract-states-and-baseline":
        if param_name == "output_dir":
            return {
                "note": (
                    "四类产物均写入该目录：``world_states.json``、``character_states.json``、"
                    "``relationship_states.json``、``state_baseline.json``（Typer：``--output-dir``）。"
                ),
            }
        if param_name == "events":
            return {"note": "默认 ``<output_dir>/extract_chain.json``。"}
        if param_name == "mentions":
            return {"note": "默认 ``<output_dir>/mentions.json``。"}
        if param_name == "entities":
            return {"note": "默认 ``<output_dir>/entities.json``。"}
    if step_name == "aggregate-characters-and-personas":
        if param_name == "output_dir":
            return {
                "note": (
                    "同一目录下生成 ``character_profiles.json`` 与 ``character_personas.json``"
                    "（Typer：``--output-dir``）。"
                ),
            }
        if param_name == "profiles":
            return {
                "note": "第一步写出、第二步读入；默认 ``<output_dir>/character_profiles.json``。",
            }
        if param_name == "personas_output":
            return {"note": "默认 ``<output_dir>/character_personas.json``。"}
        if param_name == "use_rules":
            return {"note": "仅影响人物画像步：``--rules`` 时用规则提取行动词，否则用 LLM。"}
    if step_name == "import-neo4j":
        if param_name == "json":
            return {
                "note": "默认 ``<output_dir>/extract_chain.json``；可改为已合并的图谱 JSON。",
            }
        if param_name == "clear":
            return {
                "ui_label": "导入前先清空图数据库",
                "note": "勾选后会在导入前清空当前库中的图数据。",
            }
        if param_name == "output_dir":
            return {"note": "仅用于自动补全 `json`，不会直接作为 CLI 参数。"}
    if step_name == "import-entities":
        if param_name == "entities":
            return {"note": "默认 ``<output_dir>/entities.json``。"}
        if param_name == "output_dir":
            return {"note": "仅用于自动补全 `entities`，不会直接作为 CLI 参数。"}
    if step_name == "import-state-changes":
        if param_name == "states":
            return {
                "note": (
                    "默认 ``<output_dir>/world_states.json``；"
                    "可手动改为 ``character_states.json`` 或 ``relationship_states.json``。"
                ),
            }
        if param_name == "output_dir":
            return {"note": "仅用于自动补全 `states`，不会直接作为 CLI 参数。"}
    if step_name == "import-neo4j-all":
        if param_name == "output_dir":
            return {"note": "统一输入目录；其余路径默认都从这里拼接。"}
        if param_name == "json":
            return {"note": "默认 ``<output_dir>/extract_chain.json``。"}
        if param_name == "entities":
            return {"note": "默认 ``<output_dir>/entities.json``。"}
        if param_name == "world_states":
            return {"note": "默认 ``<output_dir>/world_states.json``。"}
        if param_name == "character_states":
            return {"note": "默认 ``<output_dir>/character_states.json``。"}
        if param_name == "relationship_states":
            return {"note": "默认 ``<output_dir>/relationship_states.json``。"}
        if param_name == "clear":
            return {
                "ui_label": "导入前先清空图数据库",
                "note": "默认勾选：导入前清空图库，调试更干净；若要在已有数据上增量导入可取消勾选。",
            }
    if step_name == "generate-branch":
        if param_name == "max_branches_per_point":
            return {
                "ui_label": "主线以外的分叉选择个数",
                "preset_select_label": "档位",
                "suppress_preset_row_captions": True,
                "presets": [
                    (1, "偏少"),
                    (2, "默认"),
                    (3, "偏多"),
                ],
            }
    if step_name == "analyze-decision-points":
        if param_name == "target_branch_count":
            return {
                "ui_label": "分叉点数量上限",
                "optional_int": True,
                "optional_int_none_label": "不限制数量",
                "suppress_preset_row_captions": True,
                "presets": [
                    (2, ""),
                    (4, ""),
                    (6, ""),
                ],
                "expand_detail_title": "📖 决策点分析：流程与本参数说明",
                "expand_detail": (
                    "### 决策点分析在做什么\n\n"
                    "1. **第一轮**：对每个候选位置，模型判断更适合标成「**适合开分支**」还是「**建议跳过**」，"
                    "得到一份**分支候选**列表。\n"
                    "2. **第二轮**：在候选里再做一次整体取舍，给出**推荐启用**列表和理由（不是按列表顺序简单砍前 N 条）。\n"
                    "3. 写入分析文件的**最终生效分叉点**默认等于推荐；你也可以在预览里改选后保存，改成人工定稿。\n\n"
                    "### 和本参数的关系\n\n"
                    "- 填数字：只在第二轮对话里加一句「推荐尽量不要超过这么多」之类的**软提示**。\n"
                    "- 不填：第二轮不带这条数量偏好。\n\n"
                    "### 和「分支选项生成」里其它参数的区别\n\n"
                    "- **分支选项生成**里还有「**每个分叉点最多几条替代支线**」等参数；这里是**分析阶段**对「最后推荐保留多少个分叉点」的偏好，"
                    "二者不是同一个旋钮。\n"
                    "- 跑支线时，仍可能按你在分支选项生成里设的密度，对分叉点再做截取。"
                ),
            }
        if param_name == "canonical":
            return {"note": "已重排主线 JSON（``--canonical`` / ``-c``）。"}
        if param_name == "output":
            return {"note": "写出 ``decision_points_analysis.json``（``--output`` / ``-o``）。"}
    return None


def ui_label_for(step_name: str, param_name: str) -> str | None:
    h = hint_for(step_name, param_name)
    ul = h.get("ui_label") if h else None
    return str(ul).strip() if ul else None


def validate_extract_events_chunk_pair(params: dict[str, Any]) -> tuple[str | None, str | None]:
    """
    当 extract-events 同时给出 chunk_size 与 chunk_overlap（且为 int）时，检查二者耦合。

    返回 (致命错误文案, 仅警告文案)。无问题时 (None, None)。
    """
    cs = params.get("chunk_size")
    co = params.get("chunk_overlap")
    if not isinstance(cs, int) or isinstance(cs, bool):
        return None, None
    if not isinstance(co, int) or isinstance(co, bool):
        return None, None
    if cs <= 0:
        return "chunk_size 必须为正整数。", None
    if co < 0:
        return "chunk_overlap 不能为负数。", None
    if co >= cs:
        return (
            f"chunk_overlap（{co}）必须小于 chunk_size（{cs}）。"
            "请减小重叠或增大分块，避免无效分块。",
            None,
        )
    if co * 2 >= cs:
        return (
            None,
            f"提示：当前 overlap={co} 已达到 chunk_size={cs} 的 **一半及以上**，重叠比例偏高，"
            "冗余与费用会明显上升；一般建议 **overlap < chunk_size/2**（可与「大重叠」预设对齐到较小 chunk 试跑）。",
        )
    return None, None


def preset_widget_key(step_name: str, param_name: str) -> str:
    return f"{_spw_prefix(step_name)}{param_name}_preset"


def parse_int_from_preset_label(choice: str) -> int:
    """
    预设下拉项形如「160 (默认)」或「160 · 默认」，取首部整数。
    与 build_int_hint_labels 的格式保持一致。
    """
    head = choice.split(None, 1)[0].strip()
    return int(head)


def _preset_menu_label(x: int, descr: str) -> str:
    """预设第二段为空时只显示数字，避免下拉出现「2 ()」。"""
    t = str(descr).strip()
    return str(x) if not t else f"{x} ({t})"


def build_int_hint_labels(hint: ParamHint) -> tuple[list[str], bool]:
    """返回 (radio 选项列表, 是否为 optional_int 行)."""
    optional_int = bool(hint.get("optional_int"))
    presets = hint.get("presets") or []
    if optional_int:
        none_lbl = str(hint.get("optional_int_none_label") or "不传（全书）")
        labels = (
            [none_lbl]
            + [_preset_menu_label(x, d) for x, d in presets]
            + ["自定义数量…"]
        )
    else:
        labels = ["自定义输入…"] + [_preset_menu_label(x, d) for x, d in presets]
    return labels, optional_int


def use_hint_row_for_value(hint: ParamHint | None, merged_value: Any) -> bool:
    """是否对该键使用「备注 + 预设」行（而非普通 number_input）。"""
    if hint is None:
        return False
    if bool(hint.get("optional_int")):
        return True
    return isinstance(merged_value, int) and not isinstance(merged_value, bool)


def _spw_prefix(step_name: str) -> str:
    return f"spw_{step_name}_"


def initial_preset_index(
    *,
    merged_value: Any,
    presets: list[tuple[int, str]],
    optional_int: bool,
) -> int:
    """
    非 optional_int:
      0 = 自定义输入
      1..n = 第 n 个预设
    optional_int（如 max_chunks）:
      0 = 不传（全书）
      1..n = 第 n 个预设
      n+1 = 自定义数量
    """
    n = len(presets)
    if optional_int:
        if merged_value is None:
            return 0
        if isinstance(merged_value, int) and not isinstance(merged_value, bool):
            for i, (x, _) in enumerate(presets, start=1):
                if x == merged_value:
                    return i
            return n + 1  # 自定义数量
        return 0
    if isinstance(merged_value, int) and not isinstance(merged_value, bool):
        for i, (x, _) in enumerate(presets, start=1):
            if x == merged_value:
                return i
    return 0
