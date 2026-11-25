"""Persistent Playwright session with manual login (OTP/MFA friendly)."""

import logging
import re

from playwright.sync_api import BrowserContext, Page, Playwright

from .config import Settings

log = logging.getLogger(__name__)


def open_context(p: Playwright, settings: Settings) -> BrowserContext:
    settings.profile_path.mkdir(parents=True, exist_ok=True)
    return p.chromium.launch_persistent_context(
        user_data_dir=str(settings.profile_path),
        headless=settings.headless,
        slow_mo=settings.slow_mo_ms,
        viewport={"width": 1440, "height": 900},
    )


def is_logged_in(page: Page) -> bool:
    # The admin header shows "WELCOME, <name>. ... LOG OUT" when authenticated.
    if page.locator("#user-tools").count() > 0:
        return True
    return page.get_by_text(re.compile(r"log\s*out", re.I)).count() > 0


def ensure_logged_in(page: Page, settings: Settings, max_attempts: int = 5) -> None:
    page.goto(settings.admin_url, wait_until="domcontentloaded")
    attempts = 0
    while not is_logged_in(page):
        attempts += 1
        if attempts > max_attempts:
            raise RuntimeError("Still not logged in to the admin. Stopping.")
        print("\nPlease log in in the browser window (complete OTP/MFA if asked).")
        input("Press Enter here once you can see the admin home page... ")
        page.goto(settings.admin_url, wait_until="domcontentloaded")
    log.info("Admin session active.")
