"""
Newton School MCQ upload bot.

    python main.py check  input\\questions.docx             # parse + validate only
    python main.py upload input\\questions.docx             # preview: fills 1 question, no save
    python main.py upload input\\questions.docx --save      # upload every valid question
    python main.py upload input\\questions.docx --save --limit 1    # upload just the next one
    python main.py report                                   # rebuild the Excel report

Outputs (folder output\\):
    parsed_questions.json    what the bot understood from the document
    validation_report.xlsx   valid / invalid per question, with reasons
    upload_report.xlsx       Done / Not done / Needs check, Django link and ID
"""

import argparse
import json
import logging
import sys
from pathlib import Path

from bot.bulk_upload import run_bulk_upload
from bot.config import PROJECT_ROOT, load_settings
from bot.question_parser import parse_document
from bot.report import (REPORT_PATH, rows_for_document, rows_from_tracker,
                        write_report, write_validation_report)
from bot.tracker import Tracker

OUTPUT = PROJECT_ROOT / "output"
log = logging.getLogger("main")


def setup_logging() -> None:
    (PROJECT_ROOT / "logs").mkdir(exist_ok=True)
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)-7s %(message)s",
        handlers=[logging.FileHandler(PROJECT_ROOT / "logs" / "bot.log", encoding="utf-8"),
                  logging.StreamHandler(sys.stdout)],
    )


def check(docx: Path, settings):
    if not docx.exists():
        sys.exit(f"File not found: {docx}")
    if docx.suffix.lower() != ".docx":
        sys.exit("Only .docx files are supported (in Word: File > Save As > Word Document).")
    parsed, doc_notes = parse_document(docx, settings)
    OUTPUT.mkdir(exist_ok=True)
    (OUTPUT / "parsed_questions.json").write_text(
        json.dumps({"source": docx.name, "document_errors": doc_notes,
                    "questions": [pq.to_json() for pq in parsed]},
                   indent=2, ensure_ascii=False), encoding="utf-8")
    val_path = write_validation_report(parsed, doc_notes, OUTPUT / "validation_report.xlsx")

    valid = sum(pq.valid for pq in parsed)
    print(f"\n{docx.name}: {len(parsed)} questions found, {valid} valid, {len(parsed) - valid} invalid\n")
    for note in doc_notes:
        print(f"  !! {note}")
    for pq in parsed:
        mark = "OK " if pq.valid else "ERR"
        print(f"  {mark} MCQ {pq.mcq_no:>3}  {pq.preview}")
        for e in pq.errors:
            print(f"           x {e}")
        for w in pq.warnings:
            print(f"           ! {w}")
    print(f"\nDetails: {OUTPUT / 'parsed_questions.json'}\n         {val_path}")
    return parsed, doc_notes


def main() -> int:
    ap = argparse.ArgumentParser(description="Newton School MCQ upload bot")
    sub = ap.add_subparsers(dest="command", required=True)

    c = sub.add_parser("check", help="parse and validate a Word document (no browser)")
    c.add_argument("docx", type=Path)

    u = sub.add_parser("upload", help="upload valid MCQs (preview unless --save)")
    u.add_argument("docx", type=Path)
    u.add_argument("--save", action="store_true", help="actually create questions on the portal")
    u.add_argument("--limit", type=int, help="upload at most N questions this run")
    u.add_argument("--skip-invalid", action="store_true",
                   help="upload the valid questions even if some are invalid")
    u.add_argument("--force-retry", action="store_true",
                   help="also retry 'Needs check' questions (only after checking the portal)")

    r = sub.add_parser("report", help="rebuild output\\upload_report.xlsx from the database")
    r.add_argument("--source", help="only questions from this file name")

    args = ap.parse_args()
    setup_logging()
    settings = load_settings()

    if args.command == "check":
        parsed, notes = check(args.docx, settings)
        return 0 if parsed and not notes and all(pq.valid for pq in parsed) else 1

    if args.command == "report":
        tracker = Tracker()
        rows = rows_from_tracker(tracker, args.source)
        tracker.close()
        if not rows:
            print("No uploads recorded yet.")
            return 1
        write_report(rows, REPORT_PATH)
        return 0

    # ---------------- upload ----------------
    parsed, notes = check(args.docx, settings)
    invalid = [pq for pq in parsed if not pq.valid]
    if notes or not parsed:
        print("\nNothing can be uploaded from this document.")
        return 1
    if invalid and not args.skip_invalid:
        print(f"\n{len(invalid)} question(s) are invalid. Fix them in the Word file and run again,"
              "\nor add --skip-invalid to upload only the valid ones.")
        return 1

    tracker = Tracker()
    try:
        result = run_bulk_upload(parsed, settings, tracker, save=args.save,
                                 limit=args.limit, force_retry=args.force_retry)
        if args.save:
            report = write_report(rows_for_document(parsed, tracker, args.docx.name), REPORT_PATH)
            print(f"\nCreated: {result.created}   Failed: {result.failed}   "
                  f"Already done: {result.skipped_done}   Needs check: {result.skipped_check}")
            if result.stopped_reason:
                print(f"Stopped early: {result.stopped_reason}")
            print(f"Report: {report}")
    finally:
        tracker.close()
    ok = not result.failed and result.stopped_reason in (None, "preview only", "not confirmed")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
