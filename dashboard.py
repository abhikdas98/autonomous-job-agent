import os
import requests
import streamlit as st

st.set_page_config(page_title="Autonomous Job Agent", layout="wide")
api = os.getenv("AGENT_API_BASE", "http://127.0.0.1:8000").rstrip("/")
st.title("Autonomous Job Agent")
st.caption("One-click job discovery → ranking → factual resume drafts → review queue → local JSON records")
st.info(
    "The Run button automates discovery, filtering, ranking, resume-draft generation, tracking, and JSON export. "
    "Employer applications are not submitted automatically: review the job and exact resume version, then submit "
    "on the employer's site. This avoids sending personal data or applications externally without your review."
)


def get(path, params=None, timeout=25):
    try:
        response = requests.get(api + path, params=params, timeout=timeout)
        response.raise_for_status()
        return response.json()
    except requests.RequestException as exc:
        st.error(f"API request failed for {path}: {exc}")
        return None


def post(path, params=None, body=None, timeout=300):
    try:
        response = requests.post(api + path, params=params, json=body, timeout=timeout)
        response.raise_for_status()
        return response.json()
    except requests.HTTPError as exc:
        response = exc.response
        detail = None
        if response is not None:
            try:
                payload = response.json()
                detail = payload.get("detail") if isinstance(payload, dict) else None
            except ValueError:
                detail = None
        message = detail or (response.text[:500] if response is not None else str(exc))
        status_code = response.status_code if response is not None else "unknown"
        st.error(f"Request failed ({status_code}): {message}")
        return None
    except requests.RequestException as exc:
        st.error(f"Could not reach the API: {exc}")
        return None


def display_run(result):
    st.subheader("Latest run result")
    status = result.get("status", "unknown")
    if status == "completed":
        st.success("Workflow completed.")
    elif status == "completed_with_warnings":
        st.warning("Workflow finished with warnings. Review the items below.")
    else:
        st.warning(f"Workflow status: {status}")
    ingestion = result.get("ingestion", {})
    a, b, c, d = st.columns(4)
    a.metric("Fetched from source", result.get("found_count", ingestion.get("received", 0)))
    b.metric("New jobs", ingestion.get("inserted", 0))
    c.metric("Duplicates refreshed", ingestion.get("duplicates", 0))
    tailoring = result.get("tailoring", {})
    d.metric("Resume drafts created", tailoring.get("created_count", 0))
    if result.get("ranking"):
        st.markdown("**Ranking**")
        st.json(result["ranking"])
    created = tailoring.get("created", [])
    if created:
        st.markdown("**New resume drafts awaiting your review**")
        for item in created:
            with st.expander(f"{item['score']:.0f}/100 · {item['title']} — {item['company']} · v{item['version_number']:03d}"):
                st.write("State:", item["status"])
                st.write("These drafts are queued for job/resume review, not submitted.")
                if item.get("url"):
                    st.markdown(f"[Open job posting]({item['url']})")
                else:
                    st.caption(f"Job ID: {item.get('job_id')}")
                for file_type, label in (("pdf", "Download PDF"), ("docx", "Download DOCX"), ("report", "Audit JSON")):
                    st.markdown(
                        f"[{label}]({api}/resumes/versions/{item['version_id']}/download?file_type={file_type})"
                    )
    if tailoring.get("skipped"):
        with st.expander(f"Tailoring skipped for {len(tailoring['skipped'])} item(s)"):
            for item in tailoring["skipped"]:
                st.write("• " + item)
    if result.get("warnings"):
        st.markdown("**Warnings / things to check**")
        for warning in result["warnings"]:
            st.warning(warning)
    exports = result.get("json_exports", {})
    if exports.get("ok"):
        st.success(f"Local JSON snapshots updated in {exports.get('directory', 'data/exports')}")
        st.caption("SQLite is the transactional source of truth; JSON files are portable snapshots for tracking and backups.")
    if result.get("submission_note"):
        st.caption(result["submission_note"])
    with st.expander("Complete workflow JSON"):
        st.json(result)


