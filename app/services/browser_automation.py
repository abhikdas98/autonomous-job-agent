"""Conservative Playwright automation for explicitly batch-approved applications.

This adapter fills only fields that can be mapped to a user-verified local profile.
It never solves/bypasses CAPTCHA, invents answers, or treats an unverified page as
proof of submission. Playwright is an optional dependency.
"""
from __future__ import annotations

import re
import time
from pathlib import Path
from typing import Any

from app.config import settings
from app.db import connection, log_event
from app.resumes import ResumeError, resolve_artifact
from app.services.application_automation import (
    ApplicationAutomationError,
    list_automation_queue,
    mark_intervention,
    record_verified_submission,
)
from app.services.application_profile import read_application_profile


class BrowserAutomationError(RuntimeError):
    pass


ALIASES: dict[str, tuple[str, ...]] = {
    "full_name": ("full name", "applicant name", "candidate name", "your name", "legal name"),
    "email": ("email", "email address", "e-mail"),
    "phone": ("phone", "phone number", "mobile", "mobile number", "contact number"),
    "location": ("location", "city", "current city", "current location", "address city"),
    "linkedin_url": ("linkedin", "linkedin url", "linkedin profile"),
    "github_url": ("github", "github url", "github profile"),
    "current_company": ("current company", "current employer", "present employer"),
    "current_title": ("current job title", "current designation", "current role"),
    "years_experience": ("years of experience", "total experience", "work experience"),
    "notice_period": ("notice period", "availability to join", "joining time"),
    "work_authorization": ("work authorization", "authorized to work", "right to work"),
    "requires_sponsorship": ("require sponsorship", "visa sponsorship", "need sponsorship"),
    "desired_salary": ("expected salary", "salary expectation", "desired salary", "expected ctc"),
}
CHALLENGE_PATTERN = re.compile(
    r"captcha|recaptcha|hcaptcha|turnstile|verify you are human|are you a robot|"
    r"checking your browser|security challenge",
    re.IGNORECASE,
)
CONFIRMATION_PATTERN = re.compile(
    r"application (has been )?(submitted|received)|thank you for applying|"
    r"successfully applied|submission successful|confirmation number|application reference",
    re.IGNORECASE,
)


def _normal(value: str) -> str:
    return re.sub(r"[^a-z0-9]+", " ", value.casefold()).strip()


def _field_label(page: Any, locator: Any) -> str:
    bits = []
    for attr in ("aria-label", "placeholder", "name", "id"):
        value = locator.get_attribute(attr)
        if value:
            bits.append(value)
    element_id = locator.get_attribute("id")
    if element_id:
        for label in page.locator("label").all():
            if label.get_attribute("for") == element_id:
                text = label.inner_text().strip()
                if text:
                    bits.append(text)
    return " ".join(dict.fromkeys(bits))


def _profile_value(label: str, profile: dict[str, Any]) -> str | None:
    normalized = _normal(label)
    if not normalized:
        return None
    for key, aliases in ALIASES.items():
        if any(_normal(alias) == normalized or _normal(alias) in normalized for alias in aliases):
            value = str(profile.get(key, "")).strip()
            if value:
                return value
            return None

    # Split a verified full name only for explicit first/last-name fields.
    full_name = str(profile.get("full_name", "")).strip()
    parts = full_name.split()
    if normalized in {"first name", "given name", "forename"} and parts:
        return parts[0]
    if normalized in {"last name", "surname", "family name"} and len(parts) > 1:
        return parts[-1]

    for saved_label, saved_value in profile.get("additional_answers", {}).items():
        if _normal(saved_label) == normalized and str(saved_value).strip():
            return str(saved_value).strip()
    return None


def _visible_text(page: Any) -> str:
    try:
        return page.locator("body").inner_text(timeout=3000)[:20000]
    except Exception:
        return ""


def _challenge(page: Any) -> str | None:
    url = str(getattr(page, "url", "")).casefold()
    text = _visible_text(page)
    if CHALLENGE_PATTERN.search(url + " " + text):
        return "captcha"
    if "login" in url or "signin" in url or "sign-in" in url:
        return "authentication"
    try:
        if page.locator('input[type="password"]').count():
            return "authentication"
    except Exception:
        pass
    return None


