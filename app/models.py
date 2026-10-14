from pydantic import BaseModel, Field
from typing import Literal


class JobIn(BaseModel):
    title: str
    company: str
    location: str = ""
    url: str
    description: str = ""
    source: str = "manual"
    posted_at: str = ""


class ApprovalDecision(BaseModel):
    decision: Literal["approved", "rejected"]
    notes: str = ""


class ApplicationStatus(BaseModel):
    status: Literal["not_submitted", "submitted", "interview", "rejected", "offer", "withdrawn"]
    notes: str = ""


class ManualJobRequest(BaseModel):
    jobs: list[JobIn] = Field(min_length=1, max_length=200)


class ResumeSubmissionRecord(BaseModel):
    notes: str = Field(default="", max_length=2000)


class JobSearchPreferences(BaseModel):
    preferred_roles: list[str] = Field(default_factory=list, max_length=30)
    preferred_skills: list[str] = Field(default_factory=list, max_length=30)
    preferred_locations: list[str] = Field(default_factory=list, max_length=30)
    excluded_terms: list[str] = Field(default_factory=list, max_length=30)
    notes: str = Field(default="", max_length=1000)


class JobFeedback(BaseModel):
    feedback: Literal["interested", "not_interested", "irrelevant", "applied"]
    reason: str = Field(default="", max_length=1000)


class BatchApplicationItem(BaseModel):
    job_id: str = Field(min_length=1, max_length=100)
    version_id: str = Field(min_length=1, max_length=100)


class BatchApplicationApproval(BaseModel):
    applications: list[BatchApplicationItem] = Field(min_length=1, max_length=50)


class ApplicationProfile(BaseModel):
    full_name: str = Field(default="", max_length=200)
    email: str = Field(default="", max_length=320)
    phone: str = Field(default="", max_length=60)
    location: str = Field(default="", max_length=200)
    linkedin_url: str = Field(default="", max_length=500)
    github_url: str = Field(default="", max_length=500)
    current_company: str = Field(default="", max_length=200)
    current_title: str = Field(default="", max_length=200)
    years_experience: str = Field(default="", max_length=40)
    notice_period: str = Field(default="", max_length=100)
    work_authorization: str = Field(default="", max_length=200)
    requires_sponsorship: str = Field(default="", max_length=40)
    desired_salary: str = Field(default="", max_length=100)
    additional_answers: dict[str, str] = Field(default_factory=dict)