def get_job_url(job_id):
    if not job_id:
        return None
    # Use the jobs cache populated by the caller if available.
    cache = st.session_state.get("job_url_cache", {})
    return cache.get(job_id)


with st.sidebar:
    st.subheader("Daily run")
    source = st.selectbox("Job source", ["adzuna", "demo"], index=0)
    query = st.text_input(
        "Role query",
        "agentic AI engineer OR generative AI engineer OR GenAI developer",
    )
    location = st.text_input("Location", "Hyderabad")
    email = st.checkbox("Send email digest after run", value=False)
    st.caption("Automatic resume drafting processes up to 3 new high-scoring jobs per run by default.")
    if st.button("Run complete workflow", type="primary", use_container_width=True):
        with st.spinner("Fetching jobs, filtering duplicates, ranking matches, preparing resume drafts, and exporting JSON…"):
            result = post(
                "/agent/run",
                {"source": source, "what": query, "where": location, "send_email": email},
                timeout=600,
            )
        if result:
            st.session_state["last_agent_run_result"] = result
            st.rerun()
    left, right = st.columns(2)
    if left.button("Refresh data", use_container_width=True):
        st.rerun()
    if right.button("Send digest", use_container_width=True):
        report = post("/reports/email", timeout=60)
        if report:
            st.json(report)

health = get("/health") or {}
metrics = get("/metrics") or {}
a, b, c, d, e = st.columns(5)
a.metric("Jobs tracked", metrics.get("jobs_total", 0))
b.metric("Pending review", metrics.get("approvals_pending", 0))
c.metric("Resume versions", metrics.get("resume_versions_total", 0))
d.metric("Submitted (tracked)", metrics.get("applications_submitted", 0))
e.metric("Scheduler", "Enabled" if health.get("scheduler_enabled") else "Manual")

with st.expander("Workflow configuration", expanded=False):
    st.json({
        "API status": health.get("status", "unknown"),
        "LLM enabled": health.get("llm_enabled"),
        "model": health.get("model"),
        "auto-tailoring enabled": health.get("auto_tailor_enabled"),
        "maximum new drafts per run": health.get("auto_tailor_top_n"),
        "minimum deterministic score": health.get("auto_tailor_min_score"),
        "JSON exports enabled": health.get("json_exports_enabled"),
        "background scheduler enabled": health.get("scheduler_enabled"),
    })

last_result = st.session_state.get("last_agent_run_result")
if last_result:
    display_run(last_result)

jobs_tab, approvals_tab, resumes_tab, monitoring_tab, memory_tab = st.tabs(
    ["Ranked jobs", "Approval queue", "Resume drafts", "Monitoring & JSON", "Search memory"]
)

with jobs_tab:
    minimum = st.slider("Minimum score", 0, 100, 0)
    jobs = get("/jobs", {"limit": 100, "min_score": minimum}) or []
    st.session_state["job_url_cache"] = {item["id"]: item.get("url") for item in jobs}
    if not jobs:
        st.info("No jobs yet. Choose Adzuna and click Run complete workflow.")
    for job in jobs:
        with st.expander(f"{job['score']:.0f}/100 · {job['title']} — {job['company']}"):
            st.caption(f"{job.get('location', 'Location not stated')} · {job.get('source', 'unknown')} · {job.get('status', 'discovered')}")
            st.write(job.get("score_reasons", []))
            st.write(job.get("description", ""))
            st.markdown(f"[Open job posting]({job['url']})")
            feedback_reason = st.text_input(
                "Feedback note (optional)", key="feedback_reason_" + job["id"],
                placeholder="e.g. too senior, weak RAG match, good agentic role",
            )
            feedback_cols = st.columns(3)
            for col, feedback_value, feedback_label in (
                (feedback_cols[0], "interested", "Interested"),
                (feedback_cols[1], "not_interested", "Not interested"),
                (feedback_cols[2], "irrelevant", "Irrelevant"),
            ):
                if col.button(feedback_label, key=f"feedback_{feedback_value}_{job['id']}"):
                    saved_feedback = post(
                        f"/jobs/{job['id']}/feedback",
                        body={"feedback": feedback_value, "reason": feedback_reason},
                        timeout=20,
                    )
                    if saved_feedback:
                        st.success("Feedback saved to local memory.")
                        st.rerun()
            if st.button("Queue job for review", key="queue_" + job["id"]):
                result = post("/approvals/" + job["id"])
                if result:
                    st.success(result["message"])
                    st.rerun()

