"""
从《东方奇观》合集文本中抽取单篇故事，作为第二文本轻量验证样本。
"""

from __future__ import annotations

import argparse
from pathlib import Path


STORY_TITLES = [
    "王佛脱险记",
    "马尔戈的微笑",
    "亡人的奶汁",
    "暮年之恋",
    "海仙女的恋人",
    "燕子圣母院",
    "寡妇阿芙罗狄西亚",
    "被砍头的女神迦利",
    "失去头颅的迦梨",
    "马尔戈之死",
    "科尔内柳斯•贝格的悲哀",
    "一弹解千愁",
]


def extract_story(full_text: str, title: str) -> str:
    lines = full_text.splitlines()
    matching_indexes = [idx for idx, line in enumerate(lines) if line.strip() == title]

    if not matching_indexes:
        raise ValueError(f"未在文本中找到标题：{title}")
    # 合集前部通常有目录，正文中的标题一般是第二次出现。
    start = matching_indexes[1] if len(matching_indexes) > 1 else matching_indexes[0]

    end = len(lines)
    for idx in range(start + 1, len(lines)):
        stripped = lines[idx].strip()
        if stripped in STORY_TITLES and stripped != title:
            end = idx
            break

    extracted = "\n".join(lines[start:end]).strip()
    if not extracted:
        raise ValueError(f"标题 {title} 对应内容为空")
    return extracted + "\n"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="从合集文本中抽取单篇故事")
    parser.add_argument(
        "--input",
        default="docs/epub_to_txt/东方奇观.txt",
        help="合集文本路径",
    )
    parser.add_argument(
        "--title",
        default="马尔戈的微笑",
        help="要抽取的故事标题",
    )
    parser.add_argument(
        "--output",
        default="docs/validation_samples/马尔戈的微笑.txt",
        help="输出路径",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    project_root = Path(__file__).resolve().parent.parent
    input_path = (project_root / args.input).resolve()
    output_path = (project_root / args.output).resolve()

    full_text = input_path.read_text(encoding="utf-8")
    story_text = extract_story(full_text, args.title)

    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(story_text, encoding="utf-8")

    print(f"已抽取《{args.title}》到: {output_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
