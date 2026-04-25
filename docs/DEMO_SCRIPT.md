# 毕设 Demo 演示脚本

本文档将现有 CLI 管线收束为两条固定演示路线：

- 最短答辩演示路线：适合 5 分钟左右的现场展示。
- 完整流水线路线：适合老师追问实现细节时逐步展开。

---

## 一、最短答辩演示路线

### 1. 先展示最终成品

目标：先让老师直观看到“系统已经生成了一个可玩的文字冒险游戏”。

建议顺序：

1. 使用 Ren'Py Launcher 打开 `wangfo/`。
2. 运行游戏，进入主线。
3. 演示 1 次玩家选择，展示一个明显不同的分支或结局。
4. 结束后说明：刚才看到的剧情，并不是手工写死，而是由文本生成流水线自动产出的。

建议口播：

> 我的系统不是只生成一份静态文本，而是把文学原文逐步转成结构化事件、角色状态、多分支路径，最后自动写成可运行的 Ren'Py 游戏。下面我再回到命令行，展示这个生成过程的流水线结构。

### 2. 再展示流水线入口

在项目根目录运行：

```bash
python -m src.cli demo-guide
```

这个命令用于答辩时快速说明：

- 五阶段流水线如何划分
- 最短演示路线应该看什么
- 完整流程的关键检查点是什么

如果老师想看完整步骤，再运行：

```bash
python -m src.cli demo
```

### 3. 最后展示中间证据

建议只打开 3 类中间结果，避免现场展开过多细节：

| 展示对象 | 建议路径 | 要说明的重点 |
| --- | --- | --- |
| 事件与结构化结果 | `out/extract_chain.json` | 原文先被提取成事件，而不是直接让 LLM 自由发挥 |
| 分支与路径结果 | `out/canonical_branch.json`、`out/branches.json`、`out/all_paths_completed/` | 系统如何从主线、决策点生成多条路径 |
| 最终脚本 | `wangfo/game/paths/`、`wangfo/game/endings/` | 路径 JSON 最终如何落地为 Ren'Py 剧本 |

建议口播：

> 我把系统拆成了“结构化建模”和“可玩化输出”两部分。前半段负责把文本变成知识图谱、角色和状态；后半段负责生成分支并写入 Ren'Py。这样做的好处是，生成过程更可解释，也更容易验证。

---

## 二、完整流水线路线

适用场景：

- 老师追问“你是怎么一步步生成出来的”
- 需要展示系统设计与实现部分
- 需要解释测试点和中间产物

### 阶段一：知识图谱构建

| 步骤 | 命令 | 输入 | 关键输出 |
| --- | --- | --- | --- |
| 1 | `python -m src.cli extract-events --input 王佛脱险记.txt` | 原始文本 | `out/extract_chain.json` |
| 2 | `python -m src.cli extract-relations` | 事件链 | `out/extract_relations.json` |
| 3 | `python -m src.cli import-neo4j` | 事件链 + 事件关系 | Neo4j 中的事件图谱 |
| 4 | `python -m src.cli init-indexes` | Neo4j 数据 | 索引与约束 |

讲解重点：

- 文学原文会先被拆成结构化事件。
- 系统不仅提取事件本身，还会构建事件之间的因果和时序关系。
- 这一阶段的输出为后续角色建模和分支生成提供事实基础。

### 阶段二：角色建模与状态管理

| 步骤 | 命令 | 关键输出 |
| --- | --- | --- |
| 5 | `python -m src.cli generate-mentions` | Mention 表 |
| 6 | `python -m src.cli generate-entities` | `out/entities.json` |
| 7 | `python -m src.cli extract-character-states` | 人物内在状态变化 |
| 8 | `python -m src.cli extract-world-states` | 世界/物品状态变化 |
| 9 | `python -m src.cli extract-relationship-states` | 人物关系状态变化 |
| 10 | `python -m src.cli init-state-baseline` | `out/state_baseline.json` |
| 11 | `python -m src.cli import-entities` | Neo4j 实体节点 |
| 12 | `python -m src.cli import-state-changes` | Neo4j 状态变化节点 |
| 13 | `python -m src.cli aggregate-characters` | 动态人物画像 |
| 14 | `python -m src.cli aggregate-personas` | 静态人设 |

