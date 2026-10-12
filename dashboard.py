import os
import requests
import streamlit as st

st.set_page_config(page_title="Autonomous Job Agent", layout="wide")
api = os.getenv("AGENT_API_BASE", "http://127.0.0.1:8000").rstrip("/")
st.title("Autonomous Job Agent")
st.caption("Job discovery, relevance ranking, resume tailoring, approvals, and monitoring")


def get(path, params=None):
    try:
        response = requests.get(api + path, params=params, timeout=15)
        response.raise_for_status()
        return response.json()
    except Exception as exc:
        st.error(f"API request failed: {exc}")
        return None


def post(path, params=None, body=None):
    try:
        response = requests.post(api + path, params=params, json=body, timeout=120)
        response.raise_for_status()
        return response.json()
    except Exception as exc:
        st.error(f"Request failed: {exc}")
        return None


def download_bytes(version_id, file_type):
    try:
        response = requests.get(
            api + f"/resumes/versions/{version_id}/download",
            params={"file_type": file_type},
            timeout=30,
        )
        response.raise_for_status()
        return response.content
    except Exception as exc:
        st.error(f"Download failed: {exc}")
        return None


with st.sidebar:
    st.subheader("Agent controls")
    source = st.selectbox("Source", ["demo", "adzuna"])
    query = st.text_input("Role query", "agentic AI engineer OR generative AI engineer OR GenAI developer")
    location = st.text_input("Location", "India")
    email = st.checkbox("Email report after run")
    if st.button("Run discovery", type="primary"):
        with st.spinner("Running discovery and ranking..."):
            result = post("/agent/run", {"source": source, "what": query, "where": location, "send_email": email})
        if result:
            st.success("Run completed")
            st.json(result)
    if st.button("Send email report"):
        result = post("/reports/email")
        if result:
            st.json(result)

health = get("/health") or {}
metrics = get("/metrics") or {}
a, b, c, d = st.columns(4)
a.metric("Jobs", metrics.get("jobs_total", 0))
b.metric("Pending approvals", metrics.get("approvals_pending", 0))
c.metric("Submitted", metrics.get("applications_submitted", 0))
d.metric("Scheduler", "Enabled" if health.get("scheduler_enabled") else "Manual")

jobs_tab, approvals_tab, resumes_tab, events_tab = st.tabs(
    ["Ranked jobs", "Approval queue", "Resume tailoring", "Monitoring"]
)

with jobs_tab:
    minimum = st.slider("Minimum score", 0, 100, 0)
    jobs = get("/jobs", {"limit": 100, "min_score": minimum}) or []
    for job in jobs:
        with st.expander(f"{job['score']:.0f}/100 · {job['title']} — {job['company']}"):
            st.write(job.get("score_reasons", []))
            st.write(job.get("description", ""))
            st.markdown(f"[Open job posting]({job['url']})")
            if st.button("Queue for approval", key="queue_" + job["id"]):
                result = post("/approvals/" + job["id"])
                if result:
                    st.success(result["message"])
                    st.rerun()

with approvals_tab:
    pending = get("/approvals", {"status": "pending"}) or []
    if not pending:
        st.info("No pending approvals.")
    for item in pending:
        with st.expander(f"{item['title']} — {item['company']} ({item['score']:.0f}/100)"):
            st.markdown(f"[Open job posting]({item['url']})")
            st.write(item.get("location", ""))
            notes = st.text_input("Notes", key="notes_" + item["job_id"])
            left, right = st.columns(2)
            if left.button("Approve job", key="yes_" + item["job_id"]):
                if post(f"/approvals/{item['job_id']}/decision", body={"decision": "approved", "notes": notes}):
                    st.success("Job approved. Review and approve a specific resume version in the Resume tailoring tab before you apply.")
                    st.rerun()
            if right.button("Reject job", key="no_" + item["job_id"]):
                if post(f"/approvals/{item['job_id']}/decision", body={"decision": "rejected", "notes": notes}):
                    st.success("Job rejected.")
                    st.rerun()

