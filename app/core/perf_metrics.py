from __future__ import annotations

import statistics
import threading
from collections import deque
from dataclasses import dataclass


@dataclass
class MetricSummary:
    count: int
    p50_ms: float
    p95_ms: float
    avg_ms: float
    max_ms: float


class _RollingMetric:
    def __init__(self, maxlen: int = 512):
        self.samples = deque(maxlen=maxlen)

    def add(self, value_ms: float) -> None:
        if value_ms < 0:
            return
        self.samples.append(float(value_ms))

    def summary(self) -> MetricSummary:
        if not self.samples:
            return MetricSummary(0, 0.0, 0.0, 0.0, 0.0)
        vals = sorted(self.samples)
        n = len(vals)
        p50_idx = int((n - 1) * 0.50)
        p95_idx = int((n - 1) * 0.95)
        return MetricSummary(
            count=n,
            p50_ms=float(vals[p50_idx]),
            p95_ms=float(vals[p95_idx]),
            avg_ms=float(statistics.mean(vals)),
            max_ms=float(vals[-1]),
        )


_LOCK = threading.Lock()
_REGISTRY: dict[str, _RollingMetric] = {}


def record_timing(metric_name: str, duration_ms: float) -> None:
    with _LOCK:
        metric = _REGISTRY.get(metric_name)
        if metric is None:
            metric = _RollingMetric()
            _REGISTRY[metric_name] = metric
        metric.add(duration_ms)


def get_summary(metric_name: str) -> MetricSummary:
    with _LOCK:
        metric = _REGISTRY.get(metric_name)
        if metric is None:
            return MetricSummary(0, 0.0, 0.0, 0.0, 0.0)
        return metric.summary()


def dump_summaries() -> dict[str, MetricSummary]:
    with _LOCK:
        return {name: metric.summary() for name, metric in _REGISTRY.items()}
