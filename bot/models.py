"""
Structured MCQ model, shaped to the Newton School "Add multiple choice question" form:

  * Question text      -> CKEditor (stored as HTML)
  * Choice A..E text   -> plain textareas (max 5 options)
  * Correct choice     -> single dropdown (exactly one correct answer)
  * Correct choice explanation -> textarea
  * Difficulty level   -> required dropdown (Beginner/Easy/Medium/Hard/Challenge)
  * Bloom taxonomy     -> optional dropdown
"""

import hashlib
import html
import re
from typing import Literal, Optional

from pydantic import BaseModel, field_validator, model_validator

PORTAL_LABELS = ["A", "B", "C", "D", "E"]
Label = Literal["A", "B", "C", "D", "E"]
Difficulty = Literal["Beginner", "Easy", "Medium", "Hard", "Challenge"]
BLOOM_LEVELS = ["Remember", "Understand", "Apply", "Analyze", "Evaluate", "Create"]


class Option(BaseModel):
    label: Label
    text: str

    @field_validator("label", mode="before")
    @classmethod
    def _upper(cls, v):
        return v.strip().upper() if isinstance(v, str) else v

    @field_validator("text")
    @classmethod
    def _not_empty(cls, v: str) -> str:
        v = v.strip()
        if not v:
            raise ValueError("option text is empty")
        return v


class MCQ(BaseModel):
    question_text: Optional[str] = None   # plain text; converted to HTML for the editor
    question_html: Optional[str] = None   # use instead of question_text if you already have HTML
    options: list[Option]
    correct_answer: Label
    explanation: Optional[str] = None
    difficulty: Optional[Difficulty] = None
    bloom_taxonomy: Optional[str] = None
    topic: Optional[str] = None           # recorded, not uploaded yet (topic widget not verified)
    marks: Optional[float] = None         # no marks field on the MCQ form; kept for assessment stage
    source_ref: Optional[str] = None      # source file name, e.g. "questions.docx"
    question_no: Optional[int] = None     # position of the MCQ in the source file (1, 2, 3...)

    @field_validator("correct_answer", mode="before")
    @classmethod
    def _upper_answer(cls, v):
        return v.strip().upper() if isinstance(v, str) else v

    @field_validator("difficulty", mode="before")
    @classmethod
    def _title_difficulty(cls, v):
        return v.strip().capitalize() if isinstance(v, str) and v.strip() else None

    @field_validator("bloom_taxonomy", mode="before")
    @classmethod
    def _bloom(cls, v):
        if v is None or (isinstance(v, str) and not v.strip()):
            return None
        key = v.strip().casefold().replace("analyse", "analyze")
        for level in BLOOM_LEVELS:
            if level.casefold() == key:
                return level
        raise ValueError(f'Bloom "{v.strip()}" is not one of {", ".join(BLOOM_LEVELS)}')

    @model_validator(mode="after")
    def _check(self):
        if not (self.question_text or "").strip() and not (self.question_html or "").strip():
            raise ValueError("question statement is missing")

        labels = [o.label for o in self.options]
        if len(labels) != len(set(labels)):
            raise ValueError(f"duplicate option labels: {labels}")
        if len(self.options) < 2:
            raise ValueError("at least 2 options are required")
        if len(self.options) > 5:
            raise ValueError("portal supports at most 5 options (A–E)")
        expected = PORTAL_LABELS[: len(self.options)]
        if labels != expected:
            raise ValueError(f"options must be labelled {expected} in order, got {labels}")

        texts = [re.sub(r"\s+", " ", o.text).casefold() for o in self.options]
        if len(texts) != len(set(texts)):
            raise ValueError("two options have identical text")
        if self.correct_answer not in labels:
            raise ValueError(f"correct answer {self.correct_answer} is not one of the options {labels}")
        return self

    # ---------- helpers ----------
    def html(self) -> str:
        """HTML for CKEditor. Plain text is escaped; line breaks become <br />,
        matching how existing questions on the portal are stored."""
        if self.question_html and self.question_html.strip():
            return self.question_html.strip()
        text = html.escape(self.question_text.strip(), quote=False)
        return re.sub(r"\r?\n", "<br />", text)

    def plain_question(self) -> str:
        if self.question_text and self.question_text.strip():
            return self.question_text.strip()
        return re.sub(r"<[^>]+>", " ", self.question_html or "").strip()

    def preview(self, n: int = 70) -> str:
        t = re.sub(r"\s+", " ", self.plain_question())
        return t if len(t) <= n else t[: n - 1] + "…"

    def option_map(self) -> dict[str, str]:
        return {o.label: o.text for o in self.options}

    def fingerprint(self) -> str:
        """Stable identity for duplicate prevention: question + options + answer."""
        norm = lambda s: re.sub(r"\s+", " ", s).strip().casefold()
        parts = [norm(self.plain_question())]
        parts += [f"{o.label}:{norm(o.text)}" for o in self.options]
        parts.append(f"ans:{self.correct_answer}")
        return hashlib.sha256("\n".join(parts).encode("utf-8")).hexdigest()
