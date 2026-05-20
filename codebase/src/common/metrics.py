import threading
import time
from typing import Dict, List, Any

class MetricsCollector:
    """
    A collector for timing and general metric observations.
    Ensures thread-safe operations for recording metrics.
    """
    def __init__(self):
        # Using threading.RLock (reentrant lock) to prevent deadlocks when
        # methods holding the lock (e.g., stop_timer) call other methods
        # that also attempt to acquire the same lock (e.g., observe)
        # from the same thread.
        self._lock: threading.RLock = threading.RLock()
        self._timers: Dict[str, float] = {}
        self._observations: Dict[str, List[Any]] = {}

    def start_timer(self, name: str) -> None:
        """
        Starts a timer for a given name.
        If a timer with the same name is already active, its start time will be overwritten.
        """
        with self._lock:
            self._timers[name] = time.perf_counter()

    def stop_timer(self, name: str) -> None:
        """
        Stops a timer and records its duration as an observation.
        If the timer was not started, no action is taken.
        This method safely calls `observe` while holding the `RLock`.
        """
        with self._lock:
            start_time = self._timers.pop(name, None)
            if start_time is not None:
                duration = time.perf_counter() - start_time
                self.observe(f"{name}_duration", duration)

    def observe(self, name: str, value: Any) -> None:
        """
        Records an observation (e.g., a metric value, timer duration).
        """
        with self._lock:
            self._observations.setdefault(name, []).append(value)

    def get_metrics(self) -> Dict[str, Any]:
        """
        Returns a snapshot of the collected metrics.
        Returns a copy to prevent external modification of internal state.
        """
        with self._lock:
            return {
                "timers_active": list(self._timers.keys()),
                "observations": {k: list(v) for k, v in self._observations.items()}
            }

    def clear_metrics(self) -> None:
        """
        Clears all collected timers and observations.
        """
        with self._lock:
            self._timers.clear()
            self._observations.clear()