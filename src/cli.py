"""
统一命令行入口 🚀✨

提供统一的 CLI 接口来运行所有功能，避免多个 main 函数。

使用方式：
    # 事件提取
    python -m src.cli extract-events          # 从文本提取事件
    
    # 关系提取
    python -m src.cli extract-relations       # 从事件中提取关系
    
    # 导入到 Neo4j（两种模式）
    python -m src.cli import-neo4j           # 默认：合并 extract_chain.json 和 extract_relations.json 后导入
    python -m src.cli import-neo4j --json out/extract_merged.json  # 使用已合并的文件直接导入
    python -m src.cli import-neo4j --clear   # 导入前清空数据库
    
    # 初始化索引
    python -m src.cli init-indexes           # 初始化 Neo4j 索引和约束
    
    # Mention 提取
    python -m src.cli generate-mentions      # 生成 Mention 表
"""

import sys
from pathlib import Path
from typing import Any, Optional

# 添加 src 目录到 Python 路径
sys.path.insert(0, str(Path(__file__).parent))

import typer
from rich import print

app = typer.Typer(help="文字冒险游戏内容生成系统 CLI")


def _resolve_under_project_root(project_root: Path, path_str: str) -> Path:
    """将 CLI 传入的路径解析为绝对路径（相对路径相对项目根）。"""
    p = Path(path_str)
    if not p.is_absolute():
        p = project_root / path_str
    return p


def _load_narrative_perspective_from_content_context(*candidate_paths: Path) -> Optional[str]:
    import json

    for fp in candidate_paths:
        try:
            if fp.is_file():
                with open(fp, "r", encoding="utf-8") as f:
                    data = json.load(f)
                np = data.get("narrative_perspective")
                if np:
                    return str(np)
        except Exception:
            continue
    return None


def _ensure_neo4j_login() -> None:
    """导入前自动准备 Neo4j 连接信息并验证连通性（失败时尝试启动 neo4j console）。"""
    try:
        from core.neo4j_bootstrap import ensure_neo4j_ready

        ensure_neo4j_ready(auto_start=True)
    except Exception as exc:
        print(f"[red]{exc}[/red]")
        print(
            "[yellow]请手动确认 Neo4j 已启动，或设置环境变量 NEO4J_START_COMMAND 后重试。[/yellow]"
        )
        raise typer.Exit(code=1)


@app.command("extract-events")
def extract_events_cmd(
    input_path: str = typer.Option("王佛脱险记.txt", "--input", "-i", help="输入文本文件路径"),
    chunk_size: int = typer.Option(800, "--chunk-size", help="文本分块长度（字符）"),
    chunk_overlap: int = typer.Option(160, "--chunk-overlap", help="分块重叠长度（字符）"),
    max_chunks: Optional[int] = typer.Option(None, "--max-chunks", help="最多处理多少个文本分块（用于小样本实验）"),
) -> None:
    """提取事件 📝"""
    from event_graph.scripts.extract_events import main as extract_main
    import os
    os.environ["INPUT_TEXT_PATH"] = input_path
    os.environ["EXTRACT_CHUNK_SIZE"] = str(chunk_size)
    os.environ["EXTRACT_CHUNK_OVERLAP"] = str(chunk_overlap)
    if max_chunks is None:
        os.environ.pop("EXTRACT_MAX_CHUNKS", None)
    else:
        os.environ["EXTRACT_MAX_CHUNKS"] = str(max_chunks)
    extract_main()


@app.command("extract-relations")
def extract_relations_cmd(
    json_path: str = typer.Option("out/extract_chain.json", "--json", "-j", help="包含事件的 JSON 文件路径"),
    output_path: Optional[str] = typer.Option(
        None,
        "--output",
        "-o",
        help="关系 JSON 输出路径，默认 out/extract_relations.json",
    ),
) -> None:
    """从事件中提取关系 🔗"""
    from event_graph.scripts.extract_relations import main as extract_relations_main
    import os

    os.environ["EXTRACT_CHAIN_JSON"] = json_path
    if output_path:
        os.environ["EXTRACT_RELATIONS_OUTPUT"] = output_path
    else:
        os.environ.pop("EXTRACT_RELATIONS_OUTPUT", None)
    extract_relations_main()


@app.command("extract-events-and-relations")
def extract_events_and_relations_cmd(
    input_path: str = typer.Option(
        "王佛脱险记.txt",
        "--input",
        "-i",
        help="输入 txt / epub（与 extract-events 相同）",
    ),
    output_dir: Optional[str] = typer.Option(
        None,
        "--output-dir",
        "-d",
        help="输出目录（其下生成 extract_chain.json 与 extract_relations.json），默认 out",
    ),
    chunk_size: int = typer.Option(800, "--chunk-size", help="文本分块长度"),
    chunk_overlap: int = typer.Option(160, "--chunk-overlap", help="分块重叠长度"),
    max_chunks: Optional[int] = typer.Option(
        None, "--max-chunks", help="最多处理多少个分块（调试用）"
    ),
) -> None:
    """依次执行事件抽取与关系抽取（同一 output_dir）。"""
    root = Path(__file__).resolve().parents[1]
    if str(root) not in sys.path:
        sys.path.insert(0, str(root))
    from scripts.ui.utils import run_extract_events_and_relations_for_ui

    params = {
        "input": input_path,
        "output_dir": (output_dir or "out").strip(),
        "chunk_size": chunk_size,
        "chunk_overlap": chunk_overlap,
        "max_chunks": max_chunks,
    }
    code = run_extract_events_and_relations_for_ui(root, params)
    raise SystemExit(code)


@app.command("import-neo4j")
def import_neo4j_cmd(
    json_path: str = typer.Option(None, "--json", "-j", help="JSON 文件路径（如果指定，直接使用该文件导入，跳过合并）"),
    clear: bool = typer.Option(False, "--clear", "-c", help="是否在导入前清空数据库"),
) -> None:
    """
    导入数据到 Neo4j 知识图谱 🗄️
    
    两种模式：
    1. 默认模式：合并 extract_chain.json 和 extract_relations.json，然后导入
    2. 指定文件模式：使用 --json 指定已合并的文件，直接导入
    """
    from pathlib import Path
    import json
    
    def _json_has_relations(p: Path) -> bool:
        try:
            data = json.loads(p.read_text(encoding="utf-8"))
        except Exception:
            return False
        rel = data.get("relations", [])
        return isinstance(rel, list) and len(rel) > 0

    chain_path_guess: Path | None = None
    relations_path_guess: Path | None = None

    # 如果指定了 json_path：可能是 extract_merged.json，也可能误填了 extract_chain.json
    if json_path:
        print(f"[cyan]使用指定文件导入: {json_path}[/cyan]")
        import_path = Path(json_path)
        if not import_path.exists():
            print(f"[red]错误：找不到文件 {import_path}[/red]")
            return

        if _json_has_relations(import_path):
            print("[green]检测到 merged JSON 已包含 relations，直接导入。[/green]")
        else:
            # 推断同目录下的 extract_chain / extract_relations，然后显式合并
            base_dir = import_path.parent
            chain_path_guess = base_dir / "extract_chain.json"
            relations_path_guess = base_dir / "extract_relations.json"
            merged_out = base_dir / "extract_merged.json"

            if not chain_path_guess.exists():
                chain_path_guess = import_path  # 兜底：如果用户传的是 chain 且名称不一致
            if not relations_path_guess.exists():
                print(
                    f"[red]错误：缺少关系文件 {relations_path_guess}（无法建立事件关系）[/red]"
                )
                return

            print("[cyan]检测到 json 无 relations，自动合并 extract_chain + extract_relations…[/cyan]")
            from event_graph.scripts.merge_and_import import merge_data

            merge_data(
                chain_path=chain_path_guess,
                relations_path=relations_path_guess,
                output_path=merged_out,
            )
            import_path = merged_out
            print(f"[green]合并完成，准备导入: {import_path}[/green]")
    else:
        # 默认模式：先合并再导入（这里沿用 merge_and_import 的默认相对 out/ 约定）
        print("[cyan]正在合并数据...[/cyan]")
        from event_graph.scripts.merge_and_import import merge_data
        import_path = merge_data()
        print(f"[green]合并完成，准备导入: {import_path}[/green]")

    _ensure_neo4j_login()

    # 导入到 Neo4j
    from event_graph.scripts.import_to_neo4j import import_to_neo4j
    import_to_neo4j(import_path, clear_first=clear)


@app.command("clear-db")
def clear_db_cmd() -> None:
    """清空 Neo4j 数据库中的所有数据 🗑️"""
    from core.neo4j_client import Neo4jClient
    from rich import print
    
    print("[yellow]⚠️  警告：这将删除所有节点和关系！[/yellow]")
    client = Neo4jClient()
    try:
        client.clear_all_data()
        print("[green]✅ 数据库已清空！[/green]")
    finally:
        client.close()


@app.command("drop-indexes")
def drop_indexes_cmd() -> None:
    """删除 Neo4j 数据库中的所有索引和约束 🗑️"""
    from core.neo4j_client import Neo4jClient
    from rich import print
    
    print("[yellow]⚠️  警告：这将删除所有索引和约束！[/yellow]")
    client = Neo4jClient()
    try:
        client.drop_all_indexes_and_constraints()
    finally:
        client.close()


@app.command("init-indexes")
def init_indexes_cmd() -> None:
    """初始化 Neo4j 数据库索引和约束 🔧"""
    from event_graph.scripts.init_indexes import main as init_main
    init_main()


@app.command("generate-mentions")
def generate_mentions_cmd(
    input_path: str = typer.Option(None, "--input", "-i", help="输入 JSON 文件路径（默认：out/extract_chain.json）"),
    output_path: str = typer.Option(None, "--output", "-o", help="输出 JSON 文件路径（默认：out/mentions.json）"),
) -> None:
    """生成 Mention 表 👤✨"""
    import asyncio
    import sys
    from pathlib import Path
    from character.scripts.generate_mentions import main_async
    
    # 设置命令行参数（如果提供了路径）
    original_argv = sys.argv.copy()
    try:
        if input_path and output_path:
            sys.argv = ["generate_mentions", input_path, output_path]
        elif input_path:
            sys.argv = ["generate_mentions", input_path]
        elif output_path:
            sys.argv = ["generate_mentions", "out/extract_chain.json", output_path]
        else:
            sys.argv = ["generate_mentions"]
        
        asyncio.run(main_async())
    finally:
        sys.argv = original_argv


@app.command("generate-mentions-and-entities")
def generate_mentions_and_entities_cmd(
    input_path: Optional[str] = typer.Option(
        None,
        "--input",
        "-i",
        help="事件链 JSON（默认：<output_dir>/extract_chain.json）",
    ),
    output_dir: Optional[str] = typer.Option(
        None,
        "--output-dir",
        "-d",
        help="产出目录（写入 mentions.json 与 entities.json），默认 out",
    ),
) -> None:
    """依次执行 Mention 抽取与实体别名生成（同一 output_dir）。"""
    root = Path(__file__).resolve().parents[1]
    if str(root) not in sys.path:
        sys.path.insert(0, str(root))
    from scripts.ui.utils import run_generate_mentions_and_entities_for_ui

    params: dict[str, Any] = {
        "input": input_path,
        "output_dir": (output_dir or "out").strip(),
    }
    code = run_generate_mentions_and_entities_for_ui(root, params)
    raise SystemExit(code)


@app.command("generate-entities")
def generate_entities_cmd(
    input_path: str = typer.Option(None, "--input", "-i", help="输入 JSON 文件路径（默认：out/mentions.json）"),
    output_path: str = typer.Option(None, "--output", "-o", help="输出 JSON 文件路径（默认：out/entities.json）"),
) -> None:
    """生成实体表（别名映射）🌸✨"""
    import asyncio
    import sys
    from pathlib import Path
    from character.scripts.generate_entities import main_async
    
    # 设置命令行参数（如果提供了路径）
    original_argv = sys.argv.copy()
    try:
        if input_path and output_path:
            sys.argv = ["generate_entities", input_path, output_path]
        elif input_path:
            sys.argv = ["generate_entities", input_path]
        elif output_path:
            sys.argv = ["generate_entities", "out/mentions.json", output_path]
        else:
            sys.argv = ["generate_entities"]
        
        asyncio.run(main_async())
    finally:
        sys.argv = original_argv


@app.command("init-characters")
def init_characters_cmd(
    entities_path: str = typer.Option(None, "--entities", "-e", help="实体 JSON 文件路径（默认：out/entities.json）"),
    mentions_path: str = typer.Option(None, "--mentions", "-m", help="Mention JSON 文件路径（默认：out/mentions.json）"),
    events_path: str = typer.Option(None, "--events", help="事件 JSON 文件路径（默认：out/extract_chain.json）"),
    output_path: str = typer.Option(None, "--output", "-o", help="输出 JSON 文件路径（默认：out/characters.json）"),
) -> None:
    """初始化人物节点 🎭✨"""
    import asyncio
    import sys
    from character.scripts.init_characters import main_async
    
    # 设置命令行参数（如果提供了路径）
    original_argv = sys.argv.copy()
    try:
        args = []
        if entities_path:
            args.append(entities_path)
        if mentions_path:
            args.append(mentions_path)
        if events_path:
            args.append(events_path)
        if output_path:
            args.append(output_path)
        
        if args:
            sys.argv = ["init_characters"] + args
        else:
            sys.argv = ["init_characters"]
        
        asyncio.run(main_async())
    finally:
        sys.argv = original_argv


