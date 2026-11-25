"""
Excel upload report  ->  output/upload_report.xlsx

Sheet "Uploads": one row per MCQ
    MCQ No. | Status | Django Link | Django ID | Question | Topic | Correct Answer |
    Difficulty | Source File | Portal Hash | Uploaded At | Error / Note | Assessment Status

Sheet "Summary": live COUNTIF totals over the Uploads sheet, plus a status legend.

Status values:
    Done         saved on the portal and verified
    Not done     rejected by the portal, or invalid in the Word document
    Needs check  the run stopped right after Save was clicked; check the MCQ list
"""

import logging
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Optional

from openpyxl import Workbook
from openpyxl.styles import Alignment, Border, Font, PatternFill, Side
from openpyxl.utils import get_column_letter

from .config import PROJECT_ROOT
from .tracker import Tracker

log = logging.getLogger(__name__)

REPORT_PATH = PROJECT_ROOT / "output" / "upload_report.xlsx"

STATUS_LABELS = {"success": "Done", "failed": "Not done", "in_progress": "Needs check",
                 "invalid": "Not done"}

FONT = "Arial"
HEADER_FILL = PatternFill("solid", start_color="1F4E79")
STATUS_FILLS = {
    "Done": PatternFill("solid", start_color="C6EFCE"),
    "Not done": PatternFill("solid", start_color="FFC7CE"),
    "Needs check": PatternFill("solid", start_color="FFEB9C"),
}
THIN = Side(style="thin", color="BFBFBF")
BORDER = Border(left=THIN, right=THIN, top=THIN, bottom=THIN)

# (header, width)
COLUMNS = [
    ("MCQ No.", 9), ("Status", 13), ("Django Link", 62), ("Django ID", 12),
    ("Question", 55), ("Topic", 22), ("Correct Answer", 10), ("Difficulty", 11), ("Source File", 22),
    ("Portal Hash", 15), ("Uploaded At", 19), ("Error / Note", 50), ("Assessment Status", 18),
]


@dataclass
class ReportRow:
    mcq_no: Optional[int]
    status: str                       # Done / Not done / Needs check
    django_link: Optional[str] = None
    django_id: Optional[str] = None
    question: str = ""
    topic: Optional[str] = None
    correct_answer: Optional[str] = None
    difficulty: Optional[str] = None
    source_file: Optional[str] = None
    portal_hash: Optional[str] = None
    uploaded_at: Optional[datetime] = None
    note: Optional[str] = None
    assessment_status: str = "Not added yet"


def _clean_link(url: Optional[str]) -> Optional[str]:
    if not url:
        return None
    return url.split("?")[0].split("#")[0]


def _row_from_db(r) -> ReportRow:
    status = STATUS_LABELS.get(r["status"], r["status"])
    note = r["error"]
    if r["status"] == "in_progress" and not note:
        note = "Run stopped after Save was clicked. Search the MCQ list before retrying."
    return ReportRow(
        mcq_no=r["question_no"],
        status=status,
        django_link=_clean_link(r["portal_url"]),
        django_id=r["portal_pk"],
        question=r["question_preview"] or "",
        topic=r["topic"],
        correct_answer=r["correct_answer"],
        difficulty=r["difficulty"],
        source_file=r["source_ref"],
        portal_hash=r["portal_hash"],
        uploaded_at=datetime.fromisoformat(r["uploaded_at"]) if r["uploaded_at"] else None,
        note=note,
        assessment_status=r["assessment_status"] or "Not added yet",
    )


def rows_from_tracker(tracker: Tracker, source_ref: Optional[str] = None) -> list[ReportRow]:
    return [_row_from_db(r) for r in tracker.all_rows(source_ref)]


def rows_for_document(parsed, tracker: Tracker, source_file: str) -> list[ReportRow]:
    """One row per question in the document, in document order, including invalid ones."""
    rows = []
    for pq in parsed:
        if not pq.valid:
            rows.append(ReportRow(
                mcq_no=pq.mcq_no, status="Not done", question=pq.preview, topic=pq.raw.get("topic"),
                correct_answer=pq.raw.get("answer"), difficulty=pq.raw.get("difficulty"),
                source_file=source_file,
                note="Invalid in document: " + "; ".join(pq.errors),
            ))
            continue
        db = tracker.get(pq.mcq.fingerprint())
        if db is None:
            rows.append(ReportRow(
                mcq_no=pq.mcq_no, status="Not done", question=pq.preview, topic=pq.mcq.topic,
                correct_answer=pq.mcq.correct_answer, difficulty=pq.mcq.difficulty,
                source_file=source_file, note="Valid, not uploaded yet",
            ))
            continue
        row = _row_from_db(db)
        if db["source_ref"] and db["source_ref"] != source_file:
            extra = f"Same question was already uploaded from {db['source_ref']}."
            row.note = f"{row.note} {extra}".strip() if row.note else extra
        row.mcq_no, row.source_file = pq.mcq_no, source_file
        row.topic = pq.mcq.topic or row.topic
        rows.append(row)
    return rows


