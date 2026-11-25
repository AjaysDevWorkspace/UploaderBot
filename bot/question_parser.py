"""
Turns the lines of a Word document into validated MCQs.

Expected format (see README.md for the full guide):

    Q1. Question text (can continue over several paragraphs)
    A) option
    B) option
    C) option
    D) option
    Answer: B
    Explanation: optional, can continue over several paragraphs
    Difficulty: Easy
    Bloom: Understand            (optional: Remember, Understand, Apply, Analyze, Evaluate, Create)
    Topic: HTTP                  (optional, recorded only)
    Marks: 1                     (optional, recorded only)

Nothing is guessed: a question with a missing, ambiguous or inconsistent part is
marked invalid with a clear reason and is never uploaded.
"""

import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

from pydantic import ValidationError

from .config import Settings
from .models import MCQ, PORTAL_LABELS
from .word_reader import Line, Segment, read_docx, segments_to_html, strip_prefix

# ------------------------------------------------------------ patterns
Q_PATTERNS = [
    re.compile(r"^\s*Q\s*\.?\s*(\d+)\s*(?:[.):\-–]\s*|\s+|$)", re.I),          # Q1.  Q1)  Q 1:  Q.1
    re.compile(r"^\s*Question\s*(?:No\.?\s*)?(\d+)\s*(?:[.):\-–]\s*|\s+|$)", re.I),  # Question 1:
    re.compile(r"^\s*Question\s*()[:\-–]\s*", re.I),                           # Question:
]
# "A) text", "(A) text", "A. text", "A: text", "A - text", "Option A: text".
# ".", ":", "-" must be followed by a space so words like "A-star" stay question text.
OPTION_RE = re.compile(r"^\s*(?:Option\s+)?\(?([A-Ea-e])\s*(?:\)\s*|[.:\-–](?:\s+|$))", re.I)
FIELD_ALIASES = {
    "answer": "answer", "correct answer": "answer", "correct option": "answer",
    "ans": "answer", "correct choice": "answer",
    "explanation": "explanation", "solution": "explanation",
    "difficulty": "difficulty", "difficulty level": "difficulty",
    "bloom": "bloom", "blooms": "bloom", "bloom's": "bloom", "bloom taxonomy": "bloom",
    "blooms taxonomy": "bloom", "bloom's taxonomy": "bloom", "bloom level": "bloom",
    "topic": "topic", "marks": "marks", "mark": "marks",
}
FIELD_RE = re.compile(
    r"^\s*(" + "|".join(sorted((re.escape(k) for k in FIELD_ALIASES), key=len, reverse=True))
    + r")\s*[:\-–]\s*", re.I)
DIFFICULTIES = ["Beginner", "Easy", "Medium", "Hard", "Challenge"]
BLOOM_LEVELS = ["Remember", "Understand", "Apply", "Analyze", "Evaluate", "Create"]
BLOOM_SPELLINGS = {"analyse": "Analyze"}   # British spelling of the same level
MULTI_ANSWER_RE = re.compile(r"^\(?[A-E]\)?\s*(?:(?:,|&|and|/)\s*\(?[A-E]\)?\s*)+$", re.I)
LETTER_ANSWER_RE = re.compile(r"^\(?\s*(?:option\s*)?([A-E])\s*\)?(?:\s*[.):\-–]\s*(.*))?$", re.I | re.S)


def _match_question(text: str):
    for pat in Q_PATTERNS:
        m = pat.match(text)
        if m:
            return m
    return None


def is_known_key(cell_text: str) -> bool:
    t = cell_text.strip().rstrip(":").strip()
    return bool(
        _match_question(t + ":") or t.upper() in PORTAL_LABELS
        or t.casefold() in FIELD_ALIASES or t.casefold() == "question"
    )


def _norm(s: str) -> str:
    return re.sub(r"\s+", " ", s or "").strip().casefold()


# ------------------------------------------------------------ data
@dataclass
class Block:
    written_number: Optional[str]
    start_position: int
    question_paras: list[list[Segment]] = field(default_factory=list)
    options: list[tuple[str, list[str]]] = field(default_factory=list)   # (label, lines)
    fields: dict[str, list[str]] = field(default_factory=dict)
    state: str = "question"
    errors: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)