@app.command("extract-world-states")
def extract_world_states_cmd(
    events_path: str = typer.Option(None, "--events", "-e", help="事件 JSON 文件路径（默认：out/extract_chain.json）"),
    output_path: str = typer.Option(None, "--output", "-o", help="输出 JSON 文件路径（默认：out/world_states.json）"),
) -> None:
    """从事件图谱提取世界/物品状态变化 🌍✨"""
    import asyncio
    import sys
    from character.scripts.extract_world_states import main_async
    
    # 设置命令行参数（如果提供了路径）
    original_argv = sys.argv.copy()
    try:
        args = []
        if events_path:
            args.append(events_path)
        if output_path:
            args.append(output_path)
        
        if args:
            sys.argv = ["extract_world_states"] + args
        else:
            sys.argv = ["extract_world_states"]
        
        asyncio.run(main_async())
    finally:
        sys.argv = original_argv


@app.command("extract-character-states")
def extract_character_states_cmd(
    events_path: str = typer.Option(None, "--events", "-e", help="事件 JSON 文件路径（默认：out/extract_chain.json）"),
    mentions_path: str = typer.Option(None, "--mentions", "-m", help="Mention JSON 文件路径（默认：out/mentions.json）"),
    entities_path: str = typer.Option(None, "--entities", "-n", help="实体 JSON 文件路径（默认：out/entities.json）"),
    output_path: str = typer.Option(None, "--output", "-o", help="输出 JSON 文件路径（默认：out/character_states.json）"),
) -> None:
    """从事件图谱提取人物内在状态变化 🎭✨"""
    import asyncio
    import sys
    from character.scripts.extract_character_states import main_async
    
    # 设置命令行参数（如果提供了路径）
    original_argv = sys.argv.copy()
    try:
        args = []
        if events_path:
            args.append(events_path)
        if mentions_path:
            args.append(mentions_path)
        if entities_path:
            args.append(entities_path)
        if output_path:
            args.append(output_path)
        
        if args:
            sys.argv = ["extract_character_states"] + args
        else:
            sys.argv = ["extract_character_states"]
        
        asyncio.run(main_async())
    finally:
        sys.argv = original_argv


@app.command("extract-relationship-states")
def extract_relationship_states_cmd(
    events_path: str = typer.Option(None, "--events", "-e", help="事件 JSON 文件路径（默认：out/extract_chain.json）"),
    mentions_path: str = typer.Option(None, "--mentions", "-m", help="Mention JSON 文件路径（默认：out/mentions.json）"),
    entities_path: str = typer.Option(None, "--entities", "-n", help="实体 JSON 文件路径（默认：out/entities.json）"),
    output_path: str = typer.Option(None, "--output", "-o", help="输出 JSON 文件路径（默认：out/relationship_states.json）"),
) -> None:
    """从事件图谱提取人物关系状态变化 💕✨"""
    import asyncio
    import sys
    from character.scripts.extract_relationship_states import main_async
    
    # 设置命令行参数（如果提供了路径）
    original_argv = sys.argv.copy()
    try:
        args = []
        if events_path:
            args.append(events_path)
        if mentions_path:
            args.append(mentions_path)
        if entities_path:
            args.append(entities_path)
        if output_path:
            args.append(output_path)
        
        if args:
            sys.argv = ["extract_relationship_states"] + args
        else:
            sys.argv = ["extract_relationship_states"]
        
        asyncio.run(main_async())
    finally:
        sys.argv = original_argv


@app.command("extract-states-and-baseline")
def extract_states_and_baseline_cmd(
    output_dir: Optional[str] = typer.Option(
        None,
        "--output-dir",
        "-d",
        help="产出目录（写入四类状态 JSON 与 baseline），默认 out",
    ),
    events: Optional[str] = typer.Option(
        None,
        "--events",
        "-e",
        help="事件链 JSON（默认：<output_dir>/extract_chain.json）",
    ),
    mentions: Optional[str] = typer.Option(
        None,
        "--mentions",
        "-m",
        help="mentions.json（默认：<output_dir>/mentions.json）",
    ),
    entities: Optional[str] = typer.Option(
        None,
        "--entities",
        "-n",
        help="entities.json（默认：<output_dir>/entities.json）",
    ),
) -> None:
    """依次执行世界状态、人物状态、关系状态抽取，再初始化 state baseline（同一 output_dir）。"""
    root = Path(__file__).resolve().parents[1]
    if str(root) not in sys.path:
        sys.path.insert(0, str(root))
    from scripts.ui.utils import run_extract_states_and_baseline_for_ui

    params: dict[str, Any] = {
        "output_dir": (output_dir or "out").strip(),
        "events": events,
        "mentions": mentions,
        "entities": entities,
    }
    code = run_extract_states_and_baseline_for_ui(root, params)
    raise SystemExit(code)


@app.command("import-entities")
def import_entities_cmd(
    entities_path: str = typer.Option(None, "--entities", "-e", help="实体 JSON 文件路径（默认：out/entities.json）"),
) -> None:
    """导入实体到 Neo4j 图谱 👤✨"""
    import sys
    from character.scripts.import_entities import main

    _ensure_neo4j_login()

    # 设置命令行参数（如果提供了路径）
    original_argv = sys.argv.copy()
    try:
        if entities_path:
            sys.argv = ["import_entities", entities_path]
        else:
            sys.argv = ["import_entities"]
        
        main()
    finally:
        sys.argv = original_argv


@app.command("import-state-changes")
def import_state_changes_cmd(
    states_path: str = typer.Option(None, "--states", "-s", help="状态变化 JSON 文件路径（默认：out/world_states.json）"),
) -> None:
    """导入状态变化到 Neo4j 图谱 🎯✨"""
    import sys
    from character.scripts.import_state_changes import main

    _ensure_neo4j_login()

    # 设置命令行参数（如果提供了路径）
    original_argv = sys.argv.copy()
    try:
        if states_path:
            sys.argv = ["import_state_changes", states_path]
        else:
            sys.argv = ["import_state_changes"]
        
        main()
    finally:
        sys.argv = original_argv


@app.command("import-neo4j-all")
def import_neo4j_all_cmd(
    output_dir: str = typer.Option(
        "out",
        "--output-dir",
        "-d",
        help="统一输出目录（默认 out）",
    ),
    clear: bool = typer.Option(True, "--clear", "-c", help="导入事件前是否清空数据库（默认 true）"),
    json_path: str = typer.Option(
        None,
        "--json",
        "-j",
        help="事件图谱 JSON（默认：自动合并为 <output_dir>/extract_merged.json 后导入）",
    ),
    entities_path: str = typer.Option(
        None,
        "--entities",
        "-e",
        help="实体 JSON（默认：<output_dir>/entities.json）",
    ),
    world_states_path: str = typer.Option(
        None,
        "--world-states",
        help="世界状态 JSON（默认：<output_dir>/world_states.json）",
    ),
    character_states_path: str = typer.Option(
        None,
        "--character-states",
        help="人物状态 JSON（默认：<output_dir>/character_states.json）",
    ),
    relationship_states_path: str = typer.Option(
        None,
        "--relationship-states",
        help="关系状态 JSON（默认：<output_dir>/relationship_states.json）",
    ),
) -> None:
    """一键导入 Neo4j：事件图谱 + 实体 + 三类状态。"""
    from pathlib import Path

    _ensure_neo4j_login()

    base = Path(str(output_dir).strip() or "out")
    chain_path = base / "extract_chain.json"
    relations_path = base / "extract_relations.json"
    merged_path = Path(json_path) if json_path else (base / "extract_merged.json")
    resolved_entities = Path(entities_path) if entities_path else (base / "entities.json")
    resolved_world = (
        Path(world_states_path) if world_states_path else (base / "world_states.json")
    )
    resolved_char = (
        Path(character_states_path)
        if character_states_path
        else (base / "character_states.json")
    )
    resolved_rel = (
        Path(relationship_states_path)
        if relationship_states_path
        else (base / "relationship_states.json")
    )

    # 事件关系导入必须基于「包含 relations 的 merged JSON」。
    # 由于前端可能把 `--json` 默认填成 extract_chain.json（不含 relations），
    # 所以这里做二次校验：只要 merged 文件没 relations，就自动重新合并后再导入。
    import json as _json

    def _merged_has_relations(p: Path) -> bool:
        if not p.exists():
            return False
        try:
            data = _json.loads(p.read_text(encoding="utf-8"))
        except Exception:
            return False
        rel = data.get("relations", [])
        return isinstance(rel, list) and len(rel) > 0

    need_merge = not _merged_has_relations(merged_path)
    if need_merge:
        if not chain_path.exists():
            print(f"[red]缺少输入文件：{chain_path}[/red]")
            raise typer.Exit(code=1)
        if not relations_path.exists():
            print(
                f"[red]缺少输入文件：{relations_path}（没有 relations，导入时将无法建立事件关系）[/red]"
            )
            raise typer.Exit(code=1)
        print("[cyan]检测到 merged JSON 无 relations，重新合并 extract_chain + extract_relations…[/cyan]")
        from event_graph.scripts.merge_and_import import merge_data

        merge_data(
            chain_path=chain_path,
            relations_path=relations_path,
            output_path=merged_path,
        )

    for p in (resolved_entities, resolved_world, resolved_char, resolved_rel):
        if not p.exists():
            print(f"[red]缺少输入文件：{p}[/red]")
            raise typer.Exit(code=1)

    from event_graph.scripts.import_to_neo4j import import_to_neo4j
    from event_graph.scripts.merge_and_import import merge_data
    from character.scripts.import_entities import main as import_entities_main
    from character.scripts.import_state_changes import main as import_state_changes_main

    import sys

    print("[cyan]1/5 导入事件节点与事件关系...[/cyan]")
    import_to_neo4j(merged_path, clear_first=clear)

    print("[cyan]2/5 导入实体...[/cyan]")
    old = sys.argv.copy()
    try:
        sys.argv = ["import_entities", str(resolved_entities)]
        import_entities_main()
    finally:
        sys.argv = old

    for i, state_path in enumerate((resolved_world, resolved_char, resolved_rel), start=3):
        print(f"[cyan]{i}/5 导入状态变化：{state_path.name}[/cyan]")
        old = sys.argv.copy()
        try:
            sys.argv = ["import_state_changes", str(state_path)]
            import_state_changes_main()
        finally:
            sys.argv = old


@app.command("query-neo4j")
def query_neo4j_cmd() -> None:
    """查询 Neo4j 数据 🔍✨"""
    import sys
    from character.scripts.query_neo4j import main
    
    # 直接传递所有参数（除了命令名）
    original_argv = sys.argv.copy()
    try:
        # 移除 "query-neo4j" 命令名，保留其他参数
        if len(sys.argv) > 2 and sys.argv[1] == "query-neo4j":
            sys.argv = ["query_neo4j"] + sys.argv[2:]
        else:
            sys.argv = ["query_neo4j"]
        
        main()
    finally:
        sys.argv = original_argv


@app.command("aggregate-characters")
def aggregate_characters_cmd(
    events_path: str = typer.Option("out/extract_chain.json", "--events", "-e", help="事件 JSON 文件路径"),
    character_states_path: str = typer.Option("out/character_states.json", "--char-states", help="人物状态 JSON 文件路径"),
    relationship_states_path: str = typer.Option("out/relationship_states.json", "--rel-states", help="关系状态 JSON 文件路径"),
    entities_path: str = typer.Option("out/entities.json", "--entities", "-n", help="实体 JSON 文件路径"),
    mentions_path: str = typer.Option("out/mentions.json", "--mentions", "-m", help="Mentions JSON 文件路径"),
    output_path: str = typer.Option("out/character_profiles.json", "--output", "-o", help="输出 JSON 文件路径"),
    use_rules: bool = typer.Option(False, "--rules", "-r", help="使用规则提取行动词（默认使用 LLM 提取）"),
) -> None:
    """聚合人物节点得到完整画像 🎭✨（从事件和状态变化中提取人物信息，从 mentions.json 提取行动）"""
    import asyncio
    import sys
    from character.scripts.aggregate_characters import main_async
    
    # 设置命令行参数
    original_argv = sys.argv.copy()
    try:
        args = [events_path, character_states_path, relationship_states_path, entities_path, mentions_path, output_path]
        if use_rules:
            args.append("--rules")
        sys.argv = ["aggregate_characters"] + args
        
        asyncio.run(main_async())
    finally:
        sys.argv = original_argv


@app.command("aggregate-personas")
def aggregate_personas_cmd(
    profiles_path: str = typer.Option("out/character_profiles.json", "--profiles", "-p", help="人物画像 JSON 文件路径"),
    output_path: str = typer.Option("out/character_personas.json", "--output", "-o", help="输出 JSON 文件路径"),
) -> None:
    """聚合人物静态人设 🎭✨（从 character_profiles.json 生成静态人设）"""
    import asyncio
    import sys
    from character.scripts.aggregate_personas import main_async
    
    # 设置命令行参数
    original_argv = sys.argv.copy()
    try:
        args = [profiles_path, output_path]
        sys.argv = ["aggregate_personas"] + args
        asyncio.run(main_async())
    finally:
        sys.argv = original_argv


