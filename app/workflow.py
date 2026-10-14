"""One-click daily workflow orchestrated with LangGraph.

The graph discovers jobs, ranks them, creates source-verified resume drafts for
the strongest new matches, queues them for human review, sends an optional
digest, and exports the local state as JSON.
"""
from __future__ import annotations

import json
import threading
import uuid
from pathlib import Path
from typing import Any, TypedDict

from langgraph.graph import END, StateGraph

from app.config import settings
from app.db import connection, heartbeat_agent_run, log_event
from app.notifications.email_report import send_report
from app.services.jobs import ingest_jobs, list_jobs, queue_approval
from app.services.llm_ranking import rank_shortlist
from app.sources.adzuna import search_adzuna_jobs
from app.sources.demo import search_demo_jobs


class AgentState(TypedDict, total=False):
    run_id: str
    source: str
    query: str
    location: str
    send_email: bool
    ingestion: dict[str, Any]
    ranking: dict[str, Any]
    tailoring: dict[str, Any]
    email: dict[str, Any]
    json_exports: dict[str, Any]
    warnings: list[str]
    fatal_error: str
    result: dict[str, Any]


def _discover(state: AgentState) -> dict[str, Any]:
    try:
        if state["source"] == "demo":
            jobs = search_demo_jobs(state["query"], state["location"])
        elif state["source"] == "adzuna":
            jobs = search_adzuna_jobs(state["query"], state["location"], 50)
        else:
            raise ValueError("source must be 'adzuna' or 'demo'")
        ingestion = ingest_jobs(jobs)
        return {
            "ingestion": ingestion,
            "warnings": [],
            "found_count": len(jobs),
        }
    except Exception as exc:
        message = str(exc)[:300]
        try:
            with connection() as db:
                db.execute(
                    """UPDATE agent_runs SET status='failed', completed_at=CURRENT_TIMESTAMP,
                       error=? WHERE run_id=?""",
                    (message, state["run_id"]),
                )
            log_event("agent_discovery_failed", {"run_id": state["run_id"], "error": message}, "ERROR")
        except Exception:
            pass
        return {
            "fatal_error": message,
            "warnings": [f"Discovery failed: {message}"],
            "ingestion": {"received": 0, "inserted": 0, "duplicates": 0},
            "found_count": 0,
        }


def _rank(state: AgentState) -> dict[str, Any]:
    if state.get("fatal_error"):
        return {"ranking": {"status": "skipped", "reason": "Discovery failed"}}
    try:
        result = rank_shortlist(limit=settings.workflow_llm_rank_top_n)
        log_event("agent_ranking_completed", {"run_id": state["run_id"], **result})
        return {"ranking": {"status": "completed", **result}}
    except Exception as exc:
        message = str(exc)[:250]
        log_event("agent_ranking_failed", {"run_id": state["run_id"], "error": message}, "ERROR")
        warnings = list(state.get("warnings", []))
        warnings.append(f"LLM ranking was unavailable; deterministic scores are retained: {message}")
        return {
            "ranking": {"status": "deterministic_fallback", "error": message},
            "warnings": warnings,
        }


def _is_real_listing(job: dict[str, Any]) -> bool:
    # Demo data is useful for development but should never become an application draft.
    if str(job.get("source", "")).casefold() == "demo":
        return False
    title = str(job.get("title", "")).strip().casefold()
    company = str(job.get("company", "")).strip().casefold()
    if title.startswith(("test ", "sample ", "demo ")):
        return False
    if company in {"demo company", "sample company", "test company"}:
        return False
    return True


