"""Approval-gated application automation queue.

This module records an exact job/resume pair that the user has approved for
application automation. It does not itself interact with employer websites.
"""
import json
from typing import Any

from app.db import connection, log_event


class ApplicationAutomationError(ValueError):
    """An application cannot enter the automation queue safely."""


def approve_application_batch(items: list[dict[str, str]]) -> dict[str, Any]:
    """Atomically queue exact job/resume pairs after both approvals exist."""
    if not items:
        raise ApplicationAutomationError("Select at least one application.")
    if len(items) > 50:
        raise ApplicationAutomationError("A batch cannot exceed 50 applications.")

    # Reject repeated jobs or versions to make batch intent unambiguous.
    job_ids = [str(item.get("job_id", "")).strip() for item in items]
    version_ids = [str(item.get("version_id", "")).strip() for item in items]
    if any(not value for value in job_ids + version_ids):
        raise ApplicationAutomationError("Every item must include a job_id and version_id.")
    if len(set(job_ids)) != len(job_ids):
        raise ApplicationAutomationError("A batch cannot contain the same job more than once.")
    if len(set(version_ids)) != len(version_ids):
        raise ApplicationAutomationError("A batch cannot contain the same resume version more than once.")

    queued: list[dict[str, str]] = []
    with connection() as db:
        for job_id, version_id in zip(job_ids, version_ids):
            row = db.execute(
                """SELECT j.id AS job_id, j.title, j.company, j.url,
                          a.status AS approval_status,
                          rv.version_id, rv.status AS resume_status,
                          q.status AS queue_status
                   FROM jobs j
                   LEFT JOIN approvals a ON a.job_id=j.id
                   LEFT JOIN resume_versions rv ON rv.version_id=?
                   LEFT JOIN application_automation_queue q ON q.job_id=j.id
                   WHERE j.id=?""",
                (version_id, job_id),
            ).fetchone()
            if not row:
                raise ApplicationAutomationError(f"Job not found: {job_id}")
            if row["version_id"] != version_id:
                raise ApplicationAutomationError(f"Resume version not found: {version_id}")
            if row["approval_status"] != "approved":
                raise ApplicationAutomationError(f"Job {job_id} must be approved before it can be queued.")
            if row["resume_status"] != "approved":
                raise ApplicationAutomationError(
                    f"Resume version {version_id} must be approved before it can be queued."
                )
            # The selected immutable resume version must belong to this exact job.
            version_job = db.execute(
                "SELECT job_id FROM resume_versions WHERE version_id=?", (version_id,)
            ).fetchone()
            if not version_job or version_job["job_id"] != job_id:
                raise ApplicationAutomationError(
                    f"Resume version {version_id} does not belong to job {job_id}."
                )
            if row["queue_status"] == "submitted":
                raise ApplicationAutomationError(f"Job {job_id} has already been submitted.")
            db.execute(
                """INSERT INTO application_automation_queue(job_id,version_id,status,checkpoint,result_json)
                   VALUES(?,?,'approved','ready_for_browser_automation','{}')
                   ON CONFLICT(job_id) DO UPDATE SET
                     version_id=excluded.version_id, status='approved',
                     checkpoint='ready_for_browser_automation', result_json='{}',
                     approved_at=CURRENT_TIMESTAMP, updated_at=CURRENT_TIMESTAMP, submitted_at=NULL""",
                (job_id, version_id),
            )
            queued.append({
                "job_id": job_id,
                "version_id": version_id,
                "title": row["title"],
                "company": row["company"],
                "url": row["url"],
                "status": "approved",
            })

    log_event("application_batch_approved", {
        "count": len(queued),
        "job_ids": [item["job_id"] for item in queued],
        "version_ids": [item["version_id"] for item in queued],
    })
    return {"queued_count": len(queued), "applications": queued}


