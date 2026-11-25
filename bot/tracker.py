"""
SQLite upload tracker.

Status flow per question (keyed by MCQ.fingerprint()):
    in_progress -> success | failed

"in_progress" is written immediately BEFORE the Save button is clicked. If the
run dies after that point we cannot know whether the portal saved the question,
so it is never retried automatically: someone checks the portal first.
"""

import sqlite3
from datetime import datetime
from pathlib import Path
from typing import Optional

from .config import PROJECT_ROOT

DB_PATH = PROJECT_ROOT / "database" / "upload_tracking.db"

SCHEMA = """
CREATE TABLE IF NOT EXISTS uploads (
    fingerprint      TEXT PRIMARY KEY,
    source_ref       TEXT,
    question_preview TEXT,
    status           TEXT NOT NULL,
    portal_hash      TEXT,
    portal_pk        TEXT,
    error            TEXT,
    updated_at       TEXT NOT NULL
);
"""

# Columns added after the first version; created automatically on older databases.
EXTRA_COLUMNS = {
    "question_no": "INTEGER",
    "correct_answer": "TEXT",
    "difficulty": "TEXT",
    "portal_url": "TEXT",
    "uploaded_at": "TEXT",
    "assessment_status": "TEXT",
    "topic": "TEXT",
}


class Tracker:
    def __init__(self, path: Path = DB_PATH):
        path.parent.mkdir(parents=True, exist_ok=True)
        self.conn = sqlite3.connect(str(path))
        self.conn.row_factory = sqlite3.Row
        self.conn.execute(SCHEMA)
        existing = {r["name"] for r in self.conn.execute("PRAGMA table_info(uploads)")}
        for col, typ in EXTRA_COLUMNS.items():
            if col not in existing:
                self.conn.execute(f"ALTER TABLE uploads ADD COLUMN {col} {typ}")
        self.conn.commit()

    def get(self, fingerprint: str) -> Optional[sqlite3.Row]:
        return self.conn.execute(
            "SELECT * FROM uploads WHERE fingerprint = ?", (fingerprint,)
        ).fetchone()

    def all_rows(self, source_ref: Optional[str] = None) -> list[sqlite3.Row]:
        sql = "SELECT * FROM uploads"
        args: tuple = ()
        if source_ref:
            sql += " WHERE source_ref = ?"
            args = (source_ref,)
        sql += " ORDER BY source_ref, question_no, updated_at"
        return list(self.conn.execute(sql, args))

    def _upsert(self, fingerprint: str, **fields):
        fields["updated_at"] = datetime.now().isoformat(timespec="seconds")
        if self.get(fingerprint):
            sets = ", ".join(f"{k} = ?" for k in fields)
            self.conn.execute(
                f"UPDATE uploads SET {sets} WHERE fingerprint = ?",
                (*fields.values(), fingerprint),
            )
        else:
            cols = ["fingerprint", *fields]
            self.conn.execute(
                f"INSERT INTO uploads ({', '.join(cols)}) VALUES ({', '.join('?' * len(cols))})",
                (fingerprint, *fields.values()),
            )
        self.conn.commit()

    def mark_in_progress(self, fp: str, source_ref: str, preview: str,
                         question_no: Optional[int] = None,
                         correct_answer: Optional[str] = None,
                         difficulty: Optional[str] = None, topic: Optional[str] = None):
        self._upsert(fp, source_ref=source_ref, question_preview=preview,
                     question_no=question_no, correct_answer=correct_answer,
                     difficulty=difficulty, topic=topic, status="in_progress", error=None)

    def mark_success(self, fp: str, portal_hash: Optional[str], portal_pk: Optional[str],
                     portal_url: Optional[str], note: Optional[str] = None):
        self._upsert(fp, status="success", portal_hash=portal_hash, portal_pk=portal_pk,
                     portal_url=portal_url, error=note,
                     uploaded_at=datetime.now().isoformat(timespec="seconds"))

    def mark_failed(self, fp: str, error: str, **meta):
        """meta may include source_ref, question_preview, question_no, correct_answer, difficulty, topic."""
        extra = {k: v for k, v in meta.items() if v is not None}
        self._upsert(fp, status="failed", error=error[:2000], **extra)

    def close(self):
        self.conn.close()
