#!/usr/bin/env python3
"""
Render docs/MISTAKES.md as a plain .txt that reads well in Notepad.

The user asked for a txt file. Markdown source is fine for GitHub, but opened
in a plain editor it is full of `**`, `#` and pipe tables, which is exactly
the noise you do not want in a document you are supposed to read start to
finish. This converts:

  # Heading      -> upper case, boxed with =====
  ## Heading     -> underlined with -----
  **bold**       -> plain text
  `code`         -> plain text
  | tables |     -> aligned columns with real spaces
  ```fences```   -> indented blocks, fences removed
  - [ ] item     -> [ ] item

Line width is capped so the result is readable in a fixed 80-column window,
except inside code blocks, which are never reflowed.

Run:  python3 tools/md_to_txt.py
"""

import os
import re
import sys
import textwrap

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SRC = os.path.join(ROOT, "docs", "MISTAKES.md")
DST = os.path.join(ROOT, "docs", "MISTAKES.txt")
WIDTH = 78


def strip_inline(text):
    """Remove inline markdown emphasis and code ticks."""
    text = re.sub(r"\*\*(.+?)\*\*", r"\1", text)
    text = re.sub(r"(?<!\w)\*(?!\s)(.+?)(?<!\s)\*(?!\w)", r"\1", text)
    text = re.sub(r"`([^`]+)`", r"\1", text)
    text = re.sub(r"\[([^\]]+)\]\([^)]+\)", r"\1", text)
    return text


def render_table(rows):
    """Turn a markdown table into aligned plain columns."""
    cells = []
    for row in rows:
        parts = [strip_inline(c).strip()
                 for c in row.strip().strip("|").split("|")]
        # A separator row is all dashes; drop it.
        if all(re.fullmatch(r":?-{2,}:?", p) for p in parts if p):
            continue
        cells.append(parts)
    if not cells:
        return []

    columns = max(len(r) for r in cells)
    cells = [r + [""] * (columns - len(r)) for r in cells]

    # The last column usually holds prose, so give it whatever is left and
    # wrap it instead of letting the line run off the screen.
    widths = []
    for i in range(columns - 1):
        widths.append(max(len(r[i]) for r in cells))
    lead = sum(widths) + 3 * (columns - 1)
    last = max(20, WIDTH - lead)

    out = []
    for index, row in enumerate(cells):
        head = "   ".join(row[i].ljust(widths[i]) for i in range(columns - 1))
        tail = textwrap.wrap(row[-1], last) or [""]
        out.append((head + "   " + tail[0]).rstrip())
        for extra in tail[1:]:
            out.append(" " * (lead) + extra)
        if index == 0:
            out.append("-" * min(WIDTH, lead + last))
    return out


def join_paragraphs(text):
    """Join wrapped prose lines so inline markdown spanning a line break works.

    `**bold text` on one line and `more**` on the next is a single emphasis
    span in markdown, but a line-by-line regex never sees it and leaves the
    asterisks in. Paragraphs are re-joined first, and re-wrapped later.
    """
    out = []
    buffer = []
    in_code = False

    def flush():
        if buffer:
            out.append(" ".join(buffer))
            buffer.clear()

    for raw in text.split("\n"):
        line = raw.rstrip()
        if line.strip().startswith("```"):
            flush()
            in_code = not in_code
            out.append(line)
            continue
        if in_code:
            out.append(line)
            continue
        # Structural lines are never merged into a paragraph.
        if (not line.strip()
                or line.lstrip().startswith(("|", ">", "#"))
                or re.fullmatch(r"-{3,}", line.strip())):
            flush()
            out.append(line)
            continue
        # A list item starts a new buffer but keeps collecting: its
        # continuation lines belong to it, and emphasis often spans them.
        if re.match(r"^\s*([-*]|\d+\.)\s", line):
            flush()
            buffer.append(line)
            continue
        buffer.append(line.strip())
    flush()
    return "\n".join(out)


def convert(text):
    lines = join_paragraphs(text).split("\n")
    out = []
    in_code = False
    table = []

    def flush_table():
        if table:
            out.extend(render_table(table))
            out.append("")
            table.clear()

    for raw in lines:
        line = raw.rstrip()

        if line.strip().startswith("```"):
            flush_table()
            in_code = not in_code
            if not in_code:
                out.append("")
            continue

        if in_code:
            out.append("    " + line)
            continue

        if line.lstrip().startswith("|"):
            table.append(line)
            continue
        flush_table()

        if re.fullmatch(r"-{3,}", line.strip()):
            out.append("")
            out.append("=" * WIDTH)
            out.append("")
            continue

        m = re.match(r"^(#{1,6})\s+(.*)$", line)
        if m:
            level, title = len(m.group(1)), strip_inline(m.group(2))
            if level == 1:
                out.append("")
                out.append(title.upper())
                out.append("=" * min(WIDTH, len(title)))
            else:
                out.append("")
                out.append(title)
                out.append("-" * min(WIDTH, len(title)))
            out.append("")
            continue

        if not line.strip():
            out.append("")
            continue

        body = strip_inline(line)

        # Keep list and checkbox indentation, wrap the rest.
        m = re.match(r"^(\s*)([-*]|\d+\.)\s+(.*)$", body)
        if m:
            indent, bullet, rest = m.groups()
            prefix = "%s%s " % (indent, "-" if bullet in "-*" else bullet)
            wrapped = textwrap.wrap(rest, WIDTH - len(prefix)) or [""]
            out.append(prefix + wrapped[0])
            for extra in wrapped[1:]:
                out.append(" " * len(prefix) + extra)
            continue

        if body.startswith(">"):
            body = body.lstrip("> ").strip()
            for chunk in textwrap.wrap(body, WIDTH - 4) or [""]:
                out.append("    " + chunk)
            continue

        indent = len(body) - len(body.lstrip())
        for chunk in textwrap.wrap(body.strip(), WIDTH - indent) or [""]:
            out.append(" " * indent + chunk)

    flush_table()

    # Collapse runs of blank lines.
    result = []
    for line in out:
        if not line.strip() and result and not result[-1].strip():
            continue
        result.append(line)
    return "\n".join(result).strip() + "\n"


def main():
    text = open(SRC, encoding="utf-8").read()
    plain = convert(text)
    open(DST, "w", encoding="utf-8").write(plain)

    longest = max(len(l) for l in plain.split("\n"))
    print("wrote %s" % os.path.relpath(DST, ROOT))
    print("  %d lines, longest %d characters"
          % (len(plain.split("\n")), longest))
    leftovers = [c for c in ("**", "](", "|--") if c in plain]
    if leftovers:
        print("  WARNING leftover markdown: %s" % ", ".join(leftovers))
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
