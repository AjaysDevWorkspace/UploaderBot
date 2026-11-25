"""
Rebuild output/upload_report.xlsx from the upload database at any time.

    python make_report.py                       # every MCQ ever tracked
    python make_report.py --source questions.docx   # only MCQs from one document
"""

import argparse
import logging
import sys

from bot.report import rows_from_tracker, write_report
from bot.tracker import Tracker


def main() -> int:
    ap = argparse.ArgumentParser(description="Generate the Excel upload report.")
    ap.add_argument("--source", help="only include MCQs from this source file name")
    args = ap.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")

    tracker = Tracker()
    rows = rows_from_tracker(tracker, args.source)
    tracker.close()
    if not rows:
        print("No uploads recorded yet" + (f" for {args.source}" if args.source else "") + ".")
        return 1
    path = write_report(rows)
    done = sum(r.status == "Done" for r in rows)
    print(f"{len(rows)} MCQs in report ({done} done). File: {path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
