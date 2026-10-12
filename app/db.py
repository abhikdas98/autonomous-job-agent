
import json
import sqlite3
from contextlib import contextmanager
from pathlib import Path

from app.config import settings


@contextmanager
def connection():
    path = Path(settings.database_path)
    path.parent.mkdir(parents=True, exist_ok=True)

    conn = sqlite3.connect(
        str(path),
        timeout=30,
    )
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
        # WAL is persistent for this database.
        # Set it during initialization, not on every connection.
        c.execute("PRAGMA journal_mode=WAL")

        c.executescript("""
        CREATE TABLE IF NOT EXISTS jobs (
          id TEXT PRIMARY KEY,
          title TEXT NOT NULL,
          company TEXT NOT NULL,
          location TEXT NOT NULL DEFAULT '',
          url TEXT NOT NULL,
          description TEXT NOT NULL DEFAULT '',
          source TEXT NOT NULL,
          posted_at TEXT NOT NULL DEFAULT '',
          fingerprint TEXT NOT NULL UNIQUE,
          score REAL NOT NULL DEFAULT 0,
          score_reasons TEXT NOT NULL DEFAULT '[]',
          status TEXT NOT NULL DEFAULT 'discovered',
          created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
          updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
        );

        CREATE TABLE IF NOT EXISTS approvals (
          job_id TEXT PRIMARY KEY REFERENCES jobs(id),
          status TEXT NOT NULL DEFAULT 'pending',
          notes TEXT NOT NULL DEFAULT '',
          decided_at TEXT,
          created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
        );

        CREATE TABLE IF NOT EXISTS applications (
          job_id TEXT PRIMARY KEY REFERENCES jobs(id),
          status TEXT NOT NULL DEFAULT 'not_submitted',
          notes TEXT NOT NULL DEFAULT '',
          updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
        );

        CREATE TABLE IF NOT EXISTS events (
          id INTEGER PRIMARY KEY AUTOINCREMENT,
          level TEXT NOT NULL,
          event TEXT NOT NULL,
          details TEXT NOT NULL DEFAULT '{}',
          created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
        );

        CREATE TABLE IF NOT EXISTS llm_cache (
          cache_key TEXT PRIMARY KEY,
          response TEXT NOT NULL,
          expires_at REAL NOT NULL
        );

        CREATE TABLE IF NOT EXISTS llm_budget (
          bucket TEXT PRIMARY KEY,
          count INTEGER NOT NULL
        );

        CREATE TABLE IF NOT EXISTS llm_request_times (
          id INTEGER PRIMARY KEY AUTOINCREMENT,
          created_at REAL NOT NULL
        );

        CREATE TABLE IF NOT EXISTS llm_ranking_queue (
            job_id TEXT PRIMARY KEY REFERENCES jobs(id) ON DELETE CASCADE,
            status TEXT NOT NULL DEFAULT 'pending'
                CHECK (status IN ('pending', 'completed')),
            attempts INTEGER NOT NULL DEFAULT 0,
            available_at REAL NOT NULL DEFAULT 0,
            last_error TEXT NOT NULL DEFAULT '',
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
        );

        CREATE INDEX IF NOT EXISTS idx_llm_queue_due
            ON llm_ranking_queue(status, available_at);

        CREATE INDEX IF NOT EXISTS idx_jobs_score
          ON jobs(score DESC);

        CREATE INDEX IF NOT EXISTS idx_events_created
          ON events(created_at DESC);

        CREATE INDEX IF NOT EXISTS idx_llm_times
          ON llm_request_times(created_at);
        """)


def log_event(event, details=None, level="INFO"):
    with connection() as c:
        c.execute(
            "INSERT INTO events(level,event,details) VALUES(?,?,?)",
            (
                level,
                event,
                json.dumps(details or {}, ensure_ascii=False),
            ),
        )


def metrics_snapshot():
    with connection() as c:
        jobs = c.execute(
            "SELECT COUNT(*) FROM jobs"
        ).fetchone()[0]

        pending = c.execute(
            "SELECT COUNT(*) FROM approvals WHERE status='pending'"
        ).fetchone()[0]

        submitted = c.execute(
            "SELECT COUNT(*) FROM applications WHERE status='submitted'"
        ).fetchone()[0]

        events = c.execute(
            "SELECT event, COUNT(*) n FROM events GROUP BY event"
        ).fetchall()

    return {
        "jobs_total": jobs,
        "approvals_pending": pending,
        "applications_submitted": submitted,
        "event_counts": {
            row["event"]: row["n"] for row in events
        },
    }