@app.command("aggregate-characters-and-personas")
def aggregate_characters_and_personas_cmd(
    output_dir: Optional[str] = typer.Option(
        None,
        "--output-dir",
        "-d",
        help="产出目录（写入 character_profiles.json 与 character_personas.json），默认 out",
    ),
    events: Optional[str] = typer.Option(
        None,
        "--events",
        "-e",
        help="事件链 JSON，默认 <output_dir>/extract_chain.json",
    ),
    char_states: Optional[str] = typer.Option(
        None,
        "--char-states",
        help="人物状态 JSON，默认 <output_dir>/character_states.json",
    ),
    rel_states: Optional[str] = typer.Option(
        None,
        "--rel-states",
        help="关系状态 JSON，默认 <output_dir>/relationship_states.json",
    ),
    entities: Optional[str] = typer.Option(
        None,
        "--entities",
        "-n",
        help="实体 JSON，默认 <output_dir>/entities.json",
    ),
    mentions: Optional[str] = typer.Option(
        None,
        "--mentions",
        "-m",
        help="mentions.json，默认 <output_dir>/mentions.json",
    ),
    profiles: Optional[str] = typer.Option(
        None,
        "--profiles",
        "-p",
        help="人物画像写出路径（第二步读同一路径），默认 <output_dir>/character_profiles.json",
    ),
    personas_output: Optional[str] = typer.Option(
        None,
        "--personas-output",
        help="静态人设写出路径，默认 <output_dir>/character_personas.json",
    ),
    use_rules: bool = typer.Option(
        False,
        "--rules",
        "-r",
        help="人物画像步使用规则提取行动词（默认 LLM）",
    ),
) -> None:
    """依次聚合人物画像与静态人设（同一 output_dir）。"""
    root = Path(__file__).resolve().parents[1]
    if str(root) not in sys.path:
        sys.path.insert(0, str(root))
    from scripts.ui.utils import run_aggregate_characters_and_personas_for_ui

    params: dict[str, Any] = {
        "output_dir": (output_dir or "out").strip(),
        "events": events,
        "char_states": char_states,
        "rel_states": rel_states,
        "entities": entities,
        "mentions": mentions,
        "profiles": profiles,
        "personas_output": personas_output,
        "use_rules": use_rules,
    }
    code = run_aggregate_characters_and_personas_for_ui(root, params)
    raise SystemExit(code)


@app.command("generate-canonical-branch")
def generate_canonical_branch_cmd(
    start_event_id: str = typer.Option(None, "--start-event", "-s", help="起始事件ID（如果为 None，会自动查找第一个事件）"),
    max_events: int = typer.Option(None, "--max-events", "-m", help="最大处理事件数（如果为 None，处理所有事件）"),
    output_path: str = typer.Option("out/canonical_branch.json", "--output", "-o", help="输出 JSON 文件路径"),
    resume: bool = typer.Option(True, "--resume/--no-resume", help="是否从上次进度恢复（默认：True）"),
    save_progress_interval: int = typer.Option(1, "--save-interval", help="每处理几个事件保存一次进度（默认：1）"),
) -> None:
    """生成 Canonical Branch（主线/世界真相记录）📜✨"""
    import asyncio
    import json
    from pathlib import Path
    from core.llm_client import AsyncLLMClient
    from branch.generation.canonical_branch import CanonicalBranchGenerator
    
    async def main_async():
        print("[cyan]📜 开始生成 Canonical Branch（主线）...[/cyan]")
        
        # 初始化 LLM 客户端
        llm_client = AsyncLLMClient.create_default("deepseek")
        
        # 创建生成器
        output_file = Path(output_path)
        progress_file = output_file.parent / "canonical_branch_progress.json"
        generator = CanonicalBranchGenerator(
            llm_client=llm_client,
            progress_file=progress_file,
        )
        
        try:
            # 生成主线记录
            result = await generator.generate_canonical_branch(
                start_event_id=start_event_id,
                max_events=max_events,
                resume=resume,
                save_progress_interval=save_progress_interval,
            )
            
            # 保存结果
            output_file.parent.mkdir(parents=True, exist_ok=True)
            
            with open(output_file, 'w', encoding='utf-8') as f:
                json.dump(result, f, ensure_ascii=False, indent=2)
            
            print(f"[green]✅ 生成完成！结果已保存到: {output_file}[/green]")
            print(f"\n[cyan]生成统计：[/cyan]")
            print(f"  起始事件ID: {result.get('start_event_id')}")
            print(f"  处理事件数: {result.get('total_events', 0)}")
            print(f"  主线记录数: {len(result.get('processed_events', []))}")
            
            # 显示第一个主线记录示例
            if result.get('processed_events'):
                first_record = result['processed_events'][0]
                print(f"\n[cyan]第一个主线记录示例：[/cyan]")
                print(f"  事件ID: {first_record.get('event_id')}")
                print(f"  描述: {first_record.get('description', '')[:100]}...")
                if first_record.get('decision_point'):
                    print(f"  选择节点: {first_record.get('decision_point')}")
                    print(f"  主线选择: {first_record.get('canonical_choice')}")
                print(f"  状态变化数: {len(first_record.get('state_changes', []))}")
        finally:
            generator.close()
    
    asyncio.run(main_async())


@app.command("analyze-decision-points")
def analyze_decision_points_cmd(
    canonical_branch_path: str = typer.Option("out/canonical_branch_chronological.json", "--canonical", "-c", help="主线记录（默认用重排后的；未重排时用 -c out/canonical_branch.json）"),
    output_path: str = typer.Option("out/decision_points_analysis.json", "--output", "-o", help="分析结果输出路径"),
    target_branch_count: Optional[int] = typer.Option(
        None,
        "--target-branch-count",
        help="可选：第二轮整合推荐时「推荐列表尽量不超过 N 个」的软约束（写入提示词，非机械截断）",
    ),
    max_concurrent: int = typer.Option(5, "--max-concurrent", help="决策点分析并发数"),
) -> None:
    """分析决策点，识别最小骨架（关键事件），判断哪些适合做分支、哪些适合做合流。默认使用时间线重排后的主线。"""
    import asyncio
    from branch.analysis.decision_point_analyzer import DecisionPointAnalyzer
    
    async def run_analysis():
        analyzer = DecisionPointAnalyzer(
            canonical_branch_path=canonical_branch_path,
            max_concurrent=max_concurrent,
        )
        
        try:
            print("[cyan]加载决策点...[/cyan]")
            decision_points = analyzer.load_decision_points(canonical_branch_path)
            print(f"[green]找到 {len(decision_points)} 个决策点[/green]\n")
            
            if not decision_points:
                print("[yellow]没有找到决策点，请先运行主线生成器[/yellow]")
                return
            
            print("[cyan]开始分析决策点（识别最小骨架，判断哪些适合做分支）...[/cyan]\n")
            
            analysis_result = await analyzer.analyze_decision_points(
                decision_points,
                target_branch_count=target_branch_count,
            )
            
            # 显示结果
            critical_events = analysis_result["critical_events"]
            branch_points = analysis_result["branch_points"]
            skip_points = analysis_result["skip_points"]
            
            print("[bold cyan]分析结果：[/bold cyan]")
            print(f"  [cyan]关键事件（最小骨架）: {len(critical_events)} 个[/cyan]")
            print(f"  [green]适合做分支: {len(branch_points)} 个[/green]")
            print(f"  [dim]不适合做分支: {len(skip_points)} 个[/dim]\n")
            
            if critical_events:
                print(f"[cyan]关键事件（最小骨架）: {', '.join(critical_events)}[/cyan]")
            if branch_points:
                print(f"[green]分支点: {', '.join(branch_points)}[/green]")
            if skip_points:
                print(f"[dim]跳过点: {', '.join(skip_points)}[/dim]")
            print(f"\n[cyan]注意：[/cyan]合流点不需要单独识别，合流点 = 最小骨架中的下一个关键事件")
            
            # 保存结果
            analyzer.save_analysis_result(analysis_result, output_path)
            
            print(f"\n[green]✅ 分析完成！结果已保存到: {output_path}[/green]")
            print("\n[cyan]下一步建议：[/cyan]")
            print("  1. 使用分析结果中的 branch_points 生成支线")
            print("  2. 生成支线后，使用 merge_points 作为合流点")
            print("  3. skip_points 可以忽略，保持主线线性")
            
        except Exception as e:
            print(f"[red]❌ 分析失败: {e}[/red]")
            import traceback
            traceback.print_exc()
        finally:
            analyzer.close()
    
    asyncio.run(run_analysis())


@app.command("determine-ending-candidates")
def determine_ending_candidates_cmd(
    branches_path: str = typer.Option("out/branches.json", "--branches", "-b", help="分支记录文件路径"),
    canonical_branch_path: str = typer.Option("out/canonical_branch_chronological.json", "--canonical", "-c", help="主线记录路径（建议与 generate-all-paths 一致，用已重排的 chronological）"),
    output_path: str = typer.Option("out/ending_candidates.json", "--output", "-o", help="输出 JSON 文件路径"),
    branch_id: str = typer.Option(None, "--branch-id", "-i", help="指定分支ID（如果为 None，会为所有分支确定候选池）"),
) -> None:
    """
    确定结局候选池
    
    根据分支选择分析可能的结局类型范围，为每个分支确定结局候选池。
    路径组合 = 各分支点选择数之积（含主线选择），例如 3 个分支点每点 3 选 → 3^3 条，含 1 条全主线路径。
    """
    import asyncio
    import json
    from pathlib import Path
    from branch.analysis.ending_candidate_determiner import EndingCandidateDeterminer
    
    async def main_async():
        print("[cyan]开始确定结局候选池...[/cyan]")
        
        # 加载分支数据
        project_root = Path(__file__).resolve().parent.parent
        branches_file = _resolve_under_project_root(project_root, branches_path)
        if not branches_file.exists():
            print(f"[red]找不到分支文件: {branches_path}[/red]")
            return
        
        with open(branches_file, "r", encoding="utf-8") as f:
            branches = json.load(f)
        
        if not branches:
            print("[red]分支文件为空[/red]")
            return
        
        # 过滤分支（如果指定了 branch_id）
        target_branch_id = branch_id  # 使用局部变量避免作用域冲突
        if target_branch_id:
            branches = [b for b in branches if b.get("branch_id") == target_branch_id]
            if not branches:
                print(f"[red]找不到分支ID: {target_branch_id}[/red]")
                return
        
        print(f"[cyan]找到 {len(branches)} 个分支，开始分析...[/cyan]\n")
        
        canonical_file = _resolve_under_project_root(project_root, canonical_branch_path)
        if not canonical_file.exists():
            print(f"[yellow]主线记录不存在: {canonical_branch_path}，将使用 branches 中的 canonical_choice 作为主线[/yellow]")
        
        # 创建确定器（使用与 generate-all-paths 一致的主线文件，确保主线路径被正确识别）
        determiner = EndingCandidateDeterminer(
            canonical_branch_path=str(canonical_file),
            branches_path=str(branches_file),
        )
        
        # 使用路径组合方法确定所有路径的结局候选池
        try:
            # 调用路径组合方法
            result = await determiner.determine_ending_candidates_for_all_paths()
            # 保存结果（包含默认结局池 + 所有路径组合的筛选结果）
            output_data = dict(result) if isinstance(result, dict) else {"path_results": []}
        except Exception as e:
            print(f"[red]分析路径组合时出错: {e}[/red]")
            import traceback
            traceback.print_exc()
            output_data = {"path_results": []}
        
        # 保存结果
        output_file = _resolve_under_project_root(project_root, output_path)
        output_file.parent.mkdir(parents=True, exist_ok=True)

        # 附带本次实际使用的输入路径，方便 UI 预览展示“用的是哪一套 runs/.../out”
        output_data["_source_paths"] = {
            "branches": str(branches_file),
            "canonical": str(canonical_file),
            "decision_analysis": str(branches_file.resolve().parent / "decision_points_analysis.json"),
            "extract_chain": str(branches_file.resolve().parent / "extract_chain.json"),
        }

        with open(output_file, "w", encoding="utf-8") as f:
            json.dump(output_data, f, ensure_ascii=False, indent=2)
        
        print(f"[green]✅ 分析完成！结果已保存到: {output_path}[/green]")
        
        # 显示统计信息
        results = output_data.get("path_results", [])
        if isinstance(results, list) and results:
            print(f"\n[cyan]统计信息：[/cyan]")
            total_paths = len(results)
            canonical_paths = sum(1 for r in results if r.get("is_canonical", False))
            branch_paths = total_paths - canonical_paths
            immediate_triggers = sum(1 for r in results if r.get("is_immediate_trigger", False))
            
            print(f"  总路径数: {total_paths}")
            print(f"  主线路径: {canonical_paths} 个")
            print(f"  分支路径: {branch_paths} 个")
            print(f"  立即触发结局: {immediate_triggers} 个")
            
            # 统计候选池大小
            candidate_counts = {}
            for r in results:
                count = len(r.get("candidates", []))
                candidate_counts[count] = candidate_counts.get(count, 0) + 1
            
            print(f"\n[cyan]候选池大小分布：[/cyan]")
            for count in sorted(candidate_counts.keys()):
                print(f"  {count} 个候选: {candidate_counts[count]} 条路径")
    
    asyncio.run(main_async())


