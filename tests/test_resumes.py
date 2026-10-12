import json
import sqlite3
from pathlib import Path

import pytest

from app.config import settings
from app.db import init_db
from app.services.jobs import decide_approval
from app import resumes


@pytest.fixture
def resume_test_env(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "database_path", str(tmp_path / "resume-test.db"))
    monkeypatch.setattr(resumes, "BASE_DIR", tmp_path)
    monkeypatch.setattr(resumes, "RESUME_ROOT", tmp_path / "data" / "resumes")
    monkeypatch.setattr(resumes, "MASTER_DIR", tmp_path / "data" / "resumes" / "master")
    monkeypatch.setattr(resumes, "VERSIONS_DIR", tmp_path / "data" / "resumes" / "applications")
    monkeypatch.setattr(resumes, "MASTER_SOURCE", resumes.MASTER_DIR / "master_resume_source.txt")
    monkeypatch.setattr(resumes, "MASTER_PDF", resumes.MASTER_DIR / "master_resume.pdf")
    monkeypatch.setattr(resumes, "MASTER_DOCX", resumes.MASTER_DIR / "master_resume.docx")
    init_db()
    with sqlite3.connect(settings.database_path) as db:
        db.execute("""INSERT INTO jobs
            (id,title,company,location,url,description,source,fingerprint,score,score_reasons)
            VALUES(?,?,?,?,?,?,?,?,?,?)""",
            ("job-1", "Generative AI Engineer", "Company X", "India",
             "https://example.com/job",
             "We need a Generative AI Engineer with Python, LangGraph, RAG, FastAPI and LLM evaluation. "
             "The candidate will build retrieval-augmented applications and multi-agent workflows.",
             "test", "fp-job-1", 70, "[]"))
        db.execute("INSERT INTO applications(job_id,status) VALUES('job-1','not_submitted')")
    source = """ABHIK DAS
Hyderabad, India | +91 8876587227 | abhik@example.com | linkedin.com/in/abhik-das98 | github.com/abhikdas98
PROFESSIONAL SUMMARY
Data Analyst and AI Engineer experienced in Python, SQL, LangGraph, RAG, FastAPI and Docker.
TECHNICAL SKILLS
AI & LLM Systems: Generative AI, RAG, LangChain, LangGraph, MCP, Multi-Agent Systems, Prompt Engineering.
PROFESSIONAL EXPERIENCE
Genpact India Pvt Ltd — Process Developer (Data Analyst) — Apr 2022-Jul 2026
Automated analytical reporting with Python, Pandas and SQL.
TECHNICAL PROJECTS
Deep Agents Chatbot — Python, LangGraph, multi-agent workflows.
Credit Card Fraud Detection — XGBoost, FastAPI, Docker, MLflow.
EDUCATION
Bachelor of Commerce (Financial Management), 2021
CERTIFICATIONS
Advanced LLMs with RAG; Agentic AI Fundamentals
LANGUAGES
English, Hindi, Assamese, Bengali"""
    resumes.MASTER_DIR.mkdir(parents=True, exist_ok=True)
    resumes.MASTER_SOURCE.write_text(source, encoding="utf-8")
    monkeypatch.setattr(settings, "llm_enabled", True)
    monkeypatch.setattr(settings, "scheduler_enabled", False)
    monkeypatch.setattr(settings, "ranking_worker_enabled", False)
    return source


def response_payload():
    return json.dumps({
        "summary": "Data Analyst and AI Engineer experienced in Python, SQL, LangGraph, RAG, FastAPI and Docker.",
        "skills": ["AI & LLM Systems: Generative AI, RAG, LangChain, LangGraph, MCP, Multi-Agent Systems, Prompt Engineering."],
        "experience": [
            "Genpact India Pvt Ltd — Process Developer (Data Analyst) — Apr 2022-Jul 2026",
            "Automated analytical reporting with Python, Pandas and SQL."
        ],
        "projects": [
            "Deep Agents Chatbot — Python, LangGraph, multi-agent workflows.",
            "Credit Card Fraud Detection — XGBoost, FastAPI, Docker, MLflow."
        ],
        "education": ["Bachelor of Commerce (Financial Management), 2021"],
        "certifications": ["Advanced LLMs with RAG; Agentic AI Fundamentals"],
        "languages": ["English, Hindi, Assamese, Bengali"],
        "change_notes": ["Prioritized existing LangGraph and RAG passages for this job."],
        "unsupported_requirements": ["Production LLM evaluation at enterprise scale"]
    })


class FakeClient:
    def chat(self, *args, **kwargs):
        return response_payload()