def _wait_for_user_checkpoint(page: Any, checkpoint: str, timeout_seconds: int = 300) -> bool:
    """Give the user time to solve a visible challenge; never attempt to solve it."""
    deadline = time.monotonic() + timeout_seconds
    while time.monotonic() < deadline:
        try:
            page.wait_for_timeout(1000)
        except Exception:
            return False
        if _challenge(page) is None:
            return True
    return False


def _fill_known_fields(page: Any, profile: dict[str, Any], resume_pdf: Path) -> list[str]:
    unresolved: list[str] = []
    file_inputs = page.locator('input[type="file"]')
    resume_uploaded = False
    for index in range(file_inputs.count()):
        locator = file_inputs.nth(index)
        label = _normal(_field_label(page, locator))
        if "resume" in label or "cv" in label or (file_inputs.count() == 1 and not label):
            try:
                locator.set_input_files(str(resume_pdf))
                resume_uploaded = True
            except Exception:
                unresolved.append("resume upload")
            break

    fields = page.locator("input, textarea, select")
    for index in range(fields.count()):
        field = fields.nth(index)
        try:
            if not field.is_visible():
                continue
            tag = field.evaluate("(el) => el.tagName.toLowerCase()")
            kind = (field.get_attribute("type") or "text").casefold()
            if tag == "input" and kind in {"checkbox", "radio"}:
                if field.get_attribute("required") is not None or field.get_attribute("aria-required") == "true":
                    unresolved.append(_field_label(page, field) or f"required consent/choice #{index + 1}")
                continue
            if tag == "input" and kind in {
                "hidden", "submit", "button", "reset", "password", "file", "image",
            }:
                continue
            label = _field_label(page, field)
            value = _profile_value(label, profile)
            required = field.get_attribute("required") is not None or field.get_attribute("aria-required") == "true"
            if not value:
                if required:
                    unresolved.append(label or f"required field #{index + 1}")
                continue
            if tag == "select":
                options = field.locator("option")
                matched = None
                for option_index in range(options.count()):
                    option = options.nth(option_index)
                    option_text = option.inner_text().strip()
                    option_value = option.get_attribute("value") or ""
                    if _normal(value) in {_normal(option_text), _normal(option_value)}:
                        matched = option_value
                        break
                if matched is None:
                    if required:
                        unresolved.append(label or f"required selection #{index + 1}")
                    continue
                field.select_option(matched)
            else:
                field.fill(value)
        except Exception:
            # An individual unsupported widget is a checkpoint, not a reason to
            # guess at the field or silently claim that the form was completed.
            label = _field_label(page, field)
            unresolved.append(label or f"unhandled field #{index + 1}")

    if file_inputs.count() and not resume_uploaded:
        unresolved.append("resume upload field could not be identified")
    return sorted(set(unresolved))


def _set_queue_state(job_id: str, status: str, checkpoint: str, details: dict[str, Any]) -> None:
    import json
    with connection() as db:
        db.execute(
            """UPDATE application_automation_queue SET status=?,checkpoint=?,result_json=?,
               updated_at=CURRENT_TIMESTAMP WHERE job_id=?""",
            (status, checkpoint, json.dumps(details, ensure_ascii=False), job_id),
        )


