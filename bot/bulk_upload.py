"""
Uploads every valid MCQ from a parsed document, one by one, through the add form.

Resumable: questions already saved (status success) are skipped, so rerunning the
same command after an interruption continues where it stopped.

Stops the whole run when:
  * the outcome of a Save is unknown (timeout, crash, Ctrl+C) -> that question becomes
    'Needs check' and is never re-created automatically;
  * 3 questions in a row fail before saving (the form has probably changed).
"""

import logging
import time
from dataclasses import dataclass
from datetime import datetime
from typing import Optional

from playwright.sync_api import Page, sync_playwright

from .browser import ensure_logged_in, is_logged_in, open_context
from .config import PROJECT_ROOT, Settings
from .mcq_uploader import FormError, PortalRejected, fill_mcq, open_add_form, save_and_verify
from .question_parser import ParsedQuestion
from .tracker import Tracker

log = logging.getLogger(__name__)
MAX_CONSECUTIVE_FAILURES = 3


@dataclass
class BulkResult:
    created: int = 0
    failed: int = 0
    skipped_done: int = 0
    skipped_check: int = 0
    stopped_reason: Optional[str] = None


def _meta(pq: ParsedQuestion) -> dict:
    q = pq.mcq
    return dict(source_ref=q.source_ref, question_preview=q.preview(), question_no=q.question_no,
                correct_answer=q.correct_answer, difficulty=q.difficulty, topic=q.topic)


def _go_to_add_form(page: Page, settings: Settings) -> Settings:
    try:
        open_add_form(page, settings)
    except FormError:
        if is_logged_in(page):
            raise
        ensure_logged_in(page, settings)          # session expired: log in again, then retry once
        open_add_form(page, settings)
    if not settings.mcq_add_url:                  # remember the real URL for the rest of the run
        settings = settings.model_copy(update={"mcq_add_url": page.url.split("?")[0]})
    return settings


def run_bulk_upload(parsed: list[ParsedQuestion], settings: Settings, tracker: Tracker,
                    save: bool, limit: Optional[int] = None,
                    force_retry: bool = False) -> BulkResult:
    result = BulkResult()
    pending: list[ParsedQuestion] = []
    for pq in parsed:
        if not pq.valid:
            continue
        row = tracker.get(pq.mcq.fingerprint())
        if row and row["status"] == "success":
            result.skipped_done += 1
        elif row and row["status"] == "in_progress" and not force_retry:
            result.skipped_check += 1
            log.warning("MCQ %s is 'Needs check' (a previous Save may have worked). Skipping.",
                        pq.mcq_no)
        else:
            pending.append(pq)
    if limit:
        pending = pending[:limit]

    print(f"\nAlready uploaded: {result.skipped_done}   Needs check: {result.skipped_check}   "
          f"To upload now: {len(pending)}")
    if not pending:
        print("Nothing to upload.")
        return result

    if save:
        answer = input(f"\nType SAVE to create {len(pending)} question(s) on the portal: ").strip()
        if answer != "SAVE":
            result.stopped_reason = "not confirmed"
            print("Cancelled. Nothing was created.")
            return result

    logs = PROJECT_ROOT / "logs"
    logs.mkdir(exist_ok=True)
    with sync_playwright() as p:
        ctx = open_context(p, settings)
        page = ctx.pages[0] if ctx.pages else ctx.new_page()
        try:
            ensure_logged_in(page, settings)

            if not save:   # preview: fill the first pending question only
                pq = pending[0]
                settings = _go_to_add_form(page, settings)
                fill_mcq(page, pq.mcq, settings)
                shot = logs / f"preview_mcq{pq.mcq_no}_{datetime.now():%Y%m%d_%H%M%S}.png"
                page.screenshot(path=str(shot), full_page=True)
                print(f"\nPREVIEW: MCQ {pq.mcq_no} is filled in the browser but NOT saved.")
                print(f"Screenshot: {shot}")
                input("Check every field, then press Enter to close the browser... ")
                result.stopped_reason = "preview only"
                return result

            consecutive = 0
            for n, pq in enumerate(pending, start=1):
                q = pq.mcq
                fp = q.fingerprint()
                log.info("[%d/%d] MCQ %s: %s", n, len(pending), pq.mcq_no, q.preview(60))
                try:
                    settings = _go_to_add_form(page, settings)
                    fill_mcq(page, q, settings)
                except Exception as exc:                  # nothing was submitted yet
                    tracker.mark_failed(fp, f"Could not fill the form: {exc}", **_meta(pq))
                    result.failed += 1
                    consecutive += 1
                    log.error("MCQ %s not uploaded: %s", pq.mcq_no, exc)
                    if consecutive >= MAX_CONSECUTIVE_FAILURES:
                        result.stopped_reason = (f"{consecutive} failures in a row; the form may "
                                                 "have changed. Check logs before rerunning.")
                        break
                    continue

                tracker.mark_in_progress(fp, q.source_ref, q.preview(), q.question_no,
                                         q.correct_answer, q.difficulty, q.topic)
                try:
                    saved = save_and_verify(page, q, settings)
                except PortalRejected as exc:              # definitely not saved
                    tracker.mark_failed(fp, str(exc))
                    result.failed += 1
                    consecutive += 1
                    log.error("MCQ %s rejected by portal: %s", pq.mcq_no, exc)
                    if consecutive >= MAX_CONSECUTIVE_FAILURES:
                        result.stopped_reason = f"{consecutive} failures in a row."
                        break
                    continue
                except Exception as exc:                   # outcome unknown: stop everything
                    result.stopped_reason = (f"MCQ {pq.mcq_no}: outcome of Save is unknown "
                                             f"({type(exc).__name__}: {exc}). It is marked "
                                             "'Needs check'.")
                    log.error(result.stopped_reason)
                    break

                note = ("Saved, but differs from input: " + "; ".join(saved.mismatches)
                        if saved.mismatches else None)
                tracker.mark_success(fp, saved.portal_hash, saved.portal_pk, saved.url, note)
                result.created += 1
                consecutive = 0
                log.info("MCQ %s created: id %s, %s", pq.mcq_no, saved.portal_pk, saved.url)
                if note:
                    log.warning("MCQ %s: %s", pq.mcq_no, note)
                time.sleep(settings.delay_between_questions_s)
        except KeyboardInterrupt:
            result.stopped_reason = "stopped with Ctrl+C"
            log.warning("Stopped by user. Rerun the same command to continue.")
        finally:
            ctx.close()
    return result