讲解重点：

- 系统会把“人物是谁”与“人物状态如何变化”分开建模。
- 三层状态模型分别处理人物内在、人物关系、世界物品。
- 这一阶段是后续分支一致性的基础。

### 阶段三：分支路径生成

| 步骤 | 命令 | 关键输出 |
| --- | --- | --- |
| 15 | `python -m src.cli generate-canonical-branch` | `out/canonical_branch.json` |
| 16 | `python -m src.cli analyze-decision-points` | `out/decision_points_analysis.json` |
| 17 | `python -m src.cli determine-ending-candidates` | 结局候选 |
| 18 | `python -m src.cli generate-branch` | `out/branches.json` |
| 19 | `python -m src.cli generate-all-paths` | `out/all_paths/` |
| 20 | `python -m src.cli complete-all-path-events` | `out/all_paths_completed/` |

讲解重点：

- 系统先恢复原作主线，再识别适合作为玩家选择入口的决策点。
- LangGraph 工作流负责生成、合流和提前结局等路径控制；fork 事件补全在 **`complete-all-path-events`** 中单独执行，便于核对 `all_paths` 与 `all_paths_completed`。
- `all_paths_completed` 是后续内容增强的直接输入。

### 阶段四：内容增强

| 步骤 | 命令 | 关键输出 |
| --- | --- | --- |
| 21 | `python -m src.cli generate-content` | `out/enhanced_paths/` |

讲解重点：

- 这一阶段把简短事件扩写为更完整的叙述文本和对白。
- 输出仍然是结构化 JSON，而不是直接写入游戏脚本。

### 阶段五：游戏生成

| 步骤 | 命令 | 关键输出 |
| --- | --- | --- |
| 22 | `python -m src.cli generate-renpy-scripts` | `wangfo/game/paths/`、`wangfo/game/endings/`、`characters.rpy` |
| 23 | `python -m src.cli import-assets` | 场景图、立绘、音乐、事件场景映射 |

讲解重点：

- `generate-renpy-scripts` 负责把路径数据写成 Ren'Py 脚本。
- `import-assets` 负责把资源映射到实际游戏工程，形成完整可运行 Demo。

---

## 三、答辩时建议展示的文件

如果时间有限，建议优先打开以下文件：

| 类型 | 文件 |
| --- | --- |
| 统一入口 | `src/cli.py` |
| 演示文档 | `docs/DEMO_SCRIPT.md` |
| 快速检查 | `docs/答辩前检查.md` |
| 论文结果口径 | `docs/答辩实验结果.md` |
| 游戏主入口 | `wangfo/game/script.rpy` |
| 主线路径脚本 | `wangfo/game/paths/canonical_path.rpy` |

---

## 四、老师追问时的常用回答

### 1. 为什么不用纯前端展示？

回答要点：

- 当前系统核心价值在于从原始文本到可运行游戏的生成流水线。
- CLI 更适合展示分阶段输入输出和工程可复现性。
- 如有需要，后续可以在 CLI 之上增加极简 Web 包装，但不影响核心生成逻辑。

### 2. 怎么证明不是手工写的剧情？

回答要点：

- 展示 `out/extract_chain.json`、`out/branches.json`、`out/enhanced_paths/`。
- 展示 `src.cli demo` 中的阶段顺序。
- 展示最终 `wangfo/game/paths/*.rpy` 与 `wangfo/game/endings/*.rpy` 是由结构化中间产物自动生成的。

### 3. 怎么保证生成内容前后一致？

回答要点：

- 使用事件知识图谱约束叙事骨架。
- 使用人物内在、关系、世界三层状态模型维护一致性。
- 使用 LangGraph 工作流控制分支、合流与提前结局。

---

## 五、推荐现场顺序

1. 打开并运行 `wangfo/`，展示 1 条主线和 1 个分支。
2. 回到终端，执行 `python -m src.cli demo-guide`。
3. 说明五阶段流水线与关键中间产物。
4. 打开 `docs/答辩实验结果.md`，用真实统计数据收尾。
