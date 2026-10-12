
import threading

from app.services.llm_worker import RankingWorker


def test_worker_processes_a_batch_and_stops():
    processed = threading.Event()
    calls = []

    def fake_batch():
        calls.append(1)
        processed.set()
        return {
            "considered": 1,
            "llm_updated": 1,
            "deterministic_fallback": 0,
            "deferred": 0,
        }

    worker = RankingWorker(
        interval_seconds=1,
        process_batch=fake_batch,
    )

    assert worker.start() is True
    assert processed.wait(timeout=2)

    worker.stop(timeout=2)

    assert worker.running is False
    assert len(calls) == 1


def test_worker_does_not_start_twice():
    processed = threading.Event()

    def fake_batch():
        processed.set()
        return {"considered": 0}

    worker = RankingWorker(
        interval_seconds=5,
        process_batch=fake_batch,
    )

    try:
        assert worker.start() is True
        assert processed.wait(timeout=2)
        assert worker.start() is False
    finally:
        worker.stop(timeout=2)



def test_worker_continues_after_batch_failure():
    processed = threading.Event()
    calls = []

    def flaky_batch():
        calls.append(1)

        if len(calls) == 1:
            raise RuntimeError("Simulated ranking failure")

        processed.set()
        return {"considered": 0}

    worker = RankingWorker(
        interval_seconds=1,
        process_batch=flaky_batch,
    )

    try:
        assert worker.start() is True
        assert processed.wait(timeout=4)
    finally:
        worker.stop(timeout=2)

    assert len(calls) >= 2
    assert worker.running is False



def test_worker_reports_shutdown_timeout():
    batch_started = threading.Event()
    release_batch = threading.Event()

    def blocked_batch():
        batch_started.set()
        release_batch.wait()
        return {"considered": 0}

    worker = RankingWorker(
        interval_seconds=1,
        process_batch=blocked_batch,
    )

    try:
        assert worker.start() is True
        assert batch_started.wait(timeout=2)

        # The batch is blocked, so the worker cannot stop yet.
        assert worker.stop(timeout=0.01) is False
        assert worker.running is True
    finally:
        # Allow the batch to finish so the test leaves no thread behind.
        release_batch.set()

    assert worker.stop(timeout=2) is True
    assert worker.running is False