@dataclass
class ParsedQuestion:
    mcq_no: int
    written_number: Optional[str]
    mcq: Optional[MCQ]
    errors: list[str]
    warnings: list[str]
    raw: dict

    @property
    def valid(self) -> bool:
        return self.mcq is not None and not self.errors

    @property
    def preview(self) -> str:
        if self.mcq:
            return self.mcq.preview()
        q = re.sub(r"\s+", " ", self.raw.get("question_text") or "").strip()
        return (q[:69] + "…") if len(q) > 70 else (q or "(no question text)")

    def to_json(self) -> dict:
        return {
            "mcq_no": self.mcq_no,
            "written_number": self.written_number,
            "valid": self.valid,
            "errors": self.errors,
            "warnings": self.warnings,
            "question": self.mcq.model_dump(exclude_none=True) if self.mcq else self.raw,
            "fingerprint": self.mcq.fingerprint() if self.mcq else None,
        }


# ------------------------------------------------------------ parsing
def _split_blocks(lines: list[Line]) -> tuple[list[Block], list[str]]:
    blocks: list[Block] = []
    doc_notes: list[str] = []
    cur: Optional[Block] = None

    for line in lines:
        text = line.text
        if not text.strip():
            if cur and cur.state == "question" and cur.question_paras:
                cur.question_paras.append([])            # keep blank line as spacing
            continue

        qm = _match_question(text)
        if qm:
            cur = Block(written_number=qm.group(1) or None, start_position=line.position)
            blocks.append(cur)
            rest = strip_prefix(line.segments, qm.end())
            if "".join(s.text for s in rest).strip():
                cur.question_paras.append(rest)
            _check_line_flags(cur, line)
            continue

        if cur is None:
            continue                                   # title / instructions before Q1
        if line.is_heading:
            continue                                   # section headings are ignored
        _check_line_flags(cur, line)

        fm = FIELD_RE.match(text)
        if fm:
            key = FIELD_ALIASES[fm.group(1).casefold()]
            value = text[fm.end():].strip()
            if key in cur.fields and key != "explanation":
                cur.errors.append(f'"{fm.group(1)}" appears twice')
            cur.fields.setdefault(key, [])
            if value:
                cur.fields[key].append(value)
            cur.state = key
            continue

        om = OPTION_RE.match(text)
        if om and cur.state in ("question", "option"):
            label = om.group(1).upper()
            expected = PORTAL_LABELS[len(cur.options)] if len(cur.options) < 5 else None
            value = text[om.end():].strip()
            if label == expected:
                cur.options.append((label, [value] if value else []))
                cur.state = "option"
                continue
            if cur.state == "option":
                cur.errors.append(f"option {label} is out of order (expected {expected or 'no more options'})")
                cur.options.append((label, [value] if value else []))
                continue
            # state == "question" and label isn't A: ordinary question text (e.g. "B. Tech ...")

        if line.is_list and cur.state in ("question", "option") \
                and not any("automatic numbering" in w for w in cur.warnings):
            cur.warnings.append(
                "uses Word's automatic numbering/bullets. Letters or numbers created by Word "
                "are invisible to the bot, so type 'A) ', 'B) ' by hand.")

        if cur.state == "question":
            cur.question_paras.append(line.segments)
        elif cur.state == "option":
            cur.options[-1][1].append(text.strip())
        elif cur.state == "explanation":
            cur.fields["explanation"].append(text.strip())
        else:
            snippet = text.strip()[:60]
            cur.errors.append(f'unrecognised text after "{cur.state}": "{snippet}". '
                              "Put extra notes after 'Explanation:'.")

    if not blocks:
        doc_notes.append("No questions found. Each question must start with 'Q1.', 'Q2.' and so on.")
    return blocks, doc_notes


def _check_line_flags(block: Block, line: Line) -> None:
    if line.has_image:
        block.errors.append("contains an image. Images are not supported yet; "
                            "remove it or upload this question manually.")
    if line.has_equation:
        block.errors.append("contains a Word equation, which the bot cannot read. Type it as text.")


def _resolve_answer(raw: str, options: dict[str, str]) -> tuple[Optional[str], Optional[str]]:
    """Returns (letter, error)."""
    raw = raw.strip()
    if MULTI_ANSWER_RE.match(raw):
        return None, f'answer "{raw}" has more than one option; the portal allows exactly one'
    m = LETTER_ANSWER_RE.match(raw)
    if m:
        letter, text = m.group(1).upper(), (m.group(2) or "").strip()
        if letter not in options:
            return None, f"answer {letter} is not one of the options ({', '.join(options)})"
        if text and _norm(text) != _norm(options[letter]):
            return None, (f'answer says "{raw}" but option {letter} is "{options[letter][:50]}". '
                          "Fix whichever is wrong.")
        return letter, None
    matches = [k for k, v in options.items() if _norm(v) == _norm(raw)]
    if len(matches) == 1:
        return matches[0], None
    return None, f'answer "{raw[:60]}" does not match any option letter or option text'