def test_master_upload_preserves_original(resume_test_env, tmp_path):
    from reportlab.pdfgen import canvas
    pdf = tmp_path / "source.pdf"
    pdf_canvas = canvas.Canvas(str(pdf))
    pdf_canvas.drawString(50, 750, "ABHIK DAS - AI Engineer")
    pdf_canvas.drawString(50, 730, "Email: abhik@example.com | linkedin.com/in/abhik")
    pdf_canvas.drawString(50, 710, "Python LangGraph RAG FastAPI analytics and project experience")
    pdf_canvas.drawString(50, 690, "Bachelor of Commerce and certifications in modern AI systems")
    pdf_canvas.save()
    raw = pdf.read_bytes()
    result = resumes.upload_master("original.pdf", raw)
    assert result["configured"] is True
    assert result["characters_extracted"] > 80
    original = resumes.MASTER_DIR / f"master_original_{result['sha256'][:12]}.pdf"
    assert original.read_bytes() == raw


def test_tailoring_generates_immutable_version_artifacts(resume_test_env, monkeypatch):
    monkeypatch.setattr(resumes, "GroqClient", lambda: FakeClient())
    first = resumes.tailor_for_job("job-1")
    assert first["status"] == "needs_review"
    assert (resumes.BASE_DIR / first["docx_path"]).is_file()
    assert (resumes.BASE_DIR / first["pdf_path"]).is_file()
    report = json.loads((resumes.BASE_DIR / first["report_path"]).read_text(encoding="utf-8"))
    assert "LangGraph" in report["resume"]["summary"]
    assert first["unsupported_requirements"] == ["Production LLM evaluation at enterprise scale"]

    second = resumes.tailor_for_job("job-1")
    assert second["version_number"] == 2
    assert second["version_id"] != first["version_id"]
    assert (resumes.BASE_DIR / first["pdf_path"]).is_file(), "Creating v002 must not overwrite v001"


def test_non_verbatim_resume_claim_is_rejected(resume_test_env):
    bad = json.loads(response_payload())
    bad["projects"].append("Built production Claude systems for 10 million users.")
    with pytest.raises(resumes.ResumeError, match="not an exact source passage"):
        resumes._parse_response(json.dumps(bad), resumes.MASTER_SOURCE.read_text(encoding="utf-8"))


def test_submission_requires_both_job_and_resume_approval(resume_test_env, monkeypatch):
    monkeypatch.setattr(resumes, "GroqClient", lambda: FakeClient())
    version = resumes.tailor_for_job("job-1")

    with pytest.raises(resumes.ResumeError, match="Only the exact approved"):
        resumes.record_version_submission(version["version_id"], "submitted manually")

    resumes.approve_version(version["version_id"])
    with pytest.raises(resumes.ResumeError, match="job must also be explicitly approved"):
        resumes.record_version_submission(version["version_id"], "submitted manually")

    assert decide_approval("job-1", "approved") is True
    submitted = resumes.record_version_submission(version["version_id"], "Submitted on employer website")
    assert submitted["status"] == "submitted"
    with sqlite3.connect(settings.database_path) as db:
        application = db.execute("SELECT status, notes FROM applications WHERE job_id='job-1'").fetchone()
    assert application[0] == "submitted"
    assert version["version_id"] in application[1]


def test_bad_llm_schema_is_rejected(resume_test_env, monkeypatch):
    class BadClient:
        def chat(self, *args, **kwargs):
            return '{"summary":"invented","skills":[]}'
    monkeypatch.setattr(resumes, "GroqClient", lambda: BadClient())
    with pytest.raises(resumes.ResumeError, match="required schema"):
        resumes.tailor_for_job("job-1")


def test_missing_job_description_is_rejected(resume_test_env):
    with sqlite3.connect(settings.database_path) as db:
        db.execute("UPDATE jobs SET description='short' WHERE id='job-1'")
    with pytest.raises(resumes.ResumeError, match="too short or missing"):
        resumes.tailor_for_job("job-1")



def test_api_returns_actionable_409_when_job_not_approved(resume_test_env, monkeypatch):
    from fastapi.testclient import TestClient
    from app.main import app

    monkeypatch.setattr(resumes, "GroqClient", lambda: FakeClient())
    version = resumes.tailor_for_job("job-1")
    resumes.approve_version(version["version_id"])

    with TestClient(app) as client:
        response = client.post(
            f"/resumes/versions/{version['version_id']}/record-submission",
            json={"notes": "Submitted manually"},
        )

    assert response.status_code == 409
    assert response.json()["detail"] == (
        "The job must also be explicitly approved before a submission is recorded."
    )
