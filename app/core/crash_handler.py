from __future__ import annotations

import os
import sys
import threading
import traceback
from .config import ensure_app_data_dir


def _open_logs_folder() -> None:
    logs = ensure_app_data_dir() / "logs"
    logs.mkdir(parents=True, exist_ok=True)
    try:
        os.startfile(str(logs))  # type: ignore[attr-defined]
    except Exception:
        pass


def install_crash_handler(logger) -> None:
    def _ask_open_logs_with_qt(message: str) -> bool:
        if threading.current_thread() is not threading.main_thread():
            return False
        try:
            from PySide6.QtWidgets import QApplication, QMessageBox

            app = QApplication.instance()
            if app is None:
                return False
            reply = QMessageBox.question(
                None,
                "Erro inesperado",
                message,
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                QMessageBox.StandardButton.No,
            )
            return reply == QMessageBox.StandardButton.Yes
        except Exception:
            return False

    def handle_exception(exc_type, exc_value, exc_tb):
        if issubclass(exc_type, KeyboardInterrupt):
            return
        text = "".join(traceback.format_exception(exc_type, exc_value, exc_tb))
        try:
            logger.error("UNHANDLED_EXCEPTION\n%s", text)
        except Exception:
            pass

        msg = (
            "O aplicativo encontrou um erro inesperado.\n\n"
            "Um log tecnico foi salvo em %APPDATA%\\ComprasApp\\logs.\n\n"
            "Deseja abrir a pasta de logs agora?"
        )
        if _ask_open_logs_with_qt(msg):
            _open_logs_folder()

    sys.excepthook = handle_exception

    if hasattr(sys, "__excepthook__"):
        try:
            import threading

            def thread_hook(args):
                handle_exception(args.exc_type, args.exc_value, args.exc_traceback)

            threading.excepthook = thread_hook  # type: ignore[attr-defined]
        except Exception:
            pass
