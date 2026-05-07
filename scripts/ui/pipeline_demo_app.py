"""
文字冒险游戏内容生成系统（Streamlit 前端）。

多页面架构：
  🏠 首页/概览 → 📥 选择小说 → ⚙️ 参数配置 → 🚀 一键运行 → 🔧 单步执行
  → 📊 运行结果 → 🎮 游戏运行

运行方式（项目根目录）：
  streamlit run scripts/ui/pipeline_demo_app.py
"""
from __future__ import annotations

import base64
from collections import Counter
import html
import json
import re
import importlib
import shutil
import subprocess
import os
import sys
from pathlib import Path
from typing import Any
import streamlit as _st

# 确保项目根在 sys.path（streamlit 直接跑本文件时无包上下文，不能用相对导入）
_project_root = Path(__file__).resolve().parent.parent.parent
if str(_project_root) not in sys.path:
    sys.path.insert(0, str(_project_root))

from scripts.ui import cli_catalog
from scripts.ui import kg_preview
from scripts.ui import param_hints
from scripts.ui import personas_preview
from scripts.ui import prompt_registry
from scripts.ui import utils
from scripts.ui import decision_points_preview
from scripts.ui import ending_candidates_preview
from scripts.ui import branches_preview
from scripts.ui import all_paths_preview
from scripts.ui import enhanced_content_preview

# Streamlit 长驻进程可能缓存旧版 builtin_steps；reload 后再取符号，避免缺「复用」函数。
import src.pipeline.builtin_steps as _builtin_mod
from src.core.neo4j_bootstrap import ensure_neo4j_ready

# 默认关闭每次 rerun 的 reload；需要热重载时可手动设置环境变量开启。
if os.environ.get("STREAMLIT_RELOAD_BUILTIN_STEPS", "").strip() == "1":
    importlib.reload(_builtin_mod)
BUILTIN_STEP_SPECS = _builtin_mod.BUILTIN_STEP_SPECS
artifacts_all_present_for_reuse = getattr(
    _builtin_mod, "artifacts_all_present_for_reuse", None
)
if artifacts_all_present_for_reuse is None:
    raise ImportError(
        "src.pipeline.builtin_steps 缺少 artifacts_all_present_for_reuse；"
        "请确认已保存最新代码并重启 Streamlit（Ctrl+C 再 streamlit run）。"
    )


# ── 常量 ──────────────────────────────────────────────────────────

_PAGES = [
    "🏠 首页/概览",
    "📥 选择小说",
    "⚙️ 参数配置",
    "🚀 一键运行",
    "🔧 单步执行",
    "📊 运行结果",
    "🎮 游戏运行",
]

# 侧栏 / 首页右侧展示的兔子组图（ui_assets 下文件名顺序）
_UI_RABBIT_ASSETS: tuple[str, ...] = (
    "rabbit.png",
    "rabbit2.png",
    "rabbit3.png",
    "rabbit4.png",
    "rabbit5.png",
    "rabbit6.png",
)
_UI_ASSETS_DIR_NAME = "ui_assets"


@_st.cache_data(show_spinner=False)
def _read_file_base64(path_str: str) -> str | None:
    """读取文件并返回 base64 字符串（跨 rerun 缓存）。"""
    p = Path(path_str)
    if not p.is_file():
        return None
    return base64.b64encode(p.read_bytes()).decode("ascii")


@_st.cache_data(show_spinner=False)
def _data_uri_for_file(path_str: str, mime: str) -> str | None:
    """返回 data URI（跨 rerun 缓存）。"""
    b64 = _read_file_base64(path_str)
    if not b64:
        return None
    return f"data:{mime};base64,{b64}"

_STEP_ICONS: dict[str, str] = {
    "extract-events": "📖",
    "extract-events-and-relations": "🔗",
    "generate-mentions-and-entities": "👤",
    "extract-states-and-baseline": "🧩",
    "aggregate-characters-and-personas": "🎭",
    "import-neo4j-all": "🗄️",
    "analyze-decision-points": "🔀",
    "generate-canonical-branch": "🌿",
    "generate-branch": "🌳",
    "generate-all-paths": "🛤️",
    "complete-all-path-events": "🧩",
    "generate-content": "✍️",
    "generate-renpy-scripts": "🎮",
    "evaluate-solution": "📊",
    "import-assets": "📦",
}

# 固定步骤顺序与中文名（只保留到 import-assets）
_PIPELINE_STEPS: list[tuple[str, str]] = [
    ("extract-events-and-relations", "① 事件与关系抽取"),
    ("generate-mentions-and-entities", "② 指代与实体识别"),
    ("extract-states-and-baseline", "③ 状态抽取与基线"),
    ("aggregate-characters-and-personas", "④ 角色档案与人设聚合"),
    ("import-neo4j-all", "⑤ 导入图数据库"),
    ("analyze-decision-points", "⑥ 决策点分析"),
    ("generate-canonical-branch", "⑦ 主线生成"),
    ("generate-branch", "⑧ 分支选项生成"),
    ("determine-ending-candidates", "⑨ 结局候选"),
    ("generate-all-paths", "⑩ 分支路径生成"),
    ("complete-all-path-events", "⑪ 路径事件补全"),
    ("generate-content", "⑫ 叙事扩写"),
]

_STEP_CHINESE: dict[str, str] = {k: v for k, v in _PIPELINE_STEPS}

_HIDDEN_PARAMS: set[str] = {
    "input", "input_path", "output", "output_dir", "output_path",
    "json", "events", "mentions", "entities",
    "char_states", "rel_states", "profiles", "personas_output",
    "world_states", "character_states", "relationship_states",
    "canonical", "branches", "branches_path", "states",
    "max_chunks",
}

_STEP_SHORT_DESC: dict[str, str] = {
    "extract-events-and-relations": "从原文切块抽取剧情事件，并分析事件间的因果/时序关系。",
    "generate-mentions-and-entities": "识别人物指代（他、她、老师…）并聚合成实体。",
    "extract-states-and-baseline": "抽取世界/人物/关系状态，建立初始基线。",
    "aggregate-characters-and-personas": "把零散信息汇聚成完整的角色档案与人设。",
    "import-neo4j-all": "将事件图谱、实体与状态导入 Neo4j 图数据库（可选）。",
    "analyze-decision-points": "在事件链中找出适合做「玩家选择」的分岔点。",
    "generate-canonical-branch": "基于决策点生成一条主线走向。",
    "determine-ending-candidates": "整理可能的结局方向，为分支收束做准备。",
    "generate-branch": "从决策点长出多条支线。",
    "generate-all-paths": "枚举所有从开头到结局的路径组合。",
    "complete-all-path-events": "补全每条路径上的事件细节。",
    "generate-content": "为每条路径写出可读的叙述、对话与选项。",
    "generate-renpy-scripts": "将运行结果（enhanced_paths）转为 Ren'Py 游戏脚本。",
    "import-assets": "导入游戏资源文件。",
}


def _cli_params_for_step(step_name: str, params: dict[str, Any]) -> dict[str, Any]:
    """剔除 Typer 不认识的键（如 extract-relations 仅用于 UI 合并的 output_dir）。"""
    d = dict(params)
    if step_name == "extract-relations":
        d.pop("output_dir", None)
    if step_name in ("generate-mentions", "generate-entities"):
        d.pop("output_dir", None)
    if step_name in (
        "extract-world-states",
        "extract-character-states",
        "extract-relationship-states",
        "init-state-baseline",
        "aggregate-characters",
        "aggregate-personas",
    ):
        d.pop("output_dir", None)
    if step_name in ("import-neo4j", "import-entities", "import-state-changes"):
        d.pop("output_dir", None)
    # extract-events-and-relations / extract-states-and-baseline 的 Typer 需要 --output-dir
    return d


def _neo4j_browser_link_row(st) -> None:
    """导入图数据库步骤旁：Neo4j Browser 快捷入口（默认 http://localhost:7474）。"""
    url = (os.environ.get("NEO4J_BROWSER_URL") or "http://localhost:7474").strip()
    st.link_button("🔭 打开 Neo4j Browser", url)


def _goto(st, page: str) -> None:
    """
    请求切换到指定页并 rerun。

    不能在同一次运行里直接改 st.session_state.nav_page：侧边栏的 st.radio
    已占用该 key，会触发 StreamlitAPIException。改为写入 _nav_pending，
    在 main() 开头、创建 radio 之前再写入 nav_page。
    """
    st.session_state["_nav_pending"] = page
    st.rerun()


def _ui_asset_data_uri(ui_dir: Path, filename: str) -> str | None:
    """返回 ui_assets 下图片的 data URI；无文件则 None。"""
    p = _project_root / "assets" / _UI_ASSETS_DIR_NAME / filename
    if not p.is_file():
        return None
    suf = p.suffix.lower()
    mime = "image/png" if suf == ".png" else "image/jpeg"
    return _data_uri_for_file(str(p), mime)


def _rabbit_sticker_tags(ui_dir: Path, *, variant: str) -> str:
    """输出带槽位类的兔子贴纸 ``<img>``（``rabbit-sticker--{variant}-{1..6}``），由 CSS 分散定位。"""
    parts: list[str] = []
    for slot, name in enumerate(_UI_RABBIT_ASSETS, start=1):
        uri = _ui_asset_data_uri(ui_dir, name)
        if not uri:
            continue
        cls = f"rabbit-sticker rabbit-sticker--{variant}-{slot}"
        parts.append(f'<img class="{cls}" src="{uri}" alt="" />')
    return "".join(parts)


def _rabbit_sticker_single(ui_dir: Path, slot: int, *, extra_style: str = "") -> str:
    """返回单只兔子贴纸的 <img> HTML，用于内联到各内容块旁。"""
    if slot < 1 or slot > len(_UI_RABBIT_ASSETS):
        return ""
    uri = _ui_asset_data_uri(ui_dir, _UI_RABBIT_ASSETS[slot - 1])
    if not uri:
        return ""
    return (
        f'<img class="rabbit-inline-sticker" '
        f'src="{uri}" alt="" '
        f'style="{extra_style}" />'
    )


def _midable_xiaolai_font_face_css(ui_dir: Path) -> str:
    """Midable：仅拉丁（unicode-range）；小赖：「Xiaolai SC」全字表。

    全局 font-family 须把 Midable 写在 Xiaolai 之前：浏览器按字符选第一个有字形的字体，
    拉丁走 Midable，汉字无 Midable 字形则落到 Xiaolai SC。
    """
    parts: list[str] = []
    mid = _project_root / "assets" / _UI_ASSETS_DIR_NAME / "midable" / "OpenType-TT" / "Midable.ttf"
    if mid.is_file():
        b64 = _read_file_base64(str(mid))
        if not b64:
            b64 = ""
        parts.append(
            f"""
        @font-face {{
            font-family: "Midable";
            src: url("data:font/ttf;base64,{b64}") format("truetype");
            font-weight: 400;
            font-style: normal;
            font-display: swap;
            unicode-range: U+0000-00FF, U+0100-024F, U+0250-02AF, U+1E00-1EFF;
        }}
            """
        )
    xia = _project_root / "assets" / _UI_ASSETS_DIR_NAME / "xiaolai" / "XiaolaiSC-Regular.ttf"
    if xia.is_file():
        xia_b64 = _read_file_base64(str(xia))
        if not xia_b64:
            xia_b64 = ""
        src_css = f"""
            src: url("data:font/ttf;base64,{xia_b64}") format("truetype");
            font-weight: 400;
            font-style: normal;
            font-display: swap;
        """
        parts.append(
            f"""
        @font-face {{
            font-family: "Xiaolai SC";
            {src_css}
        }}
        @font-face {{
            font-family: "Xiaolai";
            {src_css}
        }}
            """
        )
    return "".join(parts)


def _apply_home_background(st, enabled: bool) -> None:
    """仅在「🏠 首页/概览」为主区叠浅蓝渐变；粒子在全局 CSS 的 stMain::before 上，避免被本处覆盖。"""
    if not enabled:
        st.markdown(
            """
            <style>
            [data-testid="stMain"] {
                padding-bottom: 0 !important;
            }
            </style>
            """,
            unsafe_allow_html=True,
        )
        return
    st.markdown(
        """
        <style>
        [data-testid="stMain"] {
            padding-bottom: 1.25rem !important;
            background-color: rgba(232, 245, 252, 0.96) !important;
            background-image: linear-gradient(
                rgba(232, 244, 252, 0.82),
                rgba(215, 232, 248, 0.62)
            ) !important;
            background-size: 100% 100% !important;
            background-repeat: no-repeat !important;
            background-position: 0 0 !important;
        }
        </style>
        """,
        unsafe_allow_html=True,
    )


def _apply_sidebar_background(st, ui_dir: Path) -> None:
    """为左侧栏铺底图（ui_assets/background_left.jpg），与小黄卡叠放；无文件则不改样式。

    侧栏「浅蓝罩」有两处可改：① 本函数里 linear-gradient 的 rgba（盖在底图上）；
    ② 全局注入样式中 section[data-testid="stSidebar"]::after 的 background（再叠一层统一主区色调）。
    """
    bg_path = _project_root / "assets" / _UI_ASSETS_DIR_NAME / "background_left.jpg"
    if not bg_path.is_file():
        return
    mime = "image/jpeg"
    b64 = _read_file_base64(str(bg_path))
    if not b64:
        return
    st.markdown(
        f"""
        <style>
        section[data-testid="stSidebar"] {{
            background-image:
                linear-gradient(
                    rgba(232, 244, 252, 0.4),
                    rgba(210, 228, 246, 0.8)
                ),
                url("data:{mime};base64,{b64}") !important;
            background-size: cover, cover !important;
            background-position: left center, left center !important;
            background-repeat: no-repeat, no-repeat !important;
            background-attachment: scroll, scroll !important;
        }}
        </style>
        """,
        unsafe_allow_html=True,
    )


def _apply_header_background_top(st, ui_dir: Path) -> None:
    """右上顶栏 stHeader 铺底图（ui_assets/background_top.jpg）：薄荷蓝叠层 + 单层图。无文件则保持全局薄荷蓝顶栏。"""
    bg_path = _project_root / "assets" / _UI_ASSETS_DIR_NAME / "background_top.jpg"
    if not bg_path.is_file():
        return
    mime = "image/jpeg"
    b64 = _read_file_base64(str(bg_path))
    if not b64:
        return
    st.markdown(
        f"""
        <style>
        header[data-testid="stHeader"] {{
            overflow: hidden !important;
            background-image:
                linear-gradient(
                    rgba(208, 244, 248, 0.78),
                    rgba(186, 235, 242, 0.58)
                ),
                url("data:{mime};base64,{b64}") !important;
            /* 比 cover 再放大一档（约 155% 宽），顶栏裁切；想再大一点可把 155% 调到 175% */
            background-size: 100% 100%, 155% auto !important;
            background-position: 0 0, center top !important;
            background-repeat: no-repeat, no-repeat !important;
            background-attachment: scroll, scroll !important;
        }}
        </style>
        """,
        unsafe_allow_html=True,
    )


def _mirror_select_novel_widgets_to_persist(st) -> None:
    """把 ui_run_id 等会话键镜像到 _persist_*，避免离开选择页后丢失。"""
    for widget_key, persist_key in (
        ("ui_run_id", "_persist_ui_run_id"),
        ("ui_output_root", "_persist_ui_output_root"),
        ("ui_config_out", "_persist_ui_config_out"),
    ):
        if widget_key in st.session_state:
            st.session_state[persist_key] = st.session_state[widget_key]


def _effective_ui_run_id(st) -> str:
    return (
        st.session_state.get("ui_run_id")
        or st.session_state.get("_persist_ui_run_id")
        or ""
    ).strip()


def _effective_ui_output_root(st) -> str:
    return (
        st.session_state.get("ui_output_root")
        or st.session_state.get("_persist_ui_output_root")
        or ""
    ).strip()


def _effective_ui_config_out(st) -> str:
    return (
        st.session_state.get("ui_config_out")
        or st.session_state.get("_persist_ui_config_out")
        or ""
    ).strip()


def _parse_step_params_from_ui(st, steps: dict[str, Any]) -> tuple[dict[str, Any], list[str]]:
    """读取 UI 中每个步骤的 enabled/params，返回新 steps 和错误列表。"""
    new_steps: dict[str, Any] = {}
    errors: list[str] = []

    for step_name, step_cfg in steps.items():
        enabled_default = bool(step_cfg.get("enabled", True))
        params_default = step_cfg.get("params") or {}
        if not isinstance(params_default, dict):
            params_default = {}

        icon = _STEP_ICONS.get(step_name, "⚙️")
        status_tag = "✅" if enabled_default else "⏸️"
        label = f"{icon} {step_name}  {status_tag}"

        with st.expander(label, expanded=False):
            enabled = st.checkbox(
                "enabled",
                value=enabled_default,
                key=f"enabled_{step_name}",
            )
            params_text = st.text_area(
                "params (JSON object)",
                value=json.dumps(params_default, ensure_ascii=False, indent=2),
                key=f"params_{step_name}",
                height=140,
            )
            try:
                parsed = json.loads(params_text) if params_text.strip() else {}
                if not isinstance(parsed, dict):
                    raise ValueError("params 必须是 JSON 对象")
            except Exception as exc:
                parsed = params_default
                errors.append(f"步骤 `{step_name}` 的 params JSON 无效：{exc}")

        new_steps[step_name] = {"enabled": enabled, "params": parsed}

    return new_steps, errors


