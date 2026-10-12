import json
import logging
from contextlib import asynccontextmanager

from apscheduler.schedulers.background import BackgroundScheduler
from fastapi import FastAPI, HTTPException, Query, UploadFile, File
from fastapi.responses import FileResponse

from app.config import settings
from app.db import init_db, log_event, metrics_snapshot, connection
from app.models import ManualJobRequest, ApprovalDecision, ApplicationStatus, ResumeSubmissionRecord
from app.services.jobs import ingest_jobs, list_jobs, queue_approval, list_approvals, decide_approval, update_application
from app.agent import run_discovery
from app.notifications.email_report import send_report
from app.services.llm_ranking import rank_shortlist, queue_snapshot
from app.services.llm_worker import RankingWorker
from app.resumes import (
    ResumeError, upload_master, master_status, tailor_for_job, list_versions,
    get_version, approve_version, record_version_submission, resolve_artifact,
)

logging.basicConfig(level=settings.log_level)
scheduler = BackgroundScheduler(timezone="Asia/Kolkata")
ranking_worker = RankingWorker()


def _refresh_exports():
    """Keep JSON snapshots current after local state-changing API actions."""
    try:
        from app.services.json_export import export_json_snapshot
        return export_json_snapshot()
    except Exception as exc:
        log_event("json_export_refresh_failed", {"error": str(exc)[:220]}, "ERROR")
        return {"ok": False, "error": "JSON export refresh failed; inspect /events."}


def scheduled_discovery():
    try:
        run_discovery(source="adzuna")
    except Exception as exc:
        log_event("scheduled_discovery_failed", {"error": str(exc)[:250]}, "ERROR")


def scheduled_email():
    try:
        result = send_report()
        log_event("scheduled_email_report", result, "INFO" if result.get("sent") else "WARNING")
    except Exception as exc:
        log_event("scheduled_email_report_failed", {"error": str(exc)[:250]}, "ERROR")


@asynccontextmanager
async def lifespan(app):
    init_db()
    log_event("app_started", {"app": settings.app_name})
    if settings.scheduler_enabled:
        scheduler.add_job(
            scheduled_discovery, "interval",
            hours=max(1, settings.discovery_interval_hours), id="job_discovery",
            replace_existing=True, max_instances=1, coalesce=True,
        )
        scheduler.add_job(
            scheduled_email, "cron",
            hour=max(0, min(23, settings.daily_report_hour)), minute=0,
            id="daily_email", replace_existing=True, max_instances=1, coalesce=True,
        )
        scheduler.start()
        log_event("scheduler_started", {
            "interval_hours": settings.discovery_interval_hours,
            "daily_report_hour": settings.daily_report_hour,
        })
    if settings.ranking_worker_enabled:
        ranking_worker.start()
    try:
        yield
    finally:
        if settings.ranking_worker_enabled:
            worker_stopped = ranking_worker.stop(timeout=90)
            if not worker_stopped:
                log_event("ranking_worker_shutdown_incomplete", {
                    "message": "Worker did not stop within the shutdown timeout."
                }, "ERROR")
        if scheduler.running:
            scheduler.shutdown(wait=False)
        log_event("app_stopped", {})


app = FastAPI(title=settings.app_name, version="0.4.0", lifespan=lifespan)


@app.get("/health")
def health():
    try:
        with connection() as c:
            c.execute("SELECT 1")
        return {
            "status": "ok", "database": "ok",
            "scheduler_enabled": settings.scheduler_enabled,
            "llm_enabled": settings.llm_enabled,
            "groq_api_base": settings.groq_api_base,
            "model": settings.groq_model,
            "ranking_worker_enabled": settings.ranking_worker_enabled,
            "ranking_worker_running": ranking_worker.running,
            "auto_tailor_enabled": settings.auto_tailor_enabled,
            "auto_tailor_top_n": settings.auto_tailor_top_n,
            "auto_tailor_min_score": settings.auto_tailor_min_score,
            "json_exports_enabled": settings.json_exports_enabled,
        }
    except Exception as exc:
        raise HTTPException(status_code=503, detail=f"Health check failed: {exc}")


@app.post("/agent/run")
def agent_run(source: str = Query("adzuna", pattern="^(demo|adzuna)$"),
              what: str | None = None, where: str | None = None, send_email: bool = False):
    """Run discovery, ranking, new resume drafts, review queue, JSON export, optional digest."""
    try:
        return run_discovery(source, what, where, send_email)
    except Exception as exc:
        raise HTTPException(status_code=502, detail=f"Agent run failed: {exc}")


@app.get("/agent/runs")
def agent_runs(limit: int = Query(default=30, ge=1, le=200)):
    with connection() as c:
        rows = c.execute(
            "SELECT * FROM agent_runs ORDER BY started_at DESC LIMIT ?", (limit,)
        ).fetchall()
    output = []
    for row in rows:
        item = dict(row)
        try:
            item["summary"] = json.loads(item.pop("summary_json") or "{}")
        except json.JSONDecodeError:
            item["summary"] = {}
        output.append(item)
    return output


@app.post("/jobs/discover")
def discover(source: str = Query("demo", pattern="^(demo|adzuna)$"),
             what: str = "generative ai engineer", where: str = "India", limit: int = 20):
    try:
        from app.sources.demo import search_demo_jobs
        from app.sources.adzuna import search_adzuna_jobs
        found = search_demo_jobs(what, where) if source == "demo" else search_adzuna_jobs(what, where, limit)
        result = {"source": source, **ingest_jobs(found)}
        result["json_exports"] = _refresh_exports()
        return result
    except Exception as exc:
        log_event("discovery_failed", {"source": source, "error": str(exc)[:250]}, "ERROR")
        raise HTTPException(status_code=502, detail=str(exc))


@app.post("/jobs/ingest")
def ingest(request: ManualJobRequest):
    result = ingest_jobs([job.model_dump() for job in request.jobs])
    result["json_exports"] = _refresh_exports()
    return result


@app.get("/jobs")
def jobs(limit: int = 50, min_score: float = 0):
    return list_jobs(limit, min_score)


@app.post("/approvals/{job_id}")
def create_approval(job_id: str):
    if not queue_approval(job_id):
        raise HTTPException(status_code=404, detail="Job not found")
    _refresh_exports()
    return {"job_id": job_id, "status": "pending", "message": "Queued for review; nothing has been submitted."}


@app.get("/approvals")
def approvals(status: str = Query("pending", pattern="^(pending|approved|rejected)$")):
    return list_approvals(status)


@app.post("/approvals/{job_id}/decision")
def approval_decision(job_id: str, body: ApprovalDecision):
    if not decide_approval(job_id, body.decision, body.notes):
        raise HTTPException(status_code=404, detail="Job not found")
    exports = _refresh_exports()
    return {"job_id": job_id, "decision": body.decision, "json_exports": exports}


@app.post("/applications/{job_id}/status")
def application_status(job_id: str, body: ApplicationStatus):
    if body.status == "submitted":
        raise HTTPException(
            status_code=409,
            detail="Use /resumes/versions/{version_id}/record-submission so the exact approved resume version is recorded.",
        )
    try:
        ok = update_application(job_id, body.status, body.notes)
    except PermissionError as exc:
        raise HTTPException(status_code=409, detail=str(exc))
    if not ok:
        raise HTTPException(status_code=404, detail="Job not found")
    exports = _refresh_exports()
    return {"job_id": job_id, "status": body.status, "json_exports": exports}


@app.get("/ranking/queue")
def ranking_queue():
    return queue_snapshot()


@app.post("/ranking/process")
def process_ranking_queue(limit: int = Query(default=3, ge=1, le=30)):
    try:
        result = rank_shortlist(limit=limit)
        result["json_exports"] = _refresh_exports()
        return result
    except Exception:
        log_event("ranking_queue_processing_failed", {"error": "Inspect /events."}, "ERROR")
        raise HTTPException(status_code=500, detail="Ranking queue processing failed. Inspect /events.")


