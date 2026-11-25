"""
inspect_portal.py — Phase 1 portal inspection helper (READ-ONLY).

Opens the Newton School admin in a real Chromium window using a persistent
profile, lets you log in manually (OTP/MFA included), then captures the
structure of whatever page you navigate to: form fields, labels, required
flags, select options, inline formsets, rich-text editors, buttons,
changelist columns/filters/actions and navigation links.

It NEVER clicks, types into, or submits anything on the portal.

Output per captured page (in .\\inspection\\):
    <name>.json        page structure (no field values, no CSRF token)
    <name>.png         full-page screenshot
    <name>_forms.html  sanitized form markup (values stripped) for selector work

Usage (Windows, from the project folder with the venv active):
    python inspect_portal.py
"""

import json
import re
import sys
from datetime import datetime
from pathlib import Path

from playwright.sync_api import TimeoutError as PWTimeout
from playwright.sync_api import sync_playwright

ADMIN_URL = "https://django.newtonschool.co/admin/"
BASE_DIR = Path(__file__).resolve().parent
PROFILE_DIR = BASE_DIR / ".browser_profile"   # holds your logged-in session: never commit/share
OUT_DIR = BASE_DIR / "inspection"
MAX_SELECT_OPTIONS = 25                        # cap so large user/student dropdowns aren't dumped

