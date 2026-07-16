from __future__ import annotations
import logging
from logging.handlers import RotatingFileHandler
from pathlib import Path
from .config import ensure_app_data_dir

def setup_logging() -> logging.Logger:
    d = ensure_app_data_dir() / "logs"
    d.mkdir(parents=True, exist_ok=True)
    log_path: Path = d / "app.log"
    logger = logging.getLogger("comprasapp")
    if logger.handlers:
        return logger
    logger.setLevel(logging.INFO)
    fmt = logging.Formatter("%(asctime)s | %(levelname)s | %(message)s")
    try:
        fh = RotatingFileHandler(log_path, maxBytes=2_000_000, backupCount=5, encoding="utf-8")
    except PermissionError:
        fallback = Path.cwd() / ".appdata"
        (fallback / "logs").mkdir(parents=True, exist_ok=True)
        fh = RotatingFileHandler(fallback / "logs" / "app.log", maxBytes=2_000_000, backupCount=3, encoding="utf-8")
    fh.setFormatter(fmt)
    logger.addHandler(fh)
    sh = logging.StreamHandler()
    sh.setFormatter(fmt)
    logger.addHandler(sh)
    return logger
