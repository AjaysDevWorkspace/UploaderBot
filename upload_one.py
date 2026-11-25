"""
Phase 3 — upload ONE MCQ from a JSON file.

    python upload_one.py input\\test_question.json           # dry run: fills the form, does NOT save
    python upload_one.py input\\test_question.json --save    # saves after you type SAVE to confirm

Safety:
  * Dry run is the default. Nothing is created unless --save is given AND confirmed.
  * A question already uploaded successfully (same text/options/answer) is skipped.
  * If a previous run stopped after Save was clicked, the bot refuses to retry until
    you have checked the portal and pass --force-retry.
"""

import argparse
import json
import logging
import sys
from datetime import datetime
from pathlib import Path

from playwright.sync_api import sync_playwright
from pydantic import ValidationError

from bot.browser import ensure_logged_in, open_context
from bot.config import PROJECT_ROOT, load_settings
from bot.mcq_uploader import FormError, PortalRejected, fill_mcq, open_add_form, save_and_verify
from bot.models import MCQ
from bot.report import rows_from_tracker, write_report
from bot.tracker import Tracker

LOG_DIR = PROJECT_ROOT / "logs"


def setup_logging() -> None:
    LOG_DIR.mkdir(exist_ok=True)
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)-7s %(name)s: %(message)s",
        handlers=[
            logging.FileHandler(LOG_DIR / "bot.log", encoding="utf-8"),
            logging.StreamHandler(sys.stdout),
        ],
    )


def main() -> int:
    ap = argparse.ArgumentParser(description="Upload one MCQ to the Newton School admin.")
    ap.add_argument("question_json", type=Path)
    ap.add_argument("--save", action="store_true", help="actually create the question")
    ap.add_argument("--force-retry", action="store_true",
                    help="retry a question left 'in_progress' (only after checking the portal)")
    args = ap.parse_args()

    setup_logging()
    log = logging.getLogger("upload_one")
    settings = load_settings()

    try:
        q = MCQ(**json.loads(args.question_json.read_text(encoding="utf-8")))
    except (ValidationError, json.JSONDecodeError) as exc:
        log.error("Question file is invalid:\n%s", exc)
        return 2

    fp = q.fingerprint()
    source = q.source_ref or args.question_json.name
    tracker = Tracker()
    row = tracker.get(fp)
    if row and row["status"] == "success":
        log.info("Already uploaded (hash %s, id %s). Skipping.", row["portal_hash"], row["portal_pk"])
        return 0
    if row and row["status"] == "in_progress" and args.save and not args.force_retry:
        log.error("A previous run stopped after clicking Save for this question. Search the MCQ list "
                  "for it first. If it is NOT there, rerun with --force-retry.")
        return 3

    log.info("Question: %s", q.preview())
    exit_code = 0
    with sync_playwright() as p:
        ctx = open_context(p, settings)
        page = ctx.pages[0] if ctx.pages else ctx.new_page()
        try:
            ensure_logged_in(page, settings)
            open_add_form(page, settings)
            fill_mcq(page, q, settings)
            shot = LOG_DIR / f"filled_{datetime.now():%Y%m%d_%H%M%S}.png"
            page.screenshot(path=str(shot), full_page=True)
            log.info("Form filled. Screenshot: %s", shot)

            if not args.save:
                print("\nDRY RUN: the form is filled but NOT saved.")
                print("Check every field in the browser window, then press Enter to close it.")
                input()
            else:
                if input('\nType SAVE to create this question in the portal: ').strip() != "SAVE":
                    print("Not saved.")
                else:
                    tracker.mark_in_progress(fp, source, q.preview(), q.question_no,
                                             q.correct_answer,
                                             q.difficulty or settings.default_difficulty,
                                             q.topic)
                    result = save_and_verify(page, q, settings)
                    note = ("Saved, but differs from input: " + "; ".join(result.mismatches)
                            if result.mismatches else None)
                    tracker.mark_success(fp, result.portal_hash, result.portal_pk,
                                         result.url, note)
                    log.info("Created. Hash: %s  Id: %s  URL: %s",
                             result.portal_hash, result.portal_pk, result.url)
                    if result.mismatches:
                        log.warning("Saved, but these fields differ from the input: %s",
                                    "; ".join(result.mismatches))
                        exit_code = 4
                    input("\nPress Enter to close the browser... ")
        except PortalRejected as exc:
            # Errors shown on the form: definitely not saved, safe to fix and retry.
            log.error("%s", exc)
            tracker.mark_failed(fp, str(exc))
            exit_code = 1
            input("\nThe browser is left open so you can see the problem. Press Enter to close... ")
        except Exception as exc:
            # Anything else after Save was clicked leaves the question 'in_progress'
            # so it is never re-created without a manual check.
            log.error("%s: %s", type(exc).__name__, exc)
            state = tracker.get(fp)
            if state and state["status"] == "in_progress":
                log.error("Outcome unknown. Check the MCQ list before retrying (--force-retry).")
            exit_code = 1
            input("\nThe browser is left open so you can see the problem. Press Enter to close... ")
        finally:
            ctx.close()
    if args.save:
        write_report(rows_from_tracker(tracker))
    tracker.close()
    return exit_code


if __name__ == "__main__":
    sys.exit(main())