def write_validation_report(parsed, doc_notes: list[str], path: Path) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    wb = Workbook()
    ws = wb.active
    ws.title = "Validation"
    cols = [("MCQ No.", 9), ("Result", 11), ("Question", 60), ("Options", 9),
            ("Answer", 10), ("Difficulty", 11), ("Bloom", 12), ("Topic", 22),
            ("Errors", 60), ("Warnings", 50)]
    for col, (header, width) in enumerate(cols, start=1):
        c = ws.cell(row=1, column=col, value=header)
        c.font = Font(name=FONT, bold=True, color="FFFFFF")
        c.fill = HEADER_FILL
        c.alignment = Alignment(horizontal="center", vertical="center")
        c.border = BORDER
        ws.column_dimensions[get_column_letter(col)].width = width
    valid_fill, invalid_fill = STATUS_FILLS["Done"], STATUS_FILLS["Not done"]
    row_i = 2
    for note in doc_notes:
        ws.cell(row=row_i, column=2, value="Invalid").fill = invalid_fill
        ws.cell(row=row_i, column=9, value=note)
        row_i += 1
    for pq in parsed:
        answer = pq.mcq.correct_answer if pq.mcq else pq.raw.get("answer")
        difficulty = pq.mcq.difficulty if pq.mcq else pq.raw.get("difficulty")
        values = [pq.mcq_no, "Valid" if pq.valid else "Invalid", pq.preview,
                  len(pq.raw.get("options") or {}), answer, difficulty,
                  pq.mcq.bloom_taxonomy if pq.mcq else pq.raw.get("bloom_taxonomy"),
                  pq.mcq.topic if pq.mcq else pq.raw.get("topic"),
                  "\n".join(f"• {e}" for e in pq.errors) or None,
                  "\n".join(f"• {w}" for w in pq.warnings) or None]
        for col, v in enumerate(values, start=1):
            c = ws.cell(row=row_i, column=col, value=v)
            c.font = Font(name=FONT, size=10)
            c.border = BORDER
            c.alignment = Alignment(vertical="top", wrap_text=col in (3, 8, 9, 10),
                                    horizontal="center" if col in (1, 2, 4, 5) else None)
        ws.cell(row=row_i, column=2).fill = valid_fill if pq.valid else invalid_fill
        ws.cell(row=row_i, column=2).font = Font(name=FONT, size=10, bold=True)
        row_i += 1
    ws.freeze_panes = "A2"
    ws.auto_filter.ref = f"A1:J{max(row_i - 1, 2)}"
    try:
        wb.save(path)
    except PermissionError:
        path = path.with_name(f"{path.stem}_{datetime.now():%Y%m%d_%H%M%S}{path.suffix}")
        wb.save(path)
    log.info("Validation report written: %s", path)
    return path


def _id_value(pk: Optional[str]):
    """Numeric IDs are stored as numbers so Excel sorts and filters them properly."""
    if pk and pk.isdigit():
        return int(pk)
    return pk


