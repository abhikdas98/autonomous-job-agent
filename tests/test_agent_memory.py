import json

import pytest

from app.config import settings
from app.db import connection, init_db
from app.services.agent_memory import (
    get_preferences,
    list_feedback,
    memory_summary,
    record_feedback,
    save_preferences,
)
from app.services.jobs import ingest_jobs


@pytest.fixture
def local_db(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "database_path", str(tmp_path / "agent.db"))
    init_db()
    yield
    # Connections are short-lived; no explicit teardown is needed.


def test_preferences_default_and_persist(local_db):
    defaults = get_preferences()
    assert defaults["source"] == "defaults"
    assert "Agentic AI Engineer" in defaults["preferred_roles"]

    saved = save_preferences({
        "preferred_roles": ["GenAI Engineer", "GenAI Engineer", "  "],
        "preferred_skills": ["Python", "RAG"],
        "preferred_locations": ["India"],
        "excluded_terms": ["sales"],
        "notes": "Prefer hands-on engineering",
    })
    assert saved["source"] == "saved"
    assert saved["preferred_roles"] == ["GenAI Engineer"]
    assert get_preferences()["notes"] == "Prefer hands-on engineering"


def test_feedback_requires_existing_job_and_is_upserted(local_db):
    with pytest.raises(LookupError):
        record_feedback("missing", "irrelevant")

    ingest_jobs([{
        "title": "Generative AI Engineer",
        "company": "Example",
        "location": "India",
        "url": "https://example.com/jobs/123",
        "description": "Build Python and RAG applications with LangChain and FastAPI.",
        "source": "manual",
    }])
    with connection() as db:
        job = db.execute("SELECT id FROM jobs").fetchone()
    job_id = job["id"]

    record_feedback(job_id, "not_interested", "Role is too senior")
    record_feedback(job_id, "interested", "Good RAG and agent workflow match")

    rows = list_feedback()
    assert len(rows) == 1
    assert rows[0]["job_id"] == job_id
    assert rows[0]["feedback"] == "interested"
    assert rows[0]["reason"] == "Good RAG and agent workflow match"
    assert memory_summary()["feedback_counts"] == {"interested": 1}


def test_init_db_marks_only_stale_runs_interrupted(local_db):
    with connection() as db:
        db.execute(
            """INSERT INTO agent_runs(run_id,source,query,location,status,started_at)
               VALUES('stale','demo','q','India','running',datetime('now','-2 hours'))"""
        )
        db.execute(
            """INSERT INTO agent_runs(run_id,source,query,location,status,started_at)
               VALUES('fresh','demo','q','India','running',datetime('now','-2 minutes'))"""
        )

    init_db()
    with connection() as db:
        statuses = {
            row["run_id"]: row["status"]
            for row in db.execute("SELECT run_id,status FROM agent_runs").fetchall()
        }
    assert statuses["stale"] == "interrupted"
    assert statuses["fresh"] == "running"


def test_saved_preferences_adjust_deterministic_ranking(local_db):
    from app.services.ranking import rank_job

    baseline, _ = rank_job(
        "Support Engineer", "Example", "Chennai", "Maintain internal tools.",
    )
    preferences = save_preferences({
        "preferred_roles": ["Support Engineer"],
        "preferred_skills": ["internal tools"],
        "preferred_locations": ["Chennai"],
        "excluded_terms": [],
        "notes": "",
    })
    personalized, reasons = rank_job(
        "Support Engineer", "Example", "Chennai", "Maintain internal tools.",
        preferences=preferences,
    )
    assert personalized > baseline
    assert any("saved preferred role" in reason for reason in reasons)

def test_job_listing_exposes_feedback_and_approval_state(local_db):
    from app.services.jobs import list_jobs, queue_approval

    ingest_jobs([{
        "title": "Agentic AI Engineer",
        "company": "Example",
        "location": "India",
        "url": "https://example.com/jobs/agentic",
        "description": "Build Python and RAG applications.",
        "source": "manual",
    }])
    job = list_jobs()[0]
    job_id = job["id"]

    record_feedback(job_id, "interested", "Strong agentic AI match")
    assert list_jobs()[0]["feedback"] == "interested"
    assert list_jobs()[0]["feedback_reason"] == "Strong agentic AI match"

    assert queue_approval(job_id) is True
    queued = list_jobs()[0]
    assert queued["approval_status"] == "pending"
    assert queued["status"] == "awaiting_approval"
