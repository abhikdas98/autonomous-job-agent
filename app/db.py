import json
import sqlite3
from contextlib import contextmanager
from pathlib import Path

from app.config import settings


@contextmanager
def connection():
    path = Path(settings.database_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(path), timeout=30)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA busy_timeout=30000")
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def init_db():
    with connection() as c:
        c.execute("PRAGMA journal_mode=WAL")
        c.executescript("""
        CREATE TABLE IF NOT EXISTS jobs (
          id TEXT PRIMARY KEY, title TEXT NOT NULL, company TEXT NOT NULL,
          location TEXT NOT NULL DEFAULT '', url TEXT NOT NULL,
          description TEXT NOT NULL DEFAULT '', source TEXT NOT NULL,
          posted_at TEXT NOT NULL DEFAULT '', fingerprint TEXT NOT NULL UNIQUE,
          score REAL NOT NULL DEFAULT 0, score_reasons TEXT NOT NULL DEFAULT '[]',
          status TEXT NOT NULL DEFAULT 'discovered',
          created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
          updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
        );
        CREATE TABLE IF NOT EXISTS approvals (
          job_id TEXT PRIMARY KEY REFERENCES jobs(id), status TEXT NOT NULL DEFAULT 'pending',
          notes TEXT NOT NULL DEFAULT '', decided_at TEXT,
          created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
        );
        CREATE TABLE IF NOT EXISTS applications (
          job_id TEXT PRIMARY KEY REFERENCES jobs(id), status TEXT NOT NULL DEFAULT 'not_submitted',
          notes TEXT NOT NULL DEFAULT '', updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
        );
        CREATE TABLE IF NOT EXISTS events (
          id INTEGER PRIMARY KEY AUTOINCREMENT, level TEXT NOT NULL, event TEXT NOT NULL,
          details TEXT NOT NULL DEFAULT '{}', created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
        );
        CREATE TABLE IF NOT EXISTS llm_cache (
          cache_key TEXT PRIMARY KEY, response TEXT NOT NULL, expires_at REAL NOT NULL
        );
        CREATE TABLE IF NOT EXISTS llm_budget (
          bucket TEXT PRIMARY KEY, count INTEGER NOT NULL
        );
        CREATE TABLE IF NOT EXISTS llm_request_times (
          id INTEGER PRIMARY KEY AUTOINCREMENT, created_at REAL NOT NULL
        );
        CREATE TABLE IF NOT EXISTS llm_ranking_queue (
          job_id TEXT PRIMARY KEY REFERENCES jobs(id) ON DELETE CASCADE,
          status TEXT NOT NULL DEFAULT 'pending' CHECK (status IN ('pending', 'completed')),
          attempts INTEGER NOT NULL DEFAULT 0, available_at REAL NOT NULL DEFAULT 0,
          last_error TEXT NOT NULL DEFAULT '', created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
          updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
        );
        CREATE TABLE IF NOT EXISTS resume_versions (
          version_id TEXT PRIMARY KEY,
          job_id TEXT NOT NULL REFERENCES jobs(id),
          version_number INTEGER NOT NULL,
          status TEXT NOT NULL DEFAULT 'needs_review'
            CHECK (status IN ('needs_review','approved','submitted','rejected')),
          docx_path TEXT NOT NULL,
          pdf_path TEXT NOT NULL,
          report_path TEXT NOT NULL,
          source_sha256 TEXT NOT NULL,
          job_description_sha256 TEXT NOT NULL,
          summary_json TEXT NOT NULL DEFAULT '{}',
          factuality_flags_json TEXT NOT NULL DEFAULT '[]',
          unsupported_requirements_json TEXT NOT NULL DEFAULT '[]',
          change_notes_json TEXT NOT NULL DEFAULT '[]',
          created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
          approved_at TEXT,
          submitted_at TEXT,
          submission_notes TEXT NOT NULL DEFAULT '',
          UNIQUE(job_id, version_number)
        );
        CREATE INDEX IF NOT EXISTS idx_llm_queue_due ON llm_ranking_queue(status, available_at);
        CREATE INDEX IF NOT EXISTS idx_jobs_score ON jobs(score DESC);
        CREATE INDEX IF NOT EXISTS idx_events_created ON events(created_at DESC);
        CREATE INDEX IF NOT EXISTS idx_llm_times ON llm_request_times(created_at);
        CREATE INDEX IF NOT EXISTS idx_resume_versions_job ON resume_versions(job_id, version_number DESC);
        """)
        # Idempotent migration: safe for existing databases with an earlier resume table.
        columns = {row["name"] for row in c.execute("PRAGMA table_info(resume_versions)").fetchall()}
        if "submission_notes" not in columns:
            c.execute("ALTER TABLE resume_versions ADD COLUMN submission_notes TEXT NOT NULL DEFAULT ''")


def log_event(event, details=None, level="INFO"):
    with connection() as c:
        c.execute("INSERT INTO events(level,event,details) VALUES(?,?,?)",
                  (level, event, json.dumps(details or {}, ensure_ascii=False)))


def metrics_snapshot():
    with connection() as c:
        jobs = c.execute("SELECT COUNT(*) FROM jobs").fetchone()[0]
        pending = c.execute("SELECT COUNT(*) FROM approvals WHERE status='pending'").fetchone()[0]
        submitted = c.execute("SELECT COUNT(*) FROM applications WHERE status='submitted'").fetchone()[0]
        versions = c.execute("SELECT COUNT(*) FROM resume_versions").fetchone()[0]
        events = c.execute("SELECT event, COUNT(*) n FROM events GROUP BY event").fetchall()
    return {
        "jobs_total": jobs,
        "approvals_pending": pending,
        "applications_submitted": submitted,
        "resume_versions_total": versions,
        "event_counts": {row["event"]: row["n"] for row in events},
    }
