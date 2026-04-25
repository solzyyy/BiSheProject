"""
脚本内容生成器 📝✨

职责：
- 使用 LLM 生成详细的脚本内容（场景描述、对话等）
- 将简短的 scene_description 扩展为详细的描述
- 从 source_text 提取或生成完整的对话
- 生成决策点的 player_choice 结构

文学创作相关（场景描述、对话生成、结局场景、以及按 branch_points 预生成的 player_choice 目录）使用可配置模型（默认 Gemini 2.5 Flash），并注入去 AI 味系统预设。
结果结构（与 Ren'Py 脚本生成管道一致）：
- PathContentResult：单条路径的增强结果，含 path_id, renpy_label, events, ending, metadata
- 每个 event：
  - detailed_scene_description：文游的叙述/描写（旁白，第三人称），不包含选项提示、不包含角色直接引语
  - dialogue：角色对话（speaker + text），与描述分开
  - scene_description, source_text, (optional) player_choice
"""

import json
import asyncio
from pathlib import Path
from typing import Dict, List, Any, Optional, Tuple

import os

from core.llm_client import AsyncLLMClient
from core.prompt_overrides import (
    SLOT_STORY_SYSTEM,
    prompt_env_key,
    read_prompt_with_override,
)

# 去 AI 味系统预设（文学创作时注入）
_STORY_SYSTEM_PROMPT_CACHE: Optional[str] = None

def _get_story_system_prompt() -> str:
    """加载 prompts/story_generation_system_prompt.txt；支持 ``PIPELINE_PROMPT_FILE_STORY_SYSTEM`` 覆盖。"""
    global _STORY_SYSTEM_PROMPT_CACHE
    try:
        project_root = Path(__file__).resolve().parent.parent
        path = project_root / "prompts" / "story_generation_system_prompt.txt"
        if os.environ.get(prompt_env_key(SLOT_STORY_SYSTEM), "").strip():
            return read_prompt_with_override(path, SLOT_STORY_SYSTEM).strip()
        if _STORY_SYSTEM_PROMPT_CACHE is None:
            _STORY_SYSTEM_PROMPT_CACHE = read_prompt_with_override(path, SLOT_STORY_SYSTEM).strip()
        return _STORY_SYSTEM_PROMPT_CACHE
    except Exception:
        return ""


