"""Loads config/settings.json into a validated Settings object."""

import json
from pathlib import Path
from typing import Optional

from pydantic import BaseModel, Field

PROJECT_ROOT = Path(__file__).resolve().parent.parent
SETTINGS_PATH = PROJECT_ROOT / "config" / "settings.json"


class Settings(BaseModel):
    admin_url: str = "https://django.newtonschool.co/admin/"
    # Filled in after the first run prints the real add-page URL. Until then the
    # bot reaches the form by clicking through the admin, so no URL is guessed.
    mcq_add_url: Optional[str] = None
    browser_profile_dir: str = ".browser_profile"
    headless: bool = False
    slow_mo_ms: int = 150

    question_type: str = "Multiple Choice Question"
    assessment_types: list[str] = Field(default_factory=lambda: ["Normal Assessment", "Competitive Assessment"])
    default_difficulty: Optional[str] = None      # None = difficulty must come from the document
    default_bloom_taxonomy: Optional[str] = None  # None = leave the portal's default untouched
    shuffle_options: bool = True
    min_options: int = 4
    delay_between_questions_s: float = 1.0

    @property
    def profile_path(self) -> Path:
        p = Path(self.browser_profile_dir)
        return p if p.is_absolute() else PROJECT_ROOT / p


def load_settings(path: Path = SETTINGS_PATH) -> Settings:
    if not path.exists():
        return Settings()
    return Settings(**json.loads(path.read_text(encoding="utf-8")))