@app.command("generate-branch")
def generate_branch_cmd(
    fork_event_id: str = typer.Option(None, "--fork-event", "-f", help="分叉事件ID（如果为 None，会为所有决策点生成支线）"),
    canonical_branch_path: str = typer.Option("out/canonical_branch_chronological.json", "--canonical", "-c", help="主线记录文件路径（需与 analyze-decision-points 使用同一重排主线，否则分支点可能对不齐）"),
    decision_analysis_path: str = typer.Option("out/decision_points_analysis.json", "--decision-analysis", "-d", help="决策点分析文件路径"),
    output_path: str = typer.Option("out/branches.json", "--output", "-o", help="输出 JSON 文件路径"),
    max_branches_per_point: int = typer.Option(2, "--max-branches", "-m", help="每个决策点最多生成多少条支线（默认：2）"),
    decision_density: str = typer.Option("high", "--decision-density", help="分支点密度（low, medium, high），仅在未指定 --fork-event 时生效"),
    decision_density_ratio: float = typer.Option(None, "--decision-density-ratio", help="可选：直接指定保留比例（0~1），优先于 --decision-density"),
) -> None:
    """
    生成支线（基于主线决策点的替代路径）🌿✨
    
    注意：此命令只生成分支的基础记录（选择、状态变化），不生成完整的支线路径。
    完整的支线路径（分支事件链、合流、结局）需要单独使用 BranchEventGenerator 处理。
    会在生成支线之前根据主线内容推断叙述人称，并写入与 branches 输出同目录的 content_context.json
    （例如 runs/.../out/content_context.json），供后续 generate-all-paths 与内容生成共用；仍兼容读取项目根下 out/content_context.json。
    """
    import asyncio
    import json
    from pathlib import Path
    from core.llm_client import AsyncLLMClient
    from branch.generation.branch_generator import BranchGenerator

    project_root = Path(__file__).resolve().parent.parent
    if str(project_root) not in sys.path:
        sys.path.insert(0, str(project_root))
    
    async def main_async():
        print("[cyan]开始生成支线...[/cyan]")
        
        canonical_branch_file = _resolve_under_project_root(project_root, canonical_branch_path)
        output_file = _resolve_under_project_root(project_root, output_path)
        decision_analysis_resolved = _resolve_under_project_root(
            project_root, decision_analysis_path
        )
        if not canonical_branch_file.exists():
            print(f"[red]找不到主线记录文件: {canonical_branch_file}[/red]")
            return
        with open(canonical_branch_file, "r", encoding="utf-8") as f:
            canonical_branch = json.load(f)
        # 人称写入 branches 输出同目录（与 UI runs/.../out 一致），避免只写到项目根 out/ 导致后续步骤读错
        from branch.narrative_perspective import infer_narrative_perspective_from_events
        processed = canonical_branch.get("processed_events", [])
        narrative_perspective = await infer_narrative_perspective_from_events(processed, ending_sample=None, llm_client=None)
        content_context_path = output_file.parent / "content_context.json"
        content_context_path.parent.mkdir(parents=True, exist_ok=True)
        with open(content_context_path, "w", encoding="utf-8") as f:
            json.dump({"narrative_perspective": narrative_perspective}, f, ensure_ascii=False, indent=2)
        try:
            cc_rel = content_context_path.relative_to(project_root)
        except ValueError:
            cc_rel = content_context_path
        print(f"[cyan]叙述人称（由主线推断，已写入 {cc_rel}）：{narrative_perspective}[/cyan]\n")

        # 初始化 LLM 客户端
        # generate-branch：提示词已限制只生成 N 条，token 浪费下降；
        # 将并发提升到 10 以便更快生成（网络抖动时错误概率也会上升）。
        llm_client = AsyncLLMClient(max_retries=5, max_concurrent=10)

        # 创建支线生成器（传入人称约束，与上面写入的 content_context 一致；路径用解析后的绝对路径）
        generator = BranchGenerator(
            canonical_branch_path=str(canonical_branch_file),
            llm_client=llm_client,
            narrative_perspective=narrative_perspective,
            decision_analysis_path=str(decision_analysis_resolved),
        )
        
        try:
            # 生成基础支线（分叉点的选择）
            branches = []
            if fork_event_id:
                branches = await generator.generate_all_branches(
                    fork_event_id=fork_event_id,
                    max_branches_per_point=max_branches_per_point,
                )
            else:
                decision_analysis_file = decision_analysis_resolved

                selected_fork_ids = []
                if decision_analysis_file.exists():
                    with open(decision_analysis_file, "r", encoding="utf-8") as f:
                        analysis_data = json.load(f)
                    branch_points = analysis_data.get("branch_points", [])
                    if isinstance(branch_points, list):
                        selected_fork_ids = [str(item) for item in branch_points if item]

                # 若分析文件缺失或 branch_points 为空，回退到原逻辑（所有决策点）
                if not selected_fork_ids:
                    print("[yellow]未找到可用 branch_points，回退为所有决策点生成支线[/yellow]")
                    branches = await generator.generate_all_branches(
                        fork_event_id=None,
                        max_branches_per_point=max_branches_per_point,
                    )
                else:
                    for selected_fork in selected_fork_ids:
                        partial = await generator.generate_all_branches(
                            fork_event_id=selected_fork,
                            max_branches_per_point=max_branches_per_point,
                        )
                        branches.extend(partial)
                    # 去重（同一 branch_id 仅保留一条）
                    unique = {}
                    for branch in branches:
                        branch_id = branch.get("branch_id")
                        if branch_id not in unique:
                            unique[branch_id] = branch
                    branches = list(unique.values())
            
            # 保存结果（与 content_context 同目录）
            generator.save_branches(branches, str(output_file))
            
            print(f"\n[green]生成完成！共生成 {len(branches)} 条支线[/green]")
            
            # 显示统计信息
            if branches:
                print(f"\n[cyan]支线统计：[/cyan]")
                fork_events = {}
                for branch in branches:
                    fork_id = branch.get("fork_event_id")
                    if fork_id not in fork_events:
                        fork_events[fork_id] = 0
                    fork_events[fork_id] += 1
                
                for fork_id, count in fork_events.items():
                    print(f"  事件 {fork_id}: {count} 条支线")
                
                # 显示第一个支线示例
                first_branch = branches[0]
                print(f"\n[cyan]第一个支线示例：[/cyan]")
                print(f"  支线ID: {first_branch.get('branch_id')}")
                print(f"  分叉事件: {first_branch.get('fork_event_id')}")
                print(f"  主线选择: {first_branch.get('canonical_choice')}")
                print(f"  支线选择: {first_branch.get('branch_choice')}")
                print(f"  描述: {first_branch.get('description', '')[:100]}...")
                print(f"  生成的状态变化数: {len(first_branch.get('generated_state_changes', []))}")
        except Exception as e:
            print(f"[red]❌ 生成支线时出错: {e}[/red]")
            raise
    
    asyncio.run(main_async())


@app.command("generate-all-paths")
def generate_all_paths_cmd(
    ending_candidates_path: str = typer.Option("out/ending_candidates.json", "--ending-candidates", "-e", help="结局候选文件路径"),
    canonical_branch_path: str = typer.Option("out/canonical_branch_chronological.json", "--canonical", "-c", help="主线记录文件路径（建议使用已重排的 chronological 文件）"),
    decision_analysis_path: str = typer.Option("out/decision_points_analysis.json", "--decision-analysis", "-d", help="决策点分析文件路径"),
    output_format: str = typer.Option("story_content", "--format", "-f", help="输出格式（story_content 或 raw）"),
    output_path: str = typer.Option("out/all_paths", "--output", "-o", help="输出文件夹路径（每条路径会保存为独立文件）"),
    enable_hitl: bool = typer.Option(False, "--enable-hitl", help="启用人机协作（在关键决策点暂停等待确认）"),
    max_concurrent_paths: int = typer.Option(50, "--max-concurrent", "-j", help="分支路径 worker 数（默认 50 条并发，1=顺序执行）"),
    semaphore_limit: Optional[int] = typer.Option(None, "--semaphore", "-s", help="可选：同时运行路径数上限，用于限流；不设则等于 -j"),
) -> None:
    """
    生成所有路径的完整内容。使用已按时间线重排的主线文件（默认 canonical_branch_chronological.json）生成所有路径。
    分支路径可通过 --max-concurrent 并发处理以加速。
    事件补全（写入 all_paths_completed）请单独运行 ``complete-all-path-events`` 或流水线中的对应步骤。
    """
    import asyncio
    import json
    from pathlib import Path
    from core.llm_client import AsyncLLMClient
    from state_manager.state_applier import StateApplier
    from branch.generation.path_generation_functions import PathGenerationFunctions

    project_root = Path(__file__).resolve().parent.parent
    if str(project_root) not in sys.path:
        sys.path.insert(0, str(project_root))

    async def main_async():
        print("[cyan]开始生成所有路径的完整内容...[/cyan]")
        
        # 检查必要文件
        ending_candidates_file = _resolve_under_project_root(project_root, ending_candidates_path)
        if not ending_candidates_file.exists():
            print(f"[red]找不到结局候选文件: {ending_candidates_file}[/red]")
            print("[yellow]提示：请先运行 'determine-ending-candidates' 命令生成结局候选文件[/yellow]")
            return
        
        canonical_branch_file = _resolve_under_project_root(project_root, canonical_branch_path)
        if not canonical_branch_file.exists():
            print(f"[red]找不到主线记录文件: {canonical_branch_file}[/red]")
            print("[yellow]提示：请先运行 'generate-canonical-branch' 命令生成主线记录[/yellow]")
            return

        decision_analysis_file = _resolve_under_project_root(project_root, decision_analysis_path)
        if not decision_analysis_file.exists():
            print(f"[red]找不到决策点分析文件: {decision_analysis_file}[/red]")
            print("[yellow]提示：请先运行 'analyze-decision-points' 命令生成决策点分析文件[/yellow]")
            return

        # 加载主线记录（预期为已重排的 canonical_branch_chronological.json）
        with open(canonical_branch_file, "r", encoding="utf-8") as f:
            canonical_branch = json.load(f)

        # 人称：优先主线同目录的 content_context.json（generate-branch 与 branches 同目录写入），再回退项目根 out/
        narrative_perspective = _load_narrative_perspective_from_content_context(
            canonical_branch_file.parent / "content_context.json",
            project_root / "out" / "content_context.json",
        )
        if narrative_perspective:
            print(f"[cyan]叙述人称（来自 content_context.json）：{narrative_perspective}[/cyan]\n")
        if not narrative_perspective:
            from branch.narrative_perspective import infer_narrative_perspective_from_events
            processed = canonical_branch.get("processed_events", [])
            narrative_perspective = await infer_narrative_perspective_from_events(processed, ending_sample=None, llm_client=None)
            print(f"[cyan]叙述人称（未找到 content_context，由主线内容推断）：{narrative_perspective}[/cyan]\n")

        execution_llm_client = AsyncLLMClient.create_default()
        flow_llm_client = AsyncLLMClient.create_default()
        state_applier = StateApplier()

        # 创建路径生成函数实例（传入人称，分支事件生成时会作为叙述约束）
        def make_path_functions() -> PathGenerationFunctions:
            return PathGenerationFunctions(
                llm_client=execution_llm_client,
                flow_llm_client=flow_llm_client,
                state_applier=state_applier,
                canonical_branch=canonical_branch,
                ending_candidates_path=str(ending_candidates_file),
                decision_analysis_path=str(decision_analysis_file),
                narrative_perspective=narrative_perspective,
            )
        path_functions = make_path_functions()
        # 人机协同：与 path_generation_functions 中 _enable_hitl_for_all_paths 对齐，传入 generate_path_with_function_calling
        path_functions._enable_hitl_for_all_paths = enable_hitl
        # 并发时使用多个 worker，每个分支路径占用一个 worker，避免共享可变状态
        worker_pool = [path_functions] + [make_path_functions() for _ in range(max_concurrent_paths - 1)] if max_concurrent_paths > 1 else None
        if worker_pool:
            for w in worker_pool:
                w._enable_hitl_for_all_paths = enable_hitl
        if enable_hitl and max_concurrent_paths > 1:
            print(
                "[yellow]⚠ 已启用人机协作（--enable-hitl）：多路并发时终端交互可能混乱，建议加 --max-concurrent 1 顺序执行。[/yellow]"
            )
        if worker_pool:
            cap = f"，限流 {semaphore_limit} 条同时" if semaphore_limit is not None and semaphore_limit < len(worker_pool) else ""
            print(f"[cyan]分支路径将并发处理（{len(worker_pool)} 个 worker{cap}）[/cyan]")
        
        try:
            # 处理所有路径
            print(f"\n[cyan]开始处理所有路径（输出格式: {output_format}）...[/cyan]")
            results = await path_functions.process_all_paths(
                output_format=output_format,
                worker_pool=worker_pool,
                max_concurrent=semaphore_limit,
            )
            
            # 创建输出文件夹（每条路径一个文件）
            output_dir = _resolve_under_project_root(project_root, output_path)
            output_dir.mkdir(parents=True, exist_ok=True)
            
            # 保存每条路径为独立文件
            def sanitize_filename(path_id: str) -> str:
                """清理文件名（移除特殊字符，确保文件名安全）"""
                safe = path_id.replace("/", "_").replace("\\", "_").replace(":", "_")
                return safe
            
            saved_files = []
            for result in results:
                if result.get("from_cache_only"):
                    # 整条路径来自缓存（含重复出现的提前结局路径）：未再演化、不写入 out，节省磁盘
                    continue
                path_id = result.get("path_id", "unknown")
                safe_filename = sanitize_filename(path_id)
                file_path = output_dir / f"{safe_filename}.json"
                with open(file_path, "w", encoding="utf-8") as f:
                    json.dump(result, f, ensure_ascii=False, indent=2)
                saved_files.append(file_path)
            
            # 同时保存一个索引文件（包含所有路径的摘要信息 + 叙述人称，供 generate-content 等使用）
            index_file = output_dir / "index.json"
            saved_count = len(saved_files)
            index_data = {
                "total_paths": len(results),
                "saved_paths": saved_count,
                "from_cache_skipped": len(results) - saved_count,
                "canonical_count": sum(1 for r in results if r.get("path_id") == "canonical_path"),
                "branch_count": len(results) - sum(1 for r in results if r.get("path_id") == "canonical_path"),
                "output_format": output_format,
                "narrative_perspective": narrative_perspective,
                "paths": [
                    {
                        "path_id": r.get("path_id", "unknown"),
                        "filename": f"{sanitize_filename(r.get('path_id', 'unknown'))}.json",
                        "events_count": len(r.get("events", [])),
                        "has_ending": r.get("ending") is not None,
                        "ending_name": r.get("ending", {}).get("name") if r.get("ending") else None,
                        "from_cache_only": r.get("from_cache_only", False),
                    }
                    for r in results
                ]
            }
            with open(index_file, "w", encoding="utf-8") as f:
                json.dump(index_data, f, ensure_ascii=False, indent=2)
            
            print(f"\n[green]✅ 所有路径生成完成！[/green]")
            print(f"[green]   输出文件夹: {output_dir}[/green]")
            print(f"[green]   共保存 {len(saved_files)} 个路径文件 + 1 个索引文件[/green]")
            if saved_count < len(results):
                print(f"[green]   其中 {len(results) - saved_count} 条为重复提前结局路径（缓存复用），未写入文件[/green]")

            # 显示统计信息
            if results:
                print(f"\n[cyan]统计信息：[/cyan]")
                print(f"  总路径数: {len(results)}")
                canonical_count = sum(1 for r in results if r.get("path_id") == "canonical_path")
                branch_count = len(results) - canonical_count
                print(f"  主线路径: {canonical_count}")
                print(f"  分支路径: {branch_count}")
                
                # 显示每个路径的状态
                print(f"\n[cyan]路径文件：[/cyan]")
                for i, result in enumerate(results, 1):
                    path_id = result.get("path_id", "unknown")
                    safe_filename = sanitize_filename(path_id)
                    events_count = len(result.get("events", []))
                    has_ending = result.get("ending") is not None
                    ending_name = result.get("ending", {}).get("name") if result.get("ending") else "无"
                    status = "✅" if has_ending else "⚠️"
                    print(f"  {i}. {status} {safe_filename}.json - {events_count} 个事件, 结局: {ending_name}")
        except Exception as e:
            print(f"[red]❌ 生成路径时出错: {e}[/red]")
            import traceback
            traceback.print_exc()
            raise
    
    asyncio.run(main_async())


