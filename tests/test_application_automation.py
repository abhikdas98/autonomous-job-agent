import pytest

from app.config import settings
from app.db import connection, init_db
from app.services.application_automation import (
    ApplicationAutomationError,
    approve_application_batch,
    list_automation_queue,
    mark_intervention,
    record_verified_submission,
)
from app.services.jobs import decide_approval


@pytest.fixture
def automation_db(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "database_path", str(tmp_path / "automation.db"))
    init_db()
    with connection() as db:
        for job_id in ("job-1", "job-2"):
            db.execute(
                """INSERT INTO jobs
                   (id,title,company,location,url,description,source,fingerprint,score,score_reasons)
                   VALUES(?,?,?,?,?,?,?,?,?,?)""",
                (job_id, f"GenAI Engineer {job_id}", "Example Co", "India",
                 f"https://example.com/{job_id}", "Role description long enough for test",
                 "test", f"fp-{job_id}", 90, "[]"),
            )
            db.execute(
                "INSERT INTO applications(job_id,status) VALUES(?,'not_submitted')", (job_id,)
            )
            db.execute(
                """INSERT INTO approvals(job_id,status,notes,decided_at)
                   VALUES(?,'approved','test approval',CURRENT_TIMESTAMP)""",
                (job_id,),
            )
            db.execute(
                """INSERT INTO resume_versions
                   (version_id,job_id,version_number,status,docx_path,pdf_path,report_path,
                    source_sha256,job_description_sha256)
                   VALUES(?,?,1,'approved','resume.docx','resume.pdf','report.json','source-sha','job-sha')""",
                (f"version-{job_id}", job_id),
            )


def test_batch_queues_exact_approved_job_resume_pairs(automation_db):
    result = approve_application_batch([
        {"job_id": "job-1", "version_id": "version-job-1"},
        {"job_id": "job-2", "version_id": "version-job-2"},
    ])
    assert result["queued_count"] == 2
    assert {item["job_id"] for item in result["applications"]} == {"job-1", "job-2"}
    queued = list_automation_queue(status="approved")
    assert len(queued) == 2
    assert all(item["checkpoint"] == "ready_for_browser_automation" for item in queued)


def test_batch_rejects_unapproved_job(automation_db):
    decide_approval("job-2", "rejected")
    with pytest.raises(ApplicationAutomationError, match="was rejected"):
        approve_application_batch([
            {"job_id": "job-1", "version_id": "version-job-1"},
            {"job_id": "job-2", "version_id": "version-job-2"},
        ])
    assert list_automation_queue() == []


def test_batch_rejects_resume_version_from_another_job(automation_db):
    with pytest.raises(ApplicationAutomationError, match="does not belong"):
        approve_application_batch([
            {"job_id": "job-1", "version_id": "version-job-2"},
        ])
    assert list_automation_queue() == []


def test_intervention_pauses_approved_application(automation_db):
    approve_application_batch([{"job_id": "job-1", "version_id": "version-job-1"}])
    result = mark_intervention("job-1", "captcha", {"message": "Human verification required"})
    assert result["status"] == "needs_intervention"
    item = list_automation_queue(status="needs_intervention")[0]
    assert item["checkpoint"] == "captcha"
    assert item["result"] == {"message": "Human verification required"}


def test_submission_requires_evidence_and_updates_all_records(automation_db):
    approve_application_batch([{"job_id": "job-1", "version_id": "version-job-1"}])
    with pytest.raises(ApplicationAutomationError, match="confirmation evidence"):
        record_verified_submission("job-1", "ok")
    result = record_verified_submission("job-1", "Application confirmation #ABC123")
    assert result["status"] == "submitted"
    assert list_automation_queue(status="submitted")[0]["version_id"] == "version-job-1"
    with connection() as db:
        assert db.execute("SELECT status FROM jobs WHERE id='job-1'").fetchone()["status"] == "submitted"
        assert db.execute("SELECT status FROM applications WHERE job_id='job-1'").fetchone()["status"] == "submitted"
        assert db.execute("SELECT status FROM resume_versions WHERE version_id='version-job-1'").fetchone()["status"] == "submitted"


def test_already_submitted_application_cannot_be_queued_again(automation_db):
    approve_application_batch([{"job_id": "job-1", "version_id": "version-job-1"}])
    record_verified_submission("job-1", "Application confirmation #ABC123")
    with pytest.raises(ApplicationAutomationError, match="already been submitted"):
        approve_application_batch([{"job_id": "job-1", "version_id": "version-job-1"}])
