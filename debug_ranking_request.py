import json
import httpx

from app.config import settings
from app.db import connection
from app.services.llm_ranking import SYSTEM_PROMPT

job_id = "55532e074df50a61e60abe46"

with connection() as c:
    row = c.execute(
        """
        SELECT id, title, company, location, description, score
        FROM jobs
        WHERE id = ?
        """,
        (job_id,),
    ).fetchone()

if row is None:
    raise SystemExit("Failed job not found in the database.")

job_data = {
    "title": row["title"],
    "company": row["company"],
    "location": row["location"],
    "description": (row["description"] or "")[:3500],
    "rule_score": row["score"],
}

messages = [
    {"role": "system", "content": SYSTEM_PROMPT},
    {
        "role": "user",
        "content": (
            "Score this job record as data:\n"
            + json.dumps(job_data, ensure_ascii=False)
        ),
    },
]

payload = {
    "model": settings.groq_model,
    "messages": messages,
    "temperature": 0,
    "max_completion_tokens": 900,
    "reasoning_effort": "low",
    "include_reasoning": False,
    "response_format": {"type": "json_object"},
}

url = settings.groq_api_base.rstrip("/") + "/chat/completions"

response = httpx.post(
    url,
    headers={
        "Authorization": f"Bearer {settings.groq_api_key}",
        "Content-Type": "application/json",
    },
    json=payload,
    timeout=30,
)

print("HTTP status:", response.status_code)
print("Response:", response.text[:3000])
