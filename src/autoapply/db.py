"""SQLite store for jobs, submissions, and run history."""

from __future__ import annotations

import sqlite3
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parents[2]
DB_PATH = ROOT / "data" / "autoapply.db"
IST = ZoneInfo("Asia/Kolkata")


def connect(path: Path | None = None) -> sqlite3.Connection:
    db_path = path or DB_PATH
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(db_path, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS jobs (
            id INTEGER PRIMARY KEY,
            dedupe_key TEXT UNIQUE,
            source TEXT,
            external_id TEXT,
            title TEXT,
            company TEXT,
            location TEXT,
            url TEXT,
            score INTEGER DEFAULT 0,
            status TEXT,
            skip_reason TEXT,
            created_at TEXT,
            updated_at TEXT
        );
        CREATE TABLE IF NOT EXISTS applications (
            id INTEGER PRIMARY KEY,
            job_id INTEGER,
            method TEXT,
            status TEXT,
            answers_json TEXT,
            cover_letter TEXT,
            response_excerpt TEXT,
            error TEXT,
            applied_at TEXT
        );
        CREATE TABLE IF NOT EXISTS agent_events (
            id INTEGER PRIMARY KEY,
            run_id INTEGER,
            kind TEXT,
            name TEXT,
            payload TEXT,
            created_at TEXT
        );
        CREATE TABLE IF NOT EXISTS option_fetches (
            external_id TEXT PRIMARY KEY,
            fetched_at TEXT
        );
        CREATE TABLE IF NOT EXISTS open_questions (
            question_key TEXT PRIMARY KEY,
            label TEXT NOT NULL,
            field_type TEXT NOT NULL,
            options_json TEXT NOT NULL DEFAULT '[]',
            asked_json TEXT NOT NULL DEFAULT '[]',
            created_at TEXT,
            updated_at TEXT
        );
        CREATE TABLE IF NOT EXISTS runs (
            id INTEGER PRIMARY KEY,
            trigger TEXT,
            started_at TEXT,
            finished_at TEXT,
            discovered INTEGER DEFAULT 0,
            eligible INTEGER DEFAULT 0,
            submitted INTEGER DEFAULT 0,
            needs_review INTEGER DEFAULT 0,
            skipped INTEGER DEFAULT 0,
            failed INTEGER DEFAULT 0,
            notes TEXT
        );
        """
    )
    return conn


def now_iso() -> str:
    return datetime.now(IST).isoformat(timespec="seconds")


def today_prefix() -> str:
    return datetime.now(IST).date().isoformat()