EXTRACT_JS = r"""
() => {
  const MAX_OPTS = __MAX_OPTS__;
  const MGMT = /-(TOTAL_FORMS|INITIAL_FORMS|MIN_NUM_FORMS|MAX_NUM_FORMS)$/;
  const txt = (n) => (n ? (n.innerText || n.textContent || '').replace(/\s+/g, ' ').trim() : null);
  const visible = (el) => !!(el.offsetWidth || el.offsetHeight || el.getClientRects().length);
  const cls = (el) => (typeof el.className === 'string' && el.className) ? el.className : null;

  const labelEl = (el) => el.id ? document.querySelector(`label[for="${CSS.escape(el.id)}"]`) : null;
  const labelFor = (el) => {
    const l = labelEl(el);
    if (l) return txt(l);
    const wrap = el.closest('label');
    if (wrap) return txt(wrap);
    const aria = el.getAttribute('aria-label');
    if (aria) return aria.trim();
    const lb = el.getAttribute('aria-labelledby');
    if (lb) { const n = document.getElementById(lb.split(' ')[0]); if (n) return txt(n); }
    const row = el.closest('.form-row, .fieldBox, .form-group, .field, td, tr');
    if (row) { const rl = row.querySelector('label'); if (rl) return txt(rl); }
    return null;
  };
  const helpFor = (el) => {
    const row = el.closest('.form-row, .fieldBox, .form-group, .field');
    if (!row) return null;
    const h = row.querySelector('.help, .helptext, .form-text, small');
    return h ? txt(h) : null;
  };

  // ---- form controls ----
  const fields = [];
  document.querySelectorAll('input, select, textarea, [contenteditable="true"]').forEach((el) => {
    const tag = el.tagName.toLowerCase();
    let type = tag === 'input' ? (el.getAttribute('type') || 'text').toLowerCase() : tag;
    if (el.getAttribute('contenteditable') === 'true') type = 'contenteditable';
    const name = el.getAttribute('name');
    if (name === 'csrfmiddlewaretoken') return;

    const lbl = labelEl(el);
    const fs = el.closest('fieldset');
    const ig = el.closest('.inline-group');
    const f = {
      tag, type, name, id: el.id || null,
      label: labelFor(el),
      help: helpFor(el),
      required: !!(el.required || el.getAttribute('aria-required') === 'true' ||
                   (lbl && lbl.classList.contains('required'))),
      visible: visible(el),
      disabled: !!el.disabled,
      readonly: !!el.readOnly,
      placeholder: el.getAttribute('placeholder'),
      class: cls(el),
      fieldset: fs ? txt(fs.querySelector('h2, legend')) : null,
      inline_group: ig ? (ig.id || txt(ig.querySelector('h2'))) : null,
    };
    if (MGMT.test(name || '')) f.value = el.value;          // formset counts only
    if (type === 'radio' || type === 'checkbox') f.value_attr = el.value;
    if (tag === 'select') {
      const opts = Array.from(el.options);
      f.multiple = el.multiple;
      f.option_count = opts.length;
      f.options = opts.slice(0, MAX_OPTS).map(o => ({ value: o.value, text: o.text.trim() }));
      f.autocomplete_url = el.getAttribute('data-ajax--url') ||
                           el.getAttribute('data-autocomplete-light-url') || null;
    }
    fields.push(f);
  });

  // ---- buttons / actions ----
  const buttons = Array.from(document.querySelectorAll(
    'button, input[type=submit], input[type=button], a.button, .submit-row a, .object-tools a'
  )).map(b => ({
    text: txt(b) || b.value || null, tag: b.tagName.toLowerCase(),
    type: b.getAttribute('type'), name: b.getAttribute('name'),
    id: b.id || null, href: b.getAttribute('href'), class: cls(b),
  }));

  // ---- widgets / editors ----
  const editors = {
    ckeditor4: !!window.CKEDITOR,
    ckeditor5: !!document.querySelector('.ck-editor'),
    tinymce: !!window.tinymce,
    quill: !!document.querySelector('.ql-editor'),
    codemirror: !!document.querySelector('.CodeMirror, .cm-editor'),
    ace: !!document.querySelector('.ace_editor'),
    markdownx: !!document.querySelector('.markdownx'),
    martor: !!document.querySelector('.martor'),
    select2_count: document.querySelectorAll('.select2-container').length,
    iframe_count: document.querySelectorAll('iframe').length,
  };

  // ---- changelist (listing) pages: structure only, no row data ----
  const changelist = document.querySelector('#result_list') ? {
    columns: Array.from(document.querySelectorAll('#result_list thead th')).map(txt),
    rows_on_page: document.querySelectorAll('#result_list tbody tr').length,
    filters: Array.from(document.querySelectorAll('#changelist-filter h3, #changelist-filter summary')).map(txt),
    actions: Array.from(document.querySelectorAll('select[name=action] option'))
               .map(o => ({ value: o.value, text: o.text.trim() })),
    has_search: !!document.querySelector('#searchbar'),
    paginator: txt(document.querySelector('.paginator')),
  } : null;

  // ---- navigation links (helps locate the MCQ / assessment models) ----
  const nav = [];
  const seen = new Set();
  document.querySelectorAll('#nav-sidebar a, #content-main .module a, .app-list a, nav a, .breadcrumbs a')
    .forEach(a => {
      const href = a.getAttribute('href');
      if (!href || seen.has(href)) return;
      seen.add(href);
      nav.push({ text: txt(a), href });
    });

  const messages = Array.from(document.querySelectorAll('.messagelist li, .errornote, .errorlist li, .alert')).map(txt);
  const headings = Array.from(document.querySelectorAll('h1, h2, h3')).map(txt).filter(Boolean).slice(0, 60);
  const forms = Array.from(document.forms).map(f => ({
    id: f.id || null, action: f.getAttribute('action'), method: f.method,
    enctype: f.enctype, element_count: f.elements.length,
  }));

  // ---- sanitized form markup ----
  let formsHtml = '';
  Array.from(document.forms).forEach(f => {
    const c = f.cloneNode(true);
    c.querySelectorAll('#result_list tbody, script').forEach(n => n.remove());
    c.querySelectorAll('input[name=csrfmiddlewaretoken]').forEach(n => n.remove());
    c.querySelectorAll('input').forEach(n => {
      const t = (n.getAttribute('type') || 'text').toLowerCase();
      if (!MGMT.test(n.name || '') && !['radio', 'checkbox', 'submit', 'button'].includes(t)) {
        n.removeAttribute('value');
      }
    });
    c.querySelectorAll('textarea').forEach(n => { n.textContent = ''; });
    c.querySelectorAll('[contenteditable]').forEach(n => { n.innerHTML = ''; });
    c.querySelectorAll('select').forEach(s => {
      Array.from(s.options).slice(MAX_OPTS).forEach(o => o.remove());
    });
    formsHtml += c.outerHTML + '\n\n';
  });

  return { fields, buttons, editors, changelist, nav: nav.slice(0, 400),
           messages, headings, forms, formsHtml };
}
""".replace("__MAX_OPTS__", str(MAX_SELECT_OPTIONS))


