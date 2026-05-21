import collections
import math
import threading

class MetricsCollector:
    def __init__(self, max_recent_samples: int = 100):
        """
        Initializes the MetricsCollector.

        Args:
            max_recent_samples: The maximum number of recent samples to store for
                                each metric. Set to 0 to disable storing raw samples
                                and only keep summary statistics (count, sum, min, max).
        """
        self.metrics = {}
        self.max_recent_samples = max_recent_samples
        self._lock = threading.Lock() # For thread-safe access to self.metrics

    def _create_new_metric_data(self):
        """
        Helper method to create a new metric data structure.
        """
        return {
            "count": 0,
            "sum": 0.0,
            "min": math.inf,
            "max": -math.inf,
            "recent_samples": (
                collections.deque(maxlen=self.max_recent_samples)
                if self.max_recent_samples > 0
                else None
            ),
        }

    def observe(self, name: str, value: float):
        """
        Records an observation for a given metric.

        This method replaces the previous unbounded list storage with summary statistics
        (count, sum, min, max) and an optional, bounded collection of recent samples.
        This prevents unbounded memory consumption in long-running agents, addressing
        the core memory pressure issue.
        It is thread-safe for concurrent observations, as specified by BOUNTY_HUNTER_MASTER_RULES.md (CHƯƠNG VII).
        """
        with self._lock:
            # Atomically get or create the metric data for the given name.
            # If 'name' is not in self.metrics, _create_new_metric_data() is called
            # and its return value is inserted. Otherwise, the existing value is returned.
            metric_data = self.metrics.setdefault(name, self._create_new_metric_data())
            
            metric_data["count"] += 1
            metric_data["sum"] += value
            metric_data["min"] = min(metric_data["min"], value)
            metric_data["max"] = max(metric_data["max"], value)
            
            if metric_data["recent_samples"] is not None:
                metric_data["recent_samples"].append(value)