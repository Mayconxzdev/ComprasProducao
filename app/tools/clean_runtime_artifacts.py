from __future__ import annotations

import shutil
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
DIR_NAMES = {"__pycache__", ".pytest_cache"}
FILE_SUFFIXES = {".pyc", ".pyo"}
RUNTIME_FILES = {"history_global.lock", "history_global.jsonl"}


def main() -> int:
    removed = 0
    for path in sorted(ROOT.rglob("*"), key=lambda p: len(p.parts), reverse=True):
        if path.is_dir() and path.name in DIR_NAMES:
            shutil.rmtree(path, ignore_errors=True)
            removed += 1
        elif path.is_file() and (path.suffix.lower() in FILE_SUFFIXES or path.name in RUNTIME_FILES):
            try:
                path.unlink()
                removed += 1
            except FileNotFoundError:
                pass
    compras_app = ROOT / "ComprasApp"
    if compras_app.exists():
        shutil.rmtree(compras_app, ignore_errors=True)
        removed += 1
    print(f"clean_runtime_artifacts_removed={removed}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