@app.command("generate-content")
def generate_content_cmd(
    input_dir: str = typer.Option("out/all_paths_completed", "--input", "-i", help="路径 JSON 所在目录"),
    output_dir: str = typer.Option("out/enhanced_paths", "--output", "-o", help="增强结果写入目录"),
    personas_file: str = typer.Option("out/character_personas.json", "--personas", "-p", help="角色人设 JSON 路径"),
    max_concurrent: Optional[int] = typer.Option(None, "--max-concurrent", "-j", help="同时增强的最大路径数，不设则全部并发"),
    player_choice_catalog: Optional[str] = typer.Option(
        None,
        "--player-choice-catalog",
        help="预生成 player_choice 目录 JSON 路径；默认 <input 上级>/player_choice_catalog.json",
    ),
    skip_player_choice_catalog: bool = typer.Option(
        False,
        "--skip-player-choice-catalog",
        help="禁用目录：每个决策点仍按路径单独调 LLM 生成选项",
    ),
    rebuild_player_choice_catalog: bool = typer.Option(
        False,
        "--rebuild-player-choice-catalog/--no-rebuild-player-choice-catalog",
        help="忽略已有目录文件，用 decision_points_analysis + canonical + branches 重新生成",
    ),
    force_regenerate: bool = typer.Option(
        False,
        "--force-regenerate/--no-force-regenerate",
        help="强制重新生成：不复用 enhanced_paths 缓存（会覆盖写回输出目录）",
    ),
    refresh_player_choice_only: bool = typer.Option(
        False,
        "--refresh-player-choice-only/--no-refresh-player-choice-only",
        help="仅刷新决策点 player_choice（含 jump_target），尽量复用已润色的 enhanced_paths 内容，不重写叙述/对话",
    ),
) -> None:
    """
    仅运行内容增强（ContentGenerator），异步并发处理多条路径

    加载路径 → 使用 LLM 生成详细场景描述、对话等 → 将结果写入目录（默认 out/enhanced_paths）。
    不生成 Ren'Py 脚本；若要完整管道请用 generate-renpy-scripts。

    若 ``--input`` 的上级目录存在 ``decision_points_analysis.json``、``canonical_branch_chronological.json``、
    ``branches.json``，会按 ``branch_points`` 预生成（或加载）``player_choice_catalog.json``，
    各路径在决策点处只插入目录文案并按 ``path_combination`` 补 ``jump_target``，避免同分叉多次 LLM、文案不一致。
    """
    import asyncio
    import json
    project_root = Path(__file__).resolve().parent.parent
    sys.path.insert(0, str(project_root))
    try:
        from scripts.content_generator import ContentGenerator, is_complete_enhanced_path
        from scripts.player_choice_catalog import (
            load_player_choice_catalog,
            save_player_choice_catalog,
        )
        from core.llm_client import AsyncLLMClient
    except ImportError as e:
        print(f"[red]无法导入 content_generator: {e}[/red]")
        print("[yellow]请从项目根目录运行，例如: python -m src.cli generate-content[/yellow]")
        raise SystemExit(1)

    async def run():
        all_paths_path = project_root / input_dir
        if not all_paths_path.is_dir():
            print(f"[red]路径目录不存在: {all_paths_path}[/red]")
            return
        path_list = []
        for path_file in all_paths_path.glob("*.json"):
            if path_file.name == "index.json":
                continue
            with open(path_file, "r", encoding="utf-8") as f:
                path_data = json.load(f)
            path_data["renpy_label"] = path_data.get("renpy_label") or path_file.stem
            path_list.append(path_data)
        if not path_list:
            print("[yellow]未找到任何路径 JSON（跳过 index.json）[/yellow]")
            return
        # 叙述人称：优先 index.json；否则从 all_paths 上级目录或项目根 out/ 的 content_context.json 读
        narrative_perspective = None
        index_file = all_paths_path / "index.json"
        if index_file.exists():
            try:
                with open(index_file, "r", encoding="utf-8") as f:
                    index_data = json.load(f)
                narrative_perspective = index_data.get("narrative_perspective") or None
            except Exception:
                pass
        if not narrative_perspective:
            narrative_perspective = _load_narrative_perspective_from_content_context(
                all_paths_path.parent / "content_context.json",
                project_root / "out" / "content_context.json",
            )
        if narrative_perspective:
            print(f"[cyan]使用叙述人称（与 generate-branch 阶段一致）：{narrative_perspective}[/cyan]")
        personas_path = project_root / personas_file
        if not personas_path.exists():
            print(f"[red]人设文件不存在: {personas_path}[/red]")
            return
        out_path = project_root / output_dir
        out_path.mkdir(parents=True, exist_ok=True)

        # 全路径视角：用于决策点收集 all_choices（同一 fork 的所有 branch_choice）
        all_paths_map_full: dict[str, dict[str, Any]] = {
            p.get("renpy_label") or p.get("path_id", ""): p
            for p in path_list
            if (p.get("renpy_label") or p.get("path_id"))
        }

        # 已有完整增强结果的直接复用，不重新生成
        results_by_label = {}
        to_generate = []
        for path_data in path_list:
            label = path_data.get("renpy_label") or path_data.get("path_id", "unknown").replace("/", "_").replace("\\", "_")
            cache_file = out_path / f"{label}.json"
            if (not force_regenerate) and cache_file.exists():
                try:
                    with open(cache_file, "r", encoding="utf-8") as f:
                        cached = json.load(f)
                    if is_complete_enhanced_path(cached):
                        results_by_label[label] = cached
                        continue
                except Exception:
                    pass
            to_generate.append(path_data)

        n_cached = len(path_list) - len(to_generate)
        if n_cached:
            print(f"[dim]复用已有增强结果：{n_cached} 条[/dim]")
        # 生成器（既用于重建目录，也用于 attach_jump_targets / fallback LLM）
        generator = ContentGenerator(
            llm_client=AsyncLLMClient(),
            personas_file=str(personas_path),
            player_choice_catalog=None,
        )

        # 目录加载/重建：refresh-only 也需要它来刷新选项文案与 jump_target
        choice_catalog = None
        if not skip_player_choice_catalog:
            cat_path = (
                _resolve_under_project_root(project_root, player_choice_catalog)
                if player_choice_catalog
                else (all_paths_path.parent / "player_choice_catalog.json")
            )
            da = all_paths_path.parent / "decision_points_analysis.json"
            cb = all_paths_path.parent / "canonical_branch_chronological.json"
            br = all_paths_path.parent / "branches.json"
            sources_ok = da.is_file() and cb.is_file() and br.is_file()
            if not rebuild_player_choice_catalog and cat_path.is_file():
                try:
                    choice_catalog = load_player_choice_catalog(cat_path)
                    print(f"[dim]已加载 player_choice 目录：{cat_path}[/dim]")
                except Exception as e:
                    print(f"[yellow]加载 player_choice 目录失败，将尝试重建：{e}[/yellow]")
                    choice_catalog = None
            if choice_catalog is None and sources_ok:
                try:
                    print(
                        "[dim]player_choice 目录：使用与内容增强相同的文学创作模型（默认 gemini-2.5-flash）[/dim]"
                    )
                    choice_catalog = await generator.build_player_choice_catalog(da, cb, br)
                    save_player_choice_catalog(cat_path, choice_catalog)
                    n_pts = len(choice_catalog.get("by_fork_event_id") or {})
                    print(
                        f"[green]已生成并保存 player_choice 目录：{cat_path}（{n_pts} 个分叉）[/green]"
                    )
                except Exception as e:
                    print(
                        f"[yellow]构建 player_choice 目录失败，决策点将逐路径调 LLM：{e}[/yellow]"
                    )
                    choice_catalog = None
            elif choice_catalog is None and not sources_ok:
                print(
                    "[dim]未找到 decision_points_analysis / canonical_branch_chronological / branches，"
                    "跳过 player_choice 预生成（决策点仍逐路径 LLM）[/dim]"
                )
        generator._player_choice_catalog = choice_catalog

        if refresh_player_choice_only:
            # 仅刷新决策点选项：尽量复用已有 enhanced 内容
            print("[cyan]仅刷新决策点选项：将尽量复用已润色内容，仅更新 player_choice/jump_target[/cyan]")

            # 确保 results_by_label 至少有一份可写回的路径数据
            for p in path_list:
                label = p.get("renpy_label") or p.get("path_id", "unknown").replace("/", "_").replace("\\", "_")
                if label in results_by_label:
                    continue
                cache_file = out_path / f"{label}.json"
                if cache_file.exists():
                    try:
                        with open(cache_file, "r", encoding="utf-8") as f:
                            results_by_label[label] = json.load(f)
                        continue
                    except Exception:
                        pass
                # 没有 enhanced 缓存：用 all_paths_completed 的原始 path_data 兜底写一份（后续至少能把选项补上）
                results_by_label[label] = dict(p)

            # 刷新每条路径的 decision_point.player_choice
            for p in path_list:
                label = p.get("renpy_label") or p.get("path_id", "unknown").replace("/", "_").replace("\\", "_")
                enhanced = results_by_label.get(label) or dict(p)
                events = enhanced.get("events") or []
                if not isinstance(events, list):
                    events = []
                changed_any = False
                for ev in events:
                    if not isinstance(ev, dict):
                        continue
                    if ev.get("type") != "decision_point":
                        continue
                    pc = await generator.generate_decision_point_choice(ev, p, all_paths_map_full)
                    if not pc:
                        continue
                    ev["player_choice"] = pc
                    # 同步 content_blocks（若存在 choice block）
                    blocks = ev.get("content_blocks")
                    if isinstance(blocks, list) and blocks and isinstance(blocks[0], dict) and blocks[0].get("type") == "choice":
                        blocks[0]["prompt"] = pc.get("prompt", blocks[0].get("prompt", "你打算怎么做？"))
                        blocks[0]["options"] = pc.get("options", blocks[0].get("options", []))
                    elif not blocks:
                        ev["content_blocks"] = [
                            {
                                "type": "choice",
                                "prompt": pc.get("prompt", "你打算怎么做？"),
                                "options": pc.get("options", []),
                            }
                        ]
                    changed_any = True

                if changed_any:
                    enhanced["events"] = events
                results_by_label[label] = enhanced

            # 按 path_list 顺序写回（与原逻辑一致）
            ordered = [
                results_by_label[p.get("renpy_label") or p.get("path_id", "unknown").replace("/", "_").replace("\\", "_")]
                for p in path_list
            ]
            for p_out in ordered:
                label = p_out.get("renpy_label") or p_out.get("path_id", "unknown").replace("/", "_").replace("\\", "_")
                with open(out_path / f"{label}.json", "w", encoding="utf-8") as f:
                    json.dump(p_out, f, ensure_ascii=False, indent=2)
            print(f"[green]已写入 {len(ordered)} 个文件到 {out_path}[/green]")
            return

        if to_generate:
            choice_catalog = None
            if not skip_player_choice_catalog:
                cat_path = (
                    _resolve_under_project_root(project_root, player_choice_catalog)
                    if player_choice_catalog
                    else (all_paths_path.parent / "player_choice_catalog.json")
                )
                da = all_paths_path.parent / "decision_points_analysis.json"
                cb = all_paths_path.parent / "canonical_branch_chronological.json"
                br = all_paths_path.parent / "branches.json"
                sources_ok = da.is_file() and cb.is_file() and br.is_file()
                if not rebuild_player_choice_catalog and cat_path.is_file():
                    try:
                        choice_catalog = load_player_choice_catalog(cat_path)
                        print(f"[dim]已加载 player_choice 目录：{cat_path}[/dim]")
                    except Exception as e:
                        print(f"[yellow]加载 player_choice 目录失败，将尝试重建：{e}[/yellow]")
                        choice_catalog = None
                if choice_catalog is None and sources_ok:
                    try:
                        print(
                            "[dim]player_choice 目录：使用与内容增强相同的文学创作模型（默认 gemini-2.5-flash）[/dim]"
                        )
                        choice_catalog = await generator.build_player_choice_catalog(
                            da, cb, br
                        )
                        save_player_choice_catalog(cat_path, choice_catalog)
                        n_pts = len(choice_catalog.get("by_fork_event_id") or {})
                        print(
                            f"[green]已生成并保存 player_choice 目录：{cat_path}（{n_pts} 个分叉）[/green]"
                        )
                    except Exception as e:
                        print(
                            f"[yellow]构建 player_choice 目录失败，决策点将逐路径调 LLM：{e}[/yellow]"
                        )
                        choice_catalog = None
                elif choice_catalog is None and not sources_ok:
                    print(
                        "[dim]未找到 decision_points_analysis / canonical_branch_chronological / branches，"
                        "跳过 player_choice 预生成（决策点仍逐路径 LLM）[/dim]"
                    )
            canonical_enhanced = results_by_label.get("canonical_path")
            # 若需生成且主线尚未在缓存中，先单独生成主线，供分支路径按 event_id 前缀复用
            if canonical_enhanced is None and any(
                p.get("path_id") == "canonical_path" or p.get("renpy_label") == "canonical_path"
                for p in to_generate
            ):
                canonical_path_data = next(
                    (p for p in to_generate if p.get("path_id") == "canonical_path" or p.get("renpy_label") == "canonical_path"),
                    None,
                )
                if canonical_path_data:
                    # 用全量 all_paths_map_full 生成主线，确保决策点能看到所有 choice 并补齐 jump_target。
                    canonical_out = await generator.generate_path_content(
                        canonical_path_data,
                        all_paths_map_full,
                        canonical_enhanced_path_data=None,
                        narrative_perspective=narrative_perspective or "第三人称",
                        segment_cache=None,
                    )
                    results_by_label["canonical_path"] = canonical_out
                    to_generate = [p for p in to_generate if p.get("path_id") != "canonical_path" and p.get("renpy_label") != "canonical_path"]
                    canonical_enhanced = results_by_label["canonical_path"]
            if to_generate:
                generated = await generator.generate_all_paths_content(
                    to_generate,
                    canonical_enhanced_path_data=canonical_enhanced,
                    narrative_perspective=narrative_perspective,
                    max_concurrent=max_concurrent,
                )
                for p in generated:
                    results_by_label[p.get("renpy_label") or p.get("path_id", "unknown").replace("/", "_").replace("\\", "_")] = p
        else:
            print("[green]所有路径在输出目录中已有完整增强结果，跳过 LLM 生成。[/green]")

        # 按 path_list 顺序产出结果并写回
        ordered = [results_by_label[p.get("renpy_label") or p.get("path_id", "unknown").replace("/", "_").replace("\\", "_")] for p in path_list]
        for p in ordered:
            label = p.get("renpy_label") or p.get("path_id", "unknown").replace("/", "_").replace("\\", "_")
            with open(out_path / f"{label}.json", "w", encoding="utf-8") as f:
                json.dump(p, f, ensure_ascii=False, indent=2)
        print(f"[green]已写入 {len(ordered)} 个文件到 {out_path}[/green]")

    # 兼容：在某些宿主环境（例如 Streamlit / 已启动事件循环的上下文）中，
    # 直接 asyncio.run 会抛出 “cannot be called from a running event loop”。
    # 这里改为：若检测到已有 running loop，则在新线程中 asyncio.run 并阻塞等待完成。
    try:
        asyncio.get_running_loop()
        _has_running = True
    except RuntimeError:
        _has_running = False

    if not _has_running:
        asyncio.run(run())
    else:
        import threading

        err: list[BaseException] = []

        def _runner():
            try:
                asyncio.run(run())
            except BaseException as e:
                err.append(e)

        t = threading.Thread(target=_runner, daemon=True)
        t.start()
        t.join()
        if err:
            raise err[0]


