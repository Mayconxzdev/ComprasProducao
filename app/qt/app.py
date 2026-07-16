from __future__ import annotations

import logging
import sys

from PySide6.QtCore import Qt, QtMsgType, qInstallMessageHandler
from PySide6.QtWidgets import QApplication

from app.application.context import AppContext

from . import resources as _qt_resources  # noqa: F401  # side-effect: load QRC
from .main_window import MainWindow
from .theme import ThemeManager, ensure_valid_font
from .ui_scale import init_ui_scale

_QFONT_WARNING_TOKEN = "QFont::setPointSize: Point size <= 0 (-1), must be greater than 0"
_qt_msg_handler_installed = False
_qt_msg_handler_prev = None
_qfont_warning_logged_once = False


def _install_qt_message_handler() -> None:
    global _qt_msg_handler_installed, _qt_msg_handler_prev  # noqa: PLW0603
    if _qt_msg_handler_installed:
        return
    _qt_msg_handler_prev = qInstallMessageHandler(_qt_message_handler)
    _qt_msg_handler_installed = True


def _qt_message_handler(mode, context, message):  # noqa: ANN001
    global _qfont_warning_logged_once  # noqa: PLW0603
    text = str(message or "")
    if _QFONT_WARNING_TOKEN in text:
        if not _qfont_warning_logged_once:
            _qfont_warning_logged_once = True
            logging.getLogger("comprasapp").warning(
                "Qt emitiu warning de fonte invalida (QFont pointSize <= 0). "
                "Repeticoes foram suprimidas nesta sessao."
            )
        return

    if callable(_qt_msg_handler_prev):
        _qt_msg_handler_prev(mode, context, message)
        return

    if mode in {
        QtMsgType.QtWarningMsg,
        QtMsgType.QtCriticalMsg,
        QtMsgType.QtFatalMsg,
    }:
        sys.stderr.write(text + "\n")


def _ensure_valid_app_font(app: QApplication) -> None:
    app.setFont(ensure_valid_font(app.font()))


def run_qt_app() -> int:
    _install_qt_message_handler()
    app = QApplication.instance() or QApplication([])
    app.setApplicationName("ComprasVesper")
    app.setOrganizationName("ComprasVesper")
    app.setAttribute(Qt.ApplicationAttribute.AA_UseHighDpiPixmaps, True)
    init_ui_scale(app)
    _ensure_valid_app_font(app)

    app_context = AppContext.bootstrap()
    theme = ThemeManager(app)
    theme.apply_system_theme()
    _ensure_valid_app_font(app)

    window = MainWindow(theme, app_context=app_context)
    window.show()
    return int(app.exec())
