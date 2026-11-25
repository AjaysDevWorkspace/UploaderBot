"""
Fills the "Add multiple choice question" form, saves it, and verifies the result.

Every field is located through its visible <label> (e.g. "Choice A text:") and
the label's `for` attribute, so the bot targets exactly the field you see on
screen. If a label is missing, renamed or ambiguous, the bot stops with a clear
error instead of typing into the wrong box.
"""

import html as html_lib
import logging
import re
from dataclasses import dataclass, field as dc_field
from typing import Optional

from playwright.sync_api import Locator, Page
from playwright.sync_api import TimeoutError as PWTimeout

from .config import Settings
from .models import MCQ, PORTAL_LABELS

log = logging.getLogger(__name__)


class FormError(Exception):
    """The form did not look or behave as expected; nothing should be retried blindly."""


class PortalRejected(FormError):
    """The portal showed validation errors, so the question was definitely NOT saved."""


@dataclass
class SaveResult:
    portal_pk: Optional[str]
    portal_hash: Optional[str]
    url: str
    mismatches: list[str] = dc_field(default_factory=list)


# ---------------------------------------------------------------- locators
def _label_regex(label: str) -> re.Pattern:
    return re.compile(rf"^\s*{re.escape(label)}\s*:?\s*$", re.I)


def field_id(page: Page, label: str) -> str:
    labels = page.locator("label").filter(has_text=_label_regex(label))
    count = labels.count()
    if count == 0:
        raise FormError(f'Field "{label}" not found on the page')
    ids = {labels.nth(i).get_attribute("for") for i in range(count)} - {None, ""}
    if len(ids) != 1:
        raise FormError(f'Field "{label}" is ambiguous or not linked to an input (ids: {ids})')
    return ids.pop()


def field(page: Page, label: str) -> Locator:
    return page.locator(f'[id="{field_id(page, label)}"]')


def _options(select: Locator) -> list[dict]:
    return select.locator("option").evaluate_all(
        "os => os.map(o => ({ value: o.value, text: o.textContent.trim() }))"
    )


def _match_option(options: list[dict], wanted: str) -> Optional[str]:
    w = wanted.strip().casefold()
    for o in options:
        if o["text"].casefold() == w:
            return o["value"]
    for o in options:
        if o["value"].casefold() == w:
            return o["value"]
    return None


def select_one(select: Locator, wanted: str, what: str, aliases: tuple[str, ...] = ()) -> None:
    options = _options(select)
    for candidate in (wanted, *aliases):
        value = _match_option(options, candidate)
        if value is not None:
            select.select_option(value=value)
            return
    raise FormError(f'{what}: "{wanted}" is not an available option. '
                    f'Options on the portal: {[o["text"] for o in options]}')


def select_many(select: Locator, wanted: list[str], what: str) -> None:
    options = _options(select)
    values = []
    for w in wanted:
        v = _match_option(options, w)
        if v is None:
            raise FormError(f'{what}: "{w}" is not an available option. '
                            f'Options on the portal: {[o["text"] for o in options]}')
        values.append(v)
    select.select_option(value=values)


def selected_text(select: Locator) -> str:
    return select.evaluate(
        "s => s.selectedIndex >= 0 ? s.options[s.selectedIndex].textContent.trim() : ''"
    )


def selected_texts(select: Locator) -> list[str]:
    return select.evaluate("s => Array.from(s.selectedOptions).map(o => o.textContent.trim())")


def _plain(s: str) -> str:
    s = re.sub(r"<br\s*/?>", " ", s or "", flags=re.I)
    s = re.sub(r"<[^>]+>", " ", s)
    return re.sub(r"\s+", " ", html_lib.unescape(s)).strip()


def _norm(s: str) -> str:
    return re.sub(r"\s+", " ", (s or "").replace("\r\n", "\n")).strip()


# ---------------------------------------------------------------- CKEditor
SET_CKEDITOR_JS = """
([id, html]) => new Promise((resolve) => {
  const inst = CKEDITOR.instances[id];
  inst.setData(html, { callback: () => { inst.updateElement(); resolve(true); } });
})
"""


def _wait_editor_ready(page: Page, fid: str, timeout_ms: int = 20000) -> None:
    try:
        page.wait_for_function(
            "id => CKEDITOR.instances[id] && CKEDITOR.instances[id].status === 'ready'",
            arg=fid, timeout=timeout_ms,
        )
    except PWTimeout:
        names = page.evaluate("() => Object.keys(CKEDITOR.instances)")
        raise FormError(f'Question editor "{fid}" never became ready. Editors on page: {names}')


def set_rich_text(page: Page, fid: str, html_text: str, timeout_ms: int = 20000) -> None:
    if not page.evaluate("() => !!window.CKEDITOR"):
        page.locator(f'[id="{fid}"]').fill(html_text)   # editor not present: plain textarea
        return
    _wait_editor_ready(page, fid, timeout_ms)
    page.evaluate(SET_CKEDITOR_JS, [fid, html_text])
    got = page.evaluate("id => CKEDITOR.instances[id].getData()", fid)
    if _plain(got) != _plain(html_text):
        raise FormError("Question text in the editor does not match what was sent")


def get_rich_text(page: Page, fid: str) -> str:
    if page.evaluate("() => !!window.CKEDITOR"):
        _wait_editor_ready(page, fid)
        return page.evaluate("id => CKEDITOR.instances[id].getData()", fid) or ""
    return page.locator(f'[id="{fid}"]').input_value()


# ---------------------------------------------------------------- navigation
def open_add_form(page: Page, settings: Settings) -> None:
    if settings.mcq_add_url:
        page.goto(settings.mcq_add_url, wait_until="domcontentloaded")
    else:
        page.goto(settings.admin_url, wait_until="domcontentloaded")
        page.get_by_role("link", name="Multiple choice questions", exact=True).first.click()
        page.wait_for_load_state("domcontentloaded")
        page.get_by_role("link", name=re.compile(r"add multiple choice question", re.I)).first.click()
        page.wait_for_load_state("domcontentloaded")
        log.info("MCQ add form is at %s  (you may set this as mcq_add_url in config/settings.json)",
                 page.url)
    heading = page.get_by_role("heading", name=re.compile(r"add multiple choice question", re.I))
    if heading.count() == 0:
        raise FormError(f"Expected the 'Add multiple choice question' form, but got {page.url}")


# ---------------------------------------------------------------- fill
def fill_mcq(page: Page, q: MCQ, settings: Settings) -> None:
    set_rich_text(page, field_id(page, "Question text"), q.html())

    select_one(field(page, "Question type"), settings.question_type, "Question type")
    if settings.assessment_types:
        select_many(field(page, "Question for assessment type"), settings.assessment_types,
                    "Question for assessment type")

    opts = q.option_map()
    for label in PORTAL_LABELS:
        field(page, f"Choice {label} text").fill(opts.get(label, ""))

    select_one(field(page, "Correct choice"), q.correct_answer, "Correct choice",
               aliases=(f"Choice {q.correct_answer}", f"Option {q.correct_answer}"))
    field(page, "Correct choice explanation").fill(q.explanation or "")

    difficulty = q.difficulty or settings.default_difficulty
    if not difficulty:
        raise FormError("Difficulty is missing and no default_difficulty is configured")
    select_one(field(page, "Difficulty level"), difficulty, "Difficulty level")

    bloom = q.bloom_taxonomy or settings.default_bloom_taxonomy
    if bloom:
        select_one(field(page, "Bloom taxonomy"), bloom, "Bloom taxonomy")

    field(page, "Shuffle options").set_checked(settings.shuffle_options)
    for flag in ("User generated", "Peer reviewed", "Are variables enabled"):
        field(page, flag).set_checked(False)


# ---------------------------------------------------------------- save + verify
def collect_errors(page: Page) -> list[str]:
    return page.locator(".errornote, .errorlist li").evaluate_all("""
      els => els.map(e => {
        const row = e.closest('.form-row');
        const lbl = row ? row.querySelector('label') : null;
        const t = e.textContent.replace(/\\s+/g, ' ').trim();
        return lbl ? `${lbl.textContent.trim()} ${t}` : t;
      })
    """)


def read_readonly(page: Page, label: str) -> Optional[str]:
    xp = (f"xpath=//label[normalize-space()='{label}:' or normalize-space()='{label}']"
          "/following-sibling::*[contains(concat(' ', normalize-space(@class), ' '), ' readonly ')][1]")
    loc = page.locator(xp)
    if loc.count() == 0:
        loc = page.locator(f".field-{label.lower().replace(' ', '_')} .readonly")
    if loc.count() == 0:
        return None
    value = loc.first.inner_text().strip()
    return None if value in ("", "-") else value


def verify_saved(page: Page, q: MCQ, settings: Settings) -> list[str]:
    """Re-read the saved form and compare it with what we intended to save."""
    problems = []
    saved_q = get_rich_text(page, field_id(page, "Question text"))
    if _plain(saved_q) != _plain(q.html()):
        problems.append("question text differs after save")
    opts = q.option_map()
    for label in PORTAL_LABELS:
        actual = _norm(field(page, f"Choice {label} text").input_value())
        if actual != _norm(opts.get(label, "")):
            problems.append(f"choice {label} differs after save")
    correct = selected_text(field(page, "Correct choice"))
    if not re.search(rf"\b{q.correct_answer}\b", correct):
        problems.append(f"correct choice saved as '{correct}', expected {q.correct_answer}")
    difficulty = q.difficulty or settings.default_difficulty
    if selected_text(field(page, "Difficulty level")).casefold() != (difficulty or "").casefold():
        problems.append("difficulty differs after save")
    bloom = q.bloom_taxonomy or settings.default_bloom_taxonomy
    if bloom and selected_text(field(page, "Bloom taxonomy")).casefold() != bloom.casefold():
        problems.append("Bloom taxonomy differs after save")
    if settings.assessment_types:
        saved_types = {t.casefold() for t in selected_texts(field(page, "Question for assessment type"))}
        wanted = {t.casefold() for t in settings.assessment_types}
        if saved_types != wanted:
            problems.append(f"assessment types saved as {sorted(saved_types)}, expected {sorted(wanted)}")
    return problems


def save_and_verify(page: Page, q: MCQ, settings: Settings) -> SaveResult:
    page.get_by_role("button", name="Save and continue editing", exact=True).click()
    page.wait_for_load_state("domcontentloaded")

    errors = collect_errors(page)
    if errors:
        raise PortalRejected("Portal rejected the question: " + " | ".join(errors))

    success = page.locator(".messagelist li").filter(
        has_text=re.compile(r"added successfully", re.I)).count() > 0
    m = re.search(r"/([^/?#]+)/change/?(?:[?#].*)?$", page.url)
    pk = m.group(1) if m else None
    if not success and not pk:
        raise FormError(f"No success message after saving (now at {page.url})")

    return SaveResult(
        portal_pk=pk,
        portal_hash=read_readonly(page, "Hash"),
        url=page.url,
        mismatches=verify_saved(page, q, settings),
    )
