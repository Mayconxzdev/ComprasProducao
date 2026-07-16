"""Performance monitoring utilities"""
import time
import logging
import functools
from typing import Callable

logger = logging.getLogger(__name__)


class PerfTimer:
    """Performance timer for measuring code execution"""
    def __init__(self, name: str, log_level=logging.INFO):
        self.name = name
        self.log_level = log_level
        self.start_time = 0
        self.duration_ms = 0

    def __enter__(self):
        self.start_time = time.perf_counter()
        logger.log(self.log_level, f"PERF:STAGE_START {self.name}")
        return self

    def __exit__(self, *args):
        self.duration_ms = (time.perf_counter() - self.start_time) * 1000
        logger.log(self.log_level, f"PERF:STAGE_END {self.name} dur_ms={self.duration_ms:.2f}")


def perf_log(func: Callable) -> Callable:
    """Decorator to log function performance"""
    @functools.wraps(func)
    def wrapper(*args, **kwargs):
        func_name = f"{func.__module__}.{func.__qualname__}"
        with PerfTimer(func_name):
            return func(*args, **kwargs)
    return wrapper


class UIFreezeWatchdog:
    """Monitors UI mainloop for freezes"""
    def __init__(self, tk_root, check_interval_ms: int = 100, freeze_threshold_ms: int = 300):
        self.root = tk_root
        self.check_interval_ms = check_interval_ms
        self.freeze_threshold_ms = freeze_threshold_ms
        self.last_check = time.perf_counter()
        self.running = False
        self.freeze_count = 0

    def start(self):
        """Start monitoring"""
        self.running = True
        self.last_check = time.perf_counter()
        self._schedule_check()

    def stop(self):
        """Stop monitoring"""
        self.running = False

    def _schedule_check(self):
        """Schedule next check"""
        if self.running:
            self.root.after(self.check_interval_ms, self._check)

    def _check(self):
        """Check for UI freeze"""
        now = time.perf_counter()
        actual_delay_ms = (now - self.last_check) * 1000
        expected_delay_ms = self.check_interval_ms

        if actual_delay_ms > (expected_delay_ms + self.freeze_threshold_ms):
            self.freeze_count += 1
            freeze_duration = actual_delay_ms - expected_delay_ms
            logger.warning(
                f"UI_FREEZE_DETECTED delay_ms={freeze_duration:.2f} "
                f"expected={expected_delay_ms} actual={actual_delay_ms:.2f}"
            )

        self.last_check = now
        self._schedule_check()


# Global search performance tracker
search_executions = []

def log_search_start(query: str):
    """Log search start"""
    execution = {
        "query": query,
        "start_time": time.perf_counter(),
        "compute_ms": 0,
        "render_ms": 0
    }
    search_executions.append(execution)
    logger.info(f'PERF:SEARCH_START query="{query}"')
    return execution


def log_search_compute_end(execution: dict):
    """Log search compute end"""
    execution["compute_ms"] = (time.perf_counter() - execution["start_time"]) * 1000
    logger.info(f'PERF:SEARCH_COMPUTE_END dur_ms={execution["compute_ms"]:.2f}')


def log_search_render_end(execution: dict, row_count: int):
    """Log search render end"""
    total_ms = (time.perf_counter() - execution["start_time"]) * 1000
    execution["render_ms"] = total_ms - execution["compute_ms"]
    logger.info(
        f'PERF:SEARCH_RENDER_END dur_ms={execution["render_ms"]:.2f} '
        f'rows={row_count} total_ms={total_ms:.2f}'
    )