@app.command("complete-all-path-events")
def complete_all_path_events_cmd(
    input_dir: str = typer.Option("out/all_paths", "--input", "-i", help="all_paths 目录"),
    output_dir: str = typer.Option("out/all_paths_completed", "--output", "-o", help="输出目录（默认 out/all_paths_completed）"),
) -> None:
    """
    用 metadata.path_combination 补全 all_paths 下各 JSON 的 events。

    在 ``generate-all-paths`` 产出 ``all_paths`` 之后运行（流水线中为独立一步）。

    从 E1 到最后一个事件按 event_id 检查；若中间缺了某个（如 E13），
    就到 metadata.path_combination 里找对应 fork_event_id 的条目，补上一个事件。
    """
    project_root = Path(__file__).resolve().parent.parent
    if str(project_root) not in sys.path:
        sys.path.insert(0, str(project_root))
    try:
        from scripts.complete_all_paths_events import run
    except ImportError as e:
        print(f"[red]无法导入 scripts.complete_all_paths_events: {e}[/red]")
        raise SystemExit(1)
    run(input_dir=input_dir, output_dir=output_dir, project_root=project_root)


@app.command("generate-renpy-scripts")
def generate_renpy_scripts_cmd(
    enhanced_paths: str = typer.Option(
        "out/enhanced_paths",
        "--enhanced-paths",
        "--input",
        "-i",
        help="增强路径 JSON 目录（与 generate-content 的 --output 一致）",
    ),
    personas: str = typer.Option(
        "out/character_personas.json",
        "--personas",
        "-p",
        help="角色人设 JSON",
    ),
    game_dir: str = typer.Option(
        "wangfo/game",
        "--game-dir",
        "-g",
        help="Ren'Py 工程内的 game 目录（Launcher「创建工程」后选该工程下的 game 文件夹；默认本仓库 wangfo/game）",
    ),
    entities: Optional[str] = typer.Option(
        None,
        "--entities",
        "-e",
        help="entities.json；省略则若存在 <项目根>/out/entities.json 则使用",
    ),
) -> None:
    """
    从增强结果生成 Ren'Py 脚本（不调用 LLM）

    读取 ``enhanced_paths`` 下的路径 JSON，写入 ``--game-dir`` 所指 **game** 目录
    （characters.rpy、paths/*.rpy、endings/*.rpy、script.rpy）。

    **不替代 Launcher 建工程**：请先在 Ren'Py Launcher 中 Create New Project，再将 ``--game-dir``
    设为该工程下的 ``game`` 文件夹，相当于把剧本导入该工程。本仓库默认 ``wangfo/game`` 已是完整工程。

    内容增强请先运行 ``generate-content``；本命令只负责导出剧本。
    """
    project_root = Path(__file__).resolve().parent.parent
    sys.path.insert(0, str(project_root))
    try:
        from scripts.generate_renpy_scripts import generate_content_and_scripts
    except ImportError as e:
        print(f"[red]无法导入 scripts.generate_renpy_scripts: {e}[/red]")
        print("[yellow]请从项目根目录运行，例如: python -m src.cli generate-renpy-scripts[/yellow]")
        raise SystemExit(1)
    ep = _resolve_under_project_root(project_root, enhanced_paths)
    pf = _resolve_under_project_root(project_root, personas)
    gd = _resolve_under_project_root(project_root, game_dir)
    ef: Optional[Path] = None
    if entities and str(entities).strip():
        ef = _resolve_under_project_root(project_root, str(entities).strip())
    generate_content_and_scripts(
        project_root=project_root,
        enhanced_dir=ep,
        personas_file=pf,
        wangfo_game_dir=gd,
        entities_file=ef,
    )


@app.command("import-assets")
def import_assets_cmd(
    game_dir: Optional[str] = typer.Option(
        None,
        "--game-dir",
        "-g",
        help="Ren'Py 工程的 game 目录；默认 <项目根>/wangfo/game",
    ),
    enhanced_paths: str = typer.Option(
        "out/enhanced_paths",
        "--enhanced-paths",
        "--input",
        "-i",
        help="增强路径 JSON 目录（须与 generate-content / generate-renpy-scripts 一致；末尾会据此重写 paths 等剧本）",
    ),
    personas: str = typer.Option(
        "out/character_personas.json",
        "--personas",
        "-p",
        help="角色人设 JSON",
    ),
    entities: Optional[str] = typer.Option(
        None,
        "--entities",
        "-e",
        help="entities.json；省略则若存在 <项目根>/out/entities.json 则使用",
    ),
) -> None:
    """
    导入游戏资源并写入场景映射与剧本

    从项目根运行：立绘（*-立绘生成-remove-preview.png）、豆包场景（xxx (n).png）、
    事件→场景映射（event_scene_map.json）、游戏音乐（Japanese Music Pack 内 Voice Of Evening），
    并触发 Ren'Py 脚本生成（paths/endings 中的 scene 语句）。需在项目根目录执行。

    末尾调用的剧本生成与 ``generate-renpy-scripts`` 使用同一套路径参数；若此处仍用默认
    ``out/enhanced_paths`` 而你的内容在 ``runs/ui/<run_id>/out/enhanced_paths``，会覆盖成错误剧情。
    """
    project_root = Path(__file__).resolve().parent.parent
    if str(project_root) not in sys.path:
        sys.path.insert(0, str(project_root))
    try:
        import import_assets
    except ImportError as e:
        print(f"[red]无法导入 import_assets: {e}[/red]")
        print("[yellow]请从项目根目录运行，例如: python -m src.cli import-assets[/yellow]")
        raise SystemExit(1)
    ep = _resolve_under_project_root(project_root, enhanced_paths)
    pf = _resolve_under_project_root(project_root, personas)
    ef: Optional[Path] = None
    if entities and str(entities).strip():
        ef = _resolve_under_project_root(project_root, str(entities).strip())
    import_assets.main(
        game_dir=game_dir,
        enhanced_dir=ep,
        personas_file=pf,
        entities_file=ef,
    )


@app.command("recognize-entities")
def recognize_entities_cmd(
    entities_path: str = typer.Option("out/entities.json", "--entities", "-e", help="entities.json 路径"),
    paths_dir: str = typer.Option("out/enhanced_paths", "--paths", "-p", help="扫描的路径目录（enhanced_paths）"),
) -> None:
    """
    从 entities.json 识别实体，并扫描路径下对话中的说者，输出已识别与未识别的说者列表。
    便于知道「用什么」：未识别的说者需加入 entities 或作为某实体的别名，才能在脚本中正确映射。
    """
    project_root = Path(__file__).resolve().parent.parent
    sys.path.insert(0, str(project_root))
    try:
        from src.entity_resolver import (
            load_entities,
            report_speakers_entities,
        )
    except ImportError as e:
        print(f"[red]无法导入 entity_resolver: {e}[/red]")
        raise SystemExit(1)
    e_path = project_root / entities_path
    p_dir = project_root / paths_dir
    entities = load_entities(str(e_path))
    print("[cyan]=== 实体列表 (entities.json) ===[/cyan]")
    print(f"共 {len(entities)} 个实体\n")
    for e in entities:
        name = e.get("canonical_name", "")
        eid = e.get("id", "")
        aliases = e.get("aliases") or []
        typ = e.get("entity_type", "")
        print(f"  {eid}  {name}  [{typ}]  别名: {aliases}")
    print("\n[cyan]=== 对话说者识别结果 ===[/cyan]\n")
    resolved, unresolved = report_speakers_entities(str(p_dir), str(e_path))
    print("[green]已识别（在 entities 中有对应）：[/green]")
    for speaker, info in sorted(resolved.items()):
        canonical = info.get("canonical_name", "")
        eid = info.get("id", "")
        print(f"  “{speaker}” -> 实体 {eid} ({canonical})")
    print("\n[yellow]未识别（建议加入 entities 或作为某实体的别名）：[/yellow]")
    for speaker, count in sorted(unresolved.items(), key=lambda x: -x[1]):
        print(f"  “{speaker}”  出现 {count} 次")
    print("")


