from app.config import settings
from app.db import log_event
from app.services.jobs import ingest_jobs
from app.services.llm_ranking import rank_shortlist
from app.sources.demo import search_demo_jobs
from app.sources.adzuna import search_adzuna_jobs
from app.notifications.email_report import send_report

def run_discovery(source="adzuna", what=None, where=None, send_email=False):
    query, location = what or settings.default_job_query, where or settings.default_job_location
    log_event("discovery_run_started",{"source":source,"query":query,"location":location})
    try:
        if source == "demo":
            found = search_demo_jobs(query, location)
        elif source == "adzuna":
            found = search_adzuna_jobs(query, location, 50)
        else:
            raise ValueError("source must be demo or adzuna")
        ingestion = ingest_jobs(found)
        ranking = rank_shortlist()
        email = send_report() if send_email else {"sent":False,"reason":"Not requested"}
        result = {"source":source,"ingestion":ingestion,"ranking":ranking,"email":email}
        log_event("discovery_run_completed",result)
        return result
    except Exception as exc:
        log_event("discovery_run_failed",{"source":source,"error":str(exc)[:250]},"ERROR")
        raise
