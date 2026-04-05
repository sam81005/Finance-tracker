from __future__ import annotations

import re
import textwrap
from pathlib import Path


PAGE_WIDTH = 612
PAGE_HEIGHT = 792
LEFT_MARGIN = 54
RIGHT_MARGIN = 54
TOP_MARGIN = 56
BOTTOM_MARGIN = 56
MAX_WIDTH = 88


def escape_pdf_text(value: str) -> str:
    return value.replace("\\", "\\\\").replace("(", "\\(").replace(")", "\\)")


def parse_markdown_lines(markdown: str) -> list[tuple[str, str]]:
    parsed: list[tuple[str, str]] = []
    in_code_block = False

    for raw_line in markdown.splitlines():
        if raw_line.startswith("```"):
            in_code_block = not in_code_block
            parsed.append(("blank", ""))
            continue

        if in_code_block:
            parsed.append(("code", raw_line.rstrip()))
            continue

        line = raw_line.rstrip()
        if not line:
            parsed.append(("blank", ""))
        elif line.startswith("# "):
            parsed.append(("h1", line[2:].strip()))
        elif line.startswith("## "):
            parsed.append(("h2", line[3:].strip()))
        elif line.startswith("### "):
            parsed.append(("h3", line[4:].strip()))
        elif line.startswith("- "):
            parsed.append(("bullet", line[2:].strip()))
        elif re.match(r"^\d+\.\s+", line):
            parsed.append(("number", re.sub(r"^\d+\.\s+", "", line)))
        else:
            parsed.append(("text", line))

    return parsed


def wrap_items(parsed: list[tuple[str, str]]) -> list[tuple[str, str]]:
    wrapped: list[tuple[str, str]] = []

    for kind, value in parsed:
        if kind == "blank":
            wrapped.append((kind, value))
            continue

        if kind == "h1":
            wrapped.extend((kind, line) for line in textwrap.wrap(value, width=50) or [""])
            wrapped.append(("blank", ""))
            continue

        if kind == "h2":
            wrapped.extend((kind, line) for line in textwrap.wrap(value, width=60) or [""])
            continue

        if kind == "h3":
            wrapped.extend((kind, line) for line in textwrap.wrap(value, width=68) or [""])
            continue

        if kind == "bullet":
            lines = textwrap.wrap(value, width=MAX_WIDTH - 4) or [""]
            wrapped.append(("bullet", f"- {lines[0]}"))
            for continuation in lines[1:]:
                wrapped.append(("indent", continuation))
            continue

        if kind == "number":
            lines = textwrap.wrap(value, width=MAX_WIDTH - 4) or [""]
            wrapped.append(("number", f"1. {lines[0]}"))
            for continuation in lines[1:]:
                wrapped.append(("indent", continuation))
            continue

        if kind == "code":
            code_lines = textwrap.wrap(value, width=78, replace_whitespace=False, drop_whitespace=False) or [""]
            wrapped.extend((kind, line) for line in code_lines)
            continue

        text_lines = textwrap.wrap(value, width=MAX_WIDTH) or [""]
        wrapped.extend((kind, line) for line in text_lines)

    return wrapped


def style_for(kind: str) -> tuple[str, int, int, int]:
    if kind == "h1":
        return "F2", 18, LEFT_MARGIN, 24
    if kind == "h2":
        return "F2", 13, LEFT_MARGIN, 18
    if kind == "h3":
        return "F2", 11, LEFT_MARGIN, 15
    if kind == "bullet":
        return "F1", 10, LEFT_MARGIN + 10, 14
    if kind == "number":
        return "F1", 10, LEFT_MARGIN + 10, 14
    if kind == "indent":
        return "F1", 10, LEFT_MARGIN + 26, 14
    if kind == "code":
        return "F3", 9, LEFT_MARGIN + 14, 12
    return "F1", 10, LEFT_MARGIN, 14