@app.command("visualize-langgraph")
def visualize_langgraph_cmd(
    output_path: str = typer.Option("out/langgraph_visualization.png", "--output", "-o", help="输出图片路径"),
    format: str = typer.Option("png", "--format", "-f", help="输出格式（png, mermaid, json）"),
) -> None:
    """
    可视化 LangGraph 工作流结构
    
    生成路径生成工作流的可视化图表，支持多种格式。
    """
    try:
        from core.llm_client import AsyncLLMClient
        from state_manager.state_applier import StateApplier
        from branch.generation.path_generation_functions import PathGenerationFunctions
        # LangGraph 工作流入口现在在 langgraph.workflow 中
        from branch.generation.langgraph.workflow import create_path_generation_graph
        from pathlib import Path
        
        print("[cyan]正在生成 LangGraph 可视化...[/cyan]")
        
        # 创建临时实例来生成图
        llm_client = AsyncLLMClient.create_default()
        state_applier = StateApplier()
        path_functions = PathGenerationFunctions(
            llm_client=llm_client,
            flow_llm_client=llm_client,
            state_applier=state_applier,
        )
        
        # 创建图
        graph = create_path_generation_graph(path_functions)
        if not graph:
            print("[red]无法创建 LangGraph 工作流[/red]")
            return
        
        # 获取图结构
        graph_structure = graph.get_graph()
        
        output_file = Path(output_path)
        output_file.parent.mkdir(parents=True, exist_ok=True)
        
        if format.lower() == "png":
            # 生成 PNG 图片
            try:
                png_data = graph_structure.draw_mermaid_png()
                with open(output_file, "wb") as f:
                    f.write(png_data)
                print(f"[green]✅ 可视化图表已保存到: {output_path}[/green]")
            except Exception as e:
                print(f"[yellow]⚠️  生成 PNG 失败: {e}[/yellow]")
                print("[yellow]提示：可能需要安装 graphviz: pip install pygraphviz[/yellow]")
                # 尝试生成 Mermaid 格式
                format = "mermaid"
        
        if format.lower() == "mermaid":
            # 生成 Mermaid 格式
            try:
                mermaid_code = graph_structure.draw_mermaid()
                output_file = output_file.with_suffix(".mmd")
                with open(output_file, "w", encoding="utf-8") as f:
                    f.write(mermaid_code)
                print(f"[green]✅ Mermaid 图表已保存到: {output_file}[/green]")
                print("[cyan]提示：可以在 https://mermaid.live/ 查看 Mermaid 图表[/cyan]")
            except Exception as e:
                print(f"[red]生成 Mermaid 失败: {e}[/red]")
        
        if format.lower() == "json":
            # 生成 JSON 格式（图结构）
            try:
                import json
                graph_dict = {
                    "nodes": [{"id": node.id, "data": str(node.data)} for node in graph_structure.nodes],
                    "edges": [{"source": edge.source, "target": edge.target} for edge in graph_structure.edges],
                }
                output_file = output_file.with_suffix(".json")
                with open(output_file, "w", encoding="utf-8") as f:
                    json.dump(graph_dict, f, ensure_ascii=False, indent=2)
                print(f"[green]✅ 图结构 JSON 已保存到: {output_file}[/green]")
            except Exception as e:
                print(f"[red]生成 JSON 失败: {e}[/red]")
        
        # 显示图的基本信息
        print(f"\n[cyan]图结构信息：[/cyan]")
        print(f"  节点数: {len(graph_structure.nodes)}")
        print(f"  边数: {len(graph_structure.edges)}")
        print(f"  节点列表: {[node.id for node in graph_structure.nodes]}")
        
    except ImportError as e:
        print(f"[red]导入失败: {e}[/red]")
        print("[yellow]提示：请确保已安装 langgraph[/yellow]")
    except Exception as e:
        print(f"[red]生成可视化失败: {e}[/red]")
        import traceback
        traceback.print_exc()


@app.command("langgraph-studio")
def langgraph_studio_cmd() -> None:
    """
    启动 LangGraph Studio（如果已安装）
    
    注意：需要先安装 langgraph-cli: pip install langgraph-cli
    然后创建配置文件 langgraph.json
    """
    import subprocess
    import sys
    import json
    from pathlib import Path
    
    print("[cyan]检查 LangGraph Studio 环境...[/cyan]")
    
    # 检查是否安装了 langgraph-cli
    # 方法1: 尝试导入 langgraph_cli 模块
    langgraph_cli_installed = False
    try:
        import importlib.util
        spec = importlib.util.find_spec("langgraph_cli")
        if spec is not None:
            langgraph_cli_installed = True
            print("[green]✅ langgraph-cli 已安装[/green]")
    except Exception:
        pass
    
    # 方法2: 如果方法1失败，尝试使用 pkg_resources 检查
    if not langgraph_cli_installed:
        try:
            import pkg_resources
            pkg_resources.get_distribution("langgraph-cli")
            langgraph_cli_installed = True
            print("[green]✅ langgraph-cli 已安装[/green]")
        except Exception:
            pass
    
    if not langgraph_cli_installed:
        print("[yellow]⚠️  langgraph-cli 未安装[/yellow]")
        print("[yellow]安装命令: pip install langgraph-cli 或 uv pip install langgraph-cli[/yellow]")
        return
    
    # 检查是否安装了 langgraph-api（Studio 需要）
    langgraph_api_installed = False
    try:
        import importlib.util
        spec = importlib.util.find_spec("langgraph_api")
        if spec is not None:
            langgraph_api_installed = True
    except Exception:
        pass
    
    if not langgraph_api_installed:
        try:
            import pkg_resources
            pkg_resources.get_distribution("langgraph-api")
            langgraph_api_installed = True
        except Exception:
            pass
    
    if not langgraph_api_installed:
        print("[yellow]⚠️  langgraph-api 未安装（Studio 需要此依赖）[/yellow]")
        print("[cyan]正在尝试安装 langgraph-cli[inmem]...[/cyan]")
        try:
            import subprocess
            install_cmd = [sys.executable, "-m", "pip", "install", "-U", "langgraph-cli[inmem]"]
            print(f"[cyan]执行: {' '.join(install_cmd)}[/cyan]")
            result = subprocess.run(install_cmd, check=True, capture_output=True, text=True)
            print("[green]✅ langgraph-cli[inmem] 安装成功[/green]")
        except subprocess.CalledProcessError as e:
            print(f"[red]❌ 自动安装失败: {e}[/red]")
            print("[yellow]请手动安装: pip install -U \"langgraph-cli[inmem]\" 或 uv pip install \"langgraph-cli[inmem]\"[/yellow]")
            return
        except Exception as e:
            print(f"[red]❌ 安装过程出错: {e}[/red]")
            print("[yellow]请手动安装: pip install -U \"langgraph-cli[inmem]\" 或 uv pip install \"langgraph-cli[inmem]\"[/yellow]")
            return
    
    # 检查配置文件
    config_file = Path("langgraph.json")
    if not config_file.exists():
        print("[yellow]⚠️  未找到 langgraph.json 配置文件[/yellow]")
        print("[cyan]正在创建示例配置文件...[/cyan]")
        
        # 创建示例配置
        config_content = """{
  "graphs": {
    "path_generation": {
      "path": "src.branch.generation.path_generation_langgraph:create_path_generation_graph",
      "description": "Path generation workflow"
    }
  },
  "dependencies": [
    "src"
  ]
}"""
        # 确保以 UTF-8 编码写入文件（无 BOM，纯 ASCII 兼容）
        # 注意：langgraph-cli 在 Windows 上可能使用系统默认编码读取，所以避免使用中文
        # 使用 json.dump 写入二进制模式，确保无 BOM
        config_dict = json.loads(config_content)
        with open(config_file, "wb") as f:
            content = json.dumps(config_dict, indent=2, ensure_ascii=True)
            f.write(content.encode("utf-8"))
        
        # 验证文件可以正确读取
        try:
            with open(config_file, "r", encoding="utf-8") as f:
                json.load(f)
        except Exception as e:
            print(f"[yellow]⚠️  配置文件验证失败: {e}[/yellow]")
            print("[yellow]提示：请手动检查 langgraph.json 文件编码[/yellow]")
        print(f"[green]✅ 已创建配置文件: {config_file}[/green]")
        print("[yellow]⚠️  请根据实际情况修改配置文件[/yellow]")
    
    # 启动 LangGraph Studio
    print("[cyan]启动 LangGraph Studio...[/cyan]")
    print("[cyan]Studio 将在浏览器中打开，通常是 http://localhost:8123[/cyan]")
    
    # 尝试找到 langgraph 命令
    import shutil
    langgraph_cmd = shutil.which("langgraph")
    if langgraph_cmd:
        # 如果找到了 langgraph 命令，直接使用
        # 添加 --allow-blocking 标志，允许开发环境中的阻塞操作
        cmd = [langgraph_cmd, "dev", "--allow-blocking"]
        print(f"[cyan]使用命令: {' '.join(cmd)}[/cyan]")
        print("[yellow]提示：使用 --allow-blocking 标志允许开发环境中的阻塞操作[/yellow]")
    else:
        # 否则使用 python -m langgraph_cli
        cmd = [sys.executable, "-m", "langgraph_cli", "dev", "--allow-blocking"]
        print(f"[cyan]使用命令: {' '.join(cmd)}[/cyan]")
        print("[yellow]提示：使用 --allow-blocking 标志允许开发环境中的阻塞操作[/yellow]")
    
    try:
        subprocess.run(cmd, check=True)
    except subprocess.CalledProcessError as e:
        print(f"[red]启动失败: {e}[/red]")
        # 检查是否是缺少 langgraph-api 的错误
        error_output = str(e)
        if "langgraph-api" in error_output or "Required package" in error_output:
            print("[yellow]⚠️  缺少 langgraph-api 依赖[/yellow]")
            print("[yellow]请运行: pip install -U \"langgraph-cli[inmem]\" 或 uv pip install \"langgraph-cli[inmem]\"[/yellow]")
        else:
            print("[yellow]提示：请检查配置文件是否正确[/yellow]")
            print("[yellow]配置文件路径: langgraph.json[/yellow]")
    except KeyboardInterrupt:
        print("\n[yellow]已停止 LangGraph Studio[/yellow]")
    except FileNotFoundError:
        print("[red]❌ 找不到 langgraph 命令[/red]")
        print("[yellow]请确保已正确安装 langgraph-cli[/yellow]")
        print("[yellow]安装命令: pip install langgraph-cli 或 uv pip install langgraph-cli[/yellow]")


@app.command("init-state-baseline")
def init_state_baseline_cmd(
    world_states_path: str = typer.Option(None, "--world-states", "-w", help="世界状态 JSON 文件路径（默认：out/world_states.json）"),
    character_states_path: str = typer.Option(None, "--character-states", "-c", help="人物状态 JSON 文件路径（默认：out/character_states.json）"),
    relationship_states_path: str = typer.Option(None, "--relationship-states", "-r", help="关系状态 JSON 文件路径（默认：out/relationship_states.json）"),
    output_path: str = typer.Option(None, "--output", "-o", help="输出 JSON 文件路径（默认：out/state_baseline.json）"),
) -> None:
    """初始化状态变量 baseline 🎯✨"""
    import sys
    from state_manager.init_state_baseline import main
    
    # 设置命令行参数（如果提供了路径）
    original_argv = sys.argv.copy()
    try:
        args = []
        if world_states_path:
            args.append(world_states_path)
        if character_states_path:
            args.append(character_states_path)
        if relationship_states_path:
            args.append(relationship_states_path)
        if output_path:
            args.append(output_path)
        
        sys.argv = ["init_state_baseline"] + args
        
        main()
    finally:
        sys.argv = original_argv


@app.command("apply-states")
def apply_states_cmd(
    events: str = typer.Option(None, "--events", "-e", help="要处理的事件ID列表（用逗号分隔，如果不提供，会处理所有事件）"),
    baseline_path: str = typer.Option("out/state_baseline.json", "--baseline", "-b", help="状态基线文件路径"),
    personas_path: str = typer.Option("out/character_personas.json", "--personas", "-p", help="静态人设文件路径"),
    output_dir: str = typer.Option("out", "--output", "-o", help="输出目录"),
    max_concurrent: int = typer.Option(5, "--max-concurrent", "-c", help="最大并发数"),
) -> None:
    """应用状态变化并管理内容 🎯✨（通过扫描事件知识图谱，使用 LLM function calling 更新状态）"""
    import asyncio
    from state_manager.scripts.apply_states import main_async
    
    # 解析事件ID列表
    event_ids = None
    if events:
        event_ids = [e.strip() for e in events.split(",") if e.strip()]
    
    asyncio.run(main_async(
        event_ids=event_ids,
        baseline_path=baseline_path,
        personas_path=personas_path,
        output_dir=output_dir,
        max_concurrent=max_concurrent,
    ))


# 交互式 Demo 菜单：按生成流程顺序列出的步骤（供毕设/开源用户逐步执行）
DEMO_STAGE_HEADERS = {
    "1": "阶段一：知识图谱构建",
    "5": "阶段二：角色建模与状态管理",
    "10": "阶段三：分支路径生成",
    "16": "阶段四：内容增强",
    "17": "阶段五：游戏生成",
}

DEMO_STEPS = [
    ("1", "extract-events", "从文本提取事件"),
    ("2", "extract-relations", "从事件中提取关系"),
    ("3", "import-neo4j", "导入数据到 Neo4j"),
    ("4", "init-indexes", "初始化 Neo4j 索引与约束"),
    ("5", "generate-mentions-and-entities", "生成 Mention 表并生成实体（别名映射）"),
    ("6", "extract-states-and-baseline", "抽取世界/人物/关系状态并初始化 state baseline"),
    ("7", "import-entities", "导入实体到 Neo4j"),
    ("8", "import-state-changes", "导入状态变化到 Neo4j"),
    ("9", "aggregate-characters-and-personas", "聚合人物画像并生成静态人设"),
    ("10", "generate-canonical-branch", "生成主线（Canonical Branch）"),
    ("11", "analyze-decision-points", "分析决策点"),
    ("12", "determine-ending-candidates", "确定结局候选"),
    ("13", "generate-branch", "生成支线"),
    ("14", "generate-all-paths", "生成所有路径（LangGraph）→ all_paths"),
    ("15", "complete-all-path-events", "补全路径事件 → all_paths_completed"),
    ("16", "generate-content", "内容增强 → enhanced_paths"),
    ("17", "generate-renpy-scripts", "生成 Ren'Py 脚本（若未在 import-assets 中执行可单独运行）"),
    ("18", "import-assets", "导入立绘/场景/音乐与事件→场景映射，并生成剧本"),
]


