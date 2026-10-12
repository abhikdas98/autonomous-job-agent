
import logging
import threading
from collections.abc import Callable
from typing import Any

from app.config import settings
from app.db import log_event
from app.services.llm_ranking import rank_shortlist

logger = logging.getLogger(__name__)


class RankingWorker:
    """Run the existing LLM ranking service in a background thread."""

    def __init__(
        self,
        interval_seconds: int | None = None,
        process_batch: Callable[[], dict[str, Any]] | None = None,
    ):
        configured_interval = (
            interval_seconds
            if interval_seconds is not None
            else settings.ranking_worker_interval_seconds
        )

        self.interval_seconds = max(1, int(configured_interval))
        self.process_batch = process_batch or rank_shortlist

        self._stop_event = threading.Event()
        self._thread: threading.Thread | None = None
        self._lock = threading.Lock()

    @property
    def running(self) -> bool:
        return self._thread is not None and self._thread.is_alive()

    def start(self) -> bool:
        """Start the worker once. Return False if already running."""
        with self._lock:
            if self.running:
                return False

            self._stop_event.clear()
            self._thread = threading.Thread(
                target=self._run,
                name="llm-ranking-worker",
                daemon=True,
            )
            self._thread.start()

        log_event(
            "ranking_worker_started",
            {"interval_seconds": self.interval_seconds},
        )
        return True


    def stop(self, timeout: float = 90) -> bool:
        """Request shutdown and report whether the worker stopped."""
        self._stop_event.set()

        thread = self._thread
        if thread is None:
            return True

        if thread is threading.current_thread():
            return False

        thread.join(timeout=max(0, timeout))

        if thread.is_alive():
            try:
                log_event(
                    "ranking_worker_shutdown_timeout",
                    {"timeout_seconds": timeout},
                    "WARNING",
                )
            except Exception:
                logger.exception(
                    "Could not record worker shutdown timeout"
                )
            return False

        log_event("ranking_worker_stopped", {})
        return True

    
    def _run(self) -> None:
        while not self._stop_event.is_set():
            try:
                result = self.process_batch()
                log_event("ranking_worker_batch_completed", result)
            except Exception as exc:
                logger.exception("Background ranking batch failed")
                try:
                    log_event(
                        "ranking_worker_batch_failed",
                        {"error": str(exc)[:250]},
                        "ERROR",
                    )
                except Exception:
                    logger.exception("Could not record worker failure")

            # Wait between batches; wake immediately when shutdown is requested.
            if self._stop_event.wait(self.interval_seconds):
                break
