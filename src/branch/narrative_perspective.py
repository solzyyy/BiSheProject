"""
叙述人称推断与约束 🌿📝

在 generate-branch 之前根据主线内容推断叙述人称（第一/二/三人称），
供后续支线生成、路径生成与内容生成共用，保证全文人称一致。
"""

from typing import Dict, List, Any, Optional

from core.llm_client import AsyncLLMClient


def get_narrative_perspective_instruction(perspective: str) -> str:
    """根据人称返回提示词中的叙述要求，与 path_generation_functions、content_generator 共用。"""
    if perspective == "第二人称":
        return "使用第二人称叙述，用「你」指代玩家/主角。"
    if perspective == "第一人称":
        return "使用第一人称叙述，用「我」指代主角。"
    return (
        "使用第三人称叙述。用角色名（如「林」「王佛」）或「他/她」指代人物，"
        "禁止使用「你」指代角色；叙述为旁白视角，不要对读者说话。"
    )


async def infer_narrative_perspective_from_events(
    events: List[Dict[str, Any]],
    ending_sample: Optional[str] = None,
    llm_client: Optional[Any] = None,
) -> str:
    """
    根据事件内容用 LLM 推断叙述人称（第一人称/第二人称/第三人称）。
    在 generate-branch 之前调用（CLI 或管道入口），结果应写入与本次产物同目录的
    content_context.json（与 branches.json 同目录，例如 runs/.../out/），供后续步骤读取；
    旧版 CLI 曾写入项目根下 out/content_context.json，仍可作为回退路径。

    Args:
        events: 事件列表（如 canonical_branch["processed_events"]），取前几条的 description / scene_description
        ending_sample: 可选的结局片段文本
        llm_client: 可选 LLM 客户端；若不传则使用默认 Gemini 文学模型

    Returns:
        "第一人称" | "第二人称" | "第三人称"
    """
    samples: List[str] = []
    for ev in (events or [])[:5]:
        desc = ev.get("description") or ev.get("scene_description") or ev.get("source_text", "")
        if isinstance(desc, str) and desc.strip():
            samples.append(desc.strip()[:400])
    if ending_sample and isinstance(ending_sample, str) and ending_sample.strip():
        samples.append(ending_sample.strip()[:400])
    if not samples:
        return "第三人称"
    text = "\n\n---\n\n".join(samples)
    prompt = f"""根据以下故事片段，判断本故事应采用何种叙述人称。

**片段**：
{text}

**说明**：
- 第一人称：叙述者即主角，用「我」。
- 第二人称：对玩家/主角直接说话，用「你」。
- 第三人称：旁白视角，用角色名或「他/她」，不用「你」指代角色。

请只回答一行，格式：人称：第一人称/第二人称/第三人称
若片段中已大量使用「你」且明显是文游对玩家的称呼，可答第二人称；若为传统小说式旁白或角色名/他她，答第三人称。"""
    try:
        client = llm_client
        if client is None:
            # 人称推断用 DeepSeek；仅 enhanced_paths 内容增强（ContentGenerator）用 Gemini
            client = AsyncLLMClient.create_default()
        response = await client.invoke(prompt, return_json=False)
        line = (response or "").strip().split("\n")[0]
        for p in ("第一人称", "第二人称", "第三人称"):
            if p in line:
                return p
    except Exception:
        pass
    return "第三人称"