# ── Page: 首页/概览 ───────────────────────────────────────────────

_ARROW_SVG = (
    '<svg class="home-pipeline-arrow-icon" viewBox="0 0 24 24" width="22" height="22" '
    'aria-hidden="true" focusable="false">'
    '<path fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" '
    'stroke-linejoin="round" d="M5 12h14m0 0-4-4m4 4-4 4"/></svg>'
)


def _render_pipeline_overview(st) -> None:
    """流水线阶段总览：等宽卡片 + SVG 箭头；title 为原生 tooltip。"""
    step_data = [
        ("事件抽取", "从原文提取关键叙事事件"),
        ("决策点分析", "识别可产生分支的故事节点"),
        ("主线生成", "构建故事主干路径"),
        ("分支选项生成", "基于决策点生成可选支线"),
        ("叙事扩写", "为每条路径生成对话与描写"),
        ("脚本输出", "Ren'Py 可执行脚本导入游戏工程"),
    ]
    parts: list[str] = []
    for i, (title, desc) in enumerate(step_data):
        if i > 0:
            parts.append(f'<div class="home-pipeline-flow-arrow">{_ARROW_SVG}</div>')
        tip = f"{title}：{desc}"
        tip_attr = html.escape(tip, quote=True)
        parts.append(
            f"""
        <div class="home-pipeline-card" title="{tip_attr}">
            <div class="home-pipeline-card__title">{html.escape(title)}</div>
            <div class="home-pipeline-card__desc">{html.escape(desc)}</div>
        </div>"""
        )
    st.markdown(
        f"""
        <div class="home-pipeline-overview-wrap">
            {"".join(parts)}
        </div>
        """,
        unsafe_allow_html=True,
    )


def _render_sidebar_status_expanded(st, novel: str, rid: str, oroot: str) -> None:
    """展开后详情：未选时提示；已选时仅 run_id / 输出根目录（项目名在 expander 标题上）。"""
    if novel == "未选择":
        st.markdown(
            """
            <div style="background:rgba(24,48,72,0.03);border-radius:10px;
                        padding:0.8rem;text-align:center;color:rgba(24,48,72,0.45);
                        font-size:0.82rem;">
                💡 选择小说后这里显示运行状态
            </div>
            """,
            unsafe_allow_html=True,
        )
        return
    st.markdown(
        f"""
        <div style="background:rgba(24,48,72,0.03);border-radius:10px;
                    padding:0.7rem 0.8rem;font-size:0.82rem;line-height:1.8;
                    color:#183048;">
            <div style="color:rgba(24,48,72,0.6);">🏷️ run_id <code style="
                background:rgba(24,48,72,0.06);padding:0.1rem 0.35rem;
                border-radius:4px;font-size:0.78rem;">{html.escape(rid)}</code></div>
            <div style="color:rgba(24,48,72,0.6);word-break:break-all;">
                📂 输出根目录 <code style="background:rgba(24,48,72,0.06);
                padding:0.1rem 0.35rem;border-radius:4px;
                font-size:0.78rem;">{html.escape(oroot)}</code></div>
        </div>
        """,
        unsafe_allow_html=True,
    )


def _page_home(st, ui_dir: Path) -> None:
    r1 = _rabbit_sticker_single(ui_dir, 1, extra_style="position:absolute;top:-0.3rem;right:-2rem;width:50px;transform:rotate(-12deg);")
    r2 = _rabbit_sticker_single(ui_dir, 2, extra_style="position:absolute;bottom:-0.1rem;right:-1rem;width:38px;transform:rotate(8deg);")
    r3 = _rabbit_sticker_single(ui_dir, 3, extra_style="position:absolute;top:18rem;left:1rem;width:36px;transform:rotate(6deg);")
    r4 = _rabbit_sticker_single(ui_dir, 4, extra_style="position:absolute;bottom:8.4rem;left:13rem;width:40px;transform:rotate(-7deg);")
    r5 = _rabbit_sticker_single(ui_dir, 6, extra_style="position:absolute;top:-0.2rem;right:1.2rem;width:38px;transform:rotate(-5deg);")
    r6 = _rabbit_sticker_single(ui_dir, 5, extra_style="position:absolute;bottom:9.5rem;left:8rem;width:36px;transform:rotate(10deg);")

    # 标题区：兔子1贴在标题右边，兔子3贴在副标题左边
    st.markdown(
        f"""
        <div class="home-sticker-surface" style="position:relative;overflow:visible;">
        <div class="home-hero-row">
            <div class="home-hero-text" style="position:relative;">
              <div class="home-main-title" style="position:relative;display:inline-block;">
                <h2>文字冒险游戏内容生成系统</h2>
                {r1}
          </div>
              <div class="home-intro-lede" style="position:relative;">
                <p>将小说自动转换为可在 <strong>Ren'Py</strong> 运行的多分支文字冒险脚本。</p>
                {r2}
              </div>
            </div>
          </div>
        </div>
        """,
        unsafe_allow_html=True,
    )

    with st.container(border=True):
        # 兔子3贴在「流水线阶段总览」左上角，兔子4贴在右下角
        st.markdown(
            f'<span class="home-pipeline-section-start" aria-hidden="true" '
            f'style="position:relative;display:block;overflow:visible;height:0;">{r3}</span>',
            unsafe_allow_html=True,
        )
        st.subheader("流水线阶段总览")
        _render_pipeline_overview(st)
        st.markdown(
            f'<span aria-hidden="true" style="position:relative;display:block;overflow:visible;height:0;">{r4}</span>',
            unsafe_allow_html=True,
        )

    st.markdown(
        '<div class="home-after-pipeline-gap" aria-hidden="true"></div>',
        unsafe_allow_html=True,
    )
    with st.container(border=True):
        # 兔子5贴在「快速开始」右上角，兔子6贴在右下角
        st.markdown(
            f'<span class="home-quickstart-cta-anchor" aria-hidden="true" '
            f'style="position:relative;display:block;overflow:visible;height:0;">{r5}</span>',
            unsafe_allow_html=True,
        )
        st.subheader("快速上手")
        st.markdown(
            "1. **选择小说** — 在书库中选择 txt，或上传 txt/epub 文件\n"
            "2. **参数配置** — 按需要调整参数，准备后续执行流程\n"
            "3. **一键运行** 或 **单步执行** — 完成 12 步流水线，直达 **叙事扩写**\n"
            "4. **运行结果** — 查看并按需编辑各路径结果文件\n"
            "5. **游戏运行** — 启动 Ren'Py，并导入脚本与资源"
        )
        st.markdown(
            f'<span aria-hidden="true" style="position:relative;display:block;overflow:visible;height:0;">{r6}</span>',
            unsafe_allow_html=True,
        )
        h1, h2 = st.columns(2)
        with h1:
            if st.button("开始使用", type="primary", use_container_width=True):
                _goto(st, "📥 选择小说")
        with h2:
            if st.button("去运行结果", use_container_width=True):
                _goto(st, "📊 运行结果")


# ── Page: 选择小说 ────────────────────────────────────────────────

def _page_select_novel(st, project_root: Path) -> None:
    st.header("📥 选择小说")
    st.caption("上传支持 txt / epub")

    input_source = st.radio(
        "选择输入方式", ["从项目文件选择", "上传文件"], index=0, horizontal=True
    )
    uploaded_saved_path: Path | None = None
    selected_project_file: Path | None = None

    if input_source == "从项目文件选择":
        st.session_state.pop("novel_upload_original_stem", None)
        _library_root = project_root / "experiments" / "input_library"
        browse_files, browse_error = utils.list_candidate_files(
            project_root=project_root,
            browse_root_raw="experiments/input_library",
            recursive=True,
            suffixes=[".txt"],
        )
        if browse_error:
            st.warning(browse_error)
        elif not browse_files:
            st.info("输入库中还没有 txt，可先「上传文件」归档。")
        else:
            shorts: list[str] = []
            for p in browse_files:
                try:
                    shorts.append(str(p.relative_to(_library_root)).replace("\\", "/"))
                except ValueError:
                    shorts.append(p.name)
            dup_count = Counter(shorts)
            seen_n: dict[str, int] = {}
            file_labels: list[str] = []
            for s in shorts:
                if dup_count[s] <= 1:
                    file_labels.append(s)
                    continue
                seen_n[s] = seen_n.get(s, 0) + 1
                k = seen_n[s]
                file_labels.append(s if k == 1 else f"{s} · {k}")
            selected_label = st.selectbox("选择小说文件", options=file_labels)
            selected_project_file = browse_files[file_labels.index(selected_label)]

    if input_source == "上传文件":
        uploaded_file = st.file_uploader("上传 txt / epub 文件", type=["txt", "epub"])
        if uploaded_file is not None:
            # 展示名用用户原始文件名（中文等），归档名可能已规范为拼音 slug
            st.session_state["novel_upload_original_stem"] = Path(uploaded_file.name).stem
            uploaded_saved_path, _, upload_msg = utils.save_uploaded_file_to_library(
                project_root, uploaded_file
            )
            st.caption(f"{upload_msg}: {uploaded_saved_path}")
        else:
            st.session_state.pop("novel_upload_original_stem", None)

    selected_input = utils.infer_selected_input_path(
        input_source=input_source,
        selected_project_file=selected_project_file,
        uploaded_saved_path=uploaded_saved_path,
    )

    if selected_input is not None:
        identity = utils.build_novel_identity(selected_input.name)
        st.session_state["selected_input_path"] = str(selected_input)
        st.session_state["novel_identity"] = identity
        # 展示名：去括号内容；优先用上传时的原始文件名（避免归档名被改成拼音）
        title_stem = st.session_state.get("novel_upload_original_stem") or Path(
            selected_input.name
        ).stem
        st.session_state["novel_display_name"] = utils.normalize_novel_title(title_stem)

    # ── run_id / output_root / config_out：仅后台自动填充（无表单项）──
    source_signature = str(selected_input.resolve()) if selected_input is not None else ""
    hint_identity: dict[str, str] | None = (
        st.session_state.get("novel_identity") if selected_input is not None else None
    )

    if hint_identity:
        suggested_run_id = hint_identity["default_run_id"]
    else:
        suggested_run_id = "demo_<novel_pinyin>"

    for key in [
        "ui_run_id", "ui_run_id_auto_prev",
        "ui_output_root", "ui_output_root_auto_prev",
        "ui_config_out", "ui_config_out_auto_prev",
        "ui_source_signature_prev",
    ]:
        if key not in st.session_state:
            st.session_state[key] = ""

    for persist_key, widget_key in (
        ("_persist_ui_run_id", "ui_run_id"),
        ("_persist_ui_output_root", "ui_output_root"),
        ("_persist_ui_config_out", "ui_config_out"),
    ):
        pv = (st.session_state.get(persist_key) or "").strip()
        wv = (st.session_state.get(widget_key) or "").strip()
        if pv and not wv:
            st.session_state[widget_key] = st.session_state[persist_key]

    if (not st.session_state["ui_run_id"].strip()) or (
        st.session_state["ui_run_id"] == st.session_state["ui_run_id_auto_prev"]
    ):
        st.session_state["ui_run_id"] = suggested_run_id
    st.session_state["ui_run_id_auto_prev"] = suggested_run_id

    if hint_identity:
        hint_run_id = st.session_state["ui_run_id"].strip() or hint_identity["default_run_id"]
        suggested_output_root = f"runs/ui/{hint_run_id}"
        suggested_config_out = f"configs/experiments/ui_{hint_run_id}.json"
    else:
        suggested_output_root = ""
        suggested_config_out = ""

    source_changed = source_signature != st.session_state["ui_source_signature_prev"]
    if source_changed and suggested_output_root:
        st.session_state["ui_output_root"] = suggested_output_root
        st.session_state["ui_config_out"] = suggested_config_out
    if suggested_output_root and (
        (not st.session_state["ui_output_root"].strip())
        or (st.session_state["ui_output_root"] == st.session_state["ui_output_root_auto_prev"])
    ):
        st.session_state["ui_output_root"] = suggested_output_root
    if suggested_config_out and (
        (not st.session_state["ui_config_out"].strip())
        or (st.session_state["ui_config_out"] == st.session_state["ui_config_out_auto_prev"])
    ):
        st.session_state["ui_config_out"] = suggested_config_out
    st.session_state["ui_output_root_auto_prev"] = suggested_output_root
    st.session_state["ui_config_out_auto_prev"] = suggested_config_out
    st.session_state["ui_source_signature_prev"] = source_signature

    _mirror_select_novel_widgets_to_persist(st)

    if selected_input is not None:
        st.divider()
        st.success(f"✅ 已选择：**{st.session_state.get('novel_display_name', '')}**")
        if st.button("👉 下一步：参数配置", type="primary", use_container_width=True):
                _goto(st, "⚙️ 参数配置")


# ── Page: 参数配置 ────────────────────────────────────────────────

def _page_configure(st, project_root: Path, default_base: Path) -> None:
    st.header("⚙️ 参数配置")

    if not st.session_state.get("selected_input_path"):
        st.warning("请先在「📥 选择小说」页面选择一个输入文件。")
        if st.button("👉 去选择小说"):
            _goto(st, "📥 选择小说")
        return

    novel_name = st.session_state.get("novel_display_name", "—")
    st.caption(f"当前小说：**{novel_name}**")

    # 流程模板：对新手隐藏（避免干扰）。仍使用默认模板作为底座。
    st.session_state["base_config_path"] = str(default_base)
    base_path = Path(default_base)
    if not base_path.is_absolute():
        base_path = project_root / base_path

    has_visible = False
    for step_name, cn_name in _PIPELINE_STEPS:
        # 参数配置页：标题不显示“①②③…”编号（单步执行页仍保留编号）。
        cn_name_cfg = re.sub(r"^[\s①②③④⑤⑥⑦⑧⑨⑩⑪⑫]+", "", str(cn_name)).strip()
        try:
            merged = utils.merge_step_params_for_ui(
                base_config_path=base_path,
                step_name=step_name,
                step_overrides=st.session_state.get("step_overrides") or None,
                project_root=project_root,
                session_selected_input_path=st.session_state.get("selected_input_path"),
                session_ui_run_id=_effective_ui_run_id(st) or None,
            )
        except Exception:
            continue
        visible = {
            k: v for k, v in merged.items()
            if k not in _HIDDEN_PARAMS
            and k not in ("decision_density", "decision_density_ratio")
        }
        if step_name == "generate-branch":
            visible = {k: v for k, v in visible.items() if k == "max_branches_per_point"}
        if step_name == "complete-all-path-events":
            # `input_dir` 会由运行时根据当前 run 自动推导（或从模板继承），不应该在前端展示
            visible = {k: v for k, v in visible.items() if k != "input_dir"}
        if step_name == "generate-content":
            # 叙事扩写的输出/input/personas/player_choice_catalog 均由运行时自动推导，
            # 前端仅保留非路径类开关，避免用户改这些“路径型参数”。
            visible = {
                k: v
                for k, v in visible.items()
                if k not in ("personas", "player_choice_catalog", "rebuild_player_choice_catalog")
            }
            # 不在这里暴露 rebuild_player_choice_catalog：
            # 单步页会用“生成模式”两个勾选框统一控制（避免出现 3 个勾选）。
        if step_name == "generate-renpy-scripts":
            # Ren'Py 导入同样由运行时自动推导增强文本目录与游戏目录，
            # 前端不再展示这些路径参数。
            visible = {
                k: v
                for k, v in visible.items()
                if k
                not in (
                    "enhanced_paths",
                    "game_dir",
                    "personas",
                    "entities",
                )
            }
        if step_name == "generate-all-paths":
            visible = {
                k: v
                for k, v in visible.items()
                if k not in ("ending_candidates", "decision_analysis")
            }
        
        if not visible:
            continue
        has_visible = True
        with st.expander(f"**{cn_name_cfg}**", expanded=True):
            for k in sorted(visible.keys()):
                v = visible[k]
                wk = f"cfg_{step_name}_{k}"
                _ul = param_hints.ui_label_for(step_name, k)
                label = _ul if _ul else f"`{k}`"
                if isinstance(v, bool):
                    st.checkbox(label, value=v, key=wk)
                elif isinstance(v, int) and not isinstance(v, bool):
                    st.number_input(label, value=int(v), step=1, key=wk)
                elif isinstance(v, float):
                    st.number_input(label, value=float(v), format="%.12g", key=wk)
                elif v is None:
                    st.text_input(label, value="", key=wk, placeholder="留空则不传该参数")
                else:
                    st.text_input(label, value=str(v), key=wk)
            _maybe_show_chunk_partition_ratio(st, step_name, merged, cfg_key_prefix="cfg")
            if step_name == "import-neo4j-all":
                _neo4j_browser_link_row(st)

    if not has_visible:
        st.info("当前模板中所有步骤均无需用户调参（路径已自动填充）。")

    st.divider()
    b1, b2 = st.columns(2)
    with b1:
        if st.button("👉 一键运行", type="primary", use_container_width=True):
            _goto(st, "🚀 一键运行")
    with b2:
        if st.button("👉 单步执行", use_container_width=True):
            _goto(st, "🔧 单步执行")


