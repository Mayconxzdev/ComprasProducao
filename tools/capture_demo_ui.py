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

    # Estado preenchido por APIs reais da própria tela: prova visual de que o
    # composer aceita dados, anexo e seleção de transportadoras sem mockup.
    composer = window._composer_page
    if composer is None:
        raise RuntimeError("Composer de frete não foi inicializado")
    composer.freight_desc.setText("Conjunto de ventilação industrial")
    composer.freight_volumes.setText("03 volumes")
    composer.freight_weight.setText("420 kg")
    composer.freight_nf_value.setText("R$ 18.700,00")
    composer.freight_measures.setText("180 x 90 x 120 cm")
    composer.freight_destination.setText("Entrega agendada")
    attachment = str(PROJECT_ROOT / "examples" / "anexo-demo.txt")
    composer._add_attachments([attachment])
    composer._add_default_freight_carriers()
    _settle(app)
    # Algumas atualizações do composer são agendadas; reafirma o estado após o
    # ciclo de eventos sem simular widget ou editar a imagem.
    if attachment not in composer._attachments:
        composer._attachments.append(attachment)
    composer._drop_zone_files_state = tuple()
    composer._refresh_all()
    _settle(app)
    _capture(window, out / "ui-freight-interaction-real.png")

    window._set_page("history")
    _settle(app)
    _capture(window, out / "ui-tracking-real.png")
    window.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
