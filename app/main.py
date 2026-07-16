from __future__ import annotations
import argparse
import sys

from app.core.app_log import setup_logging
from app.core.crash_handler import install_crash_handler
from app.qt.dpi_awareness import enable_windows_dpi_awareness
from app.tools.prewarm import run_prewarm


def _run_qt_ui(logger) -> int:
    try:
        from app.qt.app import run_qt_app
    except Exception as exc:
        logger.exception("Falha ao iniciar UI Qt: %s", exc)
        return 3
    return run_qt_app()


def main(argv: list[str] | None = None):
    argv = argv if argv is not None else sys.argv[1:]

    parser = argparse.ArgumentParser(add_help=False)
    parser.add_argument("--prewarm", action="store_true")
    parser.add_argument("--force-refresh", action="store_true")
    args, _unknown = parser.parse_known_args(argv)

    logger = setup_logging()
    install_crash_handler(logger)
    if args.prewarm:
        res = run_prewarm(force_refresh=args.force_refresh)
        return 0 if res.ok else 2

    enable_windows_dpi_awareness()
    return _run_qt_ui(logger)

if __name__ == "__main__":
    raise SystemExit(main())
