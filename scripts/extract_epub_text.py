"""
从 epub 提取纯文本。

用法（项目根目录）：
  python -m scripts.extract_epub_text
  python -m scripts.extract_epub_text -i docs/epub_to_txt/东方奇观.epub -o docs/epub_to_txt/东方奇观.txt

也可作为模块调用：
  from scripts.extract_epub_text import extract_epub_to_txt
  extract_epub_to_txt(Path("a.epub"), Path("a.txt"))
"""
import argparse
from pathlib import Path

import ebooklib
from ebooklib import epub
from bs4 import BeautifulSoup


def extract_epub_to_txt(in_path: Path, out_path: Path) -> None:
    """从 epub 提取纯文本到指定文件。"""
    book = epub.read_epub(str(in_path))
    out = []
    for item in book.get_items_of_type(ebooklib.ITEM_DOCUMENT):
        soup = BeautifulSoup(item.get_content(), "lxml")
        txt = soup.get_text(separator="\n").strip()
        if txt:
            out.append(txt)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    with open(out_path, "w", encoding="utf-8") as f:
        f.write("\n\n".join(out))


def main():
    parser = argparse.ArgumentParser(description="从 epub 提取纯文本")
    root = Path(__file__).resolve().parent.parent
    default_in = root / "docs" / "epub_to_txt" / "东方奇观.epub"
    default_out = root / "docs" / "epub_to_txt" / "东方奇观.txt"
    parser.add_argument("-i", "--input", default=str(default_in), help="输入 epub 路径")
    parser.add_argument("-o", "--output", default=str(default_out), help="输出 txt 路径")
    args = parser.parse_args()

    in_path = Path(args.input)
    out_path = Path(args.output)
    if not in_path.exists():
        print(f"❌ 找不到输入文件: {in_path}")
        return

    extract_epub_to_txt(in_path, out_path)
    print(f"🎉 正文已导出到 {out_path}")


if __name__ == "__main__":
    main()