def _tailor(state: AgentState) -> dict[str, Any]:
    if state.get("fatal_error"):
        return {"tailoring": {"status": "skipped", "reason": "Discovery failed", "created": []}}
    from app import resumes

    if not settings.auto_tailor_enabled:
        return {"tailoring": {"status": "disabled", "created": [], "skipped": ["Auto-tailoring is disabled by configuration."]}}
    if not resumes.MASTER_SOURCE.is_file():
        message = "Upload the master resume once in the Resume tailoring tab; discovery and tracking will still work."
        return {"tailoring": {"status": "needs_master_resume", "created": [], "skipped": [message]}}

    with connection() as db:
        existing = {
            row["job_id"]
            for row in db.execute("SELECT DISTINCT job_id FROM resume_versions").fetchall()
        }
        terminal = {
            row["id"]
            for row in db.execute(
                "SELECT id FROM jobs WHERE status IN ('submitted','rejected_by_user','rejected','withdrawn')"
            ).fetchall()
        }

    created: list[dict[str, Any]] = []
    skipped: list[str] = []
    candidates = list_jobs(limit=200, min_score=settings.auto_tailor_min_score)
    attempted = 0
    for job in candidates:
        if len(created) >= settings.auto_tailor_top_n:
            break
        job_id = job["id"]
        if job_id in existing or job_id in terminal or not _is_real_listing(job):
            continue
        if len(str(job.get("description") or "").strip()) < 80:
            skipped.append(f"{job.get('title', job_id)}: job description is too short for safe tailoring.")
            continue
        attempted += 1
        try:
            version = resumes.tailor_for_job(job_id)
            queue_approval(job_id)  # Adds to review queue; never approves or submits.
            created.append({
                "job_id": job_id,
                "url": job.get("url", ""),
                "title": job.get("title", ""),
                "company": job.get("company", ""),
                "score": job.get("score", 0),
                "version_id": version["version_id"],
                "version_number": version["version_number"],
                "status": version["status"],
                "pdf_path": version["pdf_path"],
                "docx_path": version["docx_path"],
                "report_path": version["report_path"],
                "queued_for_human_review": True,
            })
        except Exception as exc:
            reason = str(exc)[:180]
            skipped.append(f"{job.get('title', job_id)}: {reason}")
            log_event("agent_auto_tailoring_failed", {
                "run_id": state["run_id"], "job_id": job_id, "error": reason,
            }, "WARNING")

    summary = {
        "status": "completed" if created or not candidates else "completed_with_skips",
        "created_count": len(created),
        "attempted_count": attempted,
        "already_had_version_count": sum(1 for job in candidates if job["id"] in existing),
        "created": created,
        "skipped": skipped[:30],
        "threshold_score": settings.auto_tailor_min_score,
        "max_new_versions_per_run": settings.auto_tailor_top_n,
    }
    log_event("agent_auto_tailoring_completed", {
        "run_id": state["run_id"], "created_count": len(created),
        "attempted_count": attempted, "skipped_count": len(skipped),
    })
    warnings = list(state.get("warnings", []))
    if skipped:
        warnings.append(f"{len(skipped)} job(s) were skipped during resume tailoring; see this run's JSON record.")
    return {"tailoring": summary, "warnings": warnings}


def _notify(state: AgentState) -> dict[str, Any]:
    if not state.get("send_email", False):
        return {"email": {"sent": False, "reason": "Email digest not requested for this run."}}
    try:
        result = send_report()
        if not result.get("sent"):
            warnings = list(state.get("warnings", []))
            warnings.append(result.get("reason", "Email digest was not sent."))
            return {"email": result, "warnings": warnings}
        return {"email": result}
    except Exception as exc:
        message = str(exc)[:200]
        log_event("agent_email_failed", {"run_id": state["run_id"], "error": message}, "ERROR")
        warnings = list(state.get("warnings", []))
        warnings.append(f"Email digest failed: {message}")
        return {"email": {"sent": False, "reason": message}, "warnings": warnings}


