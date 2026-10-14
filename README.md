# Autonomous Job Agent

Local one-click job-search workflow built with FastAPI, Streamlit, LangChain/LangGraph, SQLite, Adzuna, and a Groq-compatible LLM endpoint. This is a standalone project and does **not** modify `job-search-pro`.

## Daily use: one run button

1. Start the FastAPI backend in PowerShell:
   ```powershell
   .\.venv\Scripts\python.exe -m uvicorn app.main:app --reload --host 127.0.0.1 --port 8000
   ```
2. In a second terminal, start the dashboard:
   ```powershell
   .\.venv\Scripts\python.exe -m streamlit run dashboard.py
   ```
3. Open the Streamlit URL (usually `http://localhost:8501`), select Adzuna, enter a job query/location, and click **Run complete workflow**.

The LangGraph workflow runs these steps in order:

- Search the configured source and store job records.
- Deduplicate listings and compute deterministic baseline scores.
- Use the configured LLM to rank a shortlist; deterministic scores remain available when the LLM is rate-limited or unavailable.
- Create fact-checked resume drafts for up to `AUTO_TAILOR_TOP_N` new, high-scoring real listings (default: 2; minimum baseline score: 65).
- Queue those jobs and exact resume versions for your review.
- Optionally email a digest when the button's email checkbox is enabled and SMTP is configured.
- Update local JSON snapshots and save run status, warnings, errors, jobs, resume metadata, applications, and events.

The default LLM call budget is intentionally conservative: one LLM ranking call plus up to two resume drafts per run, within the existing local limiter of three new requests/minute. Tune `WORKFLOW_LLM_RANK_TOP_N`, `AUTO_TAILOR_TOP_N`, and `LLM_MAX_REQUESTS_PER_MINUTE` only to match your provider's actual quota. Provider-side rate limits still apply. If you already have a version for a job, the agent preserves it and doesn't create a duplicate automatically.

## Batch approval and browser application automation

The agent can now queue a **batch of exact job/resume pairs** for application automation. In the dashboard's **Batch applications** tab, review the summaries and selected resume versions, then approve the selected applications together. This single batch action approves the selected job and that exact resume version and places each pair in the automation queue.

The browser adapter is an optional, conservative first implementation. It uses a visible Chromium session to open each approved job, fill recognizable fields from your verified local profile, upload the approved PDF resume when the upload field is identifiable, and click a clearly labeled submission control only when required fields have been handled. It checks for a confirmation signal before marking an application submitted. Portal markup varies, so this is not a promise of universal compatibility; unsupported or ambiguous forms pause for intervention instead of guessing.

### Enable browser automation on Windows

Install the optional browser dependencies in your existing virtual environment:

```powershell
.\.venv\Scripts\python.exe -m pip install -r requirements-browser.txt
.\.venv\Scripts\python.exe -m playwright install chromium
```

Then restart the FastAPI API and Streamlit dashboard. In **Batch applications**:

1. Fill and save your verified application profile. It is stored locally beside `data/agent.db` in `application_profile.json`, not committed to Git, and not automatically sent to the LLM.
2. Review the pending applications and select the exact resume version for each.
3. Use **Approve selected applications and queue automation** to authorize the selected batch.
4. Click **Run approved applications in visible browser**. Chromium opens visibly and uses a persistent local browser profile for supported login sessions.
5. If CAPTCHA or authentication appears, complete the legitimate challenge in the opened browser. The agent waits up to five minutes, then records an intervention if the challenge remains. It never bypasses CAPTCHA or invents answers.
6. If a required question is unknown, a consent checkbox needs your decision, an upload fails, or the portal is unsupported, the application pauses. Fix the profile or checkpoint and use **Retry this application**.
7. A job is marked submitted only when a recognizable confirmation signal is observed. If the result is ambiguous, verify the employer portal before retrying to avoid duplicate applications.

The browser session directory contains login cookies and other session state. Protect the local machine and never commit the browser profile, application profile, resumes, or application history. Use this automation only on portals where your use of browser automation is permitted. It does not bypass access controls or CAPTCHA.

API endpoints for this workflow include `GET/PUT /applications/profile`, `POST /applications/automation/queue`, `GET /applications/automation/queue`, `POST /applications/automation/run-approved`, and the intervention/retry/submission tracking endpoints.

The generator selects and reorders only verbatim passages from your master resume. Its audit report lists job requirements not evidenced in the source resume. This cannot prove claims already in the source resume are true, so review the selected resume version before batch approval.

## Lightweight persistent job-search memory