def _run_one(page: Any, item: dict[str, Any], profile: dict[str, Any]) -> dict[str, Any]:
    job_id = item["job_id"]
    with connection() as db:
        db.execute(
            """UPDATE application_automation_queue SET status='in_progress',
               checkpoint='opening_job',updated_at=CURRENT_TIMESTAMP WHERE job_id=? AND status='approved'""",
            (job_id,),
        )
    try:
        resume_pdf = resolve_artifact(item["version_id"], "pdf")
    except ResumeError as exc:
        return mark_intervention(job_id, "upload_failed", {"message": str(exc)[:250]})

    page.goto(item["url"], wait_until="domcontentloaded", timeout=45000)
    page.wait_for_timeout(1000)
    challenge = _challenge(page)
    if challenge:
        if not _wait_for_user_checkpoint(page, challenge):
            return mark_intervention(job_id, challenge, {
                "message": "Human verification is required. Solve it in the opened browser, then retry this application."
            })
    unresolved = _fill_known_fields(page, profile, Path(resume_pdf))
    if unresolved:
        return mark_intervention(job_id, "unknown_required_field", {
            "message": "The agent did not guess at unanswered or unsupported fields.",
            "fields": unresolved[:30],
        })

    # Only click a clearly labeled application submission control. Never infer
    # that a generic Continue/Next button is permission to submit.
    submit = page.get_by_role(
        "button",
        name=re.compile(r"^\s*(submit application|submit|apply now|send application)\s*$", re.IGNORECASE),
    )
    if submit.count() == 0:
        submit = page.get_by_role(
            "link",
            name=re.compile(r"^\s*(submit application|apply now)\s*$", re.IGNORECASE),
        )
    if submit.count() != 1:
        return mark_intervention(job_id, "manual_review", {
            "message": "Could not identify one unambiguous submission control.",
        })
    before_text = _visible_text(page)
    submit.click(timeout=10000)
    try:
        page.wait_for_load_state("domcontentloaded", timeout=10000)
    except Exception:
        pass
    page.wait_for_timeout(1500)
    after_text = _visible_text(page)
    match = CONFIRMATION_PATTERN.search(after_text)
    if match and after_text != before_text:
        evidence = f"{page.url} | {match.group(0)}"
        return record_verified_submission(job_id, evidence[:500])
    return mark_intervention(job_id, "manual_review", {
        "message": "The submission control was activated, but no reliable confirmation was detected. Verify the portal before retrying.",
        "url": str(page.url)[:500],
    })


def run_approved_applications(limit: int = 5) -> dict[str, Any]:
    try:
        from playwright.sync_api import sync_playwright
    except ImportError as exc:
        raise BrowserAutomationError(
            "Browser automation is optional. Install the browser extra and Chromium first."
        ) from exc

    profile_result = read_application_profile()
    if not profile_result["configured"]:
        raise BrowserAutomationError("Save your verified application profile before running browser automation.")
    profile = profile_result["profile"]
    if not profile.get("full_name") or not profile.get("email"):
        raise BrowserAutomationError("The application profile must include at least full_name and email.")

    items = list_automation_queue(status="approved", limit=limit)
    if not items:
        return {"processed": 0, "results": [], "message": "No approved applications are waiting."}

    data_dir = Path(settings.database_path).resolve().parent
    browser_profile = data_dir / "browser-profile"
    browser_profile.mkdir(parents=True, exist_ok=True)
    results: list[dict[str, Any]] = []
    with sync_playwright() as playwright:
        context = playwright.chromium.launch_persistent_context(
            user_data_dir=str(browser_profile),
            headless=False,
            accept_downloads=False,
        )
        try:
            for item in items:
                page = context.new_page()
                try:
                    result = _run_one(page, item, profile)
                except Exception as exc:
                    result = mark_intervention(item["job_id"], "unsupported_portal", {
                        "message": f"Automation stopped safely: {str(exc)[:220]}",
                    })
                    log_event("application_browser_automation_error", {
                        "job_id": item["job_id"], "error": str(exc)[:220],
                    }, "ERROR")
                results.append({"job_id": item["job_id"], **result})
                # Keep the authenticated context but close each job tab.
                try:
                    page.close()
                except Exception:
                    pass
        finally:
            context.close()
    summary = {
        "processed": len(results),
        "submitted": sum(1 for item in results if item.get("status") == "submitted"),
        "needs_intervention": sum(1 for item in results if item.get("status") == "needs_intervention"),
        "results": results,
    }
    log_event("application_browser_batch_completed", {
        "processed": summary["processed"],
        "submitted": summary["submitted"],
        "needs_intervention": summary["needs_intervention"],
    })
    return summary