# ── Page: 一键运行 ────────────────────────────────────────────────

def _page_run(st, project_root: Path, default_base: Path) -> None:
    st.header("🚀 一键运行")
    st.caption(
        "按步骤顺序自动执行完整流程。运行可能**较久**，请勿关闭页面。"
        " 建议先在「⚙️ 参数配置」页确认参数。"
    )

    selected_input_str = st.session_state.get("selected_input_path")
    if not selected_input_str:
        st.warning("请先在「📥 选择小说」页面选择输入文件。")
        if st.button("👉 去选择小说"):
            _goto(st, "📥 选择小说")
        return

    resolved_input_path = Path(selected_input_str)
    novel_name = st.session_state.get("novel_display_name", "—")
    run_id_val = _effective_ui_run_id(st)
    output_root_val = _effective_ui_output_root(st)
    config_out_val = _effective_ui_config_out(st)
    base_config_path = st.session_state.get("base_config_path", str(default_base))

    effective_run_id = utils.derive_run_id(
        run_id_input=run_id_val, source_path=resolved_input_path
    )
    effective_output = output_root_val or f"runs/ui/{effective_run_id}"

    # ── 配置摘要 ──
    st.subheader("📋 配置摘要")
    st.caption(f"小说：**{novel_name}**")

    step_overrides = st.session_state.get("step_overrides")
    if not step_overrides:
        try:
            base_cfg = utils.load_json(Path(base_config_path))
            base_steps = base_cfg.get("steps", {})
            step_overrides = {
                name: {"enabled": cfg.get("enabled", True), "params": cfg.get("params") or {}}
                for name, cfg in base_steps.items()
            }
        except Exception:
            st.error("无法加载默认流程模板，请确认项目文件完整，或重启前端后重试。")
            return

    flow_parts = []
    for sn, cn in _PIPELINE_STEPS:
        icon = _STEP_ICONS.get(sn, "⚙️")
        flow_parts.append(f"{icon} {cn}")
    st.markdown("**执行流程：** " + " → ".join(flow_parts))

    st.divider()
    generate_and_run = st.button("▶️ 开始运行", type="primary", use_container_width=True)

    if generate_and_run:
        try:
            cfg, novel_slug = utils.build_single_run_config(
                project_root=project_root,
                base_config_path=Path(base_config_path),
                input_path=resolved_input_path,
                effective_run_id=effective_run_id,
                output_root=output_root_val,
                step_overrides=step_overrides,
            )
        except Exception as exc:
            st.error(f"配置生成失败：{exc}")
            return

        # 修正：确保 generate-all-paths 的关键路径落在当前 run 的 runs/ui/<run_id>/out 下
        # （避免从模板继承到项目根 out/，导致读旧文件或路径不一致）
        try:
            merged_all_paths = utils.merge_step_params_for_ui(
                base_config_path=Path(base_config_path),
                step_name="generate-all-paths",
                step_overrides=step_overrides,
                project_root=project_root,
                session_selected_input_path=str(resolved_input_path),
                session_ui_run_id=effective_run_id,
            )
            cfg_steps = cfg.get("steps", {})
            cfg_gap = cfg_steps.get("generate-all-paths")
            if isinstance(cfg_gap, dict):
                cfg_gap["params"] = merged_all_paths
            merged_complete = utils.merge_step_params_for_ui(
                base_config_path=Path(base_config_path),
                step_name="complete-all-path-events",
                step_overrides=step_overrides,
                project_root=project_root,
                session_selected_input_path=str(resolved_input_path),
                session_ui_run_id=effective_run_id,
            )
            cfg_complete = cfg_steps.get("complete-all-path-events")
            if isinstance(cfg_complete, dict):
                cfg_complete["params"] = merged_complete
            merged_content = utils.merge_step_params_for_ui(
                base_config_path=Path(base_config_path),
                step_name="generate-content",
                step_overrides=step_overrides,
                project_root=project_root,
                session_selected_input_path=str(resolved_input_path),
                session_ui_run_id=effective_run_id,
            )
            cfg_content = cfg_steps.get("generate-content")
            if isinstance(cfg_content, dict):
                cfg_content["params"] = merged_content
            merged_renpy = utils.merge_step_params_for_ui(
                base_config_path=Path(base_config_path),
                step_name="generate-renpy-scripts",
                step_overrides=step_overrides,
                project_root=project_root,
                session_selected_input_path=str(resolved_input_path),
                session_ui_run_id=effective_run_id,
            )
            cfg_renpy = cfg_steps.get("generate-renpy-scripts")
            if isinstance(cfg_renpy, dict):
                cfg_renpy["params"] = merged_renpy
            merged_import = utils.merge_step_params_for_ui(
                base_config_path=Path(base_config_path),
                step_name="import-assets",
                step_overrides=step_overrides,
                project_root=project_root,
                session_selected_input_path=str(resolved_input_path),
                session_ui_run_id=effective_run_id,
            )
            cfg_import = cfg_steps.get("import-assets")
            if isinstance(cfg_import, dict):
                cfg_import["params"] = merged_import
        except Exception:
            pass

        config_rel = config_out_val or f"configs/experiments/ui_{effective_run_id}.json"
        out_path = project_root / config_rel
        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.write_text(
            json.dumps(cfg, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
        )

        st.caption(f"配置已写入 `{config_rel}`")

        if True:
            st.divider()
            with st.status("🔄 正在运行 Pipeline …", expanded=True) as run_status:
                log_placeholder = st.empty()
                _all_pp = utils.materialize_prompt_slots_to_temp_files(
                    project_root,
                    effective_run_id,
                    st.session_state,
                    list(prompt_registry.ALL_FILE_SLOT_IDS),
                )
                code = utils.run_pipeline_and_stream(
                    project_root,
                    config_rel,
                    log_placeholder,
                    prompt_path_by_slot=_all_pp,
                )
                if code == 0:
                    run_status.update(label="✅ Pipeline 运行完成！", state="complete")
                    st.balloons()
                    _personas_out = (
                        project_root
                        / Path(str(effective_output).strip())
                        / "out"
                        / "character_personas.json"
                    )
                    if _personas_out.is_file():
                        st.divider()
                        personas_preview.render_in_streamlit(st, _personas_out)
                else:
                    run_status.update(
                        label=f"❌ Pipeline 失败（退出码 {code}）", state="error"
                    )


def _page_tools(st, project_root: Path, default_base: Path) -> None:
    st.header("🎮 游戏运行")
    st.caption("将生成的脚本与资源同步到 Ren'Py 工程（本机需安装 Ren'Py SDK）。")

    selected_input_str = st.session_state.get("selected_input_path")
    if not selected_input_str:
        st.warning("请先在「📥 选择小说」页面选择输入文件。")
        if st.button("👉 去选择小说"):
            _goto(st, "📥 选择小说")
        return

    # 工具页同样需要 run_id / out 目录信息（用于推导 enhanced_paths 等路径）
    resolved_input_path = Path(selected_input_str)
    run_id_val = _effective_ui_run_id(st)
    effective_run_id = utils.derive_run_id(
        run_id_input=run_id_val, source_path=resolved_input_path
    )
    base_config_path = st.session_state.get("base_config_path", str(default_base))
    base_cfg_path = Path(base_config_path)
    if not base_cfg_path.is_absolute():
        base_cfg_path = project_root / base_cfg_path

    pending_key = "_ui_renpy_game_dir_pending"
    pending_val = (st.session_state.pop(pending_key, None) or "").strip()
    if pending_val:
        st.session_state["ui_renpy_game_dir"] = pending_val
    _default_gd = project_root / "wangfo" / "game"
    if not (st.session_state.get("ui_renpy_game_dir") or "").strip() and _default_gd.is_dir():
        st.session_state["ui_renpy_game_dir"] = str(_default_gd)

    st.subheader("🎮 Ren'Py 脚本导入")
    with st.expander("导入到 Ren'Py 工程（game 目录）", expanded=True):
        st.caption(
            "运行前请确保你已经完成「叙事扩写」，并在 Ren'Py Launcher 创建好了工程。"
            " 下方路径与 **资源导入** 共用。"
        )

        c1 = st.columns([1])[0]
        with c1:
            if st.button("📁 浏览选择", use_container_width=True):
                try:
                    import tkinter as tk
                    from tkinter import filedialog

                    root = tk.Tk()
                    root.withdraw()
                    root.attributes("-topmost", True)
                    selected = filedialog.askdirectory(
                        title="请选择 Ren'Py 工程的 game 目录"
                    )
                    try:
                        root.destroy()
                    except Exception:
                        pass
                    if selected:
                        st.session_state[pending_key] = selected
                        st.rerun()
                except Exception as exc:
                    st.warning(f"当前环境无法弹出目录选择窗口，请手动粘贴路径。原因：{exc}")
        st.text_input(
            "`game` 目录路径",
            key="ui_renpy_game_dir",
            placeholder=str(_default_gd),
            help="Ren'Py 工程下的 game 文件夹；资源导入将写入同一目录。",
        )
        run_export = st.button("▶️ Ren'Py 脚本导入", type="primary", use_container_width=True)
        if run_export:
            gd = (st.session_state.get("ui_renpy_game_dir") or "").strip()
            if not gd:
                st.error("请先选择/填写 Ren'Py 工程的 game 目录。")
                return
            # 合并参数：沿用单步执行的 merge_step_params_for_ui 逻辑，确保输入/输出路径随当前 run 推导。
            try:
                merged = utils.merge_step_params_for_ui(
                    base_config_path=base_cfg_path,
                    step_name="generate-renpy-scripts",
                    step_overrides=st.session_state.get("step_overrides") or None,
                    project_root=project_root,
                    session_selected_input_path=str(resolved_input_path),
                    session_ui_run_id=effective_run_id,
                )
                params_obj = dict(merged)
                params_obj["game_dir"] = gd
            except Exception as exc:
                st.error(f"合并参数失败：{exc}")
                return

            with st.status("🔄 正在执行 Ren'Py 脚本导入…", expanded=True) as status_box:
                _pp = _prompt_paths_for_single_step(st, project_root, "generate-renpy-scripts")
                status_box.write(utils.format_cli_command_preview("generate-renpy-scripts", params_obj))
                code = utils.run_single_cli_step(
                    project_root,
                    "generate-renpy-scripts",
                    params_obj,
                    st.empty(),
                    prompt_path_by_slot=_pp,
                )
                if code == 0:
                    status_box.update(label="✅ Ren'Py 脚本导入完成", state="complete")
                else:
                    status_box.update(label=f"❌ 导入失败（退出码 {code}）", state="error")

    st.subheader("📦 资源导入")
    with st.expander("导入资源（可选）", expanded=False):
        st.caption(
            "将 `assets/` 下立绘、场景、GUI、音乐等导入到 **上方所选同一 game 目录**（与脚本导入一致）。"
        )
        run_import = st.button("▶️ 执行资源导入", use_container_width=True)
        if run_import:
            gd_assets = (st.session_state.get("ui_renpy_game_dir") or "").strip()
            if not gd_assets:
                st.error("请先在上方填写或选择 `game` 目录。")
                return
            try:
                merged = utils.merge_step_params_for_ui(
                    base_config_path=base_cfg_path,
                    step_name="import-assets",
                    step_overrides=st.session_state.get("step_overrides") or None,
                    project_root=project_root,
                    session_selected_input_path=str(resolved_input_path),
                    session_ui_run_id=effective_run_id,
                )
                params_obj = dict(merged)
                params_obj["game_dir"] = gd_assets
            except Exception as exc:
                st.error(f"合并参数失败：{exc}")
                return

            with st.status("🔄 正在导入资源…", expanded=True) as status_box:
                _pp = _prompt_paths_for_single_step(st, project_root, "import-assets")
                status_box.write(utils.format_cli_command_preview("import-assets", params_obj))
                code = utils.run_single_cli_step(
                    project_root,
                    "import-assets",
                    params_obj,
                    st.empty(),
                    prompt_path_by_slot=_pp,
                )
                if code == 0:
                    status_box.update(label="✅ 资源导入完成", state="complete")
                else:
                    status_box.update(label=f"❌ 导入失败（退出码 {code}）", state="error")


def _single_step_widget_key(step_name: str, param_name: str) -> str:
    """Streamlit widget key（切换步骤时会清理旧 key）。"""
    return f"spw_{step_name}_{param_name}"


def _single_step_clear_widgets(st, step_name: str) -> None:
    prefix = f"spw_{step_name}_"
    for kk in list(st.session_state.keys()):
        if isinstance(kk, str) and kk.startswith(prefix):
            del st.session_state[kk]


def _reload_prompt_slots_from_disk(st, project_root: Path, step_name: str) -> None:
    """清空本步相关的 prompt_text_*，避免旧 session 覆盖磁盘上的提示词文件。"""
    for sid in prompt_registry.STEP_FILE_PROMPT_SLOTS.get(step_name, ()):
        st.session_state.pop(f"prompt_text_{sid}", None)


def _prompt_paths_for_single_step(st, project_root: Path, step_name: str) -> dict[str, str]:
    slots = prompt_registry.STEP_FILE_PROMPT_SLOTS.get(step_name, ())
    if not slots:
        return {}
    rid = (_effective_ui_run_id(st) or "").strip() or "default"
    return utils.materialize_prompt_slots_to_temp_files(
        project_root, rid, st.session_state, list(slots)
    )


def _ensure_langgraph_studio_background(st, project_root: Path) -> tuple[bool, str]:
    """
    尝试在后台启动 `python -m src.cli langgraph-studio`（仅本会话启动一次）。
    返回 (started_or_running, message)。
    """
    pid_key = "_lg_studio_pid"
    tried_key = "_lg_studio_tried"

    # 已尝试过且记录了 pid：视作已在运行/已启动过，不重复拉起
    if st.session_state.get(tried_key) and st.session_state.get(pid_key):
        return True, f"LangGraph Studio 已在后台启动（pid={st.session_state.get(pid_key)}）"

    cmd = [sys.executable, "-m", "src.cli", "langgraph-studio"]
    env = dict(os.environ)
    env["PYTHONIOENCODING"] = "utf-8"
    if "PYTHONPATH" in env and env["PYTHONPATH"].strip():
        if "src" not in env["PYTHONPATH"]:
            env["PYTHONPATH"] = f"src{os.pathsep}{env['PYTHONPATH']}"
    else:
        env["PYTHONPATH"] = "src"

    try:
        # 后台启动，不阻塞当前 Streamlit 请求
        proc = subprocess.Popen(
            cmd,
            cwd=str(project_root),
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            env=env,
        )
        st.session_state[pid_key] = proc.pid
        st.session_state[tried_key] = True
        return True, f"已在后台尝试启动 LangGraph Studio（pid={proc.pid}）"
    except Exception as exc:
        st.session_state[tried_key] = True
        return False, f"后台启动失败：{exc}"


def _collect_params_from_single_step_form(
    st,
    step_name: str,
    merged: dict[str, Any],
) -> tuple[dict[str, Any] | None, str | None, str | None]:
    """
    从表单控件读取参数；值为 None 或空字符串的键不传 CLI（与 params_to_cli_args 一致）。
    返回 (params, error_message, warning_message)。warning 不阻止预览，仅提示（如 chunk 耦合）。
    """
    out: dict[str, Any] = {}
    for k in sorted(merged.keys()):
        wk = _single_step_widget_key(step_name, k)
        v0 = merged[k]
        hint = param_hints.hint_for(step_name, k)
        if param_hints.use_hint_row_for_value(hint, v0):
            assert hint is not None
            pk = param_hints.preset_widget_key(step_name, k)
            labels, optional_int = param_hints.build_int_hint_labels(hint)
            choice = st.session_state.get(pk)
            if choice not in labels:
                out[k] = v0
                continue
            if optional_int:
                if choice == labels[0]:
                    out[k] = None
                elif choice == labels[-1]:
                    raw_c = st.session_state.get(wk)
                    try:
                        if raw_c is None or raw_c == "":
                            return None, f"参数 `{k}` 选了「自定义数量」但未填写有效整数", None
                        out[k] = int(raw_c)
                    except (TypeError, ValueError):
                        return None, f"参数 `{k}` 需要整数", None
                else:
                    try:
                        out[k] = param_hints.parse_int_from_preset_label(choice)
                    except ValueError:
                        return None, f"参数 `{k}` 预设解析失败", None
            else:
                if choice == labels[0]:
                    raw_c = st.session_state.get(wk)
                    try:
                        if raw_c is None or raw_c == "":
                            return None, f"参数 `{k}` 选了「自定义输入」但未填写整数", None
                        out[k] = int(raw_c)
                    except (TypeError, ValueError):
                        return None, f"参数 `{k}` 需要整数", None
                else:
                    try:
                        out[k] = param_hints.parse_int_from_preset_label(choice)
                    except ValueError:
                        return None, f"参数 `{k}` 预设解析失败", None
            continue

        if wk not in st.session_state:
            out[k] = v0
            continue
        raw = st.session_state[wk]

        if isinstance(v0, bool):
            out[k] = bool(raw)
        elif isinstance(v0, int) and not isinstance(v0, bool):
            try:
                if raw is None or raw == "":
                    out[k] = None
                else:
                    out[k] = int(raw)
            except (TypeError, ValueError):
                return None, f"参数 `{k}` 需要整数", None
        elif isinstance(v0, float):
            try:
                if raw is None or raw == "":
                    out[k] = None
                else:
                    out[k] = float(raw)
            except (TypeError, ValueError):
                return None, f"参数 `{k}` 需要数字", None
        elif isinstance(v0, (list, dict)):
            text = str(raw).strip() if raw is not None else ""
            if not text:
                out[k] = None
            else:
                try:
                    parsed = json.loads(text)
                    out[k] = parsed
                except json.JSONDecodeError as exc:
                    return None, f"参数 `{k}` 不是合法 JSON：{exc}", None
        elif v0 is None:
            s = str(raw).strip() if raw is not None else ""
            out[k] = s if s else None
        else:
            s = str(raw).strip() if raw is not None else ""
            out[k] = s if s else None

    extra_key = f"single_step_extra_json_{step_name}"
    extra_raw = st.session_state.get(extra_key, "{}")
    if isinstance(extra_raw, str) and extra_raw.strip():
        try:
            extra = json.loads(extra_raw)
            if not isinstance(extra, dict):
                return None, "「额外参数」必须是 JSON 对象 {}", None
            out.update(extra)
        except json.JSONDecodeError as exc:
            return None, f"「额外参数」JSON 无效：{exc}", None

    final = {k: v for k, v in out.items() if v is not None}
    if step_name in ("extract-events", "extract-events-and-relations"):
        chunk_err, chunk_warn = param_hints.validate_extract_events_chunk_pair(final)
        if chunk_err:
            return None, chunk_err, None
        if chunk_warn:
            return final, None, chunk_warn
    return final, None, None


def _reuse_params_for_artifact_check(
    merged_for_ui: dict[str, Any],
    params_obj: dict[str, Any] | None,
) -> dict[str, Any]:
    """合并模板参数与表单结果，仅用于「预期产出路径 / 可否复用」判断。

    - 路径类键（``_HIDDEN_PARAMS``）**只信** ``merge_step_params_for_ui`` 结果，不用表单收集值覆盖。
      否则旧版页面曾在 session 里注册过的 ``spw_*`` 控件会残留空串/错路径，导致明明磁盘已有产出却判不到。
    - 其余键用表单结果覆盖（chunk、开关等）。
    """
    out = dict(merged_for_ui)
    if params_obj:
        for k, v in params_obj.items():
            if k in _HIDDEN_PARAMS:
                continue
            if v is None:
                continue
            if isinstance(v, str) and not str(v).strip():
                continue
            out[k] = v
    return {
        k: v
        for k, v in out.items()
        if v is not None and not (isinstance(v, str) and not str(v).strip())
    }


def _resolve_hinted_int_from_widgets(
    st, step_name: str, k: str, v0: Any, *, cfg_key: str | None = None
) -> int | None:
    """与表单控件一致，解析当前 hinted 或非 hinted 整型参数（用于展示重叠比例等）。

    cfg_key：若传入（如参数配置页的 ``cfg_<step>_<k>``），则只读该 widget，不走预设下拉逻辑。
    """
    if cfg_key is not None:
        if cfg_key not in st.session_state:
            if isinstance(v0, int) and not isinstance(v0, bool):
                return int(v0)
            return None
        raw = st.session_state[cfg_key]
        try:
            if raw is None or raw == "":
                return None
            return int(raw)
        except (TypeError, ValueError):
            return None
    hint = param_hints.hint_for(step_name, k)
    if hint is None or not param_hints.use_hint_row_for_value(hint, v0):
        wk = _single_step_widget_key(step_name, k)
        if wk not in st.session_state:
            if isinstance(v0, int) and not isinstance(v0, bool):
                return int(v0)
            return None
        raw = st.session_state[wk]
        try:
            if raw is None or raw == "":
                return None
            return int(raw)
        except (TypeError, ValueError):
            return None
    labels, optional_int = param_hints.build_int_hint_labels(hint)
    pk = param_hints.preset_widget_key(step_name, k)
    wk = _single_step_widget_key(step_name, k)
    choice = st.session_state.get(pk)
    if choice not in labels:
        presets = hint.get("presets") or []
        di = param_hints.initial_preset_index(
            merged_value=v0, presets=presets, optional_int=optional_int
        )
        choice = labels[di]
    if optional_int:
        if choice == labels[-1]:
            raw = st.session_state.get(wk)
            try:
                if raw is None or raw == "":
                    return None
                return int(raw)
            except (TypeError, ValueError):
                return None
        if choice == labels[0]:
            return None
        try:
            return param_hints.parse_int_from_preset_label(choice)
        except ValueError:
            return None
    if choice == labels[0]:
        raw = st.session_state.get(wk)
        try:
            if raw is None or raw == "":
                return None
            return int(raw)
        except (TypeError, ValueError):
            return None
    try:
        return param_hints.parse_int_from_preset_label(choice)
    except ValueError:
        return None


def _maybe_show_chunk_partition_ratio(
    st, step_name: str, merged: dict, *, cfg_key_prefix: str | None = None
) -> None:
    """事件抽取步：根据当前「每块长度 / 重叠」换算重叠占每块的比例（便于理解粒度）。"""
    if step_name not in ("extract-events", "extract-events-and-relations"):
        return
    if "chunk_size" not in merged or "chunk_overlap" not in merged:
        return
    ck_cs = f"cfg_{step_name}_chunk_size" if cfg_key_prefix == "cfg" else None
    ck_co = f"cfg_{step_name}_chunk_overlap" if cfg_key_prefix == "cfg" else None
    cs = _resolve_hinted_int_from_widgets(
        st, step_name, "chunk_size", merged["chunk_size"], cfg_key=ck_cs
    )
    co = _resolve_hinted_int_from_widgets(
        st, step_name, "chunk_overlap", merged["chunk_overlap"], cfg_key=ck_co
    )
    if cs is None or co is None or cs <= 0:
        return
    if co >= cs:
        st.caption("当前重叠 ≥ 每块长度，将无法有效分块，请调小重叠或调大每块。")
        return
    pct = round(100.0 * co / cs)
    st.caption(
        f"相邻两块约有 **{pct}%** 的内容重叠。"
        " 比例越高，语义越连贯，但调用次数与token消耗通常更高。"
    )


def _render_single_step_param_fields_visible(st, step_name: str, merged: dict) -> None:
    """只渲染用户可调的参数（隐藏路径类字段）。"""
    visible = {
        k: v for k, v in merged.items()
        if k not in _HIDDEN_PARAMS
        and k not in ("decision_density", "decision_density_ratio")
    }
    if step_name == "generate-branch":
        visible = {k: v for k, v in visible.items() if k == "max_branches_per_point"}
    if step_name == "complete-all-path-events":
        # `input_dir` 在运行时由当前 run 自动推导/合并，不应在单步页展示
        visible = {k: v for k, v in visible.items() if k != "input_dir"}
    if step_name == "generate-content":
        visible = {
            k: v
            for k, v in visible.items()
            if k not in ("personas", "player_choice_catalog", "rebuild_player_choice_catalog")
        }
        # 同上：不暴露 rebuild_player_choice_catalog（由单步页“生成模式”控制）
    if step_name == "generate-renpy-scripts":
        visible = {
            k: v
            for k, v in visible.items()
            if k not in ("enhanced_paths", "game_dir", "personas", "entities")
        }
    if step_name == "generate-all-paths":
        visible = {
            k: v
            for k, v in visible.items()
            if k not in ("ending_candidates", "decision_analysis")
        }
    if not visible:
        st.caption("本步无需调参，直接运行即可。")
        return
    for k in sorted(visible.keys()):
        v = visible[k]
        hint = param_hints.hint_for(step_name, k)
        if param_hints.use_hint_row_for_value(hint, v):
            assert hint is not None
            _render_hinted_int_param_row(st, step_name, k, v, hint)
            continue
        wk = _single_step_widget_key(step_name, k)
        _ul = param_hints.ui_label_for(step_name, k)
        label = _ul if _ul else f"`{k}`"
        if isinstance(v, bool):
            st.checkbox(label, value=v, key=wk)
        elif isinstance(v, int) and not isinstance(v, bool):
            st.number_input(label, value=int(v), step=1, key=wk)
        elif isinstance(v, float):
            st.number_input(label, value=float(v), format="%.12g", key=wk)
        elif v is None:
            st.text_input(label, value="", key=wk, placeholder="留空则不传该参数")
        else:
            st.text_input(label, value=str(v), key=wk)

    _maybe_show_chunk_partition_ratio(st, step_name, merged)


def _render_hinted_int_param_row(
    st,
    step_name: str,
    k: str,
    v: Any,
    hint: dict[str, Any],
) -> None:
    """一行：参数名 | 预设下拉 +（可选）自定义数字 | Markdown 备注。"""
    labels, optional_int = param_hints.build_int_hint_labels(hint)
    presets = hint.get("presets") or []
    default_i = param_hints.initial_preset_index(
        merged_value=v,
        presets=presets,
        optional_int=optional_int,
    )
    pk = param_hints.preset_widget_key(step_name, k)
    wk = _single_step_widget_key(step_name, k)

    # 如果你之前在同一会话里点过“自定义”，而此时我们新增了 presets，
    # 就可能出现“下方 number_input 还在显示”的尴尬状态。
    # 这里做一个“自动纠偏”：当当前值刚好等于某个预设时，强制回到预设档。
    if pk not in st.session_state:
        st.session_state[pk] = labels[default_i]
    else:
        cur_choice = st.session_state.get(pk)
        if cur_choice not in labels:
            st.session_state[pk] = labels[default_i]

    note_text = str(hint.get("note") or "").strip()
    if note_text:
        c1, c2, c3 = st.columns([1.0, 1.8, 2.4])
    else:
        c1, c2 = st.columns([1.0, 3.2])
        c3 = None

    _ul = str(hint.get("ui_label") or "").strip()
    with c1:
        if _ul:
            st.markdown(f"**{_ul}**")
        else:
            st.markdown(f"**`{k}`**")
    with c2:
        _psl = str(hint.get("preset_select_label") or "").strip()
        st.selectbox(
            _psl if _psl else "推荐 / 自定义",
            labels,
            key=pk,
            label_visibility="collapsed",
        )
        choice = st.session_state[pk]
        if optional_int:
            need_custom_number = choice == labels[-1]
        else:
            need_custom_number = choice == labels[0]

        if need_custom_number:
            default_num = 800
            if isinstance(v, int) and not isinstance(v, bool):
                default_num = int(v)
            elif optional_int:
                default_num = 20
            st.number_input(
                "自定义数值",
                min_value=0,
                value=default_num,
                step=1,
                key=wk,
                label_visibility="collapsed",
            )
        elif k in ("chunk_size", "chunk_overlap"):
            pass
        elif not hint.get("suppress_preset_row_captions"):
            if optional_int and choice == labels[0]:
                st.caption(
                    str(
                        hint.get("optional_int_none_caption")
                        or "将 **不传入** 该参数（处理整书）。"
                    )
                )
            elif not optional_int:
                try:
                    n = param_hints.parse_int_from_preset_label(choice)
                    st.caption(f"将使用 **{n}**（仍可在上方改选「自定义输入…」）。")
                except ValueError:
                    st.caption("已选预设档位。")
            else:
                try:
                    n = param_hints.parse_int_from_preset_label(choice)
                    tpl = hint.get("optional_int_preset_caption")
                    if tpl:
                        st.caption(str(tpl).format(n=n))
                    else:
                        st.caption(f"将只处理前 **{n}** 块。")
                except ValueError:
                    st.caption("已选预设档位。")

    if c3 is not None:
        with c3:
            st.markdown(note_text)

    _detail = str(hint.get("expand_detail") or "").strip()
    if _detail:
        _title = str(hint.get("expand_detail_title") or "📖 查看流程与参数说明").strip()
        with st.expander(_title, expanded=False):
            st.markdown(_detail)


def _render_single_step_param_fields(st, step_name: str, merged: dict[str, Any]) -> None:
    """按合并后的键逐项渲染控件。"""
    if not merged:
        st.info("当前模板里本步没有合并出任何 params，请仅用下方「额外参数」填写 JSON。")
        return

    for k in sorted(merged.keys()):
        v = merged[k]
        hint = param_hints.hint_for(step_name, k)
        if param_hints.use_hint_row_for_value(hint, v):
            assert hint is not None
            _render_hinted_int_param_row(st, step_name, k, v, hint)
            continue

        wk = _single_step_widget_key(step_name, k)
        label = f"`{k}`"

        if isinstance(v, bool):
            st.checkbox(label, value=v, key=wk, help="布尔开关")
        elif isinstance(v, int) and not isinstance(v, bool):
            st.number_input(label, value=int(v), step=1, key=wk, help="整数")
        elif isinstance(v, float):
            st.number_input(label, value=float(v), format="%.12g", key=wk, help="浮点数")
        elif isinstance(v, (list, dict)):
            st.text_area(
                label,
                value=json.dumps(v, ensure_ascii=False, indent=2),
                height=min(160, 80 + 20 * len(json.dumps(v))),
                key=wk,
                help="JSON 数组或对象",
            )
        elif v is None:
            st.text_input(label, value="", key=wk, placeholder="留空则不传该参数")
        else:
            _h = (hint.get("note") if hint else None) or "字符串路径或选项值"
            st.text_input(label, value=str(v), key=wk, help=str(_h))


_SINGLE_STEP_HOLD_FOR_PREVIEW: tuple[str, ...] = (
    "extract-events-and-relations",
    "aggregate-personas",
    "aggregate-characters-and-personas",
    "analyze-decision-points",
    "generate-branch",
    "determine-ending-candidates",
    "generate-all-paths",
    "generate-content",
)


def _render_single_step_intro(st, step_name: str) -> None:
    """渲染单步页里与步骤相关的说明文案/扩展面板。"""
    if step_name == "extract-events":
        st.caption(
            "· `input`：选过小说会自动填；**epub** 会先转成 `text_variants` 下的 txt。"
            " · `output_dir`：默认 **`runs/ui/<run_id>/out`**（`ui_run_id` → 或按小说名推导 → 或模板 `run_id`）；可手改。"
        )
    elif step_name == "extract-events-and-relations":
        st.caption(
            "· **合并步骤**：先 **extract-events**（参数与下方一致），再在同一 **`output_dir`** 下生成 "
            "**`extract_relations.json`**。输入/输出目录与单独跑事件步相同；完成后在页面展示 **知识图谱预览**。"
        )
    elif step_name == "extract-relations":
        st.caption(
            "· **CLI**：`--json` 事件链、`--output` 关系结果路径。"
            " · `output_dir` 与上一步一致时会自动填 `json` / `output`；`output_dir` 不会传给命令行。"
        )
    elif step_name == "generate-content":
        st.caption(
            "· 读 ``all_paths_completed``，写出 ``enhanced_paths``。完成后可在本页或 **📊 运行结果** 预览/改稿。"
            " · ``output`` 默认随当前 run 指向 ``runs/ui/<run_id>/out/enhanced_paths``。"
        )
    elif step_name in (
        "import-neo4j",
        "import-entities",
        "import-state-changes",
        "import-neo4j-all",
    ):
        st.caption(
            "· Neo4j 导入步骤：会先做数据库登录/连通性预检查。"
            " · `import-neo4j-all` 会顺序导入事件图谱、实体、世界/人物/关系状态。"
            " · 默认路径随 `output_dir` 自动补全。"
        )
        with st.expander("🗄️ Neo4j 连接与常用命令", expanded=False):
            st.markdown("[打开 Neo4j Browser](http://localhost:7474/browser/)")
            st.code("neo4j console", language="bash")
            st.code("MATCH (n) DETACH DELETE n", language="cypher")
            st.code(
                "MATCH (a:Event)-[r:REL]->(b:Event) RETURN a, r, b LIMIT 50",
                language="cypher",
            )


def _resolve_output_preview_path(
    project_root: Path,
    params_obj: dict[str, Any],
    keys: tuple[str, ...] = ("output", "output_path"),
) -> Path | None:
    """从参数中解析预览文件路径（支持相对项目根路径）。"""
    for key in keys:
        raw = str(params_obj.get(key) or "").strip()
        if not raw:
            continue
        p = Path(raw)
        return p if p.is_absolute() else (project_root / p)
    return None


def _render_success_preview_for_single_step(
    st,
    project_root: Path,
    step_name: str,
    params_obj: dict[str, Any],
) -> None:
    """单步成功后，按步骤渲染结果预览。"""
    if step_name == "extract-events-and-relations":
        od_kg = str(params_obj.get("output_dir") or "").strip()
        if od_kg:
            st.divider()
            st.subheader("知识图谱预览")
            kg_preview.render_in_streamlit(st, project_root, od_kg)
        return

    if step_name == "analyze-decision-points":
        out_path = _resolve_output_preview_path(
            project_root,
            params_obj,
            keys=("output_path", "output"),
        )
        if out_path is not None:
            st.divider()
            st.subheader("决策点分析结果预览")
            decision_points_preview.render_in_streamlit(st, out_path, editable_branch_points=True)
        return

    if step_name == "determine-ending-candidates":
        out_path = _resolve_output_preview_path(
            project_root,
            params_obj,
            keys=("output", "output_path"),
        )
        if out_path is not None:
            st.divider()
            st.subheader("结局候选池决定过程展示")
            ending_candidates_preview.render_in_streamlit(st, out_path)
        return

    if step_name == "generate-branch":
        out_path = _resolve_output_preview_path(
            project_root,
            params_obj,
            keys=("output", "output_path"),
        )
        if out_path is not None:
            st.divider()
            st.subheader("支线记录预览")
            branches_preview.render_in_streamlit(st, out_path)
        return

    if step_name in ("aggregate-characters-and-personas", "aggregate-personas"):
        personas_path = personas_preview.resolve_character_personas_path(
            project_root, step_name, params_obj
        )
        if personas_path is not None:
            st.divider()
            st.subheader("角色人设预览")
            personas_preview.render_in_streamlit(st, personas_path)
        return

    if step_name == "generate-all-paths":
        out_path = _resolve_output_preview_path(
            project_root,
            params_obj,
            keys=("output", "output_path"),
        )
        if out_path is not None and out_path.is_dir():
            st.divider()
            st.subheader("分支路径生成结果预览")
            all_paths_preview.render_in_streamlit(st, out_path, project_root=project_root)
        elif out_path is not None:
            st.warning(f"分支路径生成的 ``output`` 应为目录，当前无法预览：{out_path}")
        return

    if step_name == "generate-content":
        out_path = _resolve_output_preview_path(
            project_root,
            params_obj,
            keys=("output", "output_dir"),
        )
        if out_path is not None and out_path.is_dir():
            st.divider()
            st.subheader("运行结果预览 / 编辑")
            enhanced_content_preview.render_in_streamlit(
                st,
                out_path,
                project_root=project_root,
                export_base_name=st.session_state.get("novel_display_name") or None,
            )
        elif out_path is not None:
            st.warning(f"``generate-content`` 的 output 不是目录，无法预览：{out_path}")
        return


def _render_langgraph_live_svg(st, *, active_node: str | None) -> None:
    """
    实时渲染 LangGraph 流程图（SVG），并高亮 active_node。

    设计目标：比 Mermaid 更“像产品”，节点分区清晰、边上有路由条件文字，用户一眼能看懂。
    """
    try:
        import streamlit.components.v1 as components

        # 固定布局（清晰 > 自动排版）。节点 id 与 workflow.add_node 保持一致。
        nodes = [
            ("initialize", 40, 70, "initialize\n初始化状态"),
            ("summarize_history", 250, 70, "summarize_history\n记忆压缩/摘要"),
            ("llm_decision", 480, 70, "llm_decision\nLLM 决策(工具调用)"),
            ("tools", 700, 70, "tools\nToolNode 执行"),
            ("generate_branch_event", 700, 200, "generate_branch_event\n生成分支事件"),
            ("process_mainline", 700, 320, "process_mainline\n合流/主线推进"),
            ("create_early_ending", 700, 440, "create_early_ending\n提前结局"),
            ("identify_similar_endings", 940, 320, "identify_similar_endings\n相似结局聚类"),
            ("merge_similar_endings", 1140, 320, "merge_similar_endings\n合并相似结局"),
            ("select_dramatic_ending", 1340, 320, "select_dramatic_ending\n选择文学性结局"),
            ("END", 1540, 320, "END\n输出路径结果"),
        ]

        edges = [
            ("initialize", "summarize_history", ""),
            ("summarize_history", "llm_decision", ""),
            ("llm_decision", "tools", "tool call"),
            ("llm_decision", "END", "no tool"),
            ("tools", "generate_branch_event", "status=generated"),
            ("tools", "process_mainline", "status=merged"),
            ("tools", "create_early_ending", "status=early_ending_created"),
            ("generate_branch_event", "summarize_history", "loop"),
            ("process_mainline", "identify_similar_endings", ""),
            ("create_early_ending", "identify_similar_endings", ""),
            ("identify_similar_endings", "merge_similar_endings", ""),
            ("merge_similar_endings", "select_dramatic_ending", ""),
            ("select_dramatic_ending", "END", ""),
        ]

        node_map = {nid: (x, y, label) for nid, x, y, label in nodes}

        def node_rect(nid: str) -> str:
            x, y, label = node_map[nid]
            w, h = 180, 74
            is_active = (nid == active_node)
            fill = "#FFF0C2" if is_active else "#F7F7FF"
            stroke = "#1b1b1f" if is_active else "#3b3b45"
            sw = 3 if is_active else 1.5
            title = label.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
            # 多行文字：用 <tspan>
            lines = title.split("\n")
            t = "".join(
                f'<tspan x="{x+12}" dy="{0 if i==0 else 18}">{ln}</tspan>'
                for i, ln in enumerate(lines)
            )
            return (
                f'<g id="node-{nid}">'
                f'<rect x="{x}" y="{y}" rx="12" ry="12" width="{w}" height="{h}" '
                f'style="fill:{fill};stroke:{stroke};stroke-width:{sw};" />'
                f'<text x="{x+12}" y="{y+28}" style="font: 13px/1.2 ui-sans-serif,system-ui,Segoe UI,Arial; fill:#1b1b1f;">'
                f'{t}</text>'
                f"</g>"
            )

        def edge_line(a: str, b: str, label: str) -> str:
            ax, ay, _ = node_map[a]
            bx, by, _ = node_map[b]
            # 从右侧中点到左侧中点（简单规则，布局固定）
            x1, y1 = ax + 180, ay + 37
            x2, y2 = bx, by + 37
            # 轻微折线，避免穿过节点
            mx = (x1 + x2) / 2
            path = f"M {x1} {y1} C {mx} {y1}, {mx} {y2}, {x2} {y2}"
            lbl = ""
            if label:
                lx, ly = mx, (y1 + y2) / 2 - 6
                lbl = (
                    f'<text x="{lx}" y="{ly}" text-anchor="middle" '
                    f'style="font: 12px ui-sans-serif,system-ui,Segoe UI,Arial; fill:#3b3b45; background:#fff;">'
                    f"{label}</text>"
                )
            return (
                f'<path d="{path}" fill="none" stroke="#3b3b45" stroke-width="1.5" marker-end="url(#arrow)" />'
                f"{lbl}"
            )

        svg_nodes = "\n".join(node_rect(nid) for nid, *_ in nodes)
        svg_edges = "\n".join(edge_line(a, b, lbl) for a, b, lbl in edges)

        components.html(
            f"""
<div style="width:100%; overflow:auto; border:1px solid rgba(49,51,63,0.2); border-radius:12px; padding:10px; background:#fff;">
  <div style="display:flex; gap:12px; align-items:center; margin:6px 0 10px 0;">
    <div style="font: 12px ui-sans-serif,system-ui,Segoe UI,Arial; color:#3b3b45;">
      图例：<span style="display:inline-block; width:10px; height:10px; background:#FFF0C2; border:2px solid #1b1b1f; border-radius:3px; vertical-align:middle;"></span>
      当前节点
      <span style="margin-left:12px; opacity:.8;">边上文字=路由条件</span>
    </div>
  </div>
  <svg width="1740" height="560" viewBox="0 0 1740 560" xmlns="http://www.w3.org/2000/svg">
    <defs>
      <marker id="arrow" markerWidth="10" markerHeight="10" refX="9" refY="3" orient="auto">
        <path d="M0,0 L10,3 L0,6 Z" fill="#3b3b45" />
      </marker>
    </defs>
    <!-- 分区标题 -->
    <text x="40" y="40" style="font: 12px ui-sans-serif,system-ui,Segoe UI,Arial; fill:#6b6b76;">主循环（生成分支事件）</text>
    <text x="700" y="280" style="font: 12px ui-sans-serif,system-ui,Segoe UI,Arial; fill:#6b6b76;">收尾链（合流/结局选择）</text>
    {svg_edges}
    {svg_nodes}
  </svg>
</div>
""",
            height=560,
        )
    except Exception:
        st.caption("（SVG 渲染失败，已降级为文本。）")
        st.code(active_node or "", language="text")


def _handle_single_step_success_navigation(
    st,
    *,
    step_name: str,
    choice_idx: int,
    total_steps: int,
) -> None:
    """单步成功后的自动跳步/提示逻辑。"""
    if choice_idx + 1 >= total_steps:
        st.info("已是最后一步。")
        return

    if step_name not in _SINGLE_STEP_HOLD_FOR_PREVIEW:
        st.session_state["_single_step_idx_pending"] = choice_idx + 1
        st.rerun()
        return

    next_cn = _PIPELINE_STEPS[choice_idx + 1][1] if choice_idx + 1 < len(_PIPELINE_STEPS) else "下一步"
    if step_name in ("extract-events-and-relations", "generate-branch", "generate-content"):
        st.info(f"已完成并展示预览。可在上方选择 **{next_cn}** 继续。")
    elif step_name == "generate-content-DISABLED":
        st.info(
            "💡 本步已展示运行结果预览；也可在侧栏进入 **📊 运行结果** 随时改稿。"
            "确认无误后再跑 **generate-renpy-scripts**。"
        )
    else:
        st.info("💡 本步已展示结果预览；可在上方步骤列表中 **手动选择下一步**。")


# ── Page: 单步执行（按流水线顺序，只跑一步）────────────────────────

def _page_single_step(st, project_root: Path, default_base: Path) -> None:
    st.header("🔧 单步执行")

    if not st.session_state.get("selected_input_path"):
        st.warning("请先在「📥 选择小说」页面选择一个输入文件（与参数配置、一键运行共用）。")
        if st.button("👉 去选择小说"):
            _goto(st, "📥 选择小说")
        return

    novel_name = st.session_state.get("novel_display_name", "—")
    st.caption(f"当前小说：**{novel_name}**")

    base_path_str = st.session_state.get("base_config_path", str(default_base))
    base_path = Path(base_path_str)
    if not base_path.is_absolute():
        base_path = project_root / base_path

    step_names = [s for s, _ in _PIPELINE_STEPS]

    _pending_step = st.session_state.pop("_single_step_idx_pending", None)
    if _pending_step is not None and isinstance(_pending_step, int):
        _clamped = max(0, min(_pending_step, len(step_names) - 1))
        st.session_state["single_step_idx"] = _clamped

    choice_idx = st.selectbox(
        "选择步骤",
        range(len(step_names)),
        format_func=lambda i: _PIPELINE_STEPS[i][1],
        key="single_step_idx",
    )
    step_name = step_names[choice_idx]

    _step_blurb = _STEP_SHORT_DESC.get(step_name, "").strip()
    if _step_blurb:
        st.info(_step_blurb)

    # 单步成功后：把预览渲染状态持久化到 session_state，
    # 避免用户在预览区域点击按钮/多选框触发 rerun 后，下面预览整块消失。
    preview_ready_key = f"_single_step_preview_ready_{step_name}"

    prev = st.session_state.get("_single_step_prev_name")
    if prev is not None and prev != step_name:
        _single_step_clear_widgets(st, prev)
    st.session_state["_single_step_prev_name"] = step_name

    step_overrides = st.session_state.get("step_overrides")
    try:
        merged = utils.merge_step_params_for_ui(
            base_config_path=base_path,
            step_name=step_name,
            step_overrides=step_overrides if isinstance(step_overrides, dict) else None,
            project_root=project_root,
            session_selected_input_path=st.session_state.get("selected_input_path"),
            session_ui_run_id=_effective_ui_run_id(st) or None,
        )
    except Exception as exc:
        st.error(f"合并参数失败：{exc}")
        return

    # 单步执行：generate-branch 不再暴露 decision_density / decision_density_ratio，
    # 避免“上一步已经选好了分支个数”，这一步再二次截断。
    merged_for_ui = merged
    if step_name == "generate-branch":
        merged_for_ui = dict(merged)
        merged_for_ui.pop("decision_density", None)
        merged_for_ui.pop("decision_density_ratio", None)
    if step_name == "generate-content":
        # 在单步执行页提供一个开关：是否重建 player_choice 目录（选项文案模板）。
        # 模板未包含该参数时也要能勾选，因此在 merged_for_ui 里补齐键。
        merged_for_ui = dict(merged_for_ui)
        merged_for_ui.setdefault("rebuild_player_choice_catalog", False)

    _render_single_step_param_fields_visible(st, step_name, merged_for_ui)
    if step_name == "import-neo4j-all":
        _neo4j_browser_link_row(st)

    # generate-content：用两个勾选框组合出三种模式
    # - 都不勾：完全重新生成
    # - 只勾「新选项」：重新生成内容 + 重新生成选项
    # - 都勾：复用内容但重新生成选项
    if step_name == "generate-content":
        st.divider()
        st.markdown("**生成模式（用勾选组合控制）**")
        opt_reuse = st.checkbox(
            "复用已有润色内容（不重新跑叙事扩写）",
            value=True,
            key="gc_mode_reuse_content",
            help="勾选：若 enhanced_paths 已有完整结果，会直接复用，不覆盖你的润色内容。",
        )
        opt_new_choice = st.checkbox(
            "重新生成分叉点选项",
            value=False,
            key="gc_mode_regen_choices",
            help="勾选：会刷新 decision_point 的选项文案与 jump_target。",
        )

    params_obj, collect_err, collect_warn = _collect_params_from_single_step_form(
        st, step_name, merged_for_ui
    )
    if collect_warn:
        st.warning(collect_warn)
    if collect_err:
        st.error(collect_err)
        params_obj = None

    reuse_params = _reuse_params_for_artifact_check(merged_for_ui, params_obj)
    _reuse_ok, _artifact_paths = artifacts_all_present_for_reuse(
        project_root, step_name, reuse_params
    )
    if _artifact_paths and _reuse_ok:
        # generate-content 的复用策略由“生成模式”两个勾选框统一控制；
        # 不再显示“复用已有产出（不重新执行本步）”，避免用户误解为“只要勾上就不改任何东西”。
        if step_name != "generate-content":
            st.checkbox(
                "复用已有产出（不重新执行本步）",
                value=False,
                key=f"single_step_reuse_artifacts_{step_name}",
            )
        if params_obj is None:
            st.caption(
                "参数表单未通过校验时仍可勾选复用：将跳过子进程，预览按合并模板中的路径。"
            )
    elif _artifact_paths and (not _reuse_ok) and any(p.exists() for p in _artifact_paths):
        st.caption(
            "💡 本步产出尚未齐全；全部就绪后可勾选「复用已有产出」。"
            )

    col_a, col_b = st.columns(2)
    with col_a:
        run_one = st.button("▶️ 执行此步骤", type="primary", use_container_width=True)
    with col_b:
        if st.button("🔄 重置参数", use_container_width=True):
            _single_step_clear_widgets(st, step_name)
            _reload_prompt_slots_from_disk(st, project_root, step_name)
            st.rerun()

    if run_one:
        _ok_reuse, _paths = artifacts_all_present_for_reuse(
            project_root, step_name, reuse_params
        )
        reuse_skip = (
            _ok_reuse
            and bool(_paths)
            and st.session_state.get(f"single_step_reuse_artifacts_{step_name}", False)
        )

        code: int | None = None
        preview_params: dict[str, Any] | None = None

        # generate-content：如果用户选择“只复用内容、也不刷新选项”，那就直接跳过子进程执行。
        # 这相当于把之前的“复用已有产出（不重新执行本步）”做成自动策略，避免界面再出现第三个勾选框。
        if step_name == "generate-content":
            opt_reuse = bool(st.session_state.get("gc_mode_reuse_content", True))
            opt_new_choice = bool(st.session_state.get("gc_mode_regen_choices", False))
            if opt_reuse and (not opt_new_choice) and _ok_reuse and bool(_paths):
                reuse_skip = True

        if reuse_skip:
            st.success("已跳过执行：直接复用已有产出。")
            code = 0
            preview_params = reuse_params
        elif params_obj is None:
            st.error(
                "请先修正上方参数，或勾选「复用已有产出」后再次点击执行（将跳过子进程）。"
            )
        else:
            # generate-content：将“勾选组合”映射到 CLI 参数
            if step_name == "generate-content":
                params_obj = dict(params_obj)
                opt_reuse = bool(st.session_state.get("gc_mode_reuse_content", True))
                opt_new_choice = bool(st.session_state.get("gc_mode_regen_choices", False))

                # 复用内容 = 不强制重跑；不复用 = 强制重跑
                params_obj["force_regenerate"] = (not opt_reuse)

                if opt_reuse and opt_new_choice:
                    # 复用内容但生成新选项：走 refresh-only（不会重写润色字段）
                    params_obj["refresh_player_choice_only"] = True
                    params_obj["rebuild_player_choice_catalog"] = True
                elif (not opt_reuse) and opt_new_choice:
                    # 重新生成内容 + 新选项：强制重跑 + 重建选项目录
                    params_obj["refresh_player_choice_only"] = False
                    params_obj["rebuild_player_choice_catalog"] = True
                elif (not opt_reuse) and (not opt_new_choice):
                    # 完全重新生成：强制重跑
                    params_obj["refresh_player_choice_only"] = False
                    # rebuild_player_choice_catalog 由用户在表单里决定（或保持默认）
                else:
                    # 复用内容且不刷新选项：纯复用/增量
                    params_obj["refresh_player_choice_only"] = False

            preview_params = params_obj
            with st.status(f"🔄 正在执行：{step_name} …", expanded=True) as status_box:
                _pp = _prompt_paths_for_single_step(st, project_root, step_name)

                def _ensure_canonical_branch_chronological(base_dir: Path) -> int:
                    """
                    确保存在 `canonical_branch_chronological.json`：
                    如果缺失，就用 `scripts/reorder_canonical_branch.py` 从 `canonical_branch.json` 重排生成。
                    """
                    canonical_ch = base_dir / "canonical_branch_chronological.json"
                    if canonical_ch.is_file():
                        st.caption(
                            "💡 检测到已存在的 `canonical_branch_chronological.json`，本次跳过重排。"
                        )
                        return 0

                    canonical = base_dir / "canonical_branch.json"
                    extract_chain = base_dir / "extract_chain.json"

                    if not canonical.is_file():
                        st.error(
                            "找不到 `canonical_branch.json`，无法重排生成 `canonical_branch_chronological.json`。"
                            f"\n缺失：`{canonical}`"
                        )
                        return 1
                    if not extract_chain.is_file():
                        st.error(
                            "找不到 `extract_chain.json`，无法重排生成 `canonical_branch_chronological.json`（脚本会用它把 event_id 还原到 source_text）。"
                            f"\n缺失：`{extract_chain}`"
                        )
                        return 1

                    st.info("💡 自动重排主线：生成 `canonical_branch_chronological.json` …")
                    log_pre = st.empty()
                    pre_lines: list[str] = []

                    cmd = [
                        sys.executable,
                        "scripts/reorder_canonical_branch.py",
                        "--input",
                        str(canonical),
                        "--output",
                        str(canonical_ch),
                        "--extract-chain",
                        str(extract_chain),
                    ]
                    return utils.run_subprocess_stream_to_log(
                        project_root,
                        cmd,
                        log_pre,
                        pre_lines,
                        tail=200,
                    )

                if step_name == "extract-events":
                    prog = st.progress(0)
                    cap = st.empty()
                    cap.caption("准备分块…")

                    def _on_extract_progress(done: int, total: int) -> None:
                        if total <= 0:
                            prog.progress(1.0)
                            cap.caption("无分块（文本为空或过短）。")
                            return
                        prog.progress(min(1.0, done / total))
                        cap.caption(f"事件抽取：**{done}** / **{total}** 块")

                    try:
                        code = utils.run_extract_events_for_ui(
                            project_root,
                            params_obj,
                            on_progress=_on_extract_progress,
                            prompt_path_by_slot=_pp or None,
                        )
                    except FileNotFoundError as exc:
                        st.error(f"找不到输入文件：{exc}")
                        code = 1
                    except Exception as exc:
                        st.exception(exc)
                        code = 1
                elif step_name == "extract-relations":
                    prog_er = st.progress(0)
                    cap_er = st.empty()
                    cap_er.caption("准备分析相邻事件对…")

                    def _on_rel_progress(done: int, total: int) -> None:
                        if total <= 0:
                            prog_er.progress(1.0)
                            cap_er.caption("无需分析（事件不足 2 个）。")
                            return
                        prog_er.progress(min(1.0, done / total))
                        cap_er.caption(f"关系抽取：**{done}** / **{total}** 对（相邻事件）")

                    try:
                        code = utils.run_extract_relations_for_ui(
                            project_root,
                            params_obj,
                            on_progress=_on_rel_progress,
                            prompt_path_by_slot=_pp or None,
                        )
                    except FileNotFoundError as exc:
                        st.error(f"找不到文件：{exc}")
                        code = 1
                    except ValueError as exc:
                        st.error(str(exc))
                        code = 1
                    except Exception as exc:
                        st.exception(exc)
                        code = 1
                elif step_name == "extract-events-and-relations":
                    prog_e = st.progress(0)
                    cap_e = st.empty()
                    prog_r = st.progress(0)
                    cap_r = st.empty()
                    cap_e.caption("阶段 1/2：准备事件抽取…")
                    cap_r.caption("阶段 2/2：等待事件抽取完成…")

                    def _on_ev(done: int, total: int) -> None:
                        if total <= 0:
                            prog_e.progress(1.0)
                            cap_e.caption("阶段 1/2：无分块或文本过短。")
                            return
                        prog_e.progress(min(1.0, done / total))
                        cap_e.caption(f"阶段 1/2 事件抽取：**{done}** / **{total}** 块")
                        prog_r.progress(0)
                        cap_r.caption("阶段 2/2：等待事件抽取结束…")

                    def _on_rel(done: int, total: int) -> None:
                        prog_e.progress(1.0)
                        cap_e.caption("阶段 1/2：事件链已写入。")
                        if total <= 0:
                            prog_r.progress(1.0)
                            cap_r.caption("阶段 2/2：无需分析相邻对。")
                            return
                        prog_r.progress(min(1.0, done / total))
                        cap_r.caption(f"阶段 2/2 关系抽取：**{done}** / **{total}** 对")

                    try:
                        code = utils.run_extract_events_and_relations_for_ui(
                            project_root,
                            params_obj,
                            on_events_progress=_on_ev,
                            on_relations_progress=_on_rel,
                            prompt_path_by_slot=_pp or None,
                        )
                    except FileNotFoundError as exc:
                        st.error(f"找不到输入文件：{exc}")
                        code = 1
                    except ValueError as exc:
                        st.error(str(exc))
                        code = 1
                    except Exception as exc:
                        st.exception(exc)
                        code = 1
                elif step_name == "generate-mentions":
                    prog_m = st.progress(0)
                    cap_m = st.empty()
                    cap_m.caption("准备调用 LLM 抽取 Mention…")
                    log_ph = st.empty()

                    def _on_mentions_line(line: str) -> None:
                        m = re.match(r"\[mentions\] (\d+)/(\d+)", line)
                        if not m:
                            return
                        i, total = int(m.group(1)), int(m.group(2))
                        if total <= 0:
                            prog_m.progress(1.0)
                            cap_m.caption("无事件可处理。")
                            return
                        prog_m.progress(min(1.0, i / total))
                        cap_m.caption(f"Mention 抽取：**{i}** / **{total}** 个事件")

                    code = utils.run_single_cli_step(
                        project_root,
                        step_name,
                        _cli_params_for_step(step_name, params_obj),
                        log_ph,
                        on_stdout_line=_on_mentions_line,
                        prompt_path_by_slot=_pp or None,
                    )
                elif step_name == "generate-entities":
                    prog_ent = st.progress(0)
                    cap_ent = st.empty()
                    cap_ent.caption("准备别名筛选与 LLM 确认…")
                    log_ph = st.empty()

                    def _on_entities_line(line: str) -> None:
                        m = re.match(r"\[entities\] (\d+)/(\d+)", line)
                        if not m:
                            return
                        i, total = int(m.group(1)), int(m.group(2))
                        if total <= 0:
                            prog_ent.progress(1.0)
                            cap_ent.caption("无候选对需 LLM 确认。")
                            return
                        prog_ent.progress(min(1.0, i / total))
                        cap_ent.caption(f"别名确认：**{i}** / **{total}** 对候选")

                    code = utils.run_single_cli_step(
                        project_root,
                        step_name,
                        _cli_params_for_step(step_name, params_obj),
                        log_ph,
                        on_stdout_line=_on_entities_line,
                        prompt_path_by_slot=_pp or None,
                    )
                elif step_name == "generate-mentions-and-entities":
                    prog_me = st.progress(0)
                    cap_me = st.empty()
                    cap_me.caption("合并步：Mention 抽取 → 实体生成…")
                    log_ph = st.empty()

                    def _on_me_line(line: str) -> None:
                        m = re.match(r"\[mentions\] (\d+)/(\d+)", line)
                        if m:
                            i, total = int(m.group(1)), int(m.group(2))
                            if total <= 0:
                                prog_me.progress(1.0)
                                cap_me.caption("无事件可处理。")
                                return
                            prog_me.progress(min(1.0, i / total))
                            cap_me.caption(f"Mention：**{i}** / **{total}** 个事件")
                            return
                        m2 = re.match(r"\[entities\] (\d+)/(\d+)", line)
                        if m2:
                            i, total = int(m2.group(1)), int(m2.group(2))
                            if total <= 0:
                                prog_me.progress(1.0)
                                cap_me.caption("无候选对需 LLM 确认。")
                                return
                            prog_me.progress(min(1.0, i / total))
                            cap_me.caption(f"实体别名确认：**{i}** / **{total}** 对")

                    code = utils.run_single_cli_step(
                        project_root,
                        step_name,
                        _cli_params_for_step(step_name, params_obj),
                        log_ph,
                        on_stdout_line=_on_me_line,
                        prompt_path_by_slot=_pp or None,
                    )
                elif step_name == "extract-states-and-baseline":
                    prog_s = st.progress(0)
                    cap_s = st.empty()
                    log_ph = st.empty()

                    def _on_state_sub(i: int, n: int, title: str) -> None:
                        if n <= 0:
                            prog_s.progress(1.0)
                            cap_s.caption(title)
                            return
                        prog_s.progress(min(1.0, i / n))
                        if i < n:
                            cap_s.caption(
                                f"合并步：**{i + 1}** / **{n}** — {title}…"
                            )
                        else:
                            prog_s.progress(1.0)
                            cap_s.caption(title)

                    code = utils.run_extract_states_and_baseline_for_ui(
                        project_root,
                        _cli_params_for_step(step_name, params_obj),
                        log_placeholder=log_ph,
                        on_substep=_on_state_sub,
                        prompt_path_by_slot=_pp or None,
                    )
                elif step_name == "aggregate-characters-and-personas":
                    prog_ap = st.progress(0)
                    cap_ap = st.empty()
                    log_ph = st.empty()

                    def _on_agg_sub(i: int, n: int, title: str) -> None:
                        if n <= 0:
                            prog_ap.progress(1.0)
                            cap_ap.caption(title)
                            return
                        prog_ap.progress(min(1.0, i / n))
                        if i < n:
                            cap_ap.caption(
                                f"合并步：**{i + 1}** / **{n}** — {title}…"
                            )
                        else:
                            prog_ap.progress(1.0)
                            cap_ap.caption(title)

                    code = utils.run_aggregate_characters_and_personas_for_ui(
                        project_root,
                        _cli_params_for_step(step_name, params_obj),
                        log_placeholder=log_ph,
                        on_substep=_on_agg_sub,
                    )
                elif step_name == "generate-canonical-branch":
                    prog_cc = st.progress(0)
                    cap_cc = st.empty()
                    log_ph = st.empty()

                    def _on_canon_line(line: str) -> None:
                        # 形如："[3/10] 处理事件: E12"
                        m = re.match(r"^\[(\d+)/(\d+)\]\s*处理事件\s*:", line)
                        if not m:
                            if "✅ 处理完成" in line:
                                prog_cc.progress(1.0)
                                cap_cc.caption("主线生成：完成")
                            return

                        done = int(m.group(1))
                        total = int(m.group(2))
                        if total <= 0:
                            prog_cc.progress(1.0)
                            cap_cc.caption("主线生成：无事件")
                            return

                        prog_cc.progress(min(1.0, done / total))
                        cap_cc.caption(f"主线生成：**{done}** / **{total}** 个事件")

                    code = utils.run_single_cli_step(
                        project_root,
                        step_name,
                        _cli_params_for_step(step_name, params_obj),
                        log_ph,
                        on_stdout_line=_on_canon_line,
                        prompt_path_by_slot=_pp or None,
                    )
                elif step_name == "analyze-decision-points":
                    log_ph = st.empty()
                    out_raw = params_obj.get("output_path") or params_obj.get("output") or ""
                    out_raw = str(out_raw).strip()
                    out_path = Path(out_raw)
                    if not out_path.is_absolute():
                        out_path = project_root / out_path
                    base_dir = out_path.parent

                    rc = _ensure_canonical_branch_chronological(base_dir)
                    if rc != 0:
                        code = rc
                    else:
                        code = utils.run_single_cli_step(
                            project_root,
                            step_name,
                            _cli_params_for_step(step_name, params_obj),
                            log_ph,
                            prompt_path_by_slot=_pp or None,
                        )
                elif step_name == "generate-branch":
                    log_ph = st.empty()
                    code = utils.run_single_cli_step(
                        project_root,
                        step_name,
                        _cli_params_for_step(step_name, params_obj),
                        log_ph,
                        prompt_path_by_slot=_pp or None,
                    )
                elif step_name == "determine-ending-candidates":
                    log_ph = st.empty()
                    branches_path_raw = (
                        params_obj.get("branches") or params_obj.get("branches_path") or ""
                    )
                    branches_path_raw = str(branches_path_raw).strip()
                    branches_path = Path(branches_path_raw)
                    if not branches_path.is_absolute():
                        branches_path = project_root / branches_path

                    if not branches_path.is_file():
                        st.error(
                            "找不到 `branches.json`。请先在单步里执行 `generate-branch`，"
                            f"生成：`{branches_path}`"
                        )
                        code = 1
                    else:
                        code = utils.run_single_cli_step(
                            project_root,
                            step_name,
                            _cli_params_for_step(step_name, params_obj),
                            log_ph,
                            prompt_path_by_slot=_pp or None,
                        )
                elif step_name == "generate-all-paths":
                    log_ph = st.empty()
                    started, start_msg = _ensure_langgraph_studio_background(st, project_root)
                    if started:
                        st.success(start_msg)
                    else:
                        st.warning(start_msg)
                    code = utils.run_single_cli_step(
                        project_root,
                        step_name,
                        _cli_params_for_step(step_name, params_obj),
                        log_ph,
                        prompt_path_by_slot=_pp or None,
                    )
                else:
                    log_ph = st.empty()
                    code = utils.run_single_cli_step(
                        project_root,
                        step_name,
                        _cli_params_for_step(step_name, params_obj),
                        log_ph,
                        prompt_path_by_slot=_pp or None,
                    )
                if code == 0:
                    status_box.update(label=f"✅ {step_name} 完成", state="complete")
                else:
                    status_box.update(
                        label=f"❌ {step_name} 失败（退出码 {code}）", state="error"
                    )

        if code == 0 and preview_params is not None:
            _render_success_preview_for_single_step(
                st, project_root, step_name, preview_params
            )
            st.session_state[preview_ready_key] = True
            _handle_single_step_success_navigation(
                st,
                step_name=step_name,
                choice_idx=choice_idx,
                total_steps=len(step_names),
            )

    # 非 run_one 触发的 rerun（例如你点了决策点分析区按钮），也要保持预览渲染不消失。
    if (not run_one) and step_name in _SINGLE_STEP_HOLD_FOR_PREVIEW and st.session_state.get(preview_ready_key):
        _hold_params = params_obj if params_obj is not None else reuse_params
        _render_success_preview_for_single_step(
            st, project_root, step_name, _hold_params
        )


# ── Page: 运行结果（enhanced_paths 预览 / 编辑）────────────────────

def _page_enhanced_text(st, project_root: Path, default_base: Path) -> None:
    st.header("📊 运行结果")
    st.caption(
        "这里展示的是「叙事扩写」步骤产出的结果（默认每条剧情路径对应一个结果文件）。"
        "主线和支线可分别选择；点击保存会直接更新该文件，后续生成游戏脚本时会自动读取你保存后的内容。"
    )

    if not st.session_state.get("selected_input_path"):
        st.warning("请先在「📥 选择小说」中选择输入，以便自动合并 `generate-content` 的默认 output 路径。")
        if st.button("👉 去选择小说"):
            _goto(st, "📥 选择小说")
        return

    novel_name = st.session_state.get("novel_display_name", "—")
    st.caption(f"当前小说：**{novel_name}**")

    base_path_str = st.session_state.get("base_config_path", str(default_base))
    base_path = Path(base_path_str)
    if not base_path.is_absolute():
        base_path = project_root / base_path

    step_overrides = st.session_state.get("step_overrides")
    try:
        merged = utils.merge_step_params_for_ui(
            base_config_path=base_path,
            step_name="generate-content",
            step_overrides=step_overrides if isinstance(step_overrides, dict) else None,
            project_root=project_root,
            session_selected_input_path=st.session_state.get("selected_input_path"),
            session_ui_run_id=_effective_ui_run_id(st) or None,
        )
    except Exception as exc:
        st.error(f"合并 generate-content 参数失败：{exc}")
        merged = {"output": "out/enhanced_paths"}

    default_rel = str(merged.get("output") or "").strip() or "out/enhanced_paths"
    # 不在前端展示目录输入框：由运行时根据当前 run 自动推导 enhanced 目录。
    p = Path(default_rel)
    enhanced_dir = p if p.is_absolute() else (project_root / p)
    enhanced_content_preview.render_in_streamlit(
        st,
        enhanced_dir,
        project_root=project_root,
        export_base_name=st.session_state.get("novel_display_name") or None,
    )


# ── Page: CLI 参数目录（反射 src.cli）──────────────────────────────

def _page_cli_reference(st, project_root: Path) -> None:
    st.header("📚 CLI 参数目录")
    st.caption(
        "从 `src.cli` 自动读取每个子命令的 Typer 参数与函数源码，便于对照 "
        "`configs/*.json` 里 `steps.<步骤>.params` 做逐项验证。"
    )

    try:
        catalog = cli_catalog.build_cli_catalog(project_root)
        pipeline_names = cli_catalog.get_pipeline_step_names(project_root)
    except Exception as exc:
        st.error(f"无法加载 CLI 目录（请从项目根运行）：{exc}")
        return

    col_a, col_b = st.columns([1, 2])
    with col_a:
        only_pipeline = st.checkbox(
            "仅显示流水线内置步骤",
            value=True,
            help="与 `src/pipeline/builtin_steps.py` 中 BUILTIN_STEP_SPECS 一致",
        )
    with col_b:
        st.info(
            "流水线里：`steps` 的键 = 子命令名；`params` 的键 = 下表「参数名」。"
            "`builtin_steps` 会把键转成 `--kebab-case`；若与「CLI 选项」不一致，"
            "需在 JSON 里按实际 flag 自行核对（见「标记不一致」列）。"
        )

    names = [c["command"] for c in catalog]
    if only_pipeline:
        names = [n for n in names if n in pipeline_names]

    if not names:
        st.warning("没有可显示的命令，请取消勾选「仅显示流水线内置步骤」试试。")
        return

    by_name = {c["command"]: c for c in catalog}
    choice = st.selectbox("选择子命令", options=names, index=0)
    entry = by_name[choice]

    st.subheader(f"`{entry['command']}`")
    if entry["description"]:
        st.markdown(entry["description"])

    rel_file = ""
    if entry["source_file"] and str(project_root) in entry["source_file"]:
        try:
            rel_file = str(Path(entry["source_file"]).relative_to(project_root)).replace("\\", "/")
        except ValueError:
            rel_file = entry["source_file"]
    elif entry["source_file"]:
        rel_file = entry["source_file"]

    if rel_file:
        st.caption(
            f"源码：`{rel_file}` · 约第 **{entry['source_start_line']}** 行起 · "
            f"回调 `{entry['callback_name']}`"
        )

    params = entry["params"]
    if not params:
        st.info("该命令没有 Typer Option/Argument（或均为隐藏）。")
    else:
        rows = []
        for p in params:
            opts_s = " ".join(p["opts"]) if p["opts"] else p["primary_cli"]
            rows.append(
                {
                    "参数名(JSON 键)": p["name"],
                    "CLI 选项": opts_s,
                    "pipeline 自动 flag": p["inferred_pipeline_flag"],
                    "标记不一致": "⚠️ 是" if p["flag_mismatch"] else "否",
                    "默认值": p["default_str"],
                    "说明": p["help"],
                }
            )
        st.dataframe(rows, use_container_width=True, hide_index=True)
        if any(p["flag_mismatch"] for p in params):
            st.warning(
                "「标记不一致」表示：`run-pipeline` 把 JSON 键转成 `--参数名-转-kebab` 时，"
                "**不等于** Typer 实际注册的主选项。此时命令行可能不认自动生成的 flag，"
                "需要改 `builtin_steps.params_to_cli_args` 或在实现里对齐选项名。"
            )

    st.subheader("函数源码（cli.py 中的定义）")
    with st.expander("展开 / 折叠源码", expanded=True):
        st.code(entry["source_code"] or "(无法获取源码)", language="python")

    st.divider()
    st.markdown("**本地验证示例**（在项目根终端执行）：")
    st.code(f"python -m src.cli {entry['command']} --help", language="bash")


# ── 入口 ──────────────────────────────────────────────────────────

def main() -> None:
    try:
        st = importlib.import_module("streamlit")
    except ModuleNotFoundError as exc:
        raise SystemExit(
            "缺少 streamlit 依赖。请先运行: python -m pip install streamlit"
        ) from exc

    st.set_page_config(page_title="小说→文游 Pipeline", page_icon="🎮", layout="wide")

    # 启动时静默探测 Neo4j（不打印终端日志、不自动拉起进程）；未连接时仅给一句短提示
    if "_neo4j_checked" not in st.session_state:
        st.session_state["_neo4j_checked"] = True
        st.session_state["_neo4j_connected"] = False
        try:
            ensure_neo4j_ready(auto_start=False, reporter=None)
            st.session_state["_neo4j_connected"] = True
        except Exception:
            pass

    project_root = Path(__file__).resolve().parent.parent.parent
    default_base = project_root / "configs" / "pipeline_demo.json"
    ui_dir = Path(__file__).resolve().parent

    # 未连接时只提示一次：用 toast 自动消失，避免常驻横幅挡界面
    if not st.session_state.get("_neo4j_connected"):
        if not st.session_state.get("_neo4j_offline_toast_shown"):
            st.session_state["_neo4j_offline_toast_shown"] = True
            _neo4j_hint = (
                "Neo4j 未连接。使用「导入图数据库」等功能前请先在本机启动 Neo4j。"
            )
            if hasattr(st, "toast"):
                st.toast(_neo4j_hint, icon="💡")
            else:
                st.caption(f"💡 {_neo4j_hint}")

    # 全局样式：网页字体 + 侧栏。拼贴风素材建议：①侧栏头图（品牌识别）②「首页/概览」首屏
    # ③空状态/占位（无小说时）④ favicon 小图；正文页以留白为主，避免满屏纹理抢操作。
    st.markdown(
        '<style>\n@import url("https://fonts.googleapis.com/css2?family=Noto+Serif+SC:wght@400;600&display=swap");\n'
        + _midable_xiaolai_font_face_css(ui_dir)
        + """
        /* Midable 在前：拉丁用 Midable（@font-face unicode-range）；中文落到 Xiaolai SC */
        .stApp {
            font-family: "Midable", "Xiaolai SC", "Xiaolai", "Noto Serif SC", "Source Han Serif SC", "Songti SC", "SimSun", serif !important;
        }
        .stApp h1, .stApp h2, .stApp h3 {
            font-family: "Midable", "Xiaolai SC", "Xiaolai", "Noto Serif SC", "Source Han Serif SC", "Songti SC", "SimSun", serif !important;
            font-weight: 600 !important;
        }
        /* 正文：同上 */
        .stApp [data-testid="stMarkdownContainer"],
        .stApp [data-testid="stMarkdownContainer"] p,
        .stApp [data-testid="stMarkdownContainer"] li,
        .stApp [data-testid="stMarkdownContainer"] td,
        .stApp [data-testid="stMarkdownContainer"] th,
        .stApp [data-testid="stMarkdownContainer"] span,
        .stApp .stMarkdown,
        .stApp .stMarkdown p,
        .stApp .stMarkdown li,
        [data-testid="stMain"] [data-testid="stMarkdownContainer"] *,
        section[data-testid="stSidebar"] [data-testid="stMarkdownContainer"] *,
        .stApp label,
        .stApp .stCaption,
        .stApp [data-testid="stWidgetLabel"],
        .stApp [data-baseweb="typo"],
        .stApp [data-testid="stText"],
        .stApp .stRadio label,
        .stApp .stCheckbox label,
        .stApp .stSelectbox label,
        .stApp .stTextInput label,
        .stApp .stNumberInput label {
            font-family: "Midable", "Xiaolai SC", "Xiaolai", "Noto Serif SC", "Source Han Serif SC", "Songti SC", "SimSun", serif !important;
        }
        .stApp [data-testid="stMarkdownContainer"] pre,
        .stApp [data-testid="stMarkdownContainer"] code {
            font-family: ui-monospace, "Cascadia Code", "Consolas", monospace !important;
        }
        /* 整页浅蓝底（粒子画在 stMain 上，否则会被主区实色底整块挡住） */
        [data-testid="stAppViewContainer"] {
            background: #e8f2fc !important;
            background-attachment: scroll !important;
            background-repeat: no-repeat !important;
            background-size: cover !important;
            background-position: center top !important;
            overflow-y: auto !important;
            overflow-x: hidden !important;
        }
        /* 粒子：仅用散点 radial（无 repeat=无网格），动效用 transform（比 background-position 可靠） */
        @keyframes noveltovn-particle-float {
            0% { transform: translate3d(0, 0, 0); }
            33% { transform: translate3d(42px, -32px, 0); }
            66% { transform: translate3d(-28px, 26px, 0); }
            100% { transform: translate3d(14px, -18px, 0); }
        }
        @media (prefers-reduced-motion: reduce) {
            [data-testid="stMain"]::before {
                animation: none !important;
            }
        }
        /* 右上顶栏：薄荷蓝底；窄条贴右；background_top.jpg 由 _apply_header_background_top 叠在上方 */
        header[data-testid="stHeader"] {
            background: #d0f0f4 !important;
            border: 1px solid rgba(64, 165, 180, 0.38) !important;
            border-top: none !important;
            border-radius: 0 0 10px 10px !important;
            width: fit-content !important;
            max-width: min(15rem, 42vw) !important;
            margin: 0.2rem 0rem 0.3rem auto !important;
            padding: 0.55rem 0.35rem 0.6rem 0.35rem !important;
            min-height: 4.5rem !important;
            box-shadow: 0 1px 3px rgba(64, 165, 180, 0.14) !important;
        }
        header[data-testid="stHeader"] [data-testid="stToolbar"] {
            min-height: 3.9rem !important;
        }
        header[data-testid="stHeader"] button {
            padding: 0.15rem 0.35rem !important;
            font-size: 0.82rem !important;
        }
        /* 侧栏：此处先透明；background_left.jpg 由 _apply_sidebar_background 覆盖；淡黄色小卡片见下 */
        section[data-testid="stSidebar"],
        section[data-testid="stSidebar"] > div,
        section[data-testid="stSidebar"] [data-testid="stSidebarContent"] {
            background: transparent !important;
            border: none !important;
            box-shadow: none !important;
        }
        /* 侧栏铺底图之上叠一层浅蓝遮罩（与主区色调统一）。
           改遮罩浓度/颜色：只改下面 ::after 的 background（alpha 越大越「发白」盖住底图） */
        section[data-testid="stSidebar"] {
            color: #183048 !important;
            margin: 0rem 0rem 0rem 0rem !important;
            position: relative !important;
            isolation: isolate !important;
            z-index: 1 !important;
        }
        section[data-testid="stSidebar"]::after {
            content: "" !important;
            position: absolute !important;
            inset: 0 !important;
            background: rgba(232, 245, 252, 0.12) !important;
            pointer-events: none !important;
            z-index: 0 !important;
            border-radius: 10px !important;
        }
        section[data-testid="stSidebar"] > div,
        section[data-testid="stSidebar"] [data-testid="stSidebarContent"] {
            position: relative !important;
            z-index: 1 !important;
        }
        section[data-testid="stSidebar"] [data-testid="stVerticalBlock"] {
            background: transparent !important;
        }
        [data-testid="stMain"] {
            /* 主内容区底色随内容滚动，避免上下分层露底 */
            background: rgba(232, 245, 252, 0.96) !important;
            background-attachment: scroll !important;
            background-size: auto !important;
            border-radius: 2px !important;
            margin: 0.05rem 5rem 5rem 0 !important;
            border: 0px solid rgba(24, 48, 72, 0.12) !important;
            box-shadow: none !important;
            min-height: 100vh !important;
            position: relative !important;
            z-index: 1 !important;
            overflow: auto !important;
        }
        /* 任意首层包裹（div / fragment 容器），避免仅 section>div 时 z-index 失效 */
        [data-testid="stMain"] > * {
            position: relative !important;
            z-index: 1 !important;
        }
        /* Streamlit 主区 block 容器默认有不透明底，会整块盖住 stMain::before 粒子 */
        [data-testid="stMain"] [data-testid="stMainBlockContainer"] {
            background: transparent !important;
            position: relative !important;
            isolation: isolate !important;
        }
        [data-testid="stMain"] [data-testid="stMainBlockContainer"]::before {
            content: "" !important;
            display: block !important;
            position: absolute !important;
            inset: 0 !important;
            z-index: 0 !important;
            pointer-events: none !important;
            opacity: 0.88 !important;
            background-size: 100% 100% !important;
            background-repeat: no-repeat !important;
            background-position: 0 0 !important;
            background-image:
                radial-gradient(circle at 7% 11%,
                    rgba(255, 255, 255, 0.95) 1.35px, transparent 2.85px),
                radial-gradient(circle at 61% 8%,
                    rgba(24, 112, 132, 0.55) 1.1px, transparent 2.45px),
                radial-gradient(circle at 23% 36%,
                    rgba(255, 255, 255, 0.88) 1.2px, transparent 2.55px),
                radial-gradient(circle at 84% 24%,
                    rgba(24, 112, 132, 0.48) 1px, transparent 2.2px),
                radial-gradient(circle at 13% 68%,
                    rgba(255, 255, 255, 0.78) 1.1px, transparent 2.35px),
                radial-gradient(circle at 93% 51%,
                    rgba(24, 112, 132, 0.42) 0.95px, transparent 2.05px),
                radial-gradient(circle at 41% 19%,
                    rgba(24, 112, 132, 0.5) 1.05px, transparent 2.15px),
                radial-gradient(circle at 54% 86%,
                    rgba(255, 255, 255, 0.82) 1.25px, transparent 2.6px),
                radial-gradient(circle at 5% 52%,
                    rgba(24, 112, 132, 0.38) 0.92px, transparent 1.95px),
                radial-gradient(circle at 72% 41%,
                    rgba(255, 255, 255, 0.72) 1.05px, transparent 2.25px),
                radial-gradient(circle at 46% 6%,
                    rgba(255, 255, 255, 0.62) 1px, transparent 2.05px),
                radial-gradient(circle at 31% 63%,
                    rgba(24, 112, 132, 0.45) 0.98px, transparent 2.02px),
                radial-gradient(circle at 88% 92%,
                    rgba(255, 255, 255, 0.68) 1.12px, transparent 2.32px),
                radial-gradient(circle at 18% 89%,
                    rgba(24, 112, 132, 0.36) 0.9px, transparent 1.88px),
                radial-gradient(circle at 52% 31%,
                    rgba(24, 112, 132, 0.4) 0.95px, transparent 1.95px),
                radial-gradient(circle at 77% 71%,
                    rgba(255, 255, 255, 0.75) 1.15px, transparent 2.4px),
                radial-gradient(circle at 35% 94%,
                    rgba(255, 255, 255, 0.58) 1.02px, transparent 2.02px),
                radial-gradient(circle at 96% 17%,
                    rgba(24, 112, 132, 0.34) 0.88px, transparent 1.82px),
                radial-gradient(circle at 11% 28%,
                    rgba(24, 112, 132, 0.46) 1px, transparent 2px),
                radial-gradient(circle at 66% 58%,
                    rgba(255, 255, 255, 0.85) 1.22px, transparent 2.5px),
                radial-gradient(circle at 39% 77%,
                    rgba(24, 112, 132, 0.38) 0.92px, transparent 1.92px),
                radial-gradient(circle at 58% 44%,
                    rgba(255, 255, 255, 0.65) 1.08px, transparent 2.18px) !important;
            animation: none !important;
        }
        /* st.container(border=True)：NovelToVN / 导航 / 当前状态 / 流水线 / 快速开始 — 淡黄色小卡片（与浅蓝页底区分） */
        section[data-testid="stSidebar"] [data-testid="stVerticalBlock"] > div > [data-testid="stVerticalBlock"],
        [data-testid="stMain"] [data-testid="stVerticalBlock"] > div > [data-testid="stVerticalBlock"] {
            border-radius: 10px !important;
            background: #fff9e6 !important;
            border: 1px solid rgba(234, 179, 8, 0.55) !important;
            box-shadow: 0 1px 3px rgba(234, 179, 8, 0.18) !important;
            padding: 0.5rem 0.6rem !important;
            margin-top: 0.2rem !important;
            margin-bottom: 0.35rem !important;
        }
        /* 首页首屏：文案撑高度 */
        [data-testid="stMain"] .home-sticker-surface {
            position: relative !important;
            isolation: isolate !important;
            margin-top: -5.75rem !important;
            margin-bottom: 0.35rem !important;
            padding: 0.25rem 1rem 0.45rem 0 !important;
            overflow: visible !important;
        }
        /* 兔子内联贴纸：散落在各内容块旁 */
        .rabbit-inline-sticker {
            pointer-events: none !important;
            filter: drop-shadow(0 3px 8px rgba(24, 48, 72, 0.12)) !important;
            z-index: 3 !important;
            height: auto !important;
            object-fit: contain !important;
        }
        [data-testid="stMain"] .home-hero-row {
            position: relative !important;
            z-index: 2 !important;
            display: flex !important;
            align-items: flex-start !important;
            justify-content: flex-start !important;
            gap: 0.65rem 1rem !important;
            flex-wrap: wrap !important;
            margin-top: 0 !important;
            margin-bottom: 0 !important;
            max-width: 100% !important;
            padding: 0.1rem 0.5rem 0 0 !important;
            box-sizing: border-box !important;
        }
        @media (max-width: 720px) {
            [data-testid="stMain"] .home-sticker-surface {
                padding-right: 0 !important;
            }
            .rabbit-inline-sticker {
                width: 28px !important;
            }
        }
        [data-testid="stMain"] .home-hero-row .home-hero-text {
            flex: 1 1 14rem !important;
            min-width: 0 !important;
        }
        [data-testid="stMain"] .home-main-title {
            margin-top: 0 !important;
            margin-bottom: 0.4rem !important;
        }
        [data-testid="stMain"] .home-main-title h2 {
            margin: 0 !important;
            padding: 0 !important;
            font-size: 1.55rem !important;
            line-height: 1.2 !important;
            border: none !important;
        }
        /* 首页/概览首段说明（正文灰阶） */
        [data-testid="stMain"] .home-intro-lede {
            margin-top: 0 !important;
            margin-bottom: 0.65rem !important;
        }
        [data-testid="stMain"] .home-intro-lede p {
            margin: 0 !important;
            line-height: 1.55 !important;
            color: rgba(44, 55, 68, 0.78) !important;
        }
        /* 流水线总览：与全站 st.container 小黄卡同色（#fff9e6 + 琥珀边） */
        .home-pipeline-overview-wrap {
            display: flex !important;
            align-items: stretch !important;
            flex-wrap: wrap !important;
            justify-content: center !important;
            gap: 0.62rem 0.42rem !important;
            margin: 0.72rem 0 1.18rem 0 !important;
        }
        .home-pipeline-card {
            flex: 1 1 0 !important;
            min-width: 5.9rem !important;
            text-align: center !important;
            padding: 0.72rem 0.52rem 0.78rem 0.52rem !important;
            min-height: 0 !important;
            box-sizing: border-box !important;
            background: #fff9e6 !important;
            border-radius: 10px !important;
            border: 1px solid rgba(234, 179, 8, 0.55) !important;
            box-shadow: 0 1px 3px rgba(234, 179, 8, 0.18) !important;
            opacity: 1 !important;
            transform: none !important;
            transition: transform 0.2s ease, box-shadow 0.2s ease, opacity 0.2s ease !important;
            cursor: default !important;
        }
        .home-pipeline-card:hover {
            transform: translateY(-2px) !important;
            box-shadow: 0 4px 12px rgba(234, 179, 8, 0.22) !important;
            opacity: 0.72 !important;
        }
        .home-pipeline-card__icon {
            font-size: 2rem !important;
            line-height: 1 !important;
            margin-bottom: 0.4rem !important;
            animation: none !important;
            filter: none !important;
        }
        .home-pipeline-card__title {
            font-weight: 600 !important;
            color: #2c3e50 !important;
            font-size: 0.84rem !important;
            letter-spacing: 0.01em !important;
            line-height: 1.34 !important;
        }
        .home-pipeline-card__desc {
            color: rgba(24, 48, 72, 0.5) !important;
            font-size: 0.7rem !important;
            margin-top: 0.32rem !important;
            line-height: 1.42 !important;
        }
        .home-pipeline-flow-arrow {
            display: flex !important;
            align-items: center !important;
            justify-content: center !important;
            flex: 0 0 auto !important;
            align-self: center !important;
            padding: 0 0.12rem !important;
            color: rgba(24, 48, 72, 0.32) !important;
        }
        .home-pipeline-arrow-icon {
            display: block !important;
        }
        .home-after-pipeline-gap {
            height: 0 !important;
            margin: 1.35rem 0 0.58rem 0 !important;
            border-top: 1px solid rgba(234, 179, 8, 0.22) !important;
        }
        [data-testid="stMain"] .home-quickstart-push {
            padding-top: 6rem !important;
            margin: 0 !important;
            height: 0 !important;
            overflow: hidden !important;
        }
        .home-pipeline-section-start {
            position: absolute !important;
            width: 0 !important;
            height: 0 !important;
            overflow: hidden !important;
            pointer-events: none !important;
        }
        [data-testid="stMain"] [data-testid="stVerticalBlock"] > div > [data-testid="stVerticalBlock"]:has(.home-pipeline-section-start) {
            background: #fff9e6 !important;
            border: 1px solid rgba(234, 179, 8, 0.55) !important;
            border-radius: 10px !important;
            padding: 1.5rem 1.2rem !important;
            margin: 1.08rem 0 1.88rem 0 !important;
            box-shadow: 0 1px 3px rgba(234, 179, 8, 0.18) !important;
            position: relative !important;
        }
        [data-testid="stMain"] [data-testid="stVerticalBlockBorder"]:has(.home-quickstart-cta-anchor) {
            background: #fff9e6 !important;
            border: 1px solid rgba(234, 179, 8, 0.55) !important;
            border-radius: 10px !important;
            padding: 1.45rem 1.2rem 1.3rem 1.2rem !important;
            margin: 0.2rem 0 0.95rem 0 !important;
            box-shadow: 0 1px 3px rgba(234, 179, 8, 0.18) !important;
        }
        [data-testid="stMain"] [data-testid="stVerticalBlockBorder"]:has(.home-quickstart-cta-anchor)
            [data-testid="stMarkdownContainer"] p {
            line-height: 1.68 !important;
            margin: 0.22rem 0 !important;
        }
        @media (prefers-reduced-motion: reduce) {
            .home-pipeline-card:hover {
                transform: none !important;
            }
        }
        @keyframes ui-fade-in {
            from {
                opacity: 0.88;
                transform: translateY(8px);
            }
            to {
                opacity: 1;
                transform: translateY(0);
            }
        }
        [data-testid="stMain"] > * [data-testid="stVerticalBlock"] {
            animation: ui-fade-in 0.32s ease-out !important;
        }
        @media (prefers-reduced-motion: reduce) {
            [data-testid="stMain"] > * [data-testid="stVerticalBlock"] {
                animation: none !important;
            }
        }
        /* Primary 按钮：海军蓝 + 快速开始区留白与强调 */
        .stApp button[kind="primary"],
        .stApp .stButton > button[data-testid="baseButton-primary"] {
            background-color: #183048 !important;
            color: #FFF8F0 !important;
            border: none !important;
            border-radius: 14px !important;
            padding: 0.75rem 2.5rem !important;
            font-size: 1.05rem !important;
            font-weight: 600 !important;
            box-shadow: 0 4px 14px rgba(24, 48, 72, 0.15) !important;
            transition: background-color 0.2s ease, transform 0.2s ease, box-shadow 0.2s ease !important;
        }
        .stApp button[kind="primary"]:hover,
        .stApp .stButton > button[data-testid="baseButton-primary"]:hover {
            background-color: #2D4A7A !important;
            transform: translateY(-2px) !important;
            box-shadow: 0 6px 20px rgba(24, 48, 72, 0.22) !important;
        }
        /* 仅首页「快速开始」主按钮：与说明留白、居中（避免全站 primary 都被顶开） */
        [data-testid="stMain"] [data-testid="stVerticalBlockBorder"]:has(.home-quickstart-cta-anchor)
            .stButton:has(button[data-testid="baseButton-primary"]) {
            margin-top: 0.7rem !important;
            width: 100% !important;
            display: flex !important;
            justify-content: center !important;
        }
        /* 让标题/说明类文字更贴合侧栏整体色调 */
        section[data-testid="stSidebar"] h1,
        section[data-testid="stSidebar"] h2,
        section[data-testid="stSidebar"] h3,
        section[data-testid="stSidebar"] p,
        section[data-testid="stSidebar"] label{
            color: #183048 !important;
        }

        /* NovelToVN 小卡片内标题区（已无顶图） */
        section[data-testid="stSidebar"] .sidebar-brand-head {
            margin-top: 0 !important;
            margin-bottom: 0.8rem !important;
        }
        section[data-testid="stSidebar"] .sidebar-brand-head h1 {
            color: #183048 !important;
            margin: 0 !important;
            padding: 0 !important;
            font-size: 1.55rem !important;
            font-weight: 700 !important;
            line-height: 1.15 !important;
        }
        section[data-testid="stSidebar"] .sidebar-brand-head .sidebar-brand-sub {
            color: rgba(24, 48, 72, 0.72) !important;
            margin: 0.12rem 0 0 0 !important;
            padding: 0 !important;
            font-size: 0.82rem !important;
            line-height: 1.3 !important;
        }
        /* 导航 radio：与上下小黄卡留白；选项行距放松 */
        section[data-testid="stSidebar"] [data-testid="stRadio"],
        section[data-testid="stSidebar"] .stRadio {
            margin-top: 0.15rem !important;
            margin-bottom: 0.2rem !important;
        }
        section[data-testid="stSidebar"] [data-testid="stRadio"] label,
        section[data-testid="stSidebar"] .stRadio label {
            padding: 0.55rem 0.4rem !important;
            border-bottom: 1px solid rgba(24, 48, 72, 0.05) !important;
            font-size: 0.9rem !important;
            transition: background 0.15s ease !important;
            border-radius: 8px !important;
        }
        section[data-testid="stSidebar"] [data-testid="stRadio"] label:hover,
        section[data-testid="stSidebar"] .stRadio label:hover {
            background: rgba(24, 48, 72, 0.03) !important;
        }
        section[data-testid="stSidebar"] [data-testid="stRadio"] label:has(input:checked),
        section[data-testid="stSidebar"] .stRadio label:has(input:checked) {
            background: rgba(24, 48, 72, 0.11) !important;
            font-weight: 600 !important;
        }
        section[data-testid="stSidebar"] [data-testid="stExpander"] {
            background: rgba(24, 48, 72, 0.02) !important;
            border-radius: 10px !important;
            border: 1px solid rgba(24, 48, 72, 0.06) !important;
        }
        section[data-testid="stSidebar"] [data-testid="stExpander"] details {
            border: none !important;
        }
        /* 「当前状态」caption 与上方导航区块间距 */
        section[data-testid="stSidebar"] [data-testid="stCaption"] {
            margin-top: 0.1rem !important;
            margin-bottom: 0.1rem !important;
            line-height: 1.3 !important;
        }
        section[data-testid="stSidebar"] [data-testid="stMarkdownContainer"] p:not(.sidebar-brand-sub) {
            margin: 0.35rem 0 !important;
            line-height: 1 !important;
        }

        /* ── 通知卡片（info / success / warning / error）── */
        [data-testid="stAlert"] {
            border-radius: 12px !important;
            box-shadow: 0 2px 8px rgba(24, 48, 72, 0.08) !important;
            border-left-width: 4px !important;
        }

        /* ── Expander 折叠面板（主区域）── */
        [data-testid="stMain"] details[data-testid="stExpander"] {
            border-radius: 12px !important;
            border: 1px solid rgba(234, 179, 8, 0.35) !important;
            background: rgba(255, 249, 230, 0.45) !important;
            box-shadow: 0 1px 4px rgba(234, 179, 8, 0.10) !important;
            transition: box-shadow 0.2s ease !important;
        }
        [data-testid="stMain"] details[data-testid="stExpander"]:hover {
            box-shadow: 0 3px 10px rgba(234, 179, 8, 0.16) !important;
        }
        [data-testid="stMain"] details[data-testid="stExpander"] summary {
            border-radius: 12px !important;
            padding: 0.65rem 0.8rem !important;
            font-weight: 600 !important;
        }

        /* ── Selectbox 下拉框 ── */
        .stApp [data-baseweb="select"] > div {
            border-radius: 10px !important;
            border-color: rgba(24, 48, 72, 0.18) !important;
            min-height: 2.6rem !important;
            transition: border-color 0.2s ease, box-shadow 0.2s ease !important;
        }
        .stApp [data-baseweb="select"] > div:hover {
            border-color: rgba(24, 112, 132, 0.45) !important;
            box-shadow: 0 0 0 1px rgba(24, 112, 132, 0.12) !important;
        }
        .stApp [data-baseweb="popover"] [role="listbox"] {
            border-radius: 10px !important;
            box-shadow: 0 6px 20px rgba(24, 48, 72, 0.14) !important;
            border: 1px solid rgba(24, 48, 72, 0.10) !important;
        }
        .stApp [data-baseweb="popover"] [role="option"] {
            padding: 0.55rem 0.8rem !important;
            line-height: 1.5 !important;
            border-radius: 6px !important;
            margin: 2px 4px !important;
        }
        .stApp [data-baseweb="popover"] [role="option"]:hover {
            background: rgba(232, 244, 252, 0.7) !important;
        }

        /* ── Primary 按钮呼吸光晕 ── */
        @keyframes noveltovn-btn-glow {
            0%, 100% { box-shadow: 0 4px 14px rgba(24, 48, 72, 0.15); }
            50% { box-shadow: 0 4px 22px rgba(24, 112, 132, 0.30); }
        }
        .stApp button[kind="primary"],
        .stApp .stButton > button[data-testid="baseButton-primary"] {
            animation: noveltovn-btn-glow 3s ease-in-out infinite !important;
        }
        .stApp button[kind="primary"]:hover,
        .stApp .stButton > button[data-testid="baseButton-primary"]:hover {
            animation: none !important;
        }
        @media (prefers-reduced-motion: reduce) {
            .stApp button[kind="primary"],
            .stApp .stButton > button[data-testid="baseButton-primary"] {
                animation: none !important;
            }
        }

        /* ── Number Input / Text Input 圆角 ── */
        .stApp [data-testid="stNumberInput"] input,
        .stApp [data-testid="stTextInput"] input {
            border-radius: 8px !important;
            transition: border-color 0.2s ease !important;
        }
        .stApp [data-testid="stNumberInput"] input:focus,
        .stApp [data-testid="stTextInput"] input:focus {
            border-color: rgba(24, 112, 132, 0.55) !important;
            box-shadow: 0 0 0 1px rgba(24, 112, 132, 0.15) !important;
        }

        /* ── Metric 卡片（一键运行页配置摘要）── */
        [data-testid="stMetric"] {
            background: rgba(255, 249, 230, 0.55) !important;
            border: 1px solid rgba(234, 179, 8, 0.30) !important;
            border-radius: 12px !important;
            padding: 0.8rem 0.7rem !important;
            box-shadow: 0 1px 4px rgba(234, 179, 8, 0.10) !important;
        }
        [data-testid="stMetric"] [data-testid="stMetricLabel"] {
            font-weight: 600 !important;
            color: rgba(24, 48, 72, 0.7) !important;
        }
        [data-testid="stMetric"] [data-testid="stMetricValue"] {
            font-size: 1.05rem !important;
            color: #183048 !important;
        }

        /* ── Checkbox 美化 ── */
        .stApp [data-testid="stCheckbox"] label {
            padding: 0.4rem 0.2rem !important;
            border-radius: 8px !important;
            transition: background 0.15s ease !important;
        }
        .stApp [data-testid="stCheckbox"] label:hover {
            background: rgba(232, 244, 252, 0.5) !important;
        }
        </style>
        """,
        unsafe_allow_html=True,
    )

    _apply_sidebar_background(st, ui_dir)
    _apply_header_background_top(st, ui_dir)

    # 必须在实例化 key=nav_page 的 radio 之前应用按钮跳转（见 _goto 说明）
    _pending = st.session_state.pop("_nav_pending", None)
    if _pending is not None:
        st.session_state["nav_page"] = _pending
    if "nav_page" not in st.session_state:
        st.session_state["nav_page"] = _PAGES[0]
    elif st.session_state["nav_page"] == "🏠 项目介绍":
        st.session_state["nav_page"] = "🏠 首页/概览"
    elif st.session_state["nav_page"] == "📖 增强文本":
        st.session_state["nav_page"] = "📊 运行结果"
    elif st.session_state["nav_page"] == "🧰 工具":
        st.session_state["nav_page"] = "🎮 游戏运行"

    # ── 侧边栏：导航 + 实时状态 ──
    with st.sidebar:
        with st.container(border=True):
            st.markdown(
                """
                <div class="sidebar-brand-head">
                  <h1>NovelToVN</h1>
                  <p class="sidebar-brand-sub">小说 → 文字冒险游戏 自动转换</p>
                </div>
                """,
                unsafe_allow_html=True,
            )

        with st.container(border=True):
            st.radio("导航", _PAGES, key="nav_page")

        with st.container(border=True):
            st.caption("当前状态")
            novel = st.session_state.get("novel_display_name", "未选择")
            rid = _effective_ui_run_id(st) or "—"
            oroot = _effective_ui_output_root(st) or "—"
            if novel == "未选择":
                status_label = "📄 未选择小说"
            elif len(novel) > 36:
                status_label = f"📄 {novel[:36]}…"
            else:
                status_label = f"📄 {novel}"
            with st.expander(status_label, expanded=False):
                _render_sidebar_status_expanded(st, novel, rid, oroot)

    # ── 页面路由 ──
    page = st.session_state["nav_page"]
    _apply_home_background(st, page == "🏠 首页/概览")
    if page == "🏠 首页/概览":
        _page_home(st, ui_dir)
    elif page == "📥 选择小说":
        _page_select_novel(st, project_root)
    elif page == "⚙️ 参数配置":
        _page_configure(st, project_root, default_base)
    elif page == "🚀 一键运行":
        _page_run(st, project_root, default_base)
    elif page == "🔧 单步执行":
        _page_single_step(st, project_root, default_base)
    elif page == "📊 运行结果":
        _page_enhanced_text(st, project_root, default_base)
    elif page == "🎮 游戏运行":
        _page_tools(st, project_root, default_base)


if __name__ == "__main__":
    main()
