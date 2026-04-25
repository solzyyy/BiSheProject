# 基于大语言模型与知识图谱的互动叙事游戏内容自动生成系统

> 以经典文学文本为输入，通过 LLM 驱动的事件抽取、知识图谱建模、状态管理与分支路径编排，全自动生成可运行的 Ren'Py 视觉小说游戏。

## 毕设 Demo 快速开始

如果你当前的目标是答辩展示，而不是从零重跑全部生成流程，建议按下面顺序进行：

1. 先确认最终游戏工程可运行：

```bash
python -m src.cli validate-outputs --mode game
```

2. 打印答辩展示路线与关键检查点：

```bash
python -m src.cli demo-guide
```

3. 如需展示完整流水线，使用交互式菜单按阶段说明：

```bash
python -m src.cli demo
```

4. 如需更新论文和答辩中的统计表，可重新生成统计结果：

```bash
python -m src.cli collect-metrics --write-markdown docs/答辩实验结果.md
```

5. 使用 [Ren'Py SDK](https://www.renpy.org/) 打开 `wangfo/` 并运行最终游戏。

答辩配套文档：

- 演示脚本：`docs/DEMO_SCRIPT.md`
- 答辩前检查单：`docs/答辩前检查.md`
- 测试执行说明：`docs/测试执行说明.md`
- 统计结果：`docs/答辩实验结果.md`

## 一、研究背景与动机

互动叙事（Interactive Narrative）是游戏领域的重要研究方向，传统方法依赖人工编写大量分支剧本，成本高、扩展性差。大语言模型（LLM）的出现为自动化叙事生成提供了新的可能，但直接使用 LLM 生成长篇分支故事面临**一致性差、状态丢失、角色行为不连贯**等问题。

本系统提出一种**"知识图谱 + 状态驱动 + LLM 编排"**的技术路线：先从文学文本中构建结构化的事件知识图谱和角色模型，再利用状态管理机制约束 LLM 的创作过程，最终通过 LangGraph 工作流自动编排分支路径，生成完整的可运行游戏。

**核心思路**：不是让 LLM 凭空创作故事，而是让 LLM 在**结构化知识约束**下进行有据可依的叙事扩展。

## 二、系统总体架构

系统采用分层模块化设计，数据从原始文本到可运行游戏经历五个阶段：

```
┌─────────────────────────────────────────────────────────────────────────┐
│                         外部服务层                                       │
│              Gemini API / DeepSeek API / OpenAI 兼容接口                  │
└────────────────────────────┬────────────────────────────────────────────┘
                             │
┌────────────────────────────▼────────────────────────────────────────────┐
│                     核心基础设施层 (src/core)                             │
│   AsyncLLMClient · Neo4jClient · VectorDB · TextProcessor               │
│   异步调用 · 并发控制 · 指数退避重试 · JSON 提取                           │
└────┬──────────┬──────────┬──────────┬──────────┬───────────────────────┘
     │          │          │          │          │
┌────▼───┐ ┌───▼────┐ ┌───▼────┐ ┌───▼────┐ ┌───▼──────┐
│事件图谱│ │人物模块│ │状态管理│ │分支生成│ │Ren'Py   │
│构建    │ │        │ │        │ │        │ │脚本生成  │
│        │ │Mention │ │状态应用│ │主线生成│ │路径脚本  │
│事件提取│ │实体识别│ │快照生成│ │支线生成│ │结局脚本  │
│关系提取│ │画像聚合│ │基线管理│ │LangGraph│ │角色定义  │
│Neo4j  │ │人设聚合│ │        │ │路径编排│ │主脚本   │
└────┬───┘ └───┬────┘ └───┬────┘ └───┬────┘ └───┬──────┘
     │         │          │          │          │
     └─────────┴──────────┴──────────┴──────────┘
                          │
              ┌───────────▼───────────┐
              │   Neo4j 图数据库       │
              │ Event · Entity ·       │
              │ StateChange 节点       │
              └───────────┬───────────┘
                          │
              ┌───────────▼───────────┐
              │   Ren'Py 游戏工程      │
              │ wangfo/game/           │
              │ 可直接运行的视觉小说    │
              └───────────────────────┘
```

## 三、技术栈

| 层次 | 技术 | 用途 |
|------|------|------|
| 大语言模型 | Gemini 2.5 Pro / DeepSeek V3 | 事件抽取、状态推理、内容增强、分支决策 |
| 工作流编排 | LangGraph | 分支路径生成的有状态工作流，含 function calling |
| 知识图谱 | Neo4j | 事件、实体、状态变化的结构化存储与查询 |
| 向量数据库 | ChromaDB | 语义相似度检索（实体消歧等） |
| 游戏引擎 | Ren'Py | 视觉小说运行时，支持多分支、多结局 |
| 后端语言 | Python 3.10+ | 异步编程（asyncio）、CLI（typer + rich） |
| LLM 框架 | LangChain | LLM 调用抽象、消息格式、工具定义 |

## 四、完整生成管线

系统通过统一 CLI（`python -m src.cli`）按步骤执行，也可通过交互式菜单（`python -m src.cli demo`）逐步引导：

```
原始文本 (王佛脱险记.txt)
  │
  ├─ 阶段一：知识图谱构建 ─────────────────────────────
  │   ① extract-events        从文本提取结构化事件
  │   ② extract-relations      提取事件间因果/时序关系
  │   ③ import-neo4j           导入 Neo4j 图数据库
  │   ④ init-indexes           建立数据库索引
  │
  ├─ 阶段二：角色建模 ─────────────────────────────────
  │   ⑤ generate-mentions      提取人物提及（Mention）
  │   ⑥ generate-entities      实体识别与别名消歧
  │   ⑦ extract-*-states       提取三类状态变化
  │   ⑧ init-state-baseline    初始化状态基线
  │   ⑨ aggregate-characters   聚合动态人物画像
  │   ⑩ aggregate-personas     提取静态人设特征
  │
  ├─ 阶段三：分支路径生成 ──────────────────────────────
  │   ⑪ generate-canonical-branch  生成主线记录
  │   ⑫ analyze-decision-points    识别决策点
  │   ⑬ determine-ending-candidates 确定结局候选
  │   ⑭ generate-branch            生成支线替代选择
  │   ⑮ generate-all-paths         LangGraph 编排所有路径并补全事件
  │
  ├─ 阶段四：内容增强 ─────────────────────────────────
  │   ⑯ generate-content       LLM 扩写场景描述与对话
  │
  ├─ 阶段五：游戏生成 ─────────────────────────────────
  │   ⑰ generate-renpy-scripts 生成 Ren'Py 脚本
  │   ⑱ import-assets          导入美术/音乐资源
  │
  ▼
  wangfo/ — 可直接运行的 Ren'Py 视觉小说游戏
```

## 五、核心模块说明

### 5.1 事件图谱构建（src/event_graph）

从原始文学文本中提取结构化事件（含人物、行动、目标、结果），分析事件间的因果关系和时间顺序，构建事件知识图谱并持久化到 Neo4j。每个事件节点包含 `event_id`、`scene_description`、`source_text`、`dialogue` 等字段。

### 5.2 人物模块（src/character）

- **Mention 提取**：识别每个事件中提及的人物
- **实体消歧**：将「皇帝」「天子」「陛下」等同一人物的不同称呼归并到统一实体
- **三类状态提取**：人物内在状态（情绪、动机、世界观）、世界/物品状态（场景、环境）、人物关系状态（情感、权力动态）
- **画像聚合**：统计行动频率，利用 TF-IDF / PMI 识别角色独有行为模式
- **人设聚合**：通过 LLM 提取叙事角色、社会位置、性格倾向、世界观等静态特征

### 5.3 状态管理（src/state_manager）

维护三层状态模型（人物内在 / 人物关系 / 世界物品），核心组件 `StateApplier` 通过 LLM function calling 智能应用状态变化：LLM 根据事件内容和当前状态上下文，自主决定调用 `update_character_state()` / `update_relationship_state()` / `update_world_state()` 等函数更新状态变量。每次状态更新后生成 `StateSnapshot`，为后续分支生成提供完整的状态上下文。

### 5.4 分支生成（src/branch）

分支生成是系统最核心的模块，分为四个子阶段：

1. **主线生成**（CanonicalBranchGenerator）：从第一个事件开始逐步遍历知识图谱，应用状态变化，记录"世界真实发生的历史"
2. **决策点分析**（DecisionPointAnalyzer）：识别主线中的决策点和关键事件
3. **支线生成**（BranchGenerator）：为决策点生成替代选择及其状态影响
4. **路径编排**（PathGenerationFunctions + LangGraph）：使用 LangGraph 有状态工作流循环处理每条分支路径——LLM 决策节点通过 function calling 决定下一步操作（生成分支事件 / 合流到主线 / 创建提前结局），执行节点调用 StateChangeGenerator 和 StateApplier 维护状态一致性

LangGraph 工作流结构：

```
initialize → summarize_history → llm_decision
                                      │
                        ┌──────────────┼──────────────┐
                        ▼              ▼              ▼
                 generate_event   merge_mainline  early_ending
                        │              │              │
                        ▼              ▼              ▼
                 summarize_history  select_ending  select_ending
                        │              │              │
                        ▼              ▼              ▼
                 llm_decision        END            END
```

### 5.5 内容增强（scripts/content_generator）

将知识图谱中简短的 `scene_description` 通过 LLM 扩写为文学性的场景描述（`detailed_scene_description`），提取或生成完整对话，注入去 AI 味系统预设以保持文风统一。支持叙述人称推断（第一 / 二 / 三人称），全文保持一致。

### 5.6 Ren'Py 脚本生成（src/renpy）

将增强后的路径数据自动转换为 Ren'Py 脚本文件：

- **CharacterGenerator**：生成 `characters.rpy`（角色定义，自动关联立绘）
- **PathScriptGenerator**：生成 `paths/*.rpy`（路径脚本，含场景切换、对话、选择菜单）
- **EndingScriptGenerator**：生成 `endings/*.rpy`（结局脚本）
- **MainScriptUpdater**：更新 `script.rpy`（主脚本入口与跳转）

路径脚本中自动插入场景图片（`scene bg_scene_xx`）、角色立绘、flavor 选项（不影响剧情的互动选择），以提升游戏体验。

## 六、项目结构

```
BiSheProject/
  src/                    # 核心代码
    core/                 #   LLM 客户端、Neo4j、向量库等基础设施
    event_graph/          #   事件提取、关系提取、Neo4j 导入
    character/            #   Mention、实体、状态提取、画像/人设聚合
    state_manager/        #   状态应用（function calling）、快照、基线
    branch/               #   主线/支线/路径生成、LangGraph 工作流
    renpy/                #   Ren'Py 脚本生成器（路径、结局、角色、主脚本）
    cli.py                #   统一命令行接口（typer），含交互式 demo 菜单
  scripts/                # 管线入口脚本（内容生成、主线分析等）
  docs/                   # 文档（技术方案、架构、答辩建议等）
  prompts/                # LLM 提示词模板
  schema/                 # 数据模式定义（事件、状态变化 JSON Schema）
  out/                    # 生成产物（路径 JSON、人设、状态等）
  wangfo/                 # Ren'Py 游戏工程（最终输出）
  assets/                 # 第三方资源（GUI 主题、背景、立绘、音乐）
  import_assets.py        # 资源导入与场景映射
```

- 一页概览：[docs/项目结构说明.md](docs/项目结构说明.md)
- 详细说明：[docs/PROJECT_STRUCTURE.md](docs/PROJECT_STRUCTURE.md)

## 七、快速开始

### 环境准备

```bash
# 1. 创建虚拟环境
python -m venv venv
source venv/bin/activate  # Windows: venv\Scripts\activate

# 2. 安装依赖
pip install -r requirements.txt

# 3. 配置环境变量（在 .env 中设置 LLM API Key）
cp .env.example .env
# 编辑 .env，填入 GEMINI_API_KEY 或 DEEPSEEK_API_KEY

# 4. 启动 Neo4j 数据库（本地或 Docker）
# 确保 NEO4J_URI, NEO4J_USER, NEO4J_PASSWORD 已在 .env 中配置
```

### 运行完整管线

```bash
# 方式一：交互式菜单（推荐，逐步引导）
python -m src.cli demo

# 方式二：逐步执行完整流水线
python -m src.cli extract-events --input 王佛脱险记.txt
python -m src.cli extract-relations
python -m src.cli import-neo4j
python -m src.cli init-indexes
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
python -m src.cli generate-canonical-branch
python -m src.cli analyze-decision-points
python -m src.cli determine-ending-candidates
python -m src.cli generate-branch
python -m src.cli generate-all-paths
python -m src.cli generate-content
python -m src.cli generate-renpy-scripts

# 最后：导入美术资源并补全最终游戏工程
python -m src.cli import-assets
```

### 运行游戏

使用 [Ren'Py SDK](https://www.renpy.org/) 打开 `wangfo/` 目录即可运行生成的视觉小说。

### 第二文本轻量验证

若需要证明系统并非只适配《王佛脱险记》，可先从合集文本中抽取另一篇样本：

```bash
python -m src.cli extract-story-sample --title "马尔戈的微笑"
```

默认输出为 `docs/validation_samples/马尔戈的微笑.txt`。答辩前建议至少对该样本跑通“事件提取 → 实体识别/状态提取”的轻量验证流程。

## 八、生成产物说明

| 产物 | 路径 | 说明 |
|------|------|------|
| 事件链 | `out/extract_chain.json` | 从原文提取的结构化事件列表 |
| 事件关系 | `out/extract_relations.json` | 事件间因果/时序关系 |
| 人物画像 | `out/character_profiles.json` | 动态人物画像（行动模式、独有行为等） |
| 静态人设 | `out/character_personas.json` | 角色的叙事定位、性格、世界观 |
| 状态基线 | `out/state_baseline.json` | 所有状态维度的初始值 |
| 主线记录 | `out/canonical_branch.json` | 原作中真实发生的事件序列与状态变化 |
| 决策点分析 | `out/decision_points_analysis.json` | 识别出的分支点与分析 |
| 支线记录 | `out/branches.json` | 各决策点的替代选择与状态影响 |
| 所有路径 | `out/all_paths/` | 主线 + 各分支路径的完整事件链 |
| 增强路径 | `out/enhanced_paths/` | 内容增强后的路径数据（含扩写场景和对话） |
| 游戏工程 | `wangfo/game/` | 可运行的 Ren'Py 视觉小说（脚本、角色、场景） |

## 九、关键技术特点

1. **知识图谱约束的叙事生成**：不依赖 LLM 凭空创作，而是基于从原文提取的事件图谱进行有据可依的分支扩展
2. **三层状态模型**：人物内在状态、人物关系状态、世界/物品状态三层分离，确保分支路径中状态变化的一致性
3. **LLM Function Calling 驱动的状态管理**：状态更新由 LLM 根据事件上下文自主决策调用哪些状态更新函数，兼顾灵活性与可控性
4. **LangGraph 有状态工作流**：分支路径生成采用 LangGraph 图结构编排，LLM 在循环中自主决策（生成事件 / 合流 / 结局），支持检查点恢复
5. **端到端自动化管线**：从原始文本到可运行游戏，23 个步骤全部通过统一 CLI 自动化执行
6. **去 AI 味文风控制**：内容增强阶段注入专用系统预设，控制 LLM 输出的文学风格
7. **叙述人称一致性**：自动推断原文人称（第一 / 二 / 三），全管线保持叙述视角统一

## 十、参考文献与相关工作

- **LangGraph**：LangChain 团队推出的有状态工作流框架，支持循环、条件路由、检查点
- **Ren'Py**：开源视觉小说引擎，支持多分支、多结局、存档、回退等游戏机制
- **Neo4j**：原生图数据库，适合存储事件间关系和状态变化的网络结构
- **Interactive Storytelling**：互动叙事领域的相关研究（Facade, Versu, AI Dungeon 等）

## 许可

本项目为毕业设计作品，仅供学术研究和学习参考使用。
