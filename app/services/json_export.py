"""Keep useful, portable JSON snapshots of the local agent database."""
from __future__ import annotations

import json
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from app.config import settings
from app.db import connection

JSON_FIELDS = {
    "jobs": {"score_reasons"},
    "events": {"details"},
    "resume_versions": {
        "summary_json", "factuality_flags_json",
        "unsupported_requirements_json", "change_notes_json",
    },
    "agent_runs": {"summary_json"},
}
TABLES = {
    "jobs": ("jobs.json", "SELECT * FROM jobs ORDER BY score DESC, created_at DESC"),
    "applications": ("applications.json", "SELECT * FROM applications ORDER BY updated_at DESC"),
    "approvals": ("approvals.json", "SELECT * FROM approvals ORDER BY created_at DESC"),
    "resume_versions": ("resume_versions.json", "SELECT * FROM resume_versions ORDER BY created_at DESC"),
    "events": ("events.json", "SELECT * FROM events ORDER BY id DESC LIMIT 5000"),
    "llm_ranking_queue": ("ranking_queue.json", "SELECT * FROM llm_ranking_queue ORDER BY updated_at DESC"),
    "agent_runs": ("agent_runs.json", "SELECT * FROM agent_runs ORDER BY started_at DESC LIMIT 500"),
}


def export_directory() -> Path:
    # Keep exports alongside the configured database so tests and local profiles stay isolated.
    return Path(settings.database_path).resolve().parent / "exports"


def _decode_row(table: str, row: dict[str, Any]) -> dict[str, Any]:
    for field in JSON_FIELDS.get(table, set()):
        value = row.get(field)
        if isinstance(value, str):
            try:
                row[field.removesuffix("_json") if field.endswith("_json") else field] = json.loads(value)
            except (json.JSONDecodeError, TypeError):
                pass
            if field.endswith("_json"):
                row.pop(field, None)
    return row


def _atomic_write(path: Path, data: Any) -> None:
    temporary = path.with_name(path.name + "." + uuid.uuid4().hex + ".tmp")
    temporary.write_text(json.dumps(data, ensure_ascii=False, indent=2, default=str) + "\n", encoding="utf-8")
    temporary.replace(path)


def export_json_snapshot(latest_run: dict[str, Any] | None = None) -> dict[str, Any]:
    """Export safe-to-store operational records; resume content is stored separately."""
    if not settings.json_exports_enabled:
        return {"ok": False, "disabled": True, "files": []}
    root = export_directory()
    root.mkdir(parents=True, exist_ok=True)
    exported: list[str] = []
    counts: dict[str, int] = {}
    with connection() as db:
        for table, (filename, query) in TABLES.items():
            rows = db.execute(query).fetchall()
            payload = [_decode_row(table, dict(row)) for row in rows]
            _atomic_write(root / filename, payload)
            exported.append(filename)
            counts[filename] = len(payload)

    if latest_run is not None:
        _atomic_write(root / "latest_run.json", latest_run)
        if "latest_run.json" not in exported:
            exported.append("latest_run.json")
    manifest = {
        "schema_version": 1,
        "exported_at": datetime.now(timezone.utc).isoformat(),
        "files": exported,
        "record_counts": counts,
        "note": "Local operational snapshots. SQLite remains the transactional source of truth.",
    }
    _atomic_write(root / "manifest.json", manifest)
    if "manifest.json" not in exported:
        exported.append("manifest.json")
    return {"ok": True, "directory": "data/exports", "files": exported, "record_counts": counts,
            "exported_at": manifest["exported_at"]}


def export_status() -> dict[str, Any]:
    root = export_directory()
    manifest_path = root / "manifest.json"
    if not manifest_path.exists():
        result = export_json_snapshot()
        return result
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {"ok": False, "directory": "data/exports", "files": [], "error": "Manifest could not be read."}
    return {"ok": True, "directory": "data/exports", **manifest}