with approvals_tab:
    pending = get("/approvals", {"status": "pending"}) or []
    if not pending:
        st.info("No pending job approvals. New resume drafts should appear here after a successful workflow run.")
    for item in pending:
        with st.expander(f"{item['score']:.0f}/100 · {item['title']} — {item['company']}"):
            st.markdown(f"[Open job posting]({item['url']})")
            st.write(item.get("location", ""))
            notes = st.text_input("Review notes", key="notes_" + item["job_id"])
            left, right = st.columns(2)
            if left.button("Approve job", key="yes_" + item["job_id"]):
                approved = post(f"/approvals/{item['job_id']}/decision", body={"decision": "approved", "notes": notes})
                if approved:
                    st.success("Job approved. Review and approve the exact resume version separately.")
                    st.rerun()
            if right.button("Reject job", key="no_" + item["job_id"]):
                rejected = post(f"/approvals/{item['job_id']}/decision", body={"decision": "rejected", "notes": notes})
                if rejected:
                    st.success("Job rejected.")
                    st.rerun()

with resumes_tab:
    st.subheader("Master resume")
    master = get("/resumes/master") or {"configured": False}
    if master.get("configured"):
        st.success(f"Master resume configured · {master.get('characters_extracted', 0):,} extracted characters")
    else:
        st.info("Upload your master resume once. The daily workflow will reuse it for new suitable jobs.")
    uploaded = st.file_uploader("Upload master resume", type=["pdf", "docx", "txt", "md"], key="master_resume_upload")
    if st.button("Save master resume", disabled=uploaded is None):
        try:
            response = requests.post(
                api + "/resumes/master",
                files={"file": (uploaded.name, uploaded.getvalue(), uploaded.type or "application/octet-stream")},
                timeout=90,
            )
            response.raise_for_status()
            st.success("Master resume saved to the local data/resumes folder.")
            st.json(response.json())
            st.rerun()
        except requests.RequestException as exc:
            st.error(f"Master resume upload failed: {exc}")

    st.divider()
    st.subheader("Resume version history")
    resume_jobs = get("/jobs", {"limit": 200}) or []
    if not resume_jobs:
        st.info("Discover jobs first.")
    else:
        jobs_by_id = {item["id"]: item for item in resume_jobs}
        st.session_state["job_url_cache"] = {item["id"]: item.get("url") for item in resume_jobs}
        selected_job_id = st.selectbox(
            "Choose job",
            options=list(jobs_by_id),
            format_func=lambda job_id: (
                f"{jobs_by_id[job_id]['title']} — {jobs_by_id[job_id]['company']} ({jobs_by_id[job_id]['score']:.0f}/100)"
            ),
        )
        selected_job = jobs_by_id[selected_job_id]
        st.markdown(f"[Open job posting]({selected_job['url']})")
        history = get(f"/resumes/{selected_job_id}") or {"versions": []}
        versions = history.get("versions", [])
        if not versions:
            st.info("The workflow creates drafts automatically for eligible new jobs. You can also generate a new version manually.")
            if st.button("Generate resume version now", key="manual_tailor"):
                with st.spinner("Validating source passages and creating PDF/DOCX…"):
                    tailored = post(f"/resumes/tailor/{selected_job_id}", timeout=180)
                if tailored:
                    st.success(f"Created version v{tailored['version_number']:03d}.")
                    st.json(tailored)
                    st.rerun()
        for version in versions:
            version_id = version["version_id"]
            with st.expander(f"v{version['version_number']:03d} · {version['status']} · {version.get('created_at', '')}"):
                st.write("Source SHA-256:", version.get("source_sha256", ""))
                st.write("Job description SHA-256:", version.get("job_description_sha256", ""))
                if version.get("change_notes"):
                    st.markdown("**Tailoring notes**")
                    st.write(version["change_notes"])
                if version.get("unsupported_requirements"):
                    st.warning("Requirements not evidenced in your master resume:\n" + "\n".join("• " + value for value in version["unsupported_requirements"]))
                if version.get("factuality_flags"):
                    st.caption("Factuality safeguards:\n" + "\n".join("• " + value for value in version["factuality_flags"]))
                cols = st.columns(3)
                cols[0].markdown(f"[Download PDF]({api}/resumes/versions/{version_id}/download?file_type=pdf)")
                cols[1].markdown(f"[Download DOCX]({api}/resumes/versions/{version_id}/download?file_type=docx)")
                cols[2].markdown(f"[Audit JSON]({api}/resumes/versions/{version_id}/download?file_type=report)")
                if version["status"] == "needs_review":
                    if st.button("Approve this exact resume version", key="approve_resume_" + version_id):
                        approved = post(f"/resumes/versions/{version_id}/approve")
                        if approved:
                            st.success("Resume version approved. The job itself must also be approved.")
                            st.rerun()
                elif version["status"] == "approved":
                    job_is_approved = selected_job.get("status") == "approved_to_apply"
                    if job_is_approved:
                        st.info("Both job and resume version are approved. After submitting manually on the employer's website, record it below.")
                    else:
                        st.warning("Resume approved, but the job is not. Approve the job in the Approval queue first.")
                    notes = st.text_input("Submission notes (optional)", key="submission_notes_" + version_id)
                    if st.button(
                        "Record manual submission",
                        key="record_submission_" + version_id,
                        disabled=not job_is_approved,
                    ):
                        st.warning("Use this only after you have actually submitted the application externally.")
                        recorded = post(
                            f"/resumes/versions/{version_id}/record-submission",
                            body={"notes": notes or "Submitted manually on employer website"},
                        )
                        if recorded:
                            st.success("Manual submission recorded in local tracking.")
                            st.rerun()


    if selected_job.get("status") in {"submitted", "interview", "rejected", "offer", "withdrawn"}:
        st.divider()
        st.subheader("Application follow-up")
        st.caption(f"Currently tracked as: {selected_job.get('status')}")
        follow_status = st.selectbox(
            "Update status after an employer response",
            ["Select status…", "interview", "offer", "rejected", "withdrawn"],
            key="follow_status_" + selected_job_id,
        )
        follow_notes = st.text_input("Follow-up notes", key="follow_notes_" + selected_job_id)
        if st.button("Save application update", key="save_followup_" + selected_job_id):
            if follow_status == "Select status…":
                st.warning("Choose a status first.")
            else:
                saved = post(
                    f"/applications/{selected_job_id}/status",
                    body={"status": follow_status, "notes": follow_notes},
                )
                if saved:
                    st.success("Application status saved to local tracking.")
                    st.rerun()