def build_path_content_result(
    path_data: Dict[str, Any],
    events: List[Dict[str, Any]],
    ending: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """
    构建单条路径的内容生成结果（供 Ren'Py 脚本生成使用）。
    保留 path_data 的 path_id、renpy_label、metadata 等，仅替换 events 与 ending。
    """
    result = {**path_data}
    result["events"] = events
    if ending is not None:
        result["ending"] = ending
    return result


def is_complete_enhanced_path(data: Dict[str, Any]) -> bool:
    """
    判断路径 JSON 是否已是完整增强结果（可直接复用，无需重新跑 LLM）。
    条件：
    1) 有 events，且至少有一个事件含 detailed_scene_description；
    2) 对于 decision_point 事件：player_choice/options 中每个选项都必须包含非空的 choice_id 与 jump_target，
       否则认为输出不完整（避免沿用缺 jump_target 的旧结果）。
    """
    events = data.get("events")
    if not events or not isinstance(events, list):
        return False
    has_detailed_scene = False
    for ev in events:
        if ev.get("detailed_scene_description"):
            has_detailed_scene = True

        if ev.get("type") == "decision_point":
            player_choice = ev.get("player_choice")
            if not isinstance(player_choice, dict):
                return False
            options = player_choice.get("options")
            if not isinstance(options, list) or not options:
                return False
            for opt in options:
                if not isinstance(opt, dict):
                    return False
                if not (opt.get("choice_id") or "").strip():
                    return False
                if not (opt.get("jump_target") or "").strip():
                    return False

    return has_detailed_scene


def _canonical_events_by_id(canonical_events: List[Dict[str, Any]]) -> Dict[str, Dict[str, Any]]:
    """主线 events 按 event_id 建表，便于按 type 复用时查找。"""
    return {ev.get("event_id", ""): ev for ev in (canonical_events or []) if ev.get("event_id")}


def _path_combination_prefix_key(path_combination: List[Dict[str, Any]], length: int) -> Tuple[Tuple[str, str], ...]:
    """path_combination 前 length 个选择组成的 key，用于分段缓存。length=0 表示尚未做任何选择。"""
    if not path_combination or length <= 0:
        return ()
    return tuple(
        (c.get("fork_event_id", ""), c.get("branch_choice", ""))
        for c in path_combination[:length]
    )


def _split_events_into_segments(events: List[Dict[str, Any]]) -> List[List[Dict[str, Any]]]:
    """按 decision_point 把事件切成段：段0=开头到第1个决策点(含)，段1=其后到第2个决策点(含)，…，最后一段=最后一个决策点之后。"""
    dp_indices = [i for i, e in enumerate(events) if e.get("type") == "decision_point"]
    if not dp_indices:
        return [events] if events else []
    segments = []
    start = 0
    for i in dp_indices:
        segments.append(events[start : i + 1])
        start = i + 1
    if start < len(events):
        segments.append(events[start:])
    return segments


def _events_through_first_ending(events: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """
    只保留到首个结局事件为止（含该条），避免对 ending 之后误塞的条目做增强。
    识别：type == "ending" 或 is_ending 为真。
    """
    if not events:
        return events
    for i, e in enumerate(events):
        if not isinstance(e, dict):
            continue
        if e.get("type") == "ending" or e.get("is_ending"):
            return events[: i + 1]
    return events


class ContentGenerator:
    """脚本内容生成器。文学创作（场景/对话/结局）用可配置模型 + 去 AI 味预设。"""
    
    def __init__(
        self,
        llm_client: AsyncLLMClient,
        personas_file: str,
        player_choice_catalog: Optional[Dict[str, Any]] = None,
        rewrite_existing_dialogue: bool = True,
    ):
        """
        初始化内容生成器
        
        Args:
            llm_client: 默认 LLM 客户端（用于决策点选项等非文学创作）
            personas_file: character_personas.json 文件路径
            player_choice_catalog: 可选；由 decision_points + canonical + branches 预生成的
                player_choice 目录（仅 prompt/options + choice_id），增强时按 path_combination 补 jump_target
        """
        self.llm_client = llm_client
        self.personas_file = Path(personas_file)
        self._player_choice_catalog = player_choice_catalog
        self.rewrite_existing_dialogue = rewrite_existing_dialogue
        self._personas_cache: Optional[List[Dict[str, Any]]] = None
        self._extract_chain_cache: Optional[Dict[str, Any]] = None
        self._entities_cache: Optional[List[Dict[str, Any]]] = None
        # 与 character_personas 同目录的 extract_chain.json / entities.json（用于按事件筛人设）
        self._data_dir = self.personas_file.parent
        # 文学创作专用模型（默认 Gemini 2.5 Flash，可通过环境变量覆盖）
        self._literary_client: Optional[AsyncLLMClient] = None
    
    def _get_literary_client(self) -> AsyncLLMClient:
        """懒加载文学创作客户端（可配置，默认 Gemini 2.5 Flash）。"""
        if self._literary_client is None:
            # 可配置项：
            # - CONTENT_LITERARY_MODEL_NAME: 模型配置名（默认 gemini）
            # - CONTENT_LITERARY_MODEL:      具体模型名（默认 gemini-2.5-flash）
            literary_model_name = os.getenv("CONTENT_LITERARY_MODEL_NAME", "gemini")
            literary_model = os.getenv("CONTENT_LITERARY_MODEL", "gemini-2.5-flash")
            self._literary_client = AsyncLLMClient(
                model_name=literary_model_name,
                model=literary_model,
            )
        return self._literary_client

    async def build_player_choice_catalog(
        self,
        decision_analysis_path: Path,
        canonical_path: Path,
        branches_path: Path,
    ) -> Dict[str, Any]:
        """
        按 branch_points 预生成全局 player_choice 目录（每个分叉一次 LLM）。
        使用与场景/对话相同的文学创作客户端（默认 Gemini 2.5 Flash，见 CONTENT_LITERARY_* 环境变量）。
        """
        from scripts.player_choice_catalog import build_player_choice_catalog as _build_catalog

        return await _build_catalog(
            decision_analysis_path,
            canonical_path,
            branches_path,
            self._get_literary_client(),
        )
    
    def _literary_prompt(self, user_prompt: str) -> str:
        """在用户提示前拼接去 AI 味系统预设（若有）。"""
        system = _get_story_system_prompt()
        if system:
            return system + "\n\n---\n\n" + user_prompt
        return user_prompt
    
    def load_personas(self) -> List[Dict[str, Any]]:
        """加载角色人设数据"""
        if self._personas_cache is None:
            with open(self.personas_file, "r", encoding="utf-8") as f:
                data = json.load(f)
            self._personas_cache = data.get("personas", [])
        return self._personas_cache

    def _load_extract_chain(self) -> Dict[str, Any]:
        """加载 extract_chain.json（event_id -> 事件.人物）。"""
        if self._extract_chain_cache is None:
            path = self._data_dir / "extract_chain.json"
            if path.exists():
                with open(path, "r", encoding="utf-8") as f:
                    self._extract_chain_cache = json.load(f)
            else:
                self._extract_chain_cache = {}
        return self._extract_chain_cache

    def _load_entities(self) -> List[Dict[str, Any]]:
        """加载 entities.json（canonical_name/aliases -> id）。"""
        if self._entities_cache is None:
            path = self._data_dir / "entities.json"
            if path.exists():
                with open(path, "r", encoding="utf-8") as f:
                    data = json.load(f)
                self._entities_cache = data.get("entities", [])
            else:
                self._entities_cache = []
        return self._entities_cache

    def get_personas_for_event(self, event: Dict[str, Any]) -> List[Dict[str, Any]]:
        """
        只返回与该事件相关的人物人设：从 extract_chain 按 event_id 取「人物」名单，
        经 entities 转为 id，再从 character_personas 取对应人设。
        """
        event_id = (event.get("event_id") or event.get("original_event_id") or "").strip()
        if not event_id:
            return self.load_personas()
        chain = self._load_extract_chain()
        new_events = chain.get("new_events") or []
        names_in_event = set()
        for item in new_events:
            if (item.get("event_id") or "").strip() != event_id:
                continue
            ev = item.get("事件") or item.get("event") or {}
            for name in ev.get("人物", []) or ev.get("characters", []):
                if name and isinstance(name, str):
                    names_in_event.add(name.strip())
            break
        if not names_in_event:
            return self.load_personas()
        entities = self._load_entities()
        ids_for_names = set()
        for ent in entities:
            cname = (ent.get("canonical_name") or "").strip()
            if cname and cname in names_in_event:
                ids_for_names.add((ent.get("id") or "").strip())
            for alias in ent.get("aliases") or []:
                if alias and str(alias).strip() in names_in_event:
                    ids_for_names.add((ent.get("id") or "").strip())
                    break
        if not ids_for_names:
            return self.load_personas()
        all_personas = self.load_personas()
        return [p for p in all_personas if (p.get("id") or "").strip() in ids_for_names]

    def format_personas_for_prompt(self, personas: Optional[List[Dict[str, Any]]] = None) -> str:
        """格式化角色人设信息用于提示词；若传入 personas 则只格式化该列表，否则用全部。"""
        if personas is None:
            personas = self.load_personas()
        if not personas:
            return "（无）"
        lines = []
        for persona in personas:
            name = persona.get("name", "")
            role = persona.get("role", "")
            disposition = persona.get("disposition", "")
            worldview = persona.get("worldview", "")
            persona_summary = persona.get("persona_summary", "")
            
            lines.append(f"**{name}** ({role})")
            lines.append(f"- 性格倾向：{disposition}")
            lines.append(f"- 世界观：{worldview}")
            lines.append(f"- 人设摘要：{persona_summary}")
            lines.append("")
        
        return "\n".join(lines)
    
    def _narrative_perspective_instruction(self, perspective: str) -> str:
        """根据人称返回提示词中的叙述要求，委托 branch.narrative_perspective。"""
        try:
            from branch.narrative_perspective import get_narrative_perspective_instruction
        except ImportError:
            from src.branch.narrative_perspective import get_narrative_perspective_instruction
        return get_narrative_perspective_instruction(perspective)

    async def infer_narrative_perspective(
        self,
        events: List[Dict[str, Any]],
        ending_sample: Optional[str] = None,
    ) -> str:
        """根据路径内容推断叙述人称，委托 branch.narrative_perspective。"""
        try:
            from branch.narrative_perspective import infer_narrative_perspective_from_events
        except ImportError:
            from src.branch.narrative_perspective import infer_narrative_perspective_from_events
        return await infer_narrative_perspective_from_events(
            events,
            ending_sample=ending_sample,
            llm_client=self._get_literary_client(),
        )
    
    async def generate_scene_description(
        self,
        event: Dict[str, Any],
        previous_event: Optional[Dict[str, Any]] = None,
        narrative_perspective: str = "第三人称",
    ) -> str:
        """
        生成详细的场景描述
        
        Args:
            event: 事件数据
            previous_event: 前一个事件（用于连贯性）
            narrative_perspective: 叙述人称（第一人称/第二人称/第三人称），由 infer_narrative_perspective 推断或默认第三人称
            
        Returns:
            详细的场景描述（100-500字）
        """
        event_id = event.get("event_id", "")
        event_type = event.get("type", "")
        scene_desc = event.get("scene_description", "")
        source_text = event.get("source_text", "")
        perspective_instruction = self._narrative_perspective_instruction(narrative_perspective)
        
        # 构建提示词
        prompt = f"""你是一位专业的文学创作助手。请基于以下信息，生成一段详细的场景描述：

**事件信息**：
- 事件ID：{event_id}
- 事件类型：{event_type}
- 当前场景描述：{scene_desc}
- 原文片段：{source_text}

**上下文信息**：
- 前一个事件：{previous_event.get("scene_description", "") if previous_event else "无"}

**要求**：
1. **语言**：全文必须使用**简体中文**书写，禁止使用英文。
2. 基于原文片段扩展，保留文学风格
3. 补充环境细节、角色动作、心理活动
4. 与前一个事件自然过渡
5. 长度：100-500字（重要场景可达800字）
6. **叙述人称**：{perspective_instruction}
7. **只输出叙述/描写**：不要包含「你打算」「请选择」等选项提示，不要将角色对话写进本段（对话由 dialogue 字段单独输出）
8. 只输出场景描述文本，不要添加任何解释或格式标记

请生成详细的场景描述："""
        full_prompt = self._literary_prompt(prompt)
        try:
            client = self._get_literary_client()
            response = await client.invoke(full_prompt, return_json=False)
            description = response.strip()
            if description.startswith('"') and description.endswith('"'):
                description = description[1:-1]
            return description
        except Exception as e:
            print(f"⚠️  生成场景描述失败（事件 {event_id}）：{e}")
            return scene_desc
    
    def _normalize_dialogue(self, raw: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        """统一为 [{speaker, text, tone}]，兼容 content 字段。"""
        out = []
        for item in raw:
            if not isinstance(item, dict):
                continue
            text = item.get("text") or item.get("content", "")
            if not text:
                continue
            out.append({
                "speaker": item.get("speaker", ""),
                "text": text,
                "tone": item.get("tone", ""),
            })
        return out

    async def generate_dialogue(
        self,
        event: Dict[str, Any]
    ) -> List[Dict[str, Any]]:
        """
        生成/改写对话。
        
        - 若 event 已有 dialogue：
          - 当 ``self.rewrite_existing_dialogue`` 为 False：直接复用旧对话
          - 当为 True：把旧对话当作“草稿”，再交给 LLM 改写润色
        - 若 event 没有 dialogue：从 source_text 生成对话
        """
        event_id = event.get("event_id", "")
        existing_dialogue_raw = event.get("dialogue", [])
        existing_dialogue = (
            self._normalize_dialogue(existing_dialogue_raw) if existing_dialogue_raw else []
        )
        if existing_dialogue and not self.rewrite_existing_dialogue:
            return existing_dialogue

        source_text = event.get("source_text", "")
        scene_desc = event.get("scene_description", "")
        # 没有任何素材时无法生成，只能回退为空
        if not source_text and not scene_desc and not existing_dialogue:
            return []
        
        # 只传入与该事件相关的人物人设
        event_personas = self.get_personas_for_event(event)
        personas_text = self.format_personas_for_prompt(event_personas)
        original_dialogue_json = json.dumps(existing_dialogue, ensure_ascii=False, indent=2) if existing_dialogue else ""

        prompt = f"""你是一位专业的对话编辑助手。请基于以下信息，提取或生成对话：

**事件信息**：
- 事件ID：{event_id}
- 场景描述：{scene_desc}
- 原文片段：{source_text}

**角色人设信息**（参考 character_personas.json）：
{personas_text}

**已有对话（如有）**：
{original_dialogue_json if original_dialogue_json else "（无已有对话，将直接生成）"}

**要求**：
1. **语言**：对话内容与角色名必须使用**简体中文**，禁止使用英文。
2. 此处只输出**角色说的话**（直接引语），与场景描述分开：描述负责叙述/描写，本字段只负责对话
3. 如果提供了“已有对话”，请在保持核心信息与角色关系不变的前提下，将其改写为更有张力、更文学、更贴合角色语气的对话；允许适度删减/重组句子。
4. 如果“已有对话”为空，请根据事件内容和角色人设生成对话
5. **严格遵循角色人设**：
   - 参考角色的 persona_summary（人设摘要）
   - 参考角色的 worldview（世界观）
   - 参考角色的 disposition（性格倾向）
   - 参考角色的 role（角色定位）
   - 参考角色的 social_position（社会位置）
6. 保持角色语气和性格
7. 对话格式：JSON 数组，每个元素包含 "speaker"、"text"、"tone" 字段

**输出格式**（JSON）：
{{
  "dialogue": [
    {{
      "speaker": "角色名",
      "text": "对话内容",
      "tone": "语气"
    }}
  ]
}}

请生成/提取对话："""
        full_prompt = self._literary_prompt(prompt)
        try:
            client = self._get_literary_client()
            response = await client.invoke(full_prompt, return_json=True)
            raw = response.get("dialogue", [])
            return self._normalize_dialogue(raw) if raw else []
        except Exception as e:
            print(f"⚠️  生成对话失败（事件 {event_id}）：{e}")
            # 改写失败时回退到已有对话，避免把原本内容“抹掉”
            return existing_dialogue if existing_dialogue else []

    async def generate_decision_point_choice(
        self,
        event: Dict[str, Any],
        path_data: Dict[str, Any],
        all_paths_map: Dict[str, Dict[str, Any]]
    ) -> Optional[Dict[str, Any]]:
        """
        生成决策点的 player_choice 结构
        
        Args:
            event: 事件数据
            path_data: 路径数据
            all_paths_map: 所有路径的映射
            
        Returns:
            player_choice 结构，如果不是决策点则返回 None
        """
        event_id = event.get("event_id", "")
        event_type = event.get("type", "")
        
        if event_type != "decision_point":
            return None
        
        # 收集该决策点的所有选择
        all_choices = []
        choices_seen = set()
        
        for renpy_label, other_path_data in all_paths_map.items():
            other_combos = other_path_data.get("metadata", {}).get("path_combination", [])
            for combo in other_combos:
                if combo.get("fork_event_id") == event_id:
                    choice_key = (event_id, combo.get("branch_choice", ""))
                    if choice_key not in choices_seen:
                        choices_seen.add(choice_key)
                        all_choices.append({
                            "choice": combo,
                            "target_path": renpy_label
                        })
        
        if not all_choices:
            return None

        catalog = self._player_choice_catalog
        if catalog:
            base = (catalog.get("by_fork_event_id") or {}).get(event_id)
            if base and isinstance(base, dict):
                try:
                    from scripts.player_choice_catalog import attach_jump_targets

                    attached = attach_jump_targets(base, all_choices)
                    options = attached.get("options")
                    # 如果目录套用后仍缺 jump_target，说明 choice_id 与路径元数据不匹配；
                    # 此时回退到 LLM 重新生成，避免 UI 显示“缺字段”的错误格式。
                    if not isinstance(options, list) or not options:
                        # 目录结构异常：直接回退到 LLM。
                        pass
                    else:
                        missing_any_jump = any(
                            not (isinstance(opt, dict) and (opt.get("jump_target") or "").strip())
                            for opt in options
                        )
                        if not missing_any_jump:
                            return attached
                except Exception as e:
                    print(
                        f"⚠️  套用 player_choice 目录失败（事件 {event_id}），回退为 LLM：{e}"
                    )
                    # fallthrough -> LLM
        
        # 构建提示词
        scene_desc = event.get("scene_description", "")
        source_text = event.get("source_text", "")
        choices_text = json.dumps([c["choice"] for c in all_choices], ensure_ascii=False, indent=2)
        
        prompt = f"""你是一位专业的游戏脚本编写助手。请基于以下信息，生成玩家选择结构：

**事件信息**：
- 事件ID：{event_id}
- 场景描述：{scene_desc}
- 原文片段：{source_text}

**选择信息**（从 path_combination 提取）：
{choices_text}

**要求**：
1. 生成选择提示文本（基于场景描述，简洁有力，1-2句话）
2. 为每个分支选择生成选项文本（基于 description，但要更简洁，适合作为菜单选项）
3. 选项的输出顺序必须与“选择信息（从 path_combination 提取）”里的条目顺序一致
4. 每个选项的 choice_id 必须等于对应条目里的 branch_choice（优先）或 canonical_choice（否则留空）
5. 为每个选项生成 jump_target（使用 target_path，格式：{{target_path}}）
6. 选项文本应该简洁明了，不超过20字

**输出格式**（JSON）：
{{
  "prompt": "选择提示文本",
  "options": [
    {{
      "text": "选项文本",
      "choice_id": "choice_id",
      "jump_target": "target_path"
    }}
  ]
}}

请生成 player_choice 结构："""
        
        try:
            response = await self.llm_client.invoke(prompt, return_json=True)
            options_in = response.get("options", [])
            if not isinstance(options_in, list):
                options_in = []

            # 强制把 options 规整成与 all_choices 同长度，确保 jump_target/choice_id 不缺字段。
            # 这里以“条目顺序一致”为前提：即第 i 个 option 对应第 i 个 all_choices。
            forced_options: List[Dict[str, Any]] = []
            for i, ac in enumerate(all_choices):
                combo = ac.get("choice") or {}
                choice_id = (combo.get("branch_choice") or combo.get("canonical_choice") or "").strip()
                target_path = ac.get("target_path") or "__next__"

                base_opt: Dict[str, Any] = {}
                if i < len(options_in) and isinstance(options_in[i], dict):
                    base_opt = options_in[i]

                text = (base_opt.get("text") or "").strip()
                if not text:
                    # 回退：用 description 做兜底文本
                    desc = (combo.get("description") or "").strip()
                    text = desc[:20] if desc else (choice_id or "继续")

                forced_options.append(
                    {
                        "text": text,
                        "choice_id": choice_id or (base_opt.get("choice_id") or "").strip(),
                        "jump_target": target_path,
                    }
                )

            response["options"] = forced_options
            return response
        except Exception as e:
            print(f"⚠️  生成决策点选择失败（事件 {event_id}）：{e}")
            return None

    async def generate_flavor_player_choice(
        self,
        event: Dict[str, Any],
        previous_event: Optional[Dict[str, Any]],
    ) -> Optional[Dict[str, Any]]:
        """
        为非决策点事件生成「无伤大雅」的 player_choice：由 LLM 生成一句提示 + 2～3 个选项，
        无论选哪个都进入下一事件（jump_target 固定为 "__next__"），仅增加一点互动趣味。
        """
        event_type = event.get("type", "")
        if event_type in ("decision_point", "ending"):
            return None
        scene_desc = event.get("scene_description", "") or ""
        detailed = event.get("detailed_scene_description", "") or ""
        dialogue = event.get("dialogue", [])
        prev_desc = (previous_event.get("scene_description", "") or "") if previous_event else ""
        # 选项数：2～3 个
        prompt_text = f"""你是一位文字冒险游戏的脚本助手。请为「当前这一幕之后」设计一个**无伤大雅**的小选择。

**当前场景概要**：{scene_desc[:300]}
**上一幕概要**（若有）：{prev_desc[:150] if prev_desc else "无"}

**要求**：
1. **语言**：全部使用简体中文。
2. 生成一个简短的「选择提示」（1 句话，例如「你打算怎么做？」「接下来呢？」），以及 2～3 个选项。
3. 这些选项**不改变剧情**：无论玩家选哪一个，都会进入下一事件。选项可以是：不同的反应（如「继续听」「沉默」「点头」）、细微动作（如「看一眼窗外」「低头」）、或情绪色彩（如「心中一动」「默然」），让玩家有一点参与感即可。
4. 选项文案简洁，每项不超过 12 字，带一点古典/文学感，不要网络用语。

**输出格式**（严格 JSON，不要其他说明）：
{{
  "prompt": "选择提示，一句话",
  "options": [
    {{ "text": "选项一", "jump_target": "__next__" }},
    {{ "text": "选项二", "jump_target": "__next__" }}
  ]
}}

请只输出上述 JSON："""
        try:
            response = await self.llm_client.invoke(prompt_text, return_json=True)
            prompt = response.get("prompt", "接下来呢？")
            options = response.get("options", [])
            if not options:
                return None
            for opt in options:
                opt["jump_target"] = "__next__"
            return {"prompt": prompt, "options": options}
        except Exception as e:
            event_id = event.get("event_id", "")
            print(f"⚠️  生成 flavor 选择失败（事件 {event_id}）：{e}")
            return None

    async def generate_content_blocks_structure(
        self,
        detailed_scene_description: str,
        dialogue: List[Dict[str, Any]],
        flavor_choice: Optional[Dict[str, Any]] = None,
    ) -> Optional[List[Dict[str, Any]]]:
        """
        用 LLM（如 DeepSeek）判断叙述与对话在文中的穿插顺序，返回 content_blocks。
        输入：段落列表、对话列表、可选的 flavor 小选择（非决策点）。决策点的分支 player_choice 由调用方固定插在块列表最前，不传入本方法。
        """
        paragraphs = [p.strip() for p in (detailed_scene_description or "").split("\n\n") if p.strip()]
        if not paragraphs and not dialogue:
            return None
        # 构建供 LLM 参考的带索引内容
        paras_json = json.dumps([f"[{i}] {p[:200]}{'…' if len(p) > 200 else ''}" for i, p in enumerate(paragraphs)], ensure_ascii=False)
        diag_json = json.dumps(
            [{"index": i, "speaker": d.get("speaker", ""), "text": (d.get("text") or "")[:150]} for i, d in enumerate(dialogue or [])],
            ensure_ascii=False,
        )
        has_flavor = bool(flavor_choice and flavor_choice.get("options"))
        prompt = f"""你是一位文字冒险脚本的结构助手。请根据「叙述段落」和「对话」的内容，决定它们在剧本中的**穿插顺序**，使阅读节奏自然（对话出现在最合适的叙述间隙）。

**叙述段落**（按段，索引从 0 开始）：
{paras_json}

**对话**（按条，索引从 0 开始）：
{diag_json}
"""
        if has_flavor:
            prompt += """
**可选**：在本段中最多插入**一处**无伤大雅的小选择（prompt + 2～3 个选项，选后都进入下一事件）。若某段叙述后适合让玩家「稍作选择再继续」，在对应位置插入一项 "choice"；否则不插。
"""
        prompt += """
**要求**：输出一个 JSON 数组，表示播放顺序。每项为以下之一：
- {{ "type": "narrative", "paragraph_index": 0 }}  （使用上面段落列表中该索引的**整段**原文）
- {{ "type": "dialogue", "dialogue_index": 0 }}  （使用上面对话列表中该索引的一条）
- {{ "type": "choice" }}  （仅当允许插入选择时使用，最多一项）

必须用完所有 paragraph_index 和 dialogue_index 各恰好一次；顺序由你决定。只输出 JSON 数组，不要其他说明。"""

        try:
            response = await self.llm_client.invoke(prompt, return_json=True)
            if isinstance(response, list):
                raw = response
            elif isinstance(response, dict):
                raw = response.get("content_blocks") or response.get("order") or response.get("result")
                if not isinstance(raw, list):
                    raw = []
            else:
                raw = []
            if not raw:
                return None
            # 按 LLM 返回的顺序与索引，拼成 content_blocks（使用原文，不信任 LLM 抄写）
            blocks: List[Dict[str, Any]] = []
            used_para = set()
            used_diag = set()
            choice_inserted = False
            for item in raw:
                if not isinstance(item, dict):
                    continue
                t = (item.get("type") or "").strip().lower()
                if t == "narrative":
                    idx = item.get("paragraph_index", 0)
                    if 0 <= idx < len(paragraphs) and idx not in used_para:
                        used_para.add(idx)
                        blocks.append({"type": "narrative", "text": paragraphs[idx]})
                elif t == "dialogue":
                    idx = item.get("dialogue_index", 0)
                    if 0 <= idx < len(dialogue) and idx not in used_diag:
                        used_diag.add(idx)
                        d = dialogue[idx]
                        blocks.append({
                            "type": "dialogue",
                            "speaker": d.get("speaker", ""),
                            "text": d.get("text", ""),
                        })
                elif t == "choice" and has_flavor and not choice_inserted:
                    choice_inserted = True
                    blocks.append({
                        "type": "choice",
                        "prompt": flavor_choice.get("prompt", "接下来呢？"),
                        "options": flavor_choice.get("options", []),
                    })
            # 若有未使用的段落或对话，按顺序补在末尾
            for i in range(len(paragraphs)):
                if i not in used_para:
                    blocks.append({"type": "narrative", "text": paragraphs[i]})
            for i in range(len(dialogue)):
                if i not in used_diag:
                    d = dialogue[i]
                    blocks.append({"type": "dialogue", "speaker": d.get("speaker", ""), "text": d.get("text", "")})
            if has_flavor and not choice_inserted:
                blocks.append({
                    "type": "choice",
                    "prompt": flavor_choice.get("prompt", "接下来呢？"),
                    "options": flavor_choice.get("options", []),
                })
            return blocks if blocks else None
        except Exception as e:
            print(f"⚠️  生成 content_blocks 穿插顺序失败：{e}")
            return None

    async def generate_ending_scene(
        self,
        ending: Dict[str, Any],
        narrative_perspective: str = "第三人称",
    ) -> Dict[str, Any]:
        """
        生成详细的结局场景
        
        Args:
            ending: 结局数据
            narrative_perspective: 叙述人称，与场景描述一致
            
        Returns:
            增强的结局数据（包含详细的场景描述和对话）
        """
        ending_id = ending.get("id", "")
        scene_desc = ending.get("scene_description", "")
        ending_reason = ending.get("ending_reason", "")
        emotional_tone = ending.get("emotional_tone", "")
        themes = ending.get("themes", [])
        
        # 构建提示词
        prompt = f"""你是一位专业的文学创作助手。请基于以下信息，生成详细的结局场景描述：

**结局信息**：
- 结局ID：{ending_id}
- 当前场景描述：{scene_desc}
- 结局原因：{ending_reason}
- 情感基调：{emotional_tone}
- 主题：{', '.join(themes)}

**要求**：
1. **语言**：全文必须使用**简体中文**书写，禁止使用英文。
2. 基于当前场景描述扩展，保留文学风格
3. 结合结局原因中的状态信息
4. 体现结局的情感基调
5. 呼应主题
6. 长度：300-600字
7. **叙述人称**：{self._narrative_perspective_instruction(narrative_perspective)}
8. 只输出场景描述文本，不要添加任何解释或格式标记

请生成详细的结局场景描述："""
        full_prompt = self._literary_prompt(prompt)
        try:
            client = self._get_literary_client()
            response = await client.invoke(full_prompt, return_json=False)
            description = response.strip()
            if description.startswith('"') and description.endswith('"'):
                description = description[1:-1]
            enhanced_ending = ending.copy()
            enhanced_ending["detailed_scene_description"] = description
            return enhanced_ending
        except Exception as e:
            print(f"⚠️  生成结局场景失败（结局 {ending_id}）：{e}")
            # 失败时返回原始结局
            return ending
    
    @staticmethod
    def _should_add_flavor(event_index: int, event_type: str, every_n: int = 4) -> bool:
        """仅在「合适」的时机加 flavor：少而精，非首事件且每隔 every_n 个事件。"""
        if event_type in ("decision_point", "ending"):
            return False
        if event_index <= 0:
            return False
        return (event_index + 1) % every_n == 0

    async def _enhance_event(
        self,
        event: Dict[str, Any],
        path_data: Dict[str, Any],
        all_paths_map: Dict[str, Dict[str, Any]],
        previous_event: Optional[Dict[str, Any]],
        narrative_perspective: str = "第三人称",
        event_index: int = 0,
    ) -> Dict[str, Any]:
        """对单个事件做内容增强，返回带 detailed_scene_description / dialogue / player_choice 的事件 dict。"""
        event_type = event.get("type", "")
        out = dict(event)
        path_id = (path_data.get("path_id") or "").strip()
        renpy_label = (path_data.get("renpy_label") or "").strip()
        is_canonical_path = (path_id == "canonical_path" or renpy_label == "canonical_path")
        out["detailed_scene_description"] = await self.generate_scene_description(
            event, previous_event, narrative_perspective=narrative_perspective
        )
        dialogue = await self.generate_dialogue(event)
        if dialogue:
            out["dialogue"] = dialogue
        if event_type == "decision_point":
            player_choice = await self.generate_decision_point_choice(
                event, path_data, all_paths_map
            )
            if player_choice:
                out["player_choice"] = player_choice
        elif self._should_add_flavor(event_index, event_type):
            flavor_choice = await self.generate_flavor_player_choice(event, previous_event)
            if flavor_choice:
                out["player_choice"] = flavor_choice
        # 用 LLM 判断叙述与对话（及可选 choice）的穿插顺序，生成 content_blocks。
        # 默认策略：decision_point 的主分支 player_choice 固定放在最前（先选分支再播本幕叙述/对白）。
        # 但 canonical_path 例外：允许 choice 参与排序（menu 可夹在叙述/对话中间），便于主线体验更自然。
        if out.get("detailed_scene_description") or out.get("dialogue"):
            # 统一策略：decision_point 也让主分支 player_choice 参与排序（choice 可插在叙述/对话中间）。
            # 这样前端/脚本生成只要“按 content_blocks 输出”，menu 的位置就会跟随 blocks。
            ordering_choice = out.get("player_choice")
            content_blocks = await self.generate_content_blocks_structure(
                out.get("detailed_scene_description") or "",
                out.get("dialogue") or [],
                ordering_choice,
            )
            pc = out.get("player_choice")
            if event_type == "decision_point" and pc:
                if content_blocks:
                    out["content_blocks"] = content_blocks
                else:
                    # 兜底：若排序器没产出 blocks，则退回“choice + 叙述/对话”拼装
                    choice_block = {
                        "type": "choice",
                        "prompt": pc.get("prompt", "你打算怎么做？"),
                        "options": pc.get("options", []),
                    }
                    blocks = [choice_block]
                    if out.get("detailed_scene_description"):
                        blocks.append(
                            {"type": "narrative", "text": out["detailed_scene_description"]}
                        )
                    for d in out.get("dialogue") or []:
                        blocks.append(
                            {
                                "type": "dialogue",
                                "speaker": d.get("speaker", ""),
                                "text": d.get("text", ""),
                            }
                        )
                    out["content_blocks"] = blocks
            elif content_blocks:
                out["content_blocks"] = content_blocks
        elif event_type == "decision_point" and out.get("player_choice"):
            pc = out["player_choice"]
            out["content_blocks"] = [
                {
                    "type": "choice",
                    "prompt": pc.get("prompt", "你打算怎么做？"),
                    "options": pc.get("options", []),
                }
            ]
        return out

    def _copy_enhanced_from_canonical(
        self, event: Dict[str, Any], canonical_event: Dict[str, Any]
    ) -> Dict[str, Any]:
        """从主线事件复制增强字段到当前事件（复用，不调 LLM）。"""
        out = dict(event)
        if canonical_event.get("detailed_scene_description") is not None:
            out["detailed_scene_description"] = canonical_event["detailed_scene_description"]
        if canonical_event.get("dialogue"):
            out["dialogue"] = canonical_event["dialogue"]
        if canonical_event.get("player_choice") is not None:
            out["player_choice"] = canonical_event["player_choice"]
        if canonical_event.get("content_blocks"):
            out["content_blocks"] = canonical_event["content_blocks"]
        return out

    @staticmethod
    def _is_canonical_choice_for_fork(
        *,
        path_combination: List[Dict[str, Any]],
        fork_event_id: str,
    ) -> bool:
        """判断当前路径在某个 fork_event_id 上是否选择了主线（is_canonical=True）。"""
        fid = (fork_event_id or "").strip()
        if not fid:
            return False
        for c in path_combination or []:
            if not isinstance(c, dict):
                continue
            if (c.get("fork_event_id") or "").strip() != fid:
                continue
            # 该 fork 可能同时存在 canonical 与 branch 两条记录；只要存在 canonical 选择就认为走主线
            if bool(c.get("is_canonical", False)):
                return True
        return False

    def _copy_decision_point_body_from_canonical(
        self,
        event: Dict[str, Any],
        canonical_event: Dict[str, Any],
    ) -> Dict[str, Any]:
        """
        仅复用主线 decision_point 的“正文内容”（叙述/对话/原文片段等），不覆盖 player_choice。

        目的：
        - decision_point 若走主线选项，其事件正文在所有路径里应一致；
        - 但 player_choice 的 jump_target 仍需按当前路径组合补齐，因此不能直接照搬 canonical 的 player_choice。
        """
        out = dict(event)
        for k in ("scene_description", "source_text", "detailed_scene_description"):
            if canonical_event.get(k) is not None:
                out[k] = canonical_event.get(k)
        if canonical_event.get("dialogue") is not None:
            out["dialogue"] = canonical_event.get("dialogue")
        # 不复制 player_choice / content_blocks（避免把 canonical 的选项结构覆盖到当前路径）
        return out

    async def generate_path_content(
        self,
        path_data: Dict[str, Any],
        all_paths_map: Dict[str, Dict[str, Any]],
        canonical_enhanced_path_data: Optional[Dict[str, Any]] = None,
        narrative_perspective: str = "第三人称",
        segment_cache: Optional[Dict[Tuple[Tuple[str, str], ...], Dict[str, Any]]] = None,
    ) -> Dict[str, Any]:
        """
        为整个路径生成详细的脚本内容。
        - 若提供 canonical_enhanced_path_data 且当前路径非主线：type=="mainline_event" 从主线同 event_id 复用。
        - 若提供 segment_cache：与 LangGraph 类似，在当前选择与分支决策相同的前缀下复用已生成的事件段
          （按 path_combination 前缀分段的 segment 缓存，key=(fork_event_id, branch_choice)* 前缀长度）。
        """
        path_id = path_data.get("path_id", "")
        renpy_label = path_data.get("renpy_label", "")
        raw_events = path_data.get("events", [])
        events = (
            _events_through_first_ending(list(raw_events))
            if isinstance(raw_events, list)
            else []
        )
        if isinstance(raw_events, list) and len(events) < len(raw_events):
            print(
                f"   ⚠️ events 在首个 ending 之后还有 {len(raw_events) - len(events)} 条，已截断（不增强、不写入输出）"
            )
        ending = path_data.get("ending")
        is_canonical = (path_id == "canonical_path" or renpy_label == "canonical_path")
        path_combination = (path_data.get("metadata") or {}).get("path_combination") or path_data.get("path_combination") or []

        canonical_by_id: Dict[str, Dict[str, Any]] = {}
        if not is_canonical and canonical_enhanced_path_data:
            canonical_by_id = _canonical_events_by_id(canonical_enhanced_path_data.get("events") or [])
            n_reuse = sum(1 for ev in events if ev.get("type") == "mainline_event" and ev.get("event_id") in canonical_by_id)
            if n_reuse:
                print(f"[content] 开始生成路径内容：{path_id}（其中 {n_reuse} 个 mainline_event 复用主线）")
            else:
                print(f"[content] 开始生成路径内容：{path_id}")
        else:
            print(f"[content] 开始生成路径内容：{path_id}")
        print(f"   事件数量：{len(events)}（叙述人称约束：{narrative_perspective}）")

        # 分段缓存：相同 path_combination 前缀可复用已生成的事件段（与 LangGraph 中“当前选择+分支决策一致则复用”一致）
        use_segment_cache = segment_cache is not None and path_combination and not is_canonical
        if use_segment_cache:
            segments = _split_events_into_segments(events)
            enhanced_events = []
            for seg_idx, segment_events in enumerate(segments):
                key = _path_combination_prefix_key(path_combination, seg_idx)
                if seg_idx == 0:
                    base = 0
                    async def process_one(global_i: int, ev: Dict[str, Any]) -> Dict[str, Any]:
                        prev = events[global_i - 1] if global_i > 0 else None
                        if ev.get("type") == "mainline_event" and ev.get("event_id") in canonical_by_id:
                            return self._copy_enhanced_from_canonical(ev, canonical_by_id[ev["event_id"]])
                        # decision_point：若该 fork 在当前路径上选择主线，则复用主线生成的正文内容
                        if (
                            ev.get("type") == "decision_point"
                            and ev.get("event_id") in canonical_by_id
                            and self._is_canonical_choice_for_fork(
                                path_combination=path_combination,
                                fork_event_id=str(ev.get("event_id") or ""),
                            )
                        ):
                            return self._copy_decision_point_body_from_canonical(
                                ev, canonical_by_id[ev["event_id"]]
                            )
                        return await self._enhance_event(
                            ev, path_data, all_paths_map, prev,
                            narrative_perspective=narrative_perspective,
                            event_index=global_i,
                        )
                    seg_enhanced = list(await asyncio.gather(*[
                        process_one(base + j, ev) for j, ev in enumerate(segment_events)
                    ]))
                    enhanced_events.extend(seg_enhanced)
                else:
                    seg_event_ids = tuple(e.get("event_id", "") for e in segment_events)
                    cached = segment_cache.get(key)
                    if cached and cached.get("event_ids") == seg_event_ids:
                        import copy
                        enhanced_events.extend(copy.deepcopy(cached["events"]))
                        print(f"   [content] 段 {seg_idx} 复用缓存 key={key}（{len(segment_events)} 事件）")
                    else:
                        base = sum(len(segments[k]) for k in range(seg_idx))
                        async def enhance_one(global_i: int, ev: Dict[str, Any]) -> Dict[str, Any]:
                            prev = events[global_i - 1] if global_i > 0 else None
                            return await self._enhance_event(
                                ev, path_data, all_paths_map, prev,
                                narrative_perspective=narrative_perspective,
                                event_index=global_i,
                            )
                        seg_enhanced = list(await asyncio.gather(*[
                            enhance_one(base + j, ev) for j, ev in enumerate(segment_events)
                        ]))
                        enhanced_events.extend(seg_enhanced)
                        segment_cache[key] = {"event_ids": seg_event_ids, "events": seg_enhanced}
            n_reused = sum(1 for ev in events if ev.get("type") == "mainline_event" and ev.get("event_id") in canonical_by_id)
            n_enhanced = len(events) - n_reused
            print(f"   [content] 路径内 {len(events)} 个事件已处理（复用主线 {n_reused}，分段复用/增强 {n_enhanced}）")
        else:
            # 原有逻辑：逐事件并行，主线复用 + 其余增强
            async def process_one_event(i: int, event: Dict[str, Any]) -> Dict[str, Any]:
                event_id = event.get("event_id", "")
                event_type = event.get("type", "")
                previous_event = events[i - 1] if i > 0 else None
                if event_type == "mainline_event" and event_id in canonical_by_id:
                    return self._copy_enhanced_from_canonical(event, canonical_by_id[event_id])
                # decision_point：若该 fork 在当前路径上选择主线，则复用主线生成的正文内容
                if (
                    event_type == "decision_point"
                    and event_id in canonical_by_id
                    and self._is_canonical_choice_for_fork(
                        path_combination=path_combination,
                        fork_event_id=str(event_id or ""),
                    )
                ):
                    return self._copy_decision_point_body_from_canonical(
                        event, canonical_by_id[event_id]
                    )
                return await self._enhance_event(
                    event, path_data, all_paths_map, previous_event,
                    narrative_perspective=narrative_perspective,
                    event_index=i,
                )

            tasks = [process_one_event(i, ev) for i, ev in enumerate(events)]
            enhanced_events = list(await asyncio.gather(*tasks))
            n_reused = sum(1 for i, ev in enumerate(events) if ev.get("type") == "mainline_event" and ev.get("event_id") in canonical_by_id)
            n_enhanced = len(events) - n_reused
            print(f"   [content] 路径内 {len(events)} 个事件已处理（复用主线 {n_reused}，并行增强 {n_enhanced}）")

        enhanced_ending: Optional[Dict[str, Any]] = None
        if ending:
            print(f"   [content] 处理结局：{ending.get('id', 'unknown')}")
            enhanced_ending = await self.generate_ending_scene(ending, narrative_perspective=narrative_perspective)

        result = build_path_content_result(path_data, enhanced_events, enhanced_ending)
        print(f"[content] 路径内容生成完成：{path_id}\n")
        return result
    
    async def generate_all_paths_content(
        self,
        all_paths_data: List[Dict[str, Any]],
        canonical_enhanced_path_data: Optional[Dict[str, Any]] = None,
        narrative_perspective: Optional[str] = None,
        max_concurrent: Optional[int] = None,
    ) -> List[Dict[str, Any]]:
        """
        批量生成所有路径的详细内容（异步并发）。
        若传入 canonical_enhanced_path_data，非主线路径会按 event_id 前缀复用主线的增强内容。
        narrative_perspective：若调用方已在「生成分支事件之前」推断好人称，可传入；否则此处会从主线内容推断一次，供所有路径共用。
        max_concurrent：同时运行的最大路径数，None 表示不限制（全部并发）。
        会使用 segment_cache 在「当前选择与分支决策相同」的前缀下复用已生成的事件段（类似 LangGraph 的复用）。
        
        Returns:
            List[PathContentResult]：每条路径的增强结果，可直接用于 PathScriptGenerator。
        """
        all_paths_map = {
            p["renpy_label"]: p
            for p in all_paths_data
            if p.get("renpy_label")
        }
        print(f"[content] 开始批量生成路径内容，共 {len(all_paths_data)} 条路径\n")
        # 人称：优先使用调用方传入的；否则根据主线（或第一条路径）内容推断一次，作为所有路径的叙述约束
        if narrative_perspective is None or not narrative_perspective.strip():
            canonical_path = canonical_enhanced_path_data or next(
                (p for p in all_paths_data if p.get("path_id") == "canonical_path" or p.get("renpy_label") == "canonical_path"),
                all_paths_data[0] if all_paths_data else None,
            )
            if canonical_path and (canonical_path.get("events") or []):
                narrative_perspective = await self.infer_narrative_perspective(
                    canonical_path.get("events", []),
                    ending_sample=(canonical_path.get("ending") or {}).get("scene_description") or (canonical_path.get("ending") or {}).get("description", ""),
                )
                print(f"[content] 根据主线内容推断叙述人称：{narrative_perspective}\n")
            else:
                narrative_perspective = "第三人称"
        else:
            print(f"[content] 使用传入的叙述人称约束：{narrative_perspective}\n")
        sem = asyncio.Semaphore(max_concurrent) if (max_concurrent is not None and max_concurrent > 0) else None
        if sem:
            print(f"[dim]并发上限：{max_concurrent} 条路径[/dim]\n")
        segment_cache: Dict[Tuple[Tuple[str, str], ...], Dict[str, Any]] = {}
        async def run_one(p: Dict[str, Any]):
            if sem:
                async with sem:
                    return await self.generate_path_content(
                        p, all_paths_map, canonical_enhanced_path_data,
                        narrative_perspective=narrative_perspective,
                        segment_cache=segment_cache,
                    )
            return await self.generate_path_content(
                p, all_paths_map, canonical_enhanced_path_data,
                narrative_perspective=narrative_perspective,
                segment_cache=segment_cache,
            )
        results = await asyncio.gather(*[run_one(p) for p in all_paths_data])
        print(f"[content] 所有路径内容生成完成！共 {len(results)} 条路径\n")
        return list(results)
