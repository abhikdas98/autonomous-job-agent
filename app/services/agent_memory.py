"""Small, local-first preference and feedback memory for job discovery."""
from __future__ import annotations

import json
from typing import Any

from app.db import connection, log_event

DEFAULT_PREFERENCES: dict[str, Any] = {
    "preferred_roles": [
        "Agentic AI Engineer",
        "Generative AI Engineer",
        "GenAI Developer",
    ],
    "preferred_skills": [
        "Python", "LLM", "RAG", "LangChain", "LangGraph", "FastAPI",
        "tool calling", "multi-agent systems",
    ],
    "preferred_locations": ["India", "Remote", "Hyderabad"],
    "excluded_terms": ["sales executive", "business development", "data entry"],
    "notes": "",
}


def _clean_list(value: Any, field: str, max_items: int = 30) -> list[str]:
    if not isinstance(value, list):
        raise ValueError(f"{field} must be a list of strings.")
    cleaned: list[str] = []
    seen: set[str] = set()
    for item in value[:max_items]:
        if not isinstance(item, str):
            raise ValueError(f"Every {field} item must be a string.")
        item = " ".join(item.split()).strip()
        if not item:
            continue
        if len(item) > 100:
            raise ValueError(f"Each {field} item must be 100 characters or fewer.")
        key = item.casefold()
        if key not in seen:
            cleaned.append(item)
            seen.add(key)
    return cleaned


def get_preferences() -> dict[str, Any]:
    with connection() as db:
        row = db.execute("SELECT value_json FROM agent_memory WHERE key='preferences'").fetchone()
    if not row:
        return {**DEFAULT_PREFERENCES, "source": "defaults"}
    try:
        saved = json.loads(row["value_json"])
    except (TypeError, json.JSONDecodeError):
        saved = {}
    result = {**DEFAULT_PREFERENCES, **saved}
    result["source"] = "saved"
    return result


def save_preferences(payload: dict[str, Any]) -> dict[str, Any]:
    cleaned = {
        "preferred_roles": _clean_list(payload.get("preferred_roles", DEFAULT_PREFERENCES["preferred_roles"]), "preferred_roles"),
        "preferred_skills": _clean_list(payload.get("preferred_skills", DEFAULT_PREFERENCES["preferred_skills"]), "preferred_skills"),
        "preferred_locations": _clean_list(payload.get("preferred_locations", DEFAULT_PREFERENCES["preferred_locations"]), "preferred_locations"),
        "excluded_terms": _clean_list(payload.get("excluded_terms", DEFAULT_PREFERENCES["excluded_terms"]), "excluded_terms"),
        "notes": " ".join(str(payload.get("notes", "")).split()).strip()[:1000],
    }
    with connection() as db:
        db.execute(
            """INSERT INTO agent_memory(key,value_json,updated_at)
               VALUES('preferences',?,CURRENT_TIMESTAMP)
               ON CONFLICT(key) DO UPDATE SET value_json=excluded.value_json,
               updated_at=CURRENT_TIMESTAMP""",
            (json.dumps(cleaned, ensure_ascii=False),),
        )
    log_event("job_search_preferences_saved", {
        "preferred_roles_count": len(cleaned["preferred_roles"]),
        "preferred_skills_count": len(cleaned["preferred_skills"]),
        "excluded_terms_count": len(cleaned["excluded_terms"]),
    })
    return {**cleaned, "source": "saved"}


def record_feedback(job_id: str, feedback: str, reason: str = "") -> dict[str, Any]:
    allowed = {"interested", "not_interested", "irrelevant", "applied"}
    if feedback not in allowed:
        raise ValueError(f"feedback must be one of: {', '.join(sorted(allowed))}.")
    reason = " ".join(reason.split()).strip()[:1000]
    with connection() as db:
        job = db.execute("SELECT id FROM jobs WHERE id=?", (job_id,)).fetchone()
        if not job:
            raise LookupError("Job not found.")
        db.execute(
            """INSERT INTO job_feedback(job_id,feedback,reason)
               VALUES(?,?,?)
               ON CONFLICT(job_id) DO UPDATE SET feedback=excluded.feedback,
               reason=excluded.reason,updated_at=CURRENT_TIMESTAMP""",
            (job_id, feedback, reason),
        )
    log_event("job_feedback_recorded", {"job_id": job_id, "feedback": feedback, "has_reason": bool(reason)})
    return {"job_id": job_id, "feedback": feedback, "reason": reason}


def list_feedback(limit: int = 50) -> list[dict[str, Any]]:
    with connection() as db:
        rows = db.execute(
            """SELECT f.job_id,f.feedback,f.reason,f.updated_at,j.title,j.company,j.url,j.score
               FROM job_feedback f JOIN jobs j ON j.id=f.job_id
               ORDER BY f.updated_at DESC LIMIT ?""",
            (min(max(int(limit), 1), 200),),
        ).fetchall()
    return [dict(row) for row in rows]


def memory_summary() -> dict[str, Any]:
    preferences = get_preferences()
    with connection() as db:
        rows = db.execute(
            "SELECT feedback,COUNT(*) AS count FROM job_feedback GROUP BY feedback"
        ).fetchall()
    counts = {row["feedback"]: row["count"] for row in rows}
    return {
        "preferences": preferences,
        "feedback_counts": counts,
        "feedback_total": sum(counts.values()),
        "note": "Feedback is stored locally. It is not used to train a model or sent to an LLM automatically.",
    }