def pick_page(ctx):
    pages = [p for p in ctx.pages if not p.is_closed()]
    if not pages:
        return None
    if len(pages) == 1:
        return pages[0]
    print("\nOpen tabs:")
    for i, p in enumerate(pages):
        print(f"  [{i}] {p.url}")
    choice = input(f"Which tab to capture? [default {len(pages) - 1}]: ").strip()
    if choice.isdigit() and int(choice) < len(pages):
        return pages[int(choice)]
    return pages[-1]


def capture(page, slug):
    OUT_DIR.mkdir(exist_ok=True)
    try:
        page.wait_for_load_state("domcontentloaded", timeout=15000)
    except PWTimeout:
        print("  (page still loading; capturing anyway)")

    result = {
        "captured_at": datetime.now().isoformat(timespec="seconds"),
        "url": page.url,
        "title": page.title(),
        "frames": [],
    }
    html_parts = []

    for i, frame in enumerate(page.frames):
        try:
            data = frame.evaluate(EXTRACT_JS)
        except Exception as exc:  # cross-origin or detached frames
            result["frames"].append({"index": i, "url": frame.url, "error": str(exc)})
            continue
        forms_html = data.pop("formsHtml", "")
        if forms_html:
            html_parts.append(f"<!-- frame {i}: {frame.url} -->\n{forms_html}")
        if i > 0 and not data["fields"] and not data["buttons"]:
            continue  # skip empty helper frames
        result["frames"].append({"index": i, "url": frame.url, **data})

    json_path = OUT_DIR / f"{slug}.json"
    png_path = OUT_DIR / f"{slug}.png"
    html_path = OUT_DIR / f"{slug}_forms.html"

    json_path.write_text(json.dumps(result, indent=2, ensure_ascii=False), encoding="utf-8")
    page.screenshot(path=str(png_path), full_page=True)
    if html_parts:
        html_path.write_text("\n".join(html_parts), encoding="utf-8")

    main = next((f for f in result["frames"] if f.get("index") == 0 and "fields" in f), None)
    if main:
        vis = [f for f in main["fields"] if f["visible"] and f["type"] != "hidden"]
        req = [f.get("label") or f.get("name") for f in vis if f["required"]]
        print(f"  URL:            {result['url']}")
        print(f"  Visible fields: {len(vis)}   Required: {req or 'none detected'}")
        print(f"  Buttons:        {[b['text'] for b in main['buttons'] if b['text']][:15]}")
        editors = {k: v for k, v in main["editors"].items() if v}
        if editors:
            print(f"  Widgets:        {editors}")
        if main["changelist"]:
            print(f"  Listing cols:   {main['changelist']['columns']}")
    print(f"  Saved: {json_path.name}, {png_path.name}" + (f", {html_path.name}" if html_parts else ""))


def main():
    print("=" * 70)
    print("Newton School admin — Phase 1 inspection (read-only)")
    print("This tool never clicks Save or submits anything.")
    print("Do NOT press Save/Publish yourself while inspecting either.")
    print("=" * 70)

    with sync_playwright() as p:
        ctx = p.chromium.launch_persistent_context(
            user_data_dir=str(PROFILE_DIR),
            headless=False,
            viewport={"width": 1440, "height": 900},
        )
        page = ctx.pages[0] if ctx.pages else ctx.new_page()
        try:
            page.goto(ADMIN_URL, wait_until="domcontentloaded", timeout=60000)
        except PWTimeout:
            print("Admin page is slow to load; continue once it appears.")

        input("\nLog in in the browser window (complete OTP/MFA if asked), then press Enter here... ")

        while True:
            name = input(
                "\nNavigate to a page in the browser, then type a short name for it "
                "(e.g. mcq_add) and press Enter.\nType q to quit: "
            ).strip()
            if name.lower() in {"q", "quit", "exit"}:
                break
            if not name:
                continue
            slug = re.sub(r"[^A-Za-z0-9_-]+", "_", name)
            target = pick_page(ctx)
            if target is None:
                print("No open tab found.")
                continue
            try:
                capture(target, slug)
            except Exception as exc:
                print(f"  Capture failed: {exc}")

        ctx.close()
    print(f"\nDone. Files are in: {OUT_DIR}")


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        sys.exit("\nInterrupted.")