@app.post("/resumes/master")
async def set_master_resume(file: UploadFile = File(...)):
    try:
        raw = await file.read(15 * 1024 * 1024 + 1)
        result = upload_master(file.filename or "master_resume", raw)
        result["json_exports"] = _refresh_exports()
        return result
    except ResumeError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    finally:
        await file.close()


@app.get("/resumes/master")
def get_master_resume_status():
    return master_status()


@app.post("/resumes/tailor/{job_id}")
def tailor_resume(job_id: str):
    try:
        result = tailor_for_job(job_id)
        result["json_exports"] = _refresh_exports()
        return result
    except ResumeError as exc:
        status_code = 404 if "job not found" in str(exc).lower() else 422
        raise HTTPException(status_code=status_code, detail=str(exc))
    except Exception as exc:
        log_event("resume_tailoring_failed", {"job_id": job_id, "error": str(exc)[:240]}, "ERROR")
        raise HTTPException(status_code=502, detail="Resume tailoring failed safely. Inspect /events for details.")


@app.get("/resumes/{job_id}")
def resume_versions(job_id: str):
    from app.services.jobs import get_job
    if not get_job(job_id):
        raise HTTPException(status_code=404, detail="Job not found")
    return {"job_id": job_id, "versions": list_versions(job_id)}


@app.get("/resumes/versions/{version_id}")
def resume_version_detail(version_id: str):
    version = get_version(version_id)
    if not version:
        raise HTTPException(status_code=404, detail="Resume version not found")
    return version


@app.get("/resumes/versions/{version_id}/download")
def download_resume_version(version_id: str, file_type: str = Query("pdf", pattern="^(pdf|docx|report)$")):
    try:
        path = resolve_artifact(version_id, file_type)
    except ResumeError as exc:
        status = 404 if "not found" in str(exc).lower() or "missing" in str(exc).lower() else 400
        raise HTTPException(status_code=status, detail=str(exc))
    media = {
        "pdf": "application/pdf",
        "docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        "report": "application/json",
    }[file_type]
    return FileResponse(path, media_type=media, filename=path.name)


@app.post("/resumes/versions/{version_id}/approve")
def approve_resume_version(version_id: str):
    try:
        result = approve_version(version_id)
        result["json_exports"] = _refresh_exports()
        return result
    except ResumeError as exc:
        status = 404 if "not found" in str(exc).lower() else 409
        raise HTTPException(status_code=status, detail=str(exc))


@app.post("/resumes/versions/{version_id}/record-submission")
def record_resume_submission(version_id: str, body: ResumeSubmissionRecord):
    # Tracks a submission confirmed by the user; this does not submit employer forms.
    try:
        result = record_version_submission(version_id, body.notes)
        result["json_exports"] = _refresh_exports()
        return result
    except ResumeError as exc:
        status = 404 if "not found" in str(exc).lower() else 409
        raise HTTPException(status_code=status, detail=str(exc))


@app.get("/exports/status")
def json_export_status():
    from app.services.json_export import export_status
    return export_status()


@app.post("/exports/refresh")
def refresh_json_exports():
    from app.services.json_export import export_json_snapshot
    return export_json_snapshot()


@app.get("/metrics")
def metrics():
    return metrics_snapshot()


@app.get("/events")
def events(limit: int = 100):
    with connection() as c:
        rows = c.execute("SELECT * FROM events ORDER BY id DESC LIMIT ?", (min(max(limit, 1), 500),)).fetchall()
    return [dict(row) for row in rows]


@app.post("/reports/email")
def email_report():
    try:
        return send_report()
    except Exception:
        log_event("email_report_failed", {"error": "Inspect /events."}, "ERROR")
        raise HTTPException(status_code=502, detail="Email report failed; inspect /events.")