def _build(block: Block, mcq_no: int, source: str, settings: Settings) -> ParsedQuestion:
    errors, warnings = list(block.errors), list(block.warnings)

    # question text: drop leading/trailing blank paragraphs
    paras = block.question_paras
    while paras and not paras[0]:
        paras = paras[1:]
    while paras and not paras[-1]:
        paras = paras[:-1]
    q_plain = "\n".join("".join(s.text for s in p) for p in paras).strip()
    q_html = "<br />".join(segments_to_html(p) for p in paras).strip()

    options = {label: "\n".join(t).strip() for label, t in block.options}
    f = {k: "\n".join(v).strip() for k, v in block.fields.items()}

    raw = {"question_text": q_plain, "options": options, "answer": f.get("answer"),
           "explanation": f.get("explanation"), "difficulty": f.get("difficulty"),
           "bloom_taxonomy": f.get("bloom"), "topic": f.get("topic"), "marks": f.get("marks")}

    if not q_plain:
        errors.append("question text is missing")
    elif q_plain.lstrip().upper().startswith("[EXAMPLE"):
        errors.append("this is an example question from the template; delete it before uploading")
    if not options:
        errors.append("no options found. Write them as 'A) ...', 'B) ...'")
    elif len(options) < settings.min_options:
        errors.append(f"only {len(options)} options; at least {settings.min_options} required")
    for label, text in options.items():
        if not text:
            errors.append(f"option {label} is empty")

    answer = None
    if not f.get("answer"):
        errors.append("'Answer:' line is missing")
    elif options:
        answer, err = _resolve_answer(f["answer"], options)
        if err:
            errors.append(err)

    difficulty = None
    if f.get("difficulty"):
        hit = [d for d in DIFFICULTIES if d.casefold() == f["difficulty"].casefold()]
        if hit:
            difficulty = hit[0]
        else:
            errors.append(f'difficulty "{f["difficulty"]}" is not one of {", ".join(DIFFICULTIES)}')
    elif settings.default_difficulty:
        difficulty = settings.default_difficulty
        warnings.append(f"no difficulty given; using default '{difficulty}'")
    else:
        errors.append("'Difficulty:' line is missing")

    bloom = None
    if f.get("bloom"):
        value = BLOOM_SPELLINGS.get(f["bloom"].casefold(), f["bloom"])
        hit = [b for b in BLOOM_LEVELS if b.casefold() == value.casefold()]
        if hit:
            bloom = hit[0]
        else:
            errors.append(f'Bloom "{f["bloom"]}" is not one of {", ".join(BLOOM_LEVELS)}')

    marks = None
    if f.get("marks"):
        try:
            marks = float(f["marks"])
        except ValueError:
            errors.append(f'marks "{f["marks"]}" is not a number')

    mcq = None
    if not errors:
        try:
            mcq = MCQ(
                question_text=q_plain, question_html=q_html,
                options=[{"label": k, "text": v} for k, v in options.items()],
                correct_answer=answer, explanation=f.get("explanation") or None,
                difficulty=difficulty, bloom_taxonomy=bloom,
                topic=f.get("topic") or None, marks=marks,
                source_ref=source, question_no=mcq_no,
            )
        except ValidationError as exc:
            for e in exc.errors():
                errors.append(str(e.get("msg", "")).replace("Value error, ", ""))
    return ParsedQuestion(mcq_no, block.written_number, mcq, errors, warnings, raw)


def parse_document(path: Path, settings: Settings) -> tuple[list[ParsedQuestion], list[str]]:
    lines = read_docx(path, is_known_key)
    blocks, doc_notes = _split_blocks(lines)
    # MCQ No. = the number written in the document (Question 10 -> 10) when every question
    # has a unique number; otherwise the position in the document (1, 2, 3...).
    written = [b.written_number for b in blocks]
    use_written = bool(blocks) and all(written) and len(set(map(int, written))) == len(written)
    numbers = [int(w) for w in written] if use_written else list(range(1, len(blocks) + 1))
    if not use_written and any(written):
        for b in blocks:
            b.warnings.append("question numbers are missing or repeated in the document, "
                              "so MCQ No. uses the position instead")
    results = [_build(b, n, path.name, settings) for n, b in zip(numbers, blocks)]

    # duplicates inside the same document
    seen: dict[str, int] = {}
    for r in results:
        key = _norm(r.raw.get("question_text") or "")
        if not key:
            continue
        if key in seen:
            r.errors.append(f"duplicate of MCQ {seen[key]} in this document")
            r.mcq = None
        else:
            seen[key] = r.mcq_no
    return results, doc_notes
