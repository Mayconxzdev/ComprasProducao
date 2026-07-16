from __future__ import annotations

import queue
import threading
import time
from concurrent.futures import Future, ThreadPoolExecutor
from dataclasses import dataclass
from typing import Any, Callable, Optional


@dataclass
class JobCallbacks:
    on_progress: Optional[Callable[[Any], None]] = None
    on_done: Optional[Callable[[Any], None]] = None
    on_error: Optional[Callable[[Exception], None]] = None
    on_cancelled: Optional[Callable[[], None]] = None


@dataclass
class JobEvent:
    kind: str
    job_key: str
    payload: Any = None
    callbacks: Optional[JobCallbacks] = None


class CancelToken:
    def __init__(self):
        self._event = threading.Event()

    def cancel(self) -> None:
        self._event.set()

    def is_cancelled(self) -> bool:
        return self._event.is_set()


class JobContext:
    def __init__(self, job_key: str, cancel_token: CancelToken, emit_progress: Callable[[Any], None], throttle_ms: int = 100):
        self.job_key = job_key
        self.cancel_token = cancel_token
        self._emit_progress = emit_progress
        self._throttle_ms = max(10, int(throttle_ms))
        self._last_progress = 0.0

    def is_cancelled(self) -> bool:
        return self.cancel_token.is_cancelled()

    def check_cancelled(self) -> None:
        if self.cancel_token.is_cancelled():
            raise CancelledError(self.job_key)

    def progress(self, payload: Any) -> None:
        now = time.perf_counter() * 1000.0
        if (now - self._last_progress) < self._throttle_ms:
            return
        self._last_progress = now
        self._emit_progress(payload)


class CancelledError(Exception):
    pass


@dataclass
class _RunningJob:
    token: CancelToken
    future: Future
    callbacks: JobCallbacks


class JobManager:
    def __init__(self, *, max_workers: int = 4):
        self._executor = ThreadPoolExecutor(max_workers=max_workers, thread_name_prefix="comprasapp-job")
        self._events: queue.Queue[JobEvent] = queue.Queue()
        self._lock = threading.Lock()
        self._running: dict[str, _RunningJob] = {}

    def submit(
        self,
        job_key: str,
        fn: Callable[..., Any],
        *args,
        dedupe: bool = True,
        callbacks: Optional[JobCallbacks] = None,
        **kwargs,
    ) -> bool:
        cb = callbacks or JobCallbacks()
        with self._lock:
            current = self._running.get(job_key)
            if dedupe and current and not current.future.done():
                return False
            token = CancelToken()

            def emit_progress(payload: Any) -> None:
                self._events.put(JobEvent(kind="progress", job_key=job_key, payload=payload, callbacks=cb))

            ctx = JobContext(job_key=job_key, cancel_token=token, emit_progress=emit_progress)

            def run_job():
                try:
                    result = fn(ctx, *args, **kwargs)
                    if token.is_cancelled():
                        self._events.put(JobEvent(kind="cancelled", job_key=job_key, callbacks=cb))
                    else:
                        self._events.put(JobEvent(kind="done", job_key=job_key, payload=result, callbacks=cb))
                except CancelledError:
                    self._events.put(JobEvent(kind="cancelled", job_key=job_key, callbacks=cb))
                except Exception as e:
                    self._events.put(JobEvent(kind="error", job_key=job_key, payload=e, callbacks=cb))

            future = self._executor.submit(run_job)
            self._running[job_key] = _RunningJob(token=token, future=future, callbacks=cb)
            return True

    def cancel(self, job_key: str) -> bool:
        with self._lock:
            current = self._running.get(job_key)
            if not current:
                return False
            current.token.cancel()
            return True

    def is_running(self, job_key: str) -> bool:
        with self._lock:
            current = self._running.get(job_key)
            return bool(current and not current.future.done())

    def drain_events(self, *, limit: int = 200) -> list[JobEvent]:
        out: list[JobEvent] = []
        for _ in range(max(1, int(limit))):
            try:
                out.append(self._events.get_nowait())
            except queue.Empty:
                break
        if not out:
            return out
        # Cleanup finished jobs after events are drained.
        with self._lock:
            done_keys = [k for k, v in self._running.items() if v.future.done()]
            for key in done_keys:
                self._running.pop(key, None)
        return out

    def shutdown(self, wait: bool = False) -> None:
        with self._lock:
            for item in self._running.values():
                item.token.cancel()
        self._executor.shutdown(wait=wait, cancel_futures=True)
