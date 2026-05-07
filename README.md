# 基于大语言模型与知识图谱的互动叙事游戏内容自动生成系统

> 输入文学文本，自动完成事件抽取、知识图谱构建、状态建模、分支生成、内容增强，并导出可运行的 Ren'Py 视觉小说工程。

## 快速开始

```bash
python -m venv venv
venv\Scripts\activate
pip install -r requirements.txt
```

配置 `.env`（至少包含可用模型 API Key）。如使用 Neo4j，请配置：

- `NEO4J_URI`
- `NEO4J_USER`
- `NEO4J_PASSWORD`

运行交互式流程：

```bash
python -m src.cli demo
```

运行 Streamlit 前端：

```bash
streamlit run scripts/ui/pipeline_demo_app.py
```

## 常用命令

```bash
python -m src.cli list-commands
python -m src.cli run-pipeline --config configs/pipeline_demo.json
python -m src.cli generate-content --output out/enhanced_paths
python -m src.cli generate-renpy-scripts --enhanced-dir out/enhanced_paths
python -m src.cli import-assets --enhanced-dir out/enhanced_paths
```

## 目录说明

- `src/`：核心流程与 CLI
- `scripts/ui/`：Streamlit 可视化前端
- `configs/`：流程配置文件
- `out/`：默认输出目录
- `runs/ui/`：前端运行产物目录
- `assets/`：素材目录（含 `ui_assets`）
- `wangfo/`：Ren'Py 游戏工程

## 许可

本项目为毕业设计作品，主要用于学术研究与学习交流。
