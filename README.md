# Autonomous Job Agent

Independent local job-search project using your configured Groq-compatible endpoint and model. It does **not** modify `job-search-pro`.

## Run locally (Windows PowerShell)

```powershell
py -3.11 -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -r requirements.txt
Copy-Item .env.example .env
uvicorn app.main:app --reload --host 127.0.0.1 --port 8000
```

Open `http://127.0.0.1:8000/docs`. The optional dashboard runs in another terminal using `streamlit run dashboard.py`.

## Job discovery

For demo data (synthetic records, not actual vacancies):

```powershell
curl -X POST "http://127.0.0.1:8000/jobs/discover?source=demo"
curl "http://127.0.0.1:8000/jobs?limit=20"
```

For real Adzuna results, set `ADZUNA_APP_ID` and `ADZUNA_APP_KEY` in `.env`, then call `POST /jobs/discover?source=adzuna&what=generative%20ai%20engineer&where=India`.

## Resume tailoring and version history

Resume files are stored locally in `data/resumes`, which is excluded from Git. Do not commit personal resume documents or API keys.

1. Start the API and open `http://127.0.0.1:8000/docs`.
2. Upload your master resume using `POST /resumes/master` (PDF, DOCX, TXT, or MD; up to 15 MB). The original bytes are retained locally under a content-hash filename, and text is extracted for the tailoring workflow.
3. Select a real role from `GET /jobs` with a substantive job description.
4. Call `POST /resumes/tailor/{job_id}`. The generated artifacts include a unique version ID and number, DOCX and PDF resumes, an audit JSON file, source/JD hashes, change notes, and unsupported JD requirements.
5. Review the version before approving:
   - `GET /resumes/{job_id}` — version history for one job
   - `GET /resumes/versions/{version_id}` — version metadata/report fields
   - `GET /resumes/versions/{version_id}/download?file_type=pdf`
   - change `file_type` to `docx` for an editable resume or `report` for the JSON audit report
6. Approve the exact resume version using `POST /resumes/versions/{version_id}/approve`.
7. Approve the **job itself** using `POST /approvals/{job_id}/decision` with `{"decision":"approved","notes":"Reviewed job and resume version"}`. These are two separate approvals; approving only the resume is not enough.
8. Submit the application manually on the employer website with that exact approved resume.
9. Record the completed submission through `POST /resumes/versions/{version_id}/record-submission` with JSON such as `{"notes":"Submitted on employer website"}`. If the job is not approved, the API intentionally returns HTTP 409 Conflict. The dashboard displays the specific reason and disables the record button until the job status is approved.

Each tailoring run creates a new version; it does not overwrite prior versions. The model is instructed to select and reorder **verbatim passages** from the master resume only. Code checks every resume passage against the source and rejects the run if the model introduces new wording. The audit report lists unsupported job requirements separately so the resume does not claim them.

Privacy note: the source resume text and selected job description are sent to the LLM endpoint configured in your local `.env` (currently your Groq-compatible endpoint) for passage selection. Resume files and generated versions remain in your local `data/resumes` folder and are excluded from Git, but the LLM provider processes the text sent to it. Do not use this feature for documents that you are not permitted to share with that provider.

This safeguard prevents the generator from adding new wording, but it cannot prove that claims already present in the original resume are true, nor fully verify whether the selected passages are contextually appropriate. Always inspect the generated PDF/DOCX before approving it.

The project does **not** automatically submit employer applications or bypass CAPTCHAs/access controls. Submission tracking records a manual submission you have completed after both the job and exact resume version have been approved.

## Operations

- Scheduler remains disabled by default. Enable it only after discovery and SMTP are verified.
- Use `/health`, `/metrics`, and `/events` to inspect the service and activity.
- Resume documents and reports are local personal data; keep them out of Git.

## Tests

```powershell
python -m pytest -q
```