def list_automation_queue(status: str | None = None, limit: int = 100) -> list[dict[str, Any]]:
    allowed = {"approved", "in_progress", "needs_intervention", "submitted", "failed"}
    if status is not None and status not in allowed:
        raise ApplicationAutomationError("Unsupported queue status.")
    with connection() as db:
        if status:
            rows = db.execute(
                """SELECT q.*, j.title, j.company, j.location, j.url, j.score
                   FROM application_automation_queue q JOIN jobs j ON j.id=q.job_id
                   WHERE q.status=? ORDER BY q.updated_at DESC LIMIT ?""",
                (status, min(max(limit, 1), 200)),
            ).fetchall()
        else:
            rows = db.execute(
                """SELECT q.*, j.title, j.company, j.location, j.url, j.score
                   FROM application_automation_queue q JOIN jobs j ON j.id=q.job_id
                   ORDER BY q.updated_at DESC LIMIT ?""",
                (min(max(limit, 1), 200),),
            ).fetchall()
    result = []
    for row in rows:
        item = dict(row)
        try:
            item["result"] = json.loads(item.pop("result_json") or "{}")
        except json.JSONDecodeError:
            item["result"] = {}
        result.append(item)
    return result


def mark_intervention(job_id: str, checkpoint: str, details: dict[str, Any] | None = None) -> dict[str, Any]:
    """Pause a queued application at a user-action checkpoint."""
    allowed_checkpoints = {
        "captcha", "authentication", "unsupported_portal", "unknown_required_field",
        "consent_required", "upload_failed", "manual_review",
    }
    if checkpoint not in allowed_checkpoints:
        raise ApplicationAutomationError("Unsupported intervention checkpoint.")
    with connection() as db:
        row = db.execute(
            "SELECT status, version_id FROM application_automation_queue WHERE job_id=?",
            (job_id,),
        ).fetchone()
        if not row:
            raise ApplicationAutomationError("Application is not in the automation queue.")
        if row["status"] == "submitted":
            raise ApplicationAutomationError("A submitted application cannot be paused.")
        db.execute(
            """UPDATE application_automation_queue
               SET status='needs_intervention', checkpoint=?, result_json=?,
                   updated_at=CURRENT_TIMESTAMP WHERE job_id=?""",
            (checkpoint, json.dumps(details or {}, ensure_ascii=False), job_id),
        )
    log_event("application_needs_intervention", {
        "job_id": job_id, "version_id": row["version_id"], "checkpoint": checkpoint,
    }, "WARNING")
    return {"job_id": job_id, "status": "needs_intervention", "checkpoint": checkpoint}


def record_verified_submission(job_id: str, confirmation: str) -> dict[str, str]:
    """Record a submission only when browser automation supplied evidence."""
    confirmation = confirmation.strip()
    if len(confirmation) < 8:
        raise ApplicationAutomationError("Submission confirmation evidence is required.")
    with connection() as db:
        row = db.execute(
            """SELECT q.version_id, q.status, a.status AS job_approval,
                      rv.status AS resume_status, rv.job_id AS resume_job_id
               FROM application_automation_queue q
               JOIN approvals a ON a.job_id=q.job_id
               JOIN resume_versions rv ON rv.version_id=q.version_id
               WHERE q.job_id=?""",
            (job_id,),
        ).fetchone()
        if not row:
            raise ApplicationAutomationError("Application is not in the automation queue.")
        if row["status"] == "submitted":
            raise ApplicationAutomationError("Application is already recorded as submitted.")
        if row["job_approval"] != "approved" or row["resume_status"] not in {"approved", "submitted"}:
            raise ApplicationAutomationError("The exact job and resume version must remain approved.")
        if row["resume_job_id"] != job_id:
            raise ApplicationAutomationError("The queued resume version does not match this job.")
        db.execute(
            """UPDATE application_automation_queue
               SET status='submitted', checkpoint='', result_json=?,
                   submitted_at=CURRENT_TIMESTAMP, updated_at=CURRENT_TIMESTAMP WHERE job_id=?""",
            (json.dumps({"confirmation": confirmation}, ensure_ascii=False), job_id),
        )
        db.execute(
            """UPDATE applications SET status='submitted',
               notes=CASE WHEN notes='' THEN ? ELSE notes || char(10) || ? END,
               updated_at=CURRENT_TIMESTAMP WHERE job_id=?""",
            (f"Automation confirmation: {confirmation}", f"Automation confirmation: {confirmation}", job_id),
        )
        db.execute("UPDATE jobs SET status='submitted' WHERE id=?", (job_id,))
        db.execute(
            "UPDATE resume_versions SET status='submitted', submitted_at=CURRENT_TIMESTAMP WHERE version_id=?",
            (row["version_id"],),
        )
    log_event("application_automation_submitted", {
        "job_id": job_id, "version_id": row["version_id"],
    })
    return {"job_id": job_id, "version_id": row["version_id"], "status": "submitted"}
