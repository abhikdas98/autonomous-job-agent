import json
import sqlite3

from app.config import settings
from app.db import init_db
from app.services.json_export import export_json_snapshot


def test_json_export_persists_jobs_applications_and_latest_run(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "database_path", str(tmp_path / "agent.db"))
    monkeypatch.setattr(settings, "json_exports_enabled", True)
    init_db()

    with sqlite3.connect(settings.database_path) as db:
        db.execute(
            """INSERT INTO jobs
               (id,title,company,location,url,description,source,fingerprint,score,score_reasons)
               VALUES(?,?,?,?,?,?,?,?,?,?)""",
            ("job-1", "GenAI Engineer", "Example Co", "Hyderabad",
             "https://example.test/job", "Build LLM applications using Python and RAG.",
             "adzuna", "fingerprint-1", 80, '["Skill: Python"]'),
        )
        db.execute(
            "INSERT INTO applications(job_id,status,notes) VALUES(?,?,?)",
            ("job-1", "not_submitted", ""),
        )
        db.execute(
            "INSERT INTO agent_runs(run_id,source,query,location,status) VALUES(?,?,?,?,?)",
            ("run-1", "adzuna", "GenAI Engineer", "Hyderabad", "running"),
        )

    result = export_json_snapshot(latest_run={"run_id": "run-1", "status": "completed"})
    root = tmp_path / "exports"
    jobs = json.loads((root / "jobs.json").read_text(encoding="utf-8"))
    applications = json.loads((root / "applications.json").read_text(encoding="utf-8"))
    latest = json.loads((root / "latest_run.json").read_text(encoding="utf-8"))

    assert result["ok"] is True
    assert jobs[0]["score_reasons"] == ["Skill: Python"]
    assert applications[0]["status"] == "not_submitted"
    assert latest["status"] == "completed"
    assert (root / "manifest.json").is_file()
