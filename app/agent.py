"""Entry point used by API and scheduler for the full workflow."""


def run_discovery(source="adzuna", what=None, where=None, send_email=False):
    # Lazy import avoids building the graph until the workflow is first used.
    from app.workflow import run_agent_workflow
    return run_agent_workflow(source=source, what=what, where=where, send_email=send_email)
