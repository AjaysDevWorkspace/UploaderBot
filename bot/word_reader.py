"""
Reads a .docx file into an ordered list of Lines (paragraphs and table rows).

Each Line keeps formatted segments, so the question text can be turned into the
HTML the portal's editor stores (<strong>, <em>, <u>, <code>), plus flags for
things the bot cannot upload safely (images, equations, auto-numbering).
"""

import html
from dataclasses import dataclass, field
from pathlib import Path

from docx import Document
from docx.table import Table
from docx.text.paragraph import Paragraph

MONO_FONTS = {"consolas", "courier new", "courier", "cascadia code", "cascadia mono",
              "lucida console", "monaco", "menlo", "source code pro", "fira code",
              "jetbrains mono", "roboto mono", "monospace"}


@dataclass
class Segment:
    text: str
    bold: bool = False
    italic: bool = False
    underline: bool = False
    code: bool = False

    def fmt(self):
        return (self.bold, self.italic, self.underline, self.code)


@dataclass
class Line:
    segments: list[Segment]
    is_list: bool = False        # Word auto-numbered or bulleted paragraph
    is_heading: bool = False
    has_image: bool = False
    has_equation: bool = False
    from_table: bool = False
    position: int = 0            # 1-based order in the document (for error messages)
    flags: list[str] = field(default_factory=list)

    @property
    def text(self) -> str:
        return "".join(s.text for s in self.segments)


# ------------------------------------------------------------ segments -> HTML
def segments_to_html(segments: list[Segment]) -> str:
    merged: list[Segment] = []
    for s in segments:
        if not s.text:
            continue
        if merged and merged[-1].fmt() == s.fmt():
            merged[-1] = Segment(merged[-1].text + s.text, *s.fmt())
        else:
            merged.append(Segment(s.text, *s.fmt()))
    out = []
    for s in merged:
        t = html.escape(s.text, quote=False).replace("\n", "<br />").replace("\t", "    ")
        if s.code:
            t = f"<code>{t}</code>"
        if s.underline:
            t = f"<u>{t}</u>"
        if s.italic:
            t = f"<em>{t}</em>"
        if s.bold:
            t = f"<strong>{t}</strong>"
        out.append(t)
    return "".join(out)


def strip_prefix(segments: list[Segment], n_chars: int) -> list[Segment]:
    """Remove the first n characters (e.g. 'Q1. ' or 'A) ') across segments."""
    result, remaining = [], n_chars
    for s in segments:
        if remaining >= len(s.text):
            remaining -= len(s.text)
            continue
        if remaining:
            s = Segment(s.text[remaining:], *s.fmt())
            remaining = 0
        result.append(s)
    return result


# ------------------------------------------------------------ paragraphs
def _is_mono(run, paragraph) -> bool:
    for name in (run.font.name,
                 run.style.font.name if run.style is not None else None,
                 paragraph.style.font.name if paragraph.style is not None else None):
        if name and name.strip().lower() in MONO_FONTS:
            return True
    return False


def _paragraph_segments(p: Paragraph) -> list[Segment]:
    segs = []
    items = p.iter_inner_content() if hasattr(p, "iter_inner_content") else p.runs
    for item in items:
        runs = getattr(item, "runs", None) or [item]   # hyperlinks contain runs
        for r in runs:
            if not r.text:
                continue
            segs.append(Segment(r.text, bool(r.bold), bool(r.italic),
                                bool(r.underline), _is_mono(r, p)))
    return segs


def _has_tag(element, suffixes: tuple[str, ...]) -> bool:
    return any(el.tag.split("}")[-1] in suffixes for el in element.iter())


def _paragraph_line(p: Paragraph) -> Line:
    ppr = p._p.pPr
    style = (p.style.name or "") if p.style is not None else ""
    is_list = (ppr is not None and ppr.numPr is not None) or style.lower().startswith("list")
    return Line(
        segments=_paragraph_segments(p),
        is_list=is_list,
        is_heading=style.lower().startswith(("heading", "title")),
        has_image=_has_tag(p._p, ("drawing", "pict")),
        has_equation=_has_tag(p._p, ("oMath", "oMathPara")),
    )


# ------------------------------------------------------------ tables
def _table_lines(t: Table, key_pattern) -> list[Line]:
    """Two-column rows like | Answer | B | become 'Answer: B'.
    Other rows become one line with cells joined by ' | '."""
    lines = []
    for row in t.rows:
        cells, seen = [], set()
        for c in row.cells:                      # merged cells repeat; keep each once
            if id(c._tc) in seen:
                continue
            seen.add(id(c._tc))
            cells.append(c)
        texts = ["\n".join(p.text for p in c.paragraphs).strip() for c in cells]
        image = any(_has_tag(c._tc, ("drawing", "pict")) for c in cells)
        equation = any(_has_tag(c._tc, ("oMath", "oMathPara")) for c in cells)
        if not any(texts):
            continue
        if len(texts) == 2 and key_pattern(texts[0]):
            text = f"{texts[0].rstrip(':').strip()}: {texts[1]}"
            if texts[0].strip().upper() in {"A", "B", "C", "D", "E"}:
                text = f"{texts[0].strip().upper()}) {texts[1]}"
        else:
            text = " | ".join(texts)
        lines.append(Line(segments=[Segment(text)], from_table=True,
                          has_image=image, has_equation=equation))
    return lines


def read_docx(path: Path, key_pattern) -> list[Line]:
    """key_pattern(text) -> bool says whether a table's first cell is a known label
    (Q1, Question, A–E, Answer, Explanation...)."""
    doc = Document(str(path))
    lines: list[Line] = []
    for child in doc.element.body.iterchildren():
        tag = child.tag.split("}")[-1]
        if tag == "p":
            lines.append(_paragraph_line(Paragraph(child, doc)))
        elif tag == "tbl":
            lines.extend(_table_lines(Table(child, doc), key_pattern))
    for i, line in enumerate(lines, start=1):
        line.position = i
    return lines
