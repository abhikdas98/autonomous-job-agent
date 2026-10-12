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
