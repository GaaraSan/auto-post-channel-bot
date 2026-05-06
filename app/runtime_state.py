import os
import threading

from dotenv import load_dotenv  # type: ignore[reportMissingImports]

# Load .env before STATE is first accessed so env vars are available immediately.
load_dotenv()


class RuntimeState:
    """
    In-process mutable configuration, adjustable without a restart.

    Stores: dry-run flag, auto-posting enabled flag, posting interval,
    and a "posting in progress" flag used by /status.

    All getters/setters are protected by a threading.Lock so they are
    safe to call from both the asyncio event loop and executor threads.
    """

    def __init__(self) -> None:
        self._lock = threading.Lock()

        self._dry_run = os.getenv("DRY_RUN", "false").lower() in {"1", "true", "yes"}
        self._posting_enabled = os.getenv("POSTING_ENABLED", "false").lower() in {"1", "true", "yes"}

        # Interval bounds in seconds (defaults: 5 h – 8 h).
        self._interval_min = int(os.getenv("POST_INTERVAL_MIN", "18000"))
        self._interval_max = int(os.getenv("POST_INTERVAL_MAX", "28800"))

        self._is_posting_now = False

    def get_dry_run(self) -> bool:
        with self._lock:
            return self._dry_run

    def set_dry_run(self, enabled: bool) -> None:
        with self._lock:
            self._dry_run = enabled

    def get_posting_enabled(self) -> bool:
        with self._lock:
            return self._posting_enabled

    def set_posting_enabled(self, enabled: bool) -> None:
        with self._lock:
            self._posting_enabled = enabled

    def get_interval(self) -> tuple[int, int]:
        with self._lock:
            return self._interval_min, self._interval_max

    def set_interval(self, min_seconds: int, max_seconds: int) -> None:
        if min_seconds <= 0 or max_seconds <= 0:
            raise ValueError("Interval must be positive")
        if min_seconds > max_seconds:
            raise ValueError("min_seconds must be <= max_seconds")
        with self._lock:
            self._interval_min = min_seconds
            self._interval_max = max_seconds

    def get_is_posting_now(self) -> bool:
        with self._lock:
            return self._is_posting_now

    def set_is_posting_now(self, value: bool) -> None:
        with self._lock:
            self._is_posting_now = value


STATE = RuntimeState()