with monitoring_tab:
    st.subheader("Local JSON snapshots")
    export_status = get("/exports/status") or {}
    if export_status.get("ok"):
        st.success(f"Export directory: {export_status.get('directory', 'data/exports')}")
        st.caption(f"Last export: {export_status.get('exported_at', 'not recorded')}")
        st.write("Files:", export_status.get("files", []))
        st.json(export_status.get("record_counts", {}))
    else:
        st.warning(export_status.get("error") or "No JSON export snapshot found yet.")
    if st.button("Refresh JSON snapshots", type="secondary"):
        export = post("/exports/refresh", timeout=60)
        if export:
            st.success("Local JSON snapshots refreshed.")
            st.json(export)
            st.rerun()

    st.divider()
    st.subheader("Agent run history")
    runs = get("/agent/runs", {"limit": 30}) or []
    if runs:
        for item in runs:
            with st.expander(f"{item.get('status', 'unknown')} · {item.get('started_at', '')} · {item.get('run_id', '')[:8]}"):
                st.write("Source:", item.get("source"))
                st.write("Query:", item.get("query"))
                st.write("Location:", item.get("location"))
                if item.get("error"):
                    st.error(item["error"])
                if item.get("summary"):
                    st.json(item["summary"])
    else:
        st.info("No workflow runs recorded yet.")

    st.divider()
    st.subheader("Recent system events")
    events = get("/events", {"limit": 100}) or []
    if events:
        for event in events[:30]:
            with st.expander(f"{event.get('level', 'INFO')} · {event.get('event', '')} · {event.get('created_at', '')}"):
                st.json(event.get("details", {}))
    else:
        st.info("No events recorded yet.")


