# scripts/ — 入口、验证与答辩辅助脚本

本目录为项目可执行入口，不放置业务核心逻辑（核心逻辑在 `src/`）。

从 2026-03 起，按功能拆分子目录，避免所有脚本平铺在同一层：

- `scripts/experiments/`：实验矩阵、批量运行、结果汇总
- `scripts/review/`：方案评估、人工评审清单
- `scripts/ui/`：演示用前端（参数面板 + 启动 pipeline）
- 其余仍在根目录的脚本：主流程辅助（内容生成、重排、验证等）

### UI 面板（MVP）

```bash
streamlit run scripts/ui/pipeline_demo_app.py
```

功能：
- 支持直接上传 `txt/epub`，自动保存到输入库
- 支持从项目目录直接点选 `txt/epub` 文件（无需手填路径）
- 选择 `txt/epub` 输入（epub 会自动转 txt）
- 输入文件会按类型+小说名自动归档到 `experiments/input_library/<type>/<novel_slug>/`
- 上传时会自动去掉括号内容（如 `xxx(来源)`），同名同内容文件会自动复用
- `run_id` 可留空，自动生成为 `demo_<小说名拼音>`（无拼音库时降级为 `demo_<小说标识>`）
- `output_root` 和配置路径默认跟随最终 `run_id`（如 `runs/ui/<run_id>`、`configs/experiments/ui_<run_id>.json`）
- 文本长度固定为全文（full，不裁剪）
- 逐步骤编辑 `enabled` 和 `params`（JSON），所有步骤参数可在面板设置
- 生成单次 run config，并可直接启动 `run-pipeline`
- **「🔧 单步执行」**：按模板顺序只跑某一步 `python -m src.cli <子命令>`，参数合并规则与整条流水线一致
- **「📚 CLI 参数目录」**：反射 `src.cli` 各命令的 Typer 参数与源码（`scripts/ui/cli_catalog.py`）
- 导航 **「📚 CLI 参数目录」**：运行时反射 `src.cli` 各子命令的 Typer 参数 + `cli.py` 中对应函数源码，便于对照 `steps.*.params` 逐项验证（见 `scripts/ui/cli_catalog.py`）

| 脚本 | 作用 |
|------|------|
| **reorder_canonical_branch.py** | 在 LangGraph / generate-all-paths 之前执行：用 LLM 将 canonical_branch 按故事时间先后重排（处理倒叙/插叙），默认输出为 `out/canonical_branch_chronological.json`（不覆盖原文件）。 |
| **generate_renpy_scripts.py** | Ren'Py 脚本生成主入口。从 `out/all_paths/` 加载路径 → 调用 ContentGenerator 做内容增强 → 生成 `wangfo/game/` 下的 characters.rpy、paths/*.rpy、endings/*.rpy、script.rpy。 |
| **content_generator.py** | 脚本内容生成器（被 generate_renpy_scripts 使用）。对每条路径做 LLM 增强：扩展场景描述、补全对话、生成决策点选项。 |
| **validate_pipeline_outputs.py** | 最小验证脚本。校验 `out/` 关键产物与 `wangfo/game/` 最终游戏工程是否完整可展示。 |
| **collect_project_metrics.py** | 收集答辩和论文中使用的静态统计信息，如 Python 文件数、Ren'Py 脚本规模、主线事件数等。 |
| **extract_story_sample.py** | 从 `docs/epub_to_txt/东方奇观.txt` 中抽取单篇故事，作为第二文本轻量验证样本。 |

## 一、完整流水线示例

推荐直接使用 CLI 菜单：

```bash
python -m src.cli demo
```

如需逐步执行，建议顺序如下：

```bash
# 1. 从原始文本生成结构化事件和关系
python -m src.cli extract-events --input 王佛脱险记.txt
python -m src.cli extract-relations
python -m src.cli import-neo4j
python -m src.cli init-indexes

# 2. 角色建模与状态管理
python -m src.cli generate-mentions
python -m src.cli generate-entities
python -m src.cli extract-character-states
python -m src.cli extract-world-states
python -m src.cli extract-relationship-states
python -m src.cli init-state-baseline
python -m src.cli import-entities
python -m src.cli import-state-changes
python -m src.cli aggregate-characters
python -m src.cli aggregate-personas

# 3. 分支路径生成
python -m src.cli generate-canonical-branch
python scripts/reorder_canonical_branch.py
python -m src.cli analyze-decision-points -c out/canonical_branch_chronological.json
python -m src.cli determine-ending-candidates
python -m src.cli generate-branch
python -m src.cli generate-all-paths -c out/canonical_branch_chronological.json
python -m src.cli complete-all-path-events

# 4. 内容增强与游戏生成
python -m src.cli generate-content
python -m src.cli generate-renpy-scripts
python -m src.cli import-assets
```

## 二、答辩前建议命令

```bash
# 1. 检查最终游戏工程是否完整
python -m src.cli validate-outputs --mode game

# 2. 若要展示完整流水线，同时检查 out/ 关键产物
python -m src.cli validate-outputs --mode all

# 3. 更新答辩/论文用统计数据
python -m src.cli collect-metrics --write-markdown docs/答辩实验结果.md

# 4. 从《东方奇观》中抽取第二文本样本
python -m src.cli extract-story-sample --title "马尔戈的微笑"
```

## 三、关键输入输出

- **reorder_canonical_branch.py**：输入 `out/canonical_branch.json`，默认输出 `out/canonical_branch_chronological.json`（可 `-o` 指定）。需在 `generate-all-paths` 之前执行，生成路径时用 `-c out/canonical_branch_chronological.json`。
- **generate_renpy_scripts.py**：输入 `out/all_paths/*.json`、`out/character_personas.json`，输出 `wangfo/game/` 下各 .rpy 文件。
- **validate_pipeline_outputs.py**：默认校验 `wangfo/game/` 与 `out/`。若仅需校验最终游戏，可使用 `--mode game`。
- **collect_project_metrics.py**：可直接输出 JSON 或 Markdown，便于同步到论文与 README。
- **extract_story_sample.py**：默认从 `docs/epub_to_txt/东方奇观.txt` 中抽取《马尔戈的微笑》，输出到 `docs/validation_samples/`。

更完整的项目结构见 [docs/PROJECT_STRUCTURE.md](../docs/PROJECT_STRUCTURE.md)。