@app.command("demo-guide")
def demo_guide_cmd() -> None:
    """
    打印答辩展示路线与关键检查点，不执行生成流程。
    """
    print("[bold cyan]毕设答辩展示建议[/bold cyan]\n")
    print("[green]最短 5 分钟路线[/green]")
    print("  1. 先打开 wangfo/ 运行最终游戏，展示 1 条主线 + 1 个分支")
    print("  2. 回到终端展示 python -m src.cli demo 的五阶段菜单")
    print("  3. 打开 3 类中间产物：事件 JSON、分支结果、Ren'Py 脚本")
    print("  4. 最后展示统计结果与验证结论\n")

    print("[green]完整流水线检查点[/green]")
    print("  - 阶段一输出：out/extract_chain.json、out/extract_relations.json")
    print("  - 阶段二输出：out/entities.json、out/state_baseline.json")
    print("  - 阶段三输出：out/canonical_branch.json、out/branches.json、out/all_paths_completed/")
    print("  - 阶段四输出：out/enhanced_paths/")
    print("  - 阶段五输出：wangfo/game/ 下的 paths/*.rpy、endings/*.rpy、characters.rpy\n")

    print("[green]推荐答辩前命令[/green]")
    print("  python -m src.cli validate-outputs --mode all")
    print("  python -m src.cli collect-metrics --write-markdown docs/答辩实验结果.md")
    print("  python -m src.cli extract-story-sample --title 马尔戈的微笑")
    print("")


@app.command("validate-outputs")
def validate_outputs_cmd(
    mode: str = typer.Option("all", "--mode", "-m", help="验证范围：game, pipeline, all"),
) -> None:
    """验证答辩演示所需的关键产物。"""
    project_root = Path(__file__).resolve().parent.parent
    sys.path.insert(0, str(project_root))
    try:
        from scripts.validate_pipeline_outputs import print_results, validate_game_dir, validate_pipeline
    except ImportError as e:
        print(f"[red]无法导入 scripts.validate_pipeline_outputs: {e}[/red]")
        raise SystemExit(1)

    if mode not in {"game", "pipeline", "all"}:
        print("[red]mode 仅支持 game、pipeline、all[/red]")
        raise SystemExit(1)

    results = []
    if mode in {"game", "all"}:
        results.extend(validate_game_dir(project_root))
    if mode in {"pipeline", "all"}:
        results.extend(validate_pipeline(project_root))

    failures = print_results(results)
    if failures:
        print(f"\n[red]验证完成：存在 {failures} 项未通过。[/red]")
        raise SystemExit(1)

    print("\n[green]验证完成：全部通过。[/green]")


@app.command("evaluate-solution")
def evaluate_solution_cmd(
    write_json: str = typer.Option("out/solution_evaluation.json", "--write-json", help="评估结果 JSON 输出路径"),
    write_markdown: str = typer.Option("", "--write-markdown", help="可选：评估结果 Markdown 输出路径"),
) -> None:
    """量化评估“小说抽取 -> 文游脚本”方案表现。"""
    project_root = Path(__file__).resolve().parent.parent
    sys.path.insert(0, str(project_root))
    try:
        from scripts.review.evaluate_solution import evaluate_solution, report_to_markdown
    except ImportError as e:
        print(f"[red]无法导入 scripts.evaluate_solution: {e}[/red]")
        raise SystemExit(1)

    report = evaluate_solution(project_root)
    import json

    print(json.dumps(report, ensure_ascii=False, indent=2))

    if write_json:
        output_path = project_root / write_json
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        print(f"[green]已写入 JSON: {output_path}[/green]")

    if write_markdown:
        output_path = project_root / write_markdown
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_text(report_to_markdown(report), encoding="utf-8")
        print(f"[green]已写入 Markdown: {output_path}[/green]")


@app.command("collect-metrics")
def collect_metrics_cmd(
    write_json: str = typer.Option("", "--write-json", help="可选：写入 JSON 结果文件"),
    write_markdown: str = typer.Option("", "--write-markdown", help="可选：写入 Markdown 结果文件"),
) -> None:
    """收集答辩与论文中使用的静态统计指标。"""
    project_root = Path(__file__).resolve().parent.parent
    sys.path.insert(0, str(project_root))
    try:
        from scripts.collect_project_metrics import collect_metrics, metrics_to_markdown
    except ImportError as e:
        print(f"[red]无法导入 scripts.collect_project_metrics: {e}[/red]")
        raise SystemExit(1)

    metrics = collect_metrics(project_root)
    print(metrics_to_markdown(metrics))

    if write_json:
        output_path = project_root / write_json
        output_path.parent.mkdir(parents=True, exist_ok=True)
        import json
        output_path.write_text(json.dumps(metrics, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        print(f"[green]已写入 JSON: {output_path}[/green]")

    if write_markdown:
        output_path = project_root / write_markdown
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_text(metrics_to_markdown(metrics), encoding="utf-8")
        print(f"[green]已写入 Markdown: {output_path}[/green]")


@app.command("extract-story-sample")
def extract_story_sample_cmd(
    title: str = typer.Option("马尔戈的微笑", "--title", "-t", help="要抽取的故事标题"),
    input_path: str = typer.Option("docs/epub_to_txt/东方奇观.txt", "--input", "-i", help="合集文本路径"),
    output_path: str = typer.Option("docs/validation_samples/马尔戈的微笑.txt", "--output", "-o", help="输出样本文本路径"),
) -> None:
    """从合集文本中抽取单篇故事，作为第二文本轻量验证样本。"""
    project_root = Path(__file__).resolve().parent.parent
    sys.path.insert(0, str(project_root))
    try:
        from scripts.extract_story_sample import extract_story
    except ImportError as e:
        print(f"[red]无法导入 scripts.extract_story_sample: {e}[/red]")
        raise SystemExit(1)

    source_path = project_root / input_path
    target_path = project_root / output_path
    full_text = source_path.read_text(encoding="utf-8")
    story_text = extract_story(full_text, title)
    target_path.parent.mkdir(parents=True, exist_ok=True)
    target_path.write_text(story_text, encoding="utf-8")
    print(f"[green]已抽取《{title}》到: {target_path}[/green]")


@app.command("demo")
def demo_cmd() -> None:
    """
    交互式生成流程菜单：按顺序选择并执行各步骤，便于毕设展示与开源使用。

    请从项目根目录运行：python -m src.cli demo
    """
    import subprocess
    project_root = Path(__file__).resolve().parent.parent
    print("[bold cyan]文字冒险游戏内容生成 — 分步执行菜单[/bold cyan]")
    print("[dim]（从项目根运行。每步会单独执行对应 CLI 命令）[/dim]\n")
    while True:
        for num, cmd, desc in DEMO_STEPS:
            stage_header = DEMO_STAGE_HEADERS.get(num)
            if stage_header:
                print(f"[bold white]{stage_header}[/bold white]")
            print(f"  [green]{num:>2}[/green]. [cyan]{cmd}[/cyan] — {desc}")
        print("  [green] 0[/green]. 退出")
        try:
            choice = input("\n请输入步骤编号 (0 退出): ").strip()
        except (EOFError, KeyboardInterrupt):
            print("\n再见。")
            return
        if choice == "0" or not choice:
            print("再见。")
            return
        found = None
        for num, cmd, desc in DEMO_STEPS:
            if choice == num:
                found = (cmd, desc)
                break
        if not found:
            print("[yellow]无效编号，请重试。[/yellow]")
            continue
        cmd_name, desc = found
        print(f"\n[cyan]>>> 执行: {cmd_name}[/cyan] ({desc})\n")
        try:
            ret = subprocess.run(
                [sys.executable, "-m", "src.cli", cmd_name],
                cwd=str(project_root),
            )
            if ret.returncode != 0:
                print(f"\n[red]步骤返回非零: {ret.returncode}[/red]")
            else:
                print(f"\n[green]步骤完成。[/green]")
        except Exception as e:
            print(f"\n[red]执行失败: {e}[/red]")
        print()


@app.command()
def list_commands() -> None:
    """列出所有可用命令 📋"""
    print("[bold cyan]可用命令：[/bold cyan]\n")
    print("  [green]extract-events[/green]     - 从文本提取事件")
    print("  [green]extract-relations[/green]  - 从事件中提取关系")
    print("  [green]import-neo4j[/green]     - 导入数据到 Neo4j")
    print("    • 默认：合并 extract_chain.json 和 extract_relations.json 后导入")
    print("    • 使用 --json 指定文件：直接使用已合并的文件导入")
    print("  [green]init-indexes[/green]      - 初始化 Neo4j 索引和约束")
    print("  [green]generate-mentions[/green] - 生成 Mention 表")
    print("  [green]generate-entities[/green] - 生成实体表（别名映射）")
    print("  [green]generate-mentions-and-entities[/green] - 依次执行上述两步（同一 output_dir）")
    print("  [green]init-characters[/green]   - 初始化人物节点")
    print("  [green]extract-states-and-baseline[/green] - 依次：世界/人物/关系状态 + 初始化 baseline（同一 output_dir）")
    print("  [green]extract-world-states[/green] - 提取世界/物品状态变化")
    print("  [green]extract-character-states[/green] - 提取人物内在状态变化")
    print("  [green]extract-relationship-states[/green] - 提取人物关系状态变化")
    print("  [green]init-state-baseline[/green] - 初始化状态变量 baseline")
    print("  [green]import-entities[/green] - 导入实体到 Neo4j")
    print("  [green]import-state-changes[/green] - 导入状态变化到 Neo4j")
    print("  [green]query-neo4j[/green] - 查询 Neo4j 数据")
    print("  [green]aggregate-characters-and-personas[/green] - 依次：人物画像 + 静态人设（同一 output_dir）")
    print("  [green]aggregate-characters[/green] - 聚合人物节点得到完整画像")
    print("  [green]aggregate-personas[/green] - 聚合人物静态人设")
    print("  [green]generate-canonical-branch[/green] - 生成 Canonical Branch（主线/世界真相记录）")
    print("  [green]analyze-decision-points[/green] - 分析决策点，判断哪些适合做分支、哪些适合做合流")
    print("  [green]generate-branch[/green] - 生成支线（基于主线决策点的替代路径）")
    print("  [green]determine-ending-candidates[/green] - 确定结局候选池（根据分支选择分析可能的结局）")
    print("  [green]generate-all-paths[/green] - 生成所有路径（LangGraph）→ all_paths")
    print("  [green]complete-all-path-events[/green] - 按 fork 补全 all_paths 事件 → all_paths_completed")
    print("  [green]generate-content[/green] - 仅运行内容增强（ContentGenerator），可选写入目录")
    print("  [green]generate-renpy-scripts[/green] - 从 enhanced_paths 生成 Ren'Py 脚本（写入 wangfo/game/）")
    print("  [green]import-assets[/green] - 导入立绘/场景/音乐与事件→场景映射，并生成剧本（需在项目根运行）")
    print("  [green]demo-guide[/green] - 打印答辩展示路线与关键检查点")
    print("  [green]validate-outputs[/green] - 校验流水线产物与最终游戏工程")
    print("  [green]evaluate-solution[/green] - 量化评估“小说抽取→文游脚本”方案优劣")
    print("  [green]collect-metrics[/green] - 收集论文/答辩用统计数据")
    print("  [green]extract-story-sample[/green] - 从《东方奇观》抽取第二文本样本")
    print("  [green]visualize-langgraph[/green] - 可视化 LangGraph 工作流结构（生成 PNG/Mermaid/JSON）")
    print("  [green]langgraph-studio[/green] - 启动 LangGraph Studio 进行交互式可视化")
    print("  [green]apply-states[/green] - 应用状态变化并管理内容（使用 LLM function calling）")
    print("\n使用 [cyan]python -m src.cli <command> --help[/cyan] 查看详细帮助")


@app.command("run-pipeline")
def run_pipeline_cmd(
    config_path: str = typer.Option("configs/pipeline_demo.json", "--config", "-c", help="流水线配置 JSON 路径"),
    resume_from: Optional[str] = typer.Option(None, "--resume-from", "-r", help="从指定步骤恢复执行"),
) -> None:
    """
    一键运行完整流水线 🚀

    读取配置文件，按步骤顺序执行。支持 --resume-from 从指定步骤恢复。
    每步自动记录状态到 state.json 和 events.jsonl。
    """
    from src.pipeline.config import load_pipeline_config
    from src.pipeline.builtin_steps import build_builtin_step_handlers
    from src.pipeline.runner import run_pipeline, PipelineExecutionError

    project_root = Path(__file__).resolve().parent.parent
    config = load_pipeline_config(project_root / config_path)
    if resume_from:
        object.__setattr__(config, "resume_from", resume_from) if hasattr(config, "__dataclass_fields__") else None
        config.resume_from = resume_from

    handlers = build_builtin_step_handlers(project_root)

    print(f"[bold cyan]流水线启动[/bold cyan]  run_id={config.run_id}")
    print(f"[dim]配置文件: {config_path}[/dim]")
    print(f"[dim]输出目录: {config.output_root}[/dim]")
    if config.resume_from:
        print(f"[yellow]从步骤 {config.resume_from} 恢复[/yellow]")
    print()

    try:
        state = run_pipeline(config, step_handlers=handlers)
        print(f"\n[green]✅ 流水线完成！状态: {state['status']}[/green]")
        print(f"[green]   状态文件: {config.output_root}/state.json[/green]")
        print(f"[green]   事件日志: {config.output_root}/events.jsonl[/green]")
    except PipelineExecutionError as exc:
        print(f"\n[red]❌ 流水线失败: {exc}[/red]")
        print(f"[yellow]可使用 --resume-from 从失败步骤恢复[/yellow]")
        raise SystemExit(1)


if __name__ == "__main__":
    app()