def paginate(lines: list[tuple[str, str]]) -> list[str]:
    pages: list[str] = []
    commands: list[str] = []
    y = PAGE_HEIGHT - TOP_MARGIN

    for kind, text in lines:
        if kind == "blank":
            y -= 8
            if y < BOTTOM_MARGIN:
                pages.append("\n".join(commands))
                commands = []
                y = PAGE_HEIGHT - TOP_MARGIN
            continue

        font_name, font_size, x, leading = style_for(kind)
        if y - leading < BOTTOM_MARGIN:
            pages.append("\n".join(commands))
            commands = []
            y = PAGE_HEIGHT - TOP_MARGIN

        escaped = escape_pdf_text(text)
        commands.append(f"BT /{font_name} {font_size} Tf 1 0 0 1 {x} {y} Tm ({escaped}) Tj ET")
        y -= leading

    if commands:
        pages.append("\n".join(commands))
    return pages


def build_pdf(page_contents: list[str]) -> bytes:
    objects: list[bytes] = []

    def add_object(data: str | bytes) -> int:
        if isinstance(data, str):
            encoded = data.encode("latin-1", "replace")
        else:
            encoded = data
        objects.append(encoded)
        return len(objects)

    font1_id = add_object("<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>")
    font2_id = add_object("<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica-Bold >>")
    font3_id = add_object("<< /Type /Font /Subtype /Type1 /BaseFont /Courier >>")

    content_ids: list[int] = []
    page_ids: list[int] = []

    for content in page_contents:
        stream = content.encode("latin-1", "replace")
        content_obj = (
            f"<< /Length {len(stream)} >>\nstream\n".encode("latin-1")
            + stream
            + b"\nendstream"
        )
        content_ids.append(add_object(content_obj))
        page_ids.append(0)

    pages_id = add_object("<< /Type /Pages /Kids [] /Count 0 >>")

    for index, content_id in enumerate(content_ids):
        page_id = add_object(
            f"<< /Type /Page /Parent {pages_id} 0 R /MediaBox [0 0 {PAGE_WIDTH} {PAGE_HEIGHT}] "
            f"/Resources << /Font << /F1 {font1_id} 0 R /F2 {font2_id} 0 R /F3 {font3_id} 0 R >> >> "
            f"/Contents {content_id} 0 R >>"
        )
        page_ids[index] = page_id

    kids = " ".join(f"{page_id} 0 R" for page_id in page_ids)
    objects[pages_id - 1] = f"<< /Type /Pages /Kids [{kids}] /Count {len(page_ids)} >>".encode("latin-1")
    catalog_id = add_object(f"<< /Type /Catalog /Pages {pages_id} 0 R >>")

    output = bytearray(b"%PDF-1.4\n%\xe2\xe3\xcf\xd3\n")
    offsets = [0]

    for index, obj in enumerate(objects, start=1):
        offsets.append(len(output))
        output.extend(f"{index} 0 obj\n".encode("latin-1"))
        output.extend(obj)
        output.extend(b"\nendobj\n")

    xref_offset = len(output)
    output.extend(f"xref\n0 {len(objects) + 1}\n".encode("latin-1"))
    output.extend(b"0000000000 65535 f \n")
    for offset in offsets[1:]:
        output.extend(f"{offset:010d} 00000 n \n".encode("latin-1"))

    output.extend(
        (
            f"trailer\n<< /Size {len(objects) + 1} /Root {catalog_id} 0 R >>\n"
            f"startxref\n{xref_offset}\n%%EOF\n"
        ).encode("latin-1")
    )
    return bytes(output)


def main() -> None:
    root = Path(__file__).resolve().parents[1]
    source_path = root / "docs" / "interview_guide.md"
    output_path = root / "docs" / "finance_backend_interview_guide.pdf"

    markdown = source_path.read_text(encoding="utf-8")
    parsed = parse_markdown_lines(markdown)
    wrapped = wrap_items(parsed)
    pages = paginate(wrapped)
    pdf_bytes = build_pdf(pages)
    output_path.write_bytes(pdf_bytes)

    print(f"Created {output_path}")


if __name__ == "__main__":
    main()