def _export(state: AgentState) -> dict[str, Any]:
    from app.services.json_export import export_json_snapshot

    warnings = list(state.get("warnings", []))
    status = "failed" if state.get("fatal_error") else ("completed_with_warnings" if warnings else "completed")
    summary = {
        "run_id": state["run_id"],
        "status": status,
        "source": state["source"],
        "query": state["query"],
        "location": state["location"],
        "found_count": state.get("found_count", 0),
        "ingestion": state.get("ingestion", {}),
        "ranking": state.get("ranking", {}),
        "tailoring": state.get("tailoring", {}),
        "email": state.get("email", {}),
        "warnings": warnings,
        "error": state.get("fatal_error", ""),
    }
    with connection() as db:
        db.execute(
            """UPDATE agent_runs SET status=?, completed_at=CURRENT_TIMESTAMP,
               summary_json=?, error=? WHERE run_id=?""",
            (status, json.dumps(summary, ensure_ascii=False), state.get("fatal_error", ""), state["run_id"]),
        )
    log_event("agent_run_completed" if not state.get("fatal_error") else "agent_run_failed", summary,
              "INFO" if not state.get("fatal_error") else "ERROR")
    try:
        exports = export_json_snapshot(latest_run=summary)
    except Exception as exc:
        message = f"JSON export failed: {str(exc)[:180]}"
        warnings.append(message)
        log_event("json_export_failed", {"run_id": state["run_id"], "error": message}, "ERROR")
        exports = {"ok": False, "error": message, "files": []}
    result = {
        **summary,
        "warnings": warnings,
        "json_exports": exports,
        "human_review_required": True,
        "submission_note": (
            "Resume drafts are queued for review. Review the exact PDF/DOCX and approve both the job "
            "and resume version before applying. This workflow does not submit applications to employers."
        ),
    }
    return {"json_exports": exports, "result": result}


def build_agent_graph():
    builder = StateGraph(AgentState)
    builder.add_node("discover", _discover)
    builder.add_node("rank", _rank)
    builder.add_node("tailor", _tailor)
    builder.add_node("notify", _notify)
    builder.add_node("export", _export)
    builder.set_entry_point("discover")
    builder.add_edge("discover", "rank")
    builder.add_edge("rank", "tailor")
    builder.add_edge("tailor", "notify")
    builder.add_edge("notify", "export")
    builder.add_edge("export", END)
    return builder.compile()


_AGENT_GRAPH = build_agent_graph()


def _heartbeat_loop(run_id: str, stop_event: threading.Event, interval_seconds: int = 60) -> None:
    """Keep a live workflow from being mistaken for a crashed process."""
    while not stop_event.wait(interval_seconds):
        try:
            if not heartbeat_agent_run(run_id):
                return
        except Exception:
            # A temporary SQLite lock must not crash the workflow. The next
            # heartbeat retries; stale recovery remains the final safeguard.
            continue


def run_agent_workflow(source: str = "adzuna", what: str | None = None,
                       where: str | None = None, send_email: bool = False) -> dict[str, Any]:
    query = what or settings.default_job_query
    location = where or settings.default_job_location
    run_id = uuid.uuid4().hex
    initial_state: AgentState = {
        "run_id": run_id,
        "source": source,
        "query": query,
        "location": location,
        "send_email": send_email,
        "warnings": [],
    }
    with connection() as db:
        db.execute(
            """INSERT INTO agent_runs(run_id, source, query, location, status, heartbeat_at)
               VALUES(?, ?, ?, ?, 'running', CURRENT_TIMESTAMP)""",
            (run_id, source, query, location),
        )
    log_event("agent_run_started", {
        "run_id": run_id, "source": source, "query": query, "location": location,
    })

    stop_event = threading.Event()
    heartbeat_thread = threading.Thread(
        target=_heartbeat_loop, args=(run_id, stop_event),
        name=f"agent-heartbeat-{run_id[:8]}", daemon=True,
    )
    heartbeat_thread.start()
    try:
        state = _AGENT_GRAPH.invoke(initial_state)
        return state.get("result", {
            "status": "failed",
            "error": state.get("fatal_error", "Workflow ended without a result."),
            "warnings": state.get("warnings", []),
        })
    except Exception as exc:
        message = str(exc)[:300]
        with connection() as db:
            db.execute(
                """UPDATE agent_runs SET status='failed', completed_at=CURRENT_TIMESTAMP,
                   error=? WHERE run_id=? AND status='running'""",
                (message, run_id),
            )
        log_event("agent_workflow_crashed", {"run_id": run_id, "error": message}, "ERROR")
        raise
    finally:
        stop_event.set()
        heartbeat_thread.join(timeout=2)