def write_report(rows: list[ReportRow], path: Path = REPORT_PATH) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    wb = Workbook()

    # ---------------- Uploads sheet ----------------
    ws = wb.active
    ws.title = "Uploads"
    for col, (header, width) in enumerate(COLUMNS, start=1):
        c = ws.cell(row=1, column=col, value=header)
        c.font = Font(name=FONT, bold=True, color="FFFFFF")
        c.fill = HEADER_FILL
        c.alignment = Alignment(horizontal="center", vertical="center", wrap_text=True)
        c.border = BORDER
        ws.column_dimensions[get_column_letter(col)].width = width
    ws.row_dimensions[1].height = 30

    col = {name: idx for idx, (name, _) in enumerate(COLUMNS, start=1)}
    for i, r in enumerate(rows, start=2):
        values = {
            "MCQ No.": r.mcq_no, "Status": r.status, "Django Link": r.django_link,
            "Django ID": _id_value(r.django_id), "Question": r.question, "Topic": r.topic,
            "Correct Answer": r.correct_answer, "Difficulty": r.difficulty,
            "Source File": r.source_file, "Portal Hash": r.portal_hash,
            "Uploaded At": r.uploaded_at, "Error / Note": r.note,
            "Assessment Status": r.assessment_status,
        }
        for name, v in values.items():
            c = ws.cell(row=i, column=col[name], value=v)
            c.font = Font(name=FONT, size=10)
            c.border = BORDER
            c.alignment = Alignment(vertical="top",
                                    wrap_text=name in ("Question", "Topic", "Error / Note"))
        for name in ("MCQ No.", "Django ID", "Correct Answer"):
            ws.cell(row=i, column=col[name]).alignment = Alignment(horizontal="center", vertical="top")
        status_cell = ws.cell(row=i, column=col["Status"])
        status_cell.fill = STATUS_FILLS.get(r.status, PatternFill())
        status_cell.font = Font(name=FONT, size=10, bold=True)
        status_cell.alignment = Alignment(horizontal="center", vertical="top")
        if r.django_link:
            link = ws.cell(row=i, column=col["Django Link"])
            link.hyperlink = r.django_link
            link.font = Font(name=FONT, size=10, color="0563C1", underline="single")
        ws.cell(row=i, column=col["Django ID"]).number_format = "0"
        ws.cell(row=i, column=col["Uploaded At"]).number_format = "yyyy-mm-dd hh:mm:ss"

    ws.freeze_panes = "A2"
    last_row = max(len(rows) + 1, 2)
    ws.auto_filter.ref = f"A1:{get_column_letter(len(COLUMNS))}{last_row}"

    # ---------------- Summary sheet ----------------
    sm = wb.create_sheet("Summary")
    sm.column_dimensions["A"].width = 24
    sm.column_dimensions["B"].width = 62
    bold = Font(name=FONT, bold=True)
    plain = Font(name=FONT)
    sm["A1"] = "MCQ Upload Report"
    sm["A1"].font = Font(name=FONT, bold=True, size=14)
    sm["A2"] = "Generated at"
    sm["B2"] = datetime.now().replace(microsecond=0)
    sm["B2"].number_format = "yyyy-mm-dd hh:mm:ss"
    sm["B2"].alignment = Alignment(horizontal="left")
    sm["A3"] = "Source file(s)"
    sm["B3"] = ", ".join(sorted({r.source_file for r in rows if r.source_file})) or "-"

    rng = f"Uploads!$B$2:$B${last_row}"
    totals = [
        ("Total MCQs", f"=COUNTA({rng})"),
        ("Done", f'=COUNTIF({rng},"Done")'),
        ("Not done", f'=COUNTIF({rng},"Not done")'),
        ("Needs check", f'=COUNTIF({rng},"Needs check")'),
    ]
    for offset, (label, formula) in enumerate(totals):
        row = 5 + offset
        sm.cell(row=row, column=1, value=label).font = bold
        c = sm.cell(row=row, column=2, value=formula)
        c.font = plain
        c.alignment = Alignment(horizontal="left")
        if label in STATUS_FILLS:
            sm.cell(row=row, column=1).fill = STATUS_FILLS[label]

    sm["A10"] = "Status meanings"
    sm["A10"].font = bold
    legend = [
        ("Done", "Saved on the portal and verified by reading it back."),
        ("Not done", "Rejected by the portal or invalid in the Word document. See Error / Note."),
        ("Needs check", "The run stopped right after Save. Search the MCQ list before retrying."),
    ]
    for offset, (label, text) in enumerate(legend):
        row = 11 + offset
        a = sm.cell(row=row, column=1, value=label)
        a.fill = STATUS_FILLS[label]
        a.font = plain
        sm.cell(row=row, column=2, value=text).font = plain
    for row in sm.iter_rows(min_row=2, max_row=3):
        for c in row:
            c.font = bold if c.column == 1 else plain

    # ---------------- save (Excel on Windows locks open files) ----------------
    try:
        wb.save(path)
    except PermissionError:
        alt = path.with_name(f"{path.stem}_{datetime.now():%Y%m%d_%H%M%S}{path.suffix}")
        wb.save(alt)
        log.warning("%s is open in Excel, so the report was saved as %s", path.name, alt.name)
        path = alt
    log.info("Excel report written: %s", path)
    return path
