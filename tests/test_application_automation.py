import json

import pytest

from app.config import settings
from app.db import connection, init_db
from app.services.application_automation import (
    ApplicationAutomationError,
    approve_application_batch,
    list_automation_queue,
    mark_intervention,
    record_verified_submission,
    retry_application,
)
from app.services.application_profile import read_application_profile, save_application_profile
from app.services.browser_automation import _profile_value
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
                   VALUES(?,'pending','test approval',CURRENT_TIMESTAMP)""",
                (job_id,),
            )
            db.execute(
                """INSERT INTO resume_versions
                   (version_id,job_id,version_number,status,docx_path,pdf_path,report_path,
                    source_sha256,job_description_sha256)
                   VALUES(?,?,1,'needs_review','resume.docx','resume.pdf','report.json','source-sha','job-sha')""",
                (f"version-{job_id}", job_id),
            )


def test_batch_approval_authorizes_exact_job_and_resume_together(automation_db):
    result = approve_application_batch([
        {"job_id": "job-1", "version_id": "version-job-1"},
        {"job_id": "job-2", "version_id": "version-job-2"},
    ])
    assert result["queued_count"] == 2
    assert {item["job_id"] for item in result["applications"]} == {"job-1", "job-2"}
    queued = list_automation_queue(status="approved")
    assert len(queued) == 2
    assert all(item["checkpoint"] == "ready_for_browser_automation" for item in queued)
    with connection() as db:
        assert db.execute("SELECT status FROM approvals WHERE job_id='job-1'").fetchone()["status"] == "approved"
        assert db.execute("SELECT status FROM resume_versions WHERE version_id='version-job-1'").fetchone()["status"] == "approved"


def test_batch_rejects_rejected_job_atomically(automation_db):
    decide_approval("job-2", "rejected")
    with pytest.raises(ApplicationAutomationError, match="was rejected"):
        approve_application_batch([
            {"job_id": "job-1", "version_id": "version-job-1"},
            {"job_id": "job-2", "version_id": "version-job-2"},
        ])
    assert list_automation_queue() == []
    with connection() as db:
        assert db.execute("SELECT status FROM approvals WHERE job_id='job-1'").fetchone()["status"] == "pending"


def test_batch_rejects_resume_version_from_another_job(automation_db):
    with pytest.raises(ApplicationAutomationError, match="does not belong"):
        approve_application_batch([{"job_id": "job-1", "version_id": "version-job-2"}])
    assert list_automation_queue() == []


def test_intervention_can_be_retried_after_profile_or_session_is_fixed(automation_db):
    approve_application_batch([{"job_id": "job-1", "version_id": "version-job-1"}])
    mark_intervention("job-1", "captcha", {"message": "Human verification required"})
    result = retry_application("job-1")
    assert result["status"] == "approved"
    assert list_automation_queue(status="approved")[0]["checkpoint"] == "ready_for_browser_automation"


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


def test_application_profile_is_local_and_allowlisted(automation_db):
    result = save_application_profile({
        "full_name": "Example Candidate",
        "email": "candidate@example.com",
        "unexpected_secret": "must not be saved",
        "additional_answers": {"Are you willing to relocate?": "Yes"},
    })
    assert result["configured"] is True
    saved = read_application_profile()["profile"]
    assert saved["full_name"] == "Example Candidate"
    assert saved["additional_answers"] == {"Are you willing to relocate?": "Yes"}
    assert "unexpected_secret" not in json.dumps(saved)


def test_browser_field_mapping_uses_verified_values_only():
    profile = {
        "full_name": "Example Candidate",
        "email": "candidate@example.com",
        "additional_answers": {"Are you willing to relocate?": "Yes"},
    }
    assert _profile_value("Email address", profile) == "candidate@example.com"
    assert _profile_value("First name", profile) == "Example"
    assert _profile_value("Are you willing to relocate?", profile) == "Yes"
    assert _profile_value("Tell us about your biggest achievement", profile) is None
