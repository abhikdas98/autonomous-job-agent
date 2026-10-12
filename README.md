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

- Search the configured job source and save records.
- Deduplicate listings and compute deterministic baseline scores.
- Use the configured LLM to rank the shortlist; deterministic scores remain available when the LLM is rate-limited or unavailable.
- Automatically create source-verified resume drafts for up to `AUTO_TAILOR_TOP_N` new, high-scoring real listings (default: 3; score threshold default: 65).
- Queue those jobs and exact resume versions for review.
- Optionally email a digest when the button's email checkbox is enabled and SMTP is configured.
- Update JSON snapshots and store run status, warnings, errors, job records, resume metadata, application status, and events locally.

Repeated discovery does not create another resume version for a job that already has a version. Prior resume versions remain unchanged. The API's `/agent/runs` and dashboard's Monitoring tab show prior workflow runs and failures.

## Application submission boundary

The agent automates **preparation and tracking**, but it does not automatically submit employer forms. Employer sites have different application forms, sign-in requirements, consent prompts, and anti-bot protections. A draft is not evidence that an application has been submitted.

Review the job description and exact generated PDF/DOCX, approve the job and resume version separately, then submit on the employer's official site. Only after submission should you click **Record manual submission**. That records the application status and approved resume version locally; it does not submit anything externally.

The generator selects and reorders only verbatim passages from your master resume. Its audit report lists job requirements not evidenced in the source resume. This cannot prove claims already in the source resume are true, so always review each draft.

## Local storage and JSON records

SQLite is the transactional source of truth at `data/agent.db`. After each complete workflow run and relevant API change, the agent refreshes portable JSON snapshots under `data/exports/`:

- `jobs.json` — listing details, score, rationale, status, and dates
- `applications.json` — application tracking status and notes
- `approvals.json` — job approval decisions
- `resume_versions.json` — resume versions, source/JD hashes, selected resume text, status, and artifact paths
- `events.json` — recent operations, warnings, and errors
- `ranking_queue.json` — LLM ranking queue and retry states
- `agent_runs.json` and `latest_run.json` — run-by-run workflow outcomes
- `manifest.json` — export time and per-file record counts

Actual master resume files, generated PDFs/DOCX, and per-version tailoring audit JSON files stay under `data/resumes/`. Both `data/` and `.env` are Git-ignored; do not commit personal resume files, generated application data, or credentials. Back up the local `data/` directory securely if you need to preserve history.

You can inspect exports from the dashboard's Monitoring & JSON tab or use `GET /exports/status` and `POST /exports/refresh`.

## Configuration

Install packages after pulling an update:

```powershell
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
```

Create `.env` if you have not already:
```powershell
Copy-Item .env.example .env
```

Set these values in `.env` (never paste them into chat or commit them):

- `ADZUNA_APP_ID`, `ADZUNA_APP_KEY`: Adzuna API credentials
- `GROQ_API_KEY`: LLM API key
- `GROQ_API_BASE`, `GROQ_MODEL`: configured OpenAI-compatible endpoint and model
- `AUTO_TAILOR_ENABLED=true`, `AUTO_TAILOR_TOP_N=3`, `AUTO_TAILOR_MIN_SCORE=65`: automatic drafting limits
- `JSON_EXPORTS_ENABLED=true`: local JSON snapshot export
- `SMTP_HOST`, `SMTP_PORT`, `SMTP_USERNAME`, `SMTP_PASSWORD`, `EMAIL_FROM`, `EMAIL_TO`: optional email digest configuration

SMTP is optional for the discovery and resume workflow. If email is not configured, run without selecting the email checkbox; the workflow will report why an email was not sent.

Background scheduling stays disabled by default (`SCHEDULER_ENABLED=false`) to match the requested manual morning routine. With it disabled, starting the API does not trigger discovery; you manually click **Run complete workflow** once each morning. If you later enable the scheduler, discovery intervals and daily email time are configured by `DISCOVERY_INTERVAL_HOURS` and `DAILY_REPORT_HOUR`.

## API and troubleshooting

- `GET /health` — health and active configuration
- `POST /agent/run` — run the complete workflow
- `GET /agent/runs` — previous run history
- `GET /jobs`, `GET /approvals?status=pending` — tracked jobs and pending review
- `GET /resumes/{job_id}` — resume versions for a job
- `GET /metrics`, `GET /events` — monitoring
- `GET /exports/status`, `POST /exports/refresh` — JSON export status and refresh

Run tests:

```powershell
.\.venv\Scripts\python.exe -m pytest -q
```
