from __future__ import annotations

import os
import time
from contextlib import contextmanager
from pathlib import Path


@contextmanager
def cross_process_file_lock(lock_path: Path, timeout_sec: int = 10):
    """Small cross-platform advisory file lock.

    The app is deployed on Windows/NAS, where ``msvcrt.locking`` is the native
    option. Tests and developer scripts may run on Linux/macOS, so this helper
    also uses ``fcntl.flock`` when available. A timeout avoids hanging the UI or
    background workers forever if another process dies while holding the lock.
    """
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    fh = open(lock_path, "a+b")
    start = time.time()
    locked = False
    backend = "none"
    try:
        try:
            import msvcrt  # type: ignore
            backend = "msvcrt"
            while not locked:
                try:
                    fh.seek(0)
                    msvcrt.locking(fh.fileno(), msvcrt.LK_NBLCK, 1)
                    locked = True
                except OSError:
                    if time.time() - start >= timeout_sec:
                        raise TimeoutError(f"Timeout acquiring lock: {lock_path}")
                    time.sleep(0.1)
        except ImportError:
            try:
                import fcntl  # type: ignore
                backend = "fcntl"
                while not locked:
                    try:
                        fcntl.flock(fh.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
                        locked = True
                    except OSError:
                        if time.time() - start >= timeout_sec:
                            raise TimeoutError(f"Timeout acquiring lock: {lock_path}")
                        time.sleep(0.1)
            except ImportError:
                # Last-resort atomic lock file. Good enough for smoke tests and
                # platforms without msvcrt/fcntl; Windows production uses msvcrt.
                backend = "atomic"
                marker = lock_path.with_suffix(lock_path.suffix + ".owner")
                while not locked:
                    try:
                        fd = os.open(str(marker), os.O_CREAT | os.O_EXCL | os.O_WRONLY)
                        os.close(fd)
                        locked = True
                    except FileExistsError:
                        if time.time() - start >= timeout_sec:
                            raise TimeoutError(f"Timeout acquiring lock: {lock_path}")
                        time.sleep(0.1)
        yield
    finally:
        if locked:
            try:
                if backend == "msvcrt":
                    import msvcrt  # type: ignore
                    fh.seek(0)
                    msvcrt.locking(fh.fileno(), msvcrt.LK_UNLCK, 1)
                elif backend == "fcntl":
                    import fcntl  # type: ignore
                    fcntl.flock(fh.fileno(), fcntl.LOCK_UN)
                elif backend == "atomic":
                    marker = lock_path.with_suffix(lock_path.suffix + ".owner")
                    marker.unlink(missing_ok=True)
            except Exception:
                pass
        fh.close()