with resumes_tab:
    st.subheader("Master resume")
    master = get("/resumes/master") or {"configured": False}
    if master.get("configured"):
        st.success(f"Master resume configured · {master.get('characters_extracted', 0):,} extracted characters")
        st.caption("Uploading a new master preserves the prior original snapshot. Existing job-specific versions remain unchanged.")
    else:
        st.info("Upload your master resume to begin. Supported formats: PDF, DOCX, TXT, and MD; maximum 15 MB.")

    uploaded = st.file_uploader(
        "Upload master resume",
        type=["pdf", "docx", "txt", "md"],
        key="master_resume_upload",
    )
    if st.button("Save as master resume", disabled=uploaded is None):
        try:
            response = requests.post(
                api + "/resumes/master",
                files={"file": (uploaded.name, uploaded.getvalue(), uploaded.type or "application/octet-stream")},
                timeout=90,
            )
            response.raise_for_status()
            st.success("Master resume saved locally in the agent's data/resumes folder.")
            st.json(response.json())
            st.rerun()
        except Exception as exc:
            st.error(f"Master resume upload failed: {exc}")

    st.divider()
    st.subheader("Tailor for a job")
    resume_jobs = get("/jobs", {"limit": 200}) or []
    if not resume_jobs:
        st.info("Discover jobs first. A substantive job description is needed for tailoring.")
    else:
        jobs_by_id = {item["id"]: item for item in resume_jobs}
        selected_job_id = st.selectbox(
            "Choose a job",
            options=list(jobs_by_id),
            format_func=lambda job_id: (
                f"{jobs_by_id[job_id]['title']} — {jobs_by_id[job_id]['company']} "
                f"({jobs_by_id[job_id]['score']:.0f}/100)"
            ),
        )
        selected_job = jobs_by_id[selected_job_id]
        st.markdown(f"[Open job posting]({selected_job['url']})")
        st.caption(f"Location: {selected_job.get('location', 'Not specified')} · Source: {selected_job.get('source', 'unknown')}")
        with st.expander("Job description"):
            st.write(selected_job.get("description") or "No description available.")
        if st.button("Generate a new resume version", type="primary", key="tailor_resume"):
            with st.spinner("Selecting relevant source-backed resume passages and generating PDF/DOCX..."):
                result = post(f"/resumes/tailor/{selected_job_id}")
            if result:
                st.success(f"Created resume v{result['version_number']:03d}. Review it before approving.")
                st.json(result)
                st.rerun()

        history = get(f"/resumes/{selected_job_id}") or {"versions": []}
        versions = history.get("versions", [])
        st.subheader("Version history")
        if not versions:
            st.info("No resume versions for this job yet.")
        for version in versions:
            version_id = version["version_id"]
            with st.expander(f"v{version['version_number']:03d} · {version['status']} · {version.get('created_at', '')}"):
                st.write("Source SHA-256:", version.get("source_sha256", ""))
                st.write("Job description SHA-256:", version.get("job_description_sha256", ""))
                if version.get("change_notes"):
                    st.markdown("**Tailoring notes**")
                    st.write(version["change_notes"])
                if version.get("unsupported_requirements"):
                    st.markdown("**Job requirements not evidenced in the master resume**")
                    st.warning("\n".join("• " + value for value in version["unsupported_requirements"]))
                if version.get("factuality_flags"):
                    st.markdown("**Factuality safeguards**")
                    st.caption("\n".join("• " + value for value in version["factuality_flags"]))

                download_cols = st.columns(3)
                for col, file_type, label, mime, suffix in [
                    (download_cols[0], "pdf", "Download PDF", "application/pdf", ".pdf"),
                    (download_cols[1], "docx", "Download DOCX", "application/vnd.openxmlformats-officedocument.wordprocessingml.document", ".docx"),
                    (download_cols[2], "report", "Audit report", "application/json", ".json"),
                ]:
                    data = download_bytes(version_id, file_type)
                    if data:
                        col.download_button(
                            label, data=data,
                            file_name=f"{selected_job_id}_resume_v{version['version_number']:03d}{suffix}",
                            mime=mime, key=f"download_{version_id}_{file_type}",
                        )

                if version["status"] == "needs_review":
                    if st.button("Approve this exact resume version", key="approve_resume_" + version_id):
                        approved = post(f"/resumes/versions/{version_id}/approve")
                        if approved:
                            st.success("Resume version approved. The job itself must also be approved in the Approval queue.")
                            st.rerun()
                elif version["status"] == "approved":
                    st.info("Approved version. After submitting this file manually on the employer's website, record that submission below.")
                    notes = st.text_input("Submission note (optional)", key="submission_note_" + version_id)
                    if st.button("Record manual submission", key="record_submission_" + version_id):
                        result = post(f"/resumes/versions/{version_id}/record-submission", body={"notes": notes})
                        if result:
                            st.success("Submission recorded against this exact resume version.")
                            st.rerun()
                elif version["status"] == "submitted":
                    st.success(f"Submission recorded on {version.get('submitted_at') or 'timestamp unavailable'}")

    st.warning(
        "Factuality safeguard: generated resume content must match passages from your master resume. "
        "This blocks added wording, but it cannot prove the original claims are true. Inspect the PDF/DOCX before approval. "
        "This app does not auto-submit employer forms; you submit manually, then record the exact version here."
    )

with events_tab:
    st.write("Health")
    st.json(health)
    st.write("Metrics")
    st.json(metrics)
    st.write("Recent events")
    st.json(get("/events", {"limit": 100}) or [])
