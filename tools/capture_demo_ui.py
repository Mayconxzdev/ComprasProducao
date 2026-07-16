"""Generate portfolio screenshots from the real PySide6 interface in demo mode."""

from __future__ import annotations

import os
import sys
from pathlib import Path

os.environ.setdefault("COMPRAS_VESPER_DEMO", "1")
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from PySide6.QtCore import QEventLoop
from PySide6.QtWidgets import QApplication

from app.application.context import AppContext
from app.core.smart_parser import REQUEST_FREIGHT
from app.qt.main_window import MainWindow
from app.qt.theme import ThemeManager, ensure_valid_font
from app.qt.ui_scale import init_ui_scale


def _settle(app: QApplication, cycles: int = 16) -> None:
    for _ in range(cycles):
        app.processEvents(QEventLoop.ProcessEventsFlag.AllEvents, 100)


def _capture(window: MainWindow, destination: Path) -> None:
    destination.parent.mkdir(parents=True, exist_ok=True)
    image = window.grab()
    if image.isNull() or not image.save(str(destination), "PNG"):
        raise RuntimeError(f"Não foi possível gerar {destination}")


def main() -> int:
    out = PROJECT_ROOT / "docs" / "assets"
    app = QApplication.instance() or QApplication(sys.argv[:1])
    app.setApplicationName("ComprasVesper")
    init_ui_scale(app)
    app.setFont(ensure_valid_font(app.font()))
    theme = ThemeManager(app)
    theme.apply_system_theme()
    window = MainWindow(theme, app_context=AppContext.bootstrap())
    window.resize(1560, 900)
    window.show()
    _settle(app)
    _capture(window, out / "ui-dashboard-real.png")

    window._open_task(REQUEST_FREIGHT)
    _settle(app)
    _capture(window, out / "ui-freight-real.png")

    window._set_page("history")
    _settle(app)
    _capture(window, out / "ui-tracking-real.png")
    window.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
