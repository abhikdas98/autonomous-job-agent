import hashlib
import json

from app.db import connection, log_event
from app.services.identity import fingerprint
from app.services.ranking import rank_job
from app.services.agent_memory import get_preferences


def ingest_jobs(items):
    inserted = duplicates = 0
    preferences = get_preferences()
    for item in items:
        title = str(item.get("title") or "Untitled").strip()
        company = str(item.get("company") or "Unknown").strip()
        location = str(item.get("location") or "").strip()
        url = str(item.get("url") or "").strip()
        if not url:
            continue
        desc = str(item.get("description") or "")
        source = str(item.get("source") or "unknown")
        posted = str(item.get("posted_at") or "")
        fp = fingerprint(title, company, location, url)
        score, reasons = rank_job(title, company, location, desc, preferences=preferences)
        job_id = hashlib.sha256(fp.encode()).hexdigest()[:24]
        with connection() as c:
            old = c.execute("SELECT id, score, score_reasons FROM jobs WHERE fingerprint=?", (fp,)).fetchone()
            if old:
                previous_reasons = json.loads(old["score_reasons"] or "[]")
                # A subsequent discovery shouldn't erase a useful LLM ranking.
                if any(str(reason).startswith("LLM:") for reason in previous_reasons):
                    score, reasons = old["score"], previous_reasons
                c.execute("""UPDATE jobs SET title=?,company=?,location=?,url=?,
                    description=CASE WHEN length(?)>length(description) THEN ? ELSE description END,
                    source=?,posted_at=CASE WHEN ?!='' THEN ? ELSE posted_at END,
                    score=?,score_reasons=?,updated_at=CURRENT_TIMESTAMP WHERE fingerprint=?""",
                    (title, company, location, url, desc, desc, source, posted, posted,
                     score, json.dumps(reasons), fp))
                duplicates += 1
            else:
                c.execute("""INSERT INTO jobs(id,title,company,location,url,description,source,posted_at,fingerprint,score,score_reasons)
                    VALUES(?,?,?,?,?,?,?,?,?,?,?)""",
                    (job_id, title, company, location, url, desc, source, posted, fp, score, json.dumps(reasons)))
                c.execute("INSERT OR IGNORE INTO applications(job_id,status) VALUES(?,'not_submitted')", (job_id,))
                inserted += 1
    log_event("job_ingestion_completed", {"received": len(items), "inserted": inserted, "duplicates": duplicates})
    return {"received": len(items), "inserted": inserted, "duplicates": duplicates}


def list_jobs(limit=50, min_score=0):
    with connection() as c:
        rows = c.execute("SELECT * FROM jobs WHERE score>=? ORDER BY score DESC,posted_at DESC LIMIT ?",
                         (min_score, min(max(limit, 1), 200))).fetchall()
    return [{**dict(row), "score_reasons": json.loads(row["score_reasons"])} for row in rows]


def get_job(job_id):
    with connection() as c:
        row = c.execute("SELECT * FROM jobs WHERE id=?", (job_id,)).fetchone()
    if not row:
        return None
    item = dict(row)
    item["score_reasons"] = json.loads(item.get("score_reasons") or "[]")
    return item


def queue_approval(job_id):
    with connection() as c:
        if not c.execute("SELECT id FROM jobs WHERE id=?", (job_id,)).fetchone():
            return False
        c.execute("""INSERT INTO approvals(job_id,status) VALUES(?,'pending')
            ON CONFLICT(job_id) DO UPDATE SET status='pending',notes='',decided_at=NULL""", (job_id,))
        c.execute("UPDATE jobs SET status='awaiting_approval' WHERE id=?", (job_id,))
    log_event("approval_queued", {"job_id": job_id})
    return True


def list_approvals(status="pending"):
    with connection() as c:
        rows = c.execute("""SELECT a.*,j.title,j.company,j.location,j.url,j.score FROM approvals a JOIN jobs j ON j.id=a.job_id
            WHERE a.status=? ORDER BY a.created_at DESC""", (status,)).fetchall()
    return [dict(row) for row in rows]


def decide_approval(job_id, decision, notes=""):
    with connection() as c:
        if not c.execute("SELECT id FROM jobs WHERE id=?", (job_id,)).fetchone():
            return False
        c.execute("""INSERT INTO approvals(job_id,status,notes,decided_at) VALUES(?,?,?,CURRENT_TIMESTAMP)
            ON CONFLICT(job_id) DO UPDATE SET status=excluded.status,notes=excluded.notes,decided_at=CURRENT_TIMESTAMP""",
            (job_id, decision, notes))
        c.execute("UPDATE jobs SET status=? WHERE id=?", ("approved_to_apply" if decision == "approved" else "rejected_by_user", job_id))
    log_event("approval_decision", {"job_id": job_id, "decision": decision})
    return True


def update_application(job_id, status, notes=""):
    with connection() as c:
        job = c.execute("SELECT id FROM jobs WHERE id=?", (job_id,)).fetchone()
        if not job:
            return False
        if status == "submitted":
            approval = c.execute("SELECT status FROM approvals WHERE job_id=?", (job_id,)).fetchone()
            version = c.execute("""SELECT version_id FROM resume_versions
                WHERE job_id=? AND status IN ('approved','submitted')
                ORDER BY version_number DESC LIMIT 1""", (job_id,)).fetchone()
            if not approval or approval["status"] != "approved":
                raise PermissionError("Cannot record submission until the job is explicitly approved.")
            if not version:
                raise PermissionError("Cannot record submission until a specific resume version is approved.")
        c.execute("""INSERT INTO applications(job_id,status,notes) VALUES(?,?,?)
            ON CONFLICT(job_id) DO UPDATE SET status=excluded.status,notes=excluded.notes,updated_at=CURRENT_TIMESTAMP""",
            (job_id, status, notes))
        c.execute("UPDATE jobs SET status=? WHERE id=?", (status, job_id))
    log_event("application_status_updated", {"job_id": job_id, "status": status})
    return True
