#!/usr/bin/env python3
"""Build one self-contained children's picture-book HTML file from book.json.

Usage:
    python scripts/build_book.py book.json output/book.html

The builder validates the expected structure and rejects common active or
external SVG content. It is not a substitute for reviewing untrusted input.
"""

from __future__ import annotations

import html
import json
import re
import sys
from pathlib import Path
from typing import Any

TEMPLATE = Path(__file__).parent.parent / "assets" / "book_template.html"
AUDIO_DATA_URI = re.compile(
    r"^data:audio/[a-zA-Z0-9.+-]+(?:;[a-zA-Z0-9.+-]+=[^;,]+)*;base64,[A-Za-z0-9+/=]+$"
)
DANGEROUS_SVG_PATTERNS: tuple[tuple[str, re.Pattern[str]], ...] = (
    (
        "可执行或嵌入式标签",
        re.compile(r"<\s*(?:script|foreignobject|iframe|object|embed|link|style|base)\b", re.IGNORECASE),
    ),
    ("内联事件处理器", re.compile(r"\son[a-z][\w:-]*\s*=", re.IGNORECASE)),
    (
        "外部或可执行链接",
        re.compile(
            r"(?:href|xlink:href|src)\s*=\s*(['\"]?)\s*(?:https?:|//|javascript:|data:|file:)",
            re.IGNORECASE,
        ),
    ),
    (
        "外部或可执行 CSS URL",
        re.compile(r"url\(\s*(['\"]?)\s*(?:https?:|//|javascript:|data:|file:)", re.IGNORECASE),
    ),
)


def fail(message: str) -> None:
    print(f"[build_book] 错误: {message}", file=sys.stderr)
    raise SystemExit(1)


def is_nonempty_string(value: Any) -> bool:
    return isinstance(value, str) and bool(value.strip())


def validate_svg(svg: str, label: str, problems: list[str], seen_ids: dict[str, str]) -> None:
    stripped = svg.lstrip()
    if not stripped.startswith("<svg"):
        problems.append(f"{label} 插画缺失或不是 <svg> 开头")
        return
    if "viewBox" not in svg:
        problems.append(f"{label} SVG 没有 viewBox（缩放会出问题）")

    for description, pattern in DANGEROUS_SVG_PATTERNS:
        if pattern.search(svg):
            problems.append(f"{label} SVG 包含不允许的{description}")

    for match in re.finditer(r"\bid\s*=\s*(['\"])(.*?)\1", svg, re.IGNORECASE | re.DOTALL):
        svg_id = match.group(2).strip()
        if not svg_id:
            problems.append(f"{label} SVG 有空的 id 属性")
        elif svg_id in seen_ids:
            problems.append(
                f"{label} SVG 的 id=\"{svg_id}\" 与 {seen_ids[svg_id]} 重复；"
                "同一 HTML 中所有 SVG ID 必须唯一"
            )
        else:
            seen_ids[svg_id] = label


def validate_book(book: Any) -> list[str]:
    problems: list[str] = []
    if not isinstance(book, dict):
        return ["book.json 顶层必须是 JSON 对象"]

    if not is_nonempty_string(book.get("title")):
        problems.append("缺少 title，或 title 不是非空字符串")

    for key in ("subtitle", "age", "lang", "end_text"):
        if key in book and book[key] is not None and not isinstance(book[key], str):
            problems.append(f"{key} 必须是字符串")

    cover_svg = book.get("cover_svg")
    seen_ids: dict[str, str] = {}
    if not isinstance(cover_svg, str):
        problems.append("cover_svg 缺失或不是字符串")
    else:
        validate_svg(cover_svg, "封面", problems, seen_ids)

    pages = book.get("pages")
    if not isinstance(pages, list):
        problems.append("pages 必须是数组")
        return problems
    if len(pages) < 4:
        problems.append(f"只有 {len(pages)} 页正文，绘本至少要 4 页")

    for index, page in enumerate(pages, start=1):
        label = f"第 {index} 页"
        if not isinstance(page, dict):
            problems.append(f"{label} 必须是对象")
            continue

        text = page.get("text")
        if not is_nonempty_string(text):
            problems.append(f"{label} 没有文案，或文案不是非空字符串")

        svg = page.get("svg")
        if not isinstance(svg, str):
            problems.append(f"{label} SVG 缺失或不是字符串")
        else:
            validate_svg(svg, label, problems, seen_ids)

        audio = page.get("audio", "")
        if not isinstance(audio, str):
            problems.append(f"{label} audio 必须是字符串")
        elif audio and not AUDIO_DATA_URI.fullmatch(audio):
            problems.append(
                f"{label} audio 必须是空字符串，或 data:audio/...;base64,... 形式的内嵌音频"
            )

    return problems


def json_for_script(book: dict[str, Any]) -> str:
    """Serialize JSON safely for insertion inside a <script> tag."""
    return (
        json.dumps(book, ensure_ascii=False, separators=(",", ":"))
        .replace("<", "\\u003c")
        .replace(">", "\\u003e")
        .replace("&", "\\u0026")
        .replace("\u2028", "\\u2028")
        .replace("\u2029", "\\u2029")
    )


def main() -> None:
    if len(sys.argv) != 3:
        fail("用法: python scripts/build_book.py book.json output/book.html")

    source_path = Path(sys.argv[1])
    output_path = Path(sys.argv[2])
    if not source_path.is_file():
        fail(f"找不到输入文件: {source_path}")
    if not TEMPLATE.is_file():
        fail(f"找不到模板文件: {TEMPLATE}")

    try:
        book = json.loads(source_path.read_text(encoding="utf-8"))
    except UnicodeDecodeError:
        fail(f"无法以 UTF-8 读取: {source_path}")
    except json.JSONDecodeError as error:
        fail(f"book.json 不是有效 JSON：第 {error.lineno} 行、第 {error.colno} 列：{error.msg}")

    problems = validate_book(book)
    if problems:
        for problem in problems:
            print(f"[build_book] ✗ {problem}", file=sys.stderr)
        raise SystemExit(1)

    template = TEMPLATE.read_text(encoding="utf-8")
    if "__TITLE__" not in template or "__BOOK_DATA__" not in template:
        fail("模板缺少 __TITLE__ 或 __BOOK_DATA__ 占位符")

    output_path.parent.mkdir(parents=True, exist_ok=True)
    generated = template.replace("__TITLE__", html.escape(book["title"], quote=True))
    generated = generated.replace("__BOOK_DATA__", json_for_script(book))
    output_path.write_text(generated, encoding="utf-8")

    pages = book["pages"]
    audio_count = sum(1 for page in pages if page.get("audio"))
    mode = (
        f"{audio_count}/{len(pages)} 页内嵌真实音频"
        if audio_count
        else "浏览器朗读模式（没有内嵌音频；取决于浏览器可用语音）"
    )
    size_kb = output_path.stat().st_size // 1024
    print(
        f"[build_book] ✓ 生成 {output_path}（{len(pages)} 页正文 + 封面 + 结尾，"
        f"{mode}，{size_kb} KB）"
    )


if __name__ == "__main__":
    main()
