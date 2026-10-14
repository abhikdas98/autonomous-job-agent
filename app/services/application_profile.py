"""Local-only verified profile storage for application form filling."""
import json
import os
from pathlib import Path
from typing import Any

from app.config import settings
from app.db import log_event


PROFILE_FIELDS = {
    "full_name", "email", "phone", "location", "linkedin_url", "github_url",
    "current_company", "current_title", "years_experience", "notice_period",
    "work_authorization", "requires_sponsorship", "desired_salary",
}


def _profile_path() -> Path:
    return Path(settings.database_path).resolve().parent / "application_profile.json"


def read_application_profile() -> dict[str, Any]:
    path = _profile_path()
    if not path.is_file():
        return {"configured": False, "profile": {}}
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError("The local application profile could not be read safely.") from exc
    if not isinstance(payload, dict):
        raise ValueError("The local application profile has an invalid format.")
    return {"configured": True, "profile": payload}


def save_application_profile(profile: dict[str, Any]) -> dict[str, Any]:
    # Keep only declared fields and string values. Unknown fields are not silently
    # used to populate arbitrary questions on employer websites.
    clean: dict[str, Any] = {}
    for key in PROFILE_FIELDS:
        value = profile.get(key, "")
        if not isinstance(value, str):
            raise ValueError(f"Profile field {key} must be text.")
        clean[key] = value.strip()
    extra = profile.get("additional_answers", {})
    if not isinstance(extra, dict) or len(extra) > 50:
        raise ValueError("Additional answers must be a dictionary with at most 50 entries.")
    clean["additional_answers"] = {}
    for key, value in extra.items():
        if not isinstance(key, str) or not isinstance(value, str):
            raise ValueError("Additional answer labels and values must be text.")
        if len(key) > 200 or len(value) > 2000:
            raise ValueError("Additional answer labels or values exceed the allowed length.")
        clean["additional_answers"][key.strip()] = value.strip()

    path = _profile_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(".json.tmp")
    temporary.write_text(json.dumps(clean, ensure_ascii=False, indent=2), encoding="utf-8")
    os.replace(temporary, path)
    log_event("application_profile_saved", {"field_count": len(clean)})
    return {"configured": True, "fields_saved": len(clean), "path": str(path)}
