# 实验工作流（变量对照 + 严格对齐）

## 目标

在以下变量上做可复现实验，并保证结果严格对齐：

- 事件粒度（`event_granularity`）
- 决策点密度（`decision_density`）
- 分支数量（`branch_count`）
- 输入文本长度（`text_length`）

## 1) 生成实验配置

```bash
python scripts/experiments/generate_experiment_matrix.py \
  --base-config configs/pipeline_demo.json \
  --output-dir configs/experiments \
  --branch-counts 2,3,4 \
  --event-granularities coarse,medium,fine \
  --decision-densities low,medium,high \
  --text-lengths short,medium,long \
  --repeats 1 \
  --seed 42
```

输出：

- `configs/experiments/exp_XXX.json`
- `configs/experiments/manifest.json`
- `experiments/text_variants/{short,medium,long}.txt`

## 2) 先做 dry-run 对齐检查

```bash
python scripts/experiments/run_experiments.py --manifest configs/experiments/manifest.json --limit 2 --dry-run
python scripts/experiments/summarize_experiments.py --manifest configs/experiments/manifest.json
```

## 3) 批量跑实验

```bash
python scripts/experiments/run_experiments.py --manifest configs/experiments/manifest.json
python scripts/experiments/summarize_experiments.py --manifest configs/experiments/manifest.json --strict
```

输出：

- `runs/experiments/execution_report.json`
- `runs/experiments/summary.json`
- `runs/experiments/summary.csv`

## 对齐保障

每组实验会写 `experiment_meta.json`，汇总时必须满足以下主键一致：

- `experiment_id`
- `run_id`
- `config_hash`

`--strict` 模式下，只要有任一组不一致，汇总直接报错并停止。

## 变量映射说明（当前实现）

- `event_granularity`：通过 `extract-events --chunk-size/--chunk-overlap` 控制文本分块粒度。
- `decision_density`：通过 `analyze-decision-points --target-branch-count` 控制分支点数量（low/medium/high -> 2/4/6）。
- `branch_count`：通过 `generate-branch --max-branches` 控制每决策点支线数。
- `text_length`：通过 `input_text` 切片文本（short/medium/long）控制。
