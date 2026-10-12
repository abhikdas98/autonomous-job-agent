# Autonomous Job Agent

Independent starter project using your existing Groq endpoint/model (`https://api.groq.com/openai/v1`, `openai/gpt-oss-120b`). It does not modify `job-search-pro`.

## Run locally (Windows PowerShell)
```powershell
py -3.11 -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
Copy-Item .env.example .env
uvicorn app.main:app --reload
```
Open `http://127.0.0.1:8000/docs`. Start the monitoring UI in a second terminal with `streamlit run dashboard.py`.

## Test without API credentials
```powershell
curl -X POST "http://127.0.0.1:8000/jobs/discover?source=demo"
curl "http://127.0.0.1:8000/jobs?limit=20"
```
Demo jobs are synthetic examples, not real vacancies.

## Real jobs
Set `ADZUNA_APP_ID` and `ADZUNA_APP_KEY` in `.env`, then call:
`POST /jobs/discover?source=adzuna&what=generative%20ai%20engineer&where=India`

Use only authorized job sources and respect their terms. No CAPTCHA/access-control bypass.

## Human approval
`POST /approvals/{job_id}` queues a job for review. `GET /approvals` lists pending jobs. `POST /approvals/{job_id}/decision` records approval/rejection. This starter does not submit applications to third-party websites. After you personally submit, record it through `POST /applications/{job_id}/status`.

## Reliability and rate limits
- Local defaults: 3 LLM requests/minute and 60/day.
- Persistent SQLite response cache; cache hits don't use the request budget.
- Bounded retries, exponential backoff with jitter, timeouts, and long `Retry-After` deferral.
- Deterministic ranking continues if the LLM is unavailable.
- Local budget is single-instance protection; multi-replica deployments need a shared limiter.
- Upstream provider limits can still change or return 429s; this app limits its own calls but cannot guarantee upstream availability.

## Email and monitoring
Configure SMTP variables in `.env`, then `POST /reports/email`. Inspect `/health`, `/metrics`, and `/events`. This is a runnable foundation, not a claim of production certification; test each integration before relying on scheduled runs.

## Autonomous workflow
- `POST /agent/run?source=demo` runs ingestion, deduplication, deterministic scoring, optional LLM ranking, and optional email reporting.
- `POST /agent/run?source=adzuna&send_email=true` uses Adzuna when credentials are configured.
- Set `SCHEDULER_ENABLED=true` only after testing your job source and SMTP. Discovery then runs every `DISCOVERY_INTERVAL_HOURS`; email digest runs daily at `DAILY_REPORT_HOUR` India time.
- Scheduler is disabled by default to avoid unexpected requests/emails.
- `streamlit run dashboard.py` opens a local dashboard.
- LLM ranking is limited to a shortlist and is cached. If Groq is rate-limited, unavailable, or returns invalid JSON, the existing deterministic ranking remains.
