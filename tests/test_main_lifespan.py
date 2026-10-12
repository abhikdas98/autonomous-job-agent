
import asyncio
from types import SimpleNamespace

import app.main as main


class FakeWorker:
    def __init__(self):
        self.start_calls = 0
        self.stop_calls = 0
        self.running = False

    def start(self):
        self.start_calls += 1
        self.running = True
        return True

    def stop(self, timeout=90):
        self.stop_calls += 1
        self.running = False


class FakeScheduler:
    def __init__(self):
        self.jobs = []
        self.running = False
        self.shutdown_calls = 0

    def add_job(self, func, trigger, **kwargs):
        self.jobs.append((func, trigger, kwargs))

    def start(self):
        self.running = True

    def shutdown(self, wait=False):
        self.shutdown_calls += 1
        self.running = False


def run_lifespan(monkeypatch, worker_enabled, scheduler_enabled):
    worker = FakeWorker()
    scheduler = FakeScheduler()

    monkeypatch.setattr(main, "ranking_worker", worker)
    monkeypatch.setattr(main, "scheduler", scheduler)
    monkeypatch.setattr(main, "init_db", lambda: None)
    monkeypatch.setattr(main, "log_event", lambda *args, **kwargs: None)
    monkeypatch.setattr(
        main.settings, "ranking_worker_enabled", worker_enabled
    )
    monkeypatch.setattr(
        main.settings, "scheduler_enabled", scheduler_enabled
    )

    async def exercise():
        async with main.lifespan(SimpleNamespace()):
            assert worker.running is worker_enabled
            assert scheduler.running is scheduler_enabled

    asyncio.run(exercise())

    return worker, scheduler


def test_lifespan_keeps_automation_disabled(monkeypatch):
    worker, scheduler = run_lifespan(
        monkeypatch,
        worker_enabled=False,
        scheduler_enabled=False,
    )

    assert worker.start_calls == 0
    assert worker.stop_calls == 0
    assert scheduler.running is False
    assert scheduler.shutdown_calls == 0


def test_lifespan_starts_and_stops_ranking_worker(monkeypatch):
    worker, scheduler = run_lifespan(
        monkeypatch,
        worker_enabled=True,
        scheduler_enabled=False,
    )

    assert worker.start_calls == 1
    assert worker.stop_calls == 1
    assert worker.running is False
    assert scheduler.running is False


def test_lifespan_scheduler_remains_independent(monkeypatch):
    worker, scheduler = run_lifespan(
        monkeypatch,
        worker_enabled=False,
        scheduler_enabled=True,
    )

    assert worker.start_calls == 0
    assert worker.stop_calls == 0
    assert scheduler.shutdown_calls == 1
    assert len(scheduler.jobs) == 2