with memory_tab:
    st.subheader("Persistent job-search preferences")
    st.caption("Saved locally in SQLite. These settings are stored for future workflow improvements; they do not trigger model training or get sent to an LLM automatically.")
    preferences = get("/memory/preferences") or {}
    with st.form("job_search_memory_form"):
        roles_text = st.text_area(
            "Preferred roles (one per line)",
            "\\n".join(preferences.get("preferred_roles", [])),
        )
        skills_text = st.text_area(
            "Preferred skills (one per line)",
            "\\n".join(preferences.get("preferred_skills", [])),
        )
        locations_text = st.text_area(
            "Preferred locations (one per line)",
            "\\n".join(preferences.get("preferred_locations", [])),
        )
        excluded_text = st.text_area(
            "Terms to avoid (one per line)",
            "\\n".join(preferences.get("excluded_terms", [])),
        )
        notes_text = st.text_area("Additional preferences", preferences.get("notes", ""))
        save_memory = st.form_submit_button("Save preferences", type="primary")
    if save_memory:
        payload = {
            "preferred_roles": [x.strip() for x in roles_text.splitlines() if x.strip()],
            "preferred_skills": [x.strip() for x in skills_text.splitlines() if x.strip()],
            "preferred_locations": [x.strip() for x in locations_text.splitlines() if x.strip()],
            "excluded_terms": [x.strip() for x in excluded_text.splitlines() if x.strip()],
            "notes": notes_text,
        }
        saved = None
        try:
            response = requests.put(api + "/memory/preferences", json=payload, timeout=25)
            response.raise_for_status()
            saved = response.json()
        except requests.RequestException as exc:
            st.error(f"Could not save preferences: {exc}")
        if saved:
            st.success("Preferences saved locally.")
            st.rerun()

    st.divider()
    st.subheader("Feedback memory")
    summary = get("/memory/summary") or {}
    counts = summary.get("feedback_counts", {})
    a, b, c, d = st.columns(4)
    a.metric("Feedback records", summary.get("feedback_total", 0))
    b.metric("Interested", counts.get("interested", 0))
    c.metric("Not interested", counts.get("not_interested", 0))
    d.metric("Irrelevant", counts.get("irrelevant", 0))
    st.caption(summary.get("note", ""))
    feedback_rows = get("/memory/feedback", {"limit": 100}) or []
    if feedback_rows:
        for item in feedback_rows:
            with st.expander(f"{item.get('feedback', '')} · {item.get('title', '')} — {item.get('company', '')}"):
                st.write("Reason:", item.get("reason") or "No reason provided")
                st.write("Score:", item.get("score"))
                if item.get("url"):
                    st.markdown(f"[Open job posting]({item['url']})")
    else:
        st.info("Rate jobs in the Ranked jobs tab to build a local feedback history.")
