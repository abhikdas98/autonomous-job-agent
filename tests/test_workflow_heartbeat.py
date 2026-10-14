import threading

import pytest

from app.config import settings
from app.db import connection, init_db
import app.workflow as workflow


@pytest.fixture
def workflow_db(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "database_path", str(tmp_path / "workflow.db"))
    init_db()


def test_workflow_registers_run_before_graph_and_stops_heartbeat(workflow_db, monkeypatch):
    class FakeGraph:
        def invoke(self, state):
            with connection() as db:
                row = db.execute(
                    "SELECT status,heartbeat_at FROM agent_runs WHERE run_id=?",
                    (state["run_id"],),
                ).fetchone()
            assert row is not None
            assert row["status"] == "running"
            assert row["heartbeat_at"] is not None
            with connection() as db:
                db.execute(
                    "UPDATE agent_runs SET status='completed',completed_at=CURRENT_TIMESTAMP WHERE run_id=?",
                    (state["run_id"],),
                )
            return {"result": {"status": "completed", "run_id": state["run_id"]}}

    monkeypatch.setattr(workflow, "_AGENT_GRAPH", FakeGraph())
    result = workflow.run_agent_workflow(source="demo", what="GenAI Engineer", where="India")

    assert result["status"] == "completed"
    with connection() as db:
        row = db.execute(
            "SELECT status FROM agent_runs WHERE run_id=?", (result["run_id"],)
        ).fetchone()
    assert row["status"] == "completed"
    assert not any(
        thread.name.startswith("agent-heartbeat-") and thread.is_alive()
        for thread in threading.enumerate()
    )


def test_workflow_marks_unhandled_graph_error_failed(workflow_db, monkeypatch):
    class BrokenGraph:
        def invoke(self, state):
            raise RuntimeError("unexpected graph failure")

    monkeypatch.setattr(workflow, "_AGENT_GRAPH", BrokenGraph())
    monkeypatch.setattr(workflow.uuid, "uuid4", lambda: type("RunId", (), {"hex": "expected-crash-run"})())
    with pytest.raises(RuntimeError, match="unexpected graph failure"):
        workflow.run_agent_workflow(source="demo", what="GenAI Engineer", where="India")

    with connection() as db:
        row = db.execute(
            "SELECT status,error FROM agent_runs WHERE run_id='expected-crash-run'"
        ).fetchone()
    assert row["status"] == "failed"
    assert row["error"] == "unexpected graph failure"