The **Search memory** dashboard tab stores your preferred roles, skills, locations, terms to avoid, and optional notes in SQLite. These saved preferences apply small, explainable adjustments to deterministic job scores during discovery; the existing baseline scoring and LLM fallback remain in place. Use the Interested / Not interested / Irrelevant controls on each ranked job to keep one feedback record per job, including an optional reason. Feedback is currently stored for review and future tuning; it is not used to train a model.

The database initialization also marks workflow runs older than 30 minutes as `interrupted` if they were still marked `running`. This is conservative recovery for a process that exited unexpectedly; recent runs are left alone to avoid interfering with a live workflow. SQLite remains local and no extra database, vector store, browser process, or local model is required.

Memory API endpoints:

- `GET /memory/preferences` and `PUT /memory/preferences` — read/save search preferences
- `GET /memory/summary` and `GET /memory/feedback` — inspect saved memory
- `POST /jobs/{job_id}/feedback` — save `interested`, `not_interested`, `irrelevant`, or `applied` feedback with an optional reason
- `DELETE /jobs/{job_id}/feedback` — remove a mistaken feedback record; the Ranked jobs tab provides an **Undo feedback** action

## Local storage and JSON records

SQLite is the transactional source of truth at `data/agent.db`. After each workflow run and relevant API mutation, portable JSON snapshots are written under `data/exports/`:

- `jobs.json` — listing details, score, rationale, status, dates
- `applications.json` — application tracking status and notes
- `approvals.json` — job approval decisions
- `resume_versions.json` — each resume version, selected source passages, provenance hashes, statuses, and artifact paths
- `events.json` — operations, warnings, and errors
- `ranking_queue.json` — LLM ranking queue and retry states
- `agent_runs.json` and `latest_run.json` — run-by-run workflow outcomes
- `manifest.json` — export time and record counts

Actual master resume files, generated PDFs/DOCX, and per-version tailoring audit JSON files stay under `data/resumes/`. Both `data/` and `.env` are Git-ignored; never commit your personal resume, application history, or credentials. Back up `data/` securely if you need to preserve history.

Inspect exports from the dashboard's Monitoring & JSON tab or use `GET /exports/status` and `POST /exports/refresh`.

## Configuration

Install dependencies after pulling an update:

```powershell
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
```

Create `.env` only if you don't already have one:
```powershell
Copy-Item .env.example .env
```

Set these values in your private local `.env` file (never paste them into chat or commit them):

- `ADZUNA_APP_ID`, `ADZUNA_APP_KEY`: Adzuna credentials
- `GROQ_API_KEY`: LLM API key
- `GROQ_API_BASE`, `GROQ_MODEL`: configured OpenAI-compatible endpoint and model
- `AUTO_TAILOR_ENABLED=true`, `AUTO_TAILOR_TOP_N=2`, `AUTO_TAILOR_MIN_SCORE=65`: automatic draft limits
- `WORKFLOW_LLM_RANK_TOP_N=1`: LLM ranking calls per workflow run
- `JSON_EXPORTS_ENABLED=true`: local JSON snapshots
- `SMTP_HOST`, `SMTP_PORT`, `SMTP_USERNAME`, `SMTP_PASSWORD`, `EMAIL_FROM`, `EMAIL_TO`: optional email digest configuration

SMTP is optional. If email is not configured, leave the email checkbox off; the workflow reports why an email wasn't sent. Your resume content is sent to the configured LLM provider for passage selection, so use only a provider you trust and are permitted to share your resume with.

Background scheduling stays disabled by default (`SCHEDULER_ENABLED=false`) to match the manual-morning routine. Starting the API doesn't start discovery; click **Run complete workflow** once each morning. If you later enable scheduling, `DISCOVERY_INTERVAL_HOURS` controls discovery cadence and `DAILY_REPORT_HOUR` controls the report time (Asia/Kolkata).

## API and troubleshooting

- `GET /health` — service and active configuration
- `POST /agent/run` — full workflow
- `GET /agent/runs` — previous workflow runs
- `GET /jobs`, `GET /approvals?status=pending` — jobs and approval queue
- `GET /resumes/{job_id}` — resume version history
- `GET /metrics`, `GET /events` — monitoring
- `GET/PUT /memory/preferences`, `GET /memory/summary`, `GET /memory/feedback`, `POST /jobs/{job_id}/feedback`, `DELETE /jobs/{job_id}/feedback` — persistent job-search memory and feedback correction
- `GET /exports/status`, `POST /exports/refresh` — JSON export status and refresh

Run tests:

```powershell
.\.venv\Scripts\python.exe -m pytest -q
```
