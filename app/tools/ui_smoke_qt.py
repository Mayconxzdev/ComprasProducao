from __future__ import annotations

"""Smoke test visual/funcional para rodar no Windows com PySide6 instalado.

Uso:
    set QT_QPA_PLATFORM=offscreen
    python -m app.tools.ui_smoke_qt

O teste abre o shell, alterna tema claro/escuro, visita as telas principais,
exercita seleção/deseleção do primeiro fornecedor quando há base carregada
e valida o método de seleção por card de destinatário quando possível.
"""

import os
import sys

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication  # type: ignore

from app.application.context import AppContext
from app.qt.main_window import MainWindow
from app.qt.theme import ThemeManager


def pump(app: QApplication, count: int = 20) -> None:
    for _ in range(max(1, count)):
        app.processEvents()


def main() -> int:
    app = QApplication.instance() or QApplication(sys.argv)
    theme = ThemeManager(app)
    ctx = AppContext.bootstrap()
    try:
        ctx.state.config.update_enabled = False
    except Exception:
        pass
    window = MainWindow(theme, app_context=ctx)
    window.show()
    pump(app)

    for mode in ("dark", "light"):
        theme.set_theme(mode)
        pump(app)
        for key in ("new_request", "suppliers", "history", "models", "settings"):
            window._set_page(key)  # smoke interno: troca de tela sem travar/crash
            pump(app, 40)

    window._set_page("suppliers")
    pump(app, 40)
    page = window.stack.currentWidget()
    if hasattr(page, "_proxy") and hasattr(page, "_toggle_proxy_row_selection"):
        rows = int(page._proxy.rowCount())
        if rows > 0:
            page._toggle_proxy_row_selection(0)
            pump(app)
            page._toggle_proxy_row_selection(0)
            pump(app)

    window._set_page("new_request")
    pump(app, 40)
    page = window.stack.currentWidget()
    if hasattr(page, "_toggle_recipient_from_card"):
        sample = {"empresa": "Smoke Fornecedor", "email": "smoke.fornecedor@example.com", "produto": "Teste"}
        page._toggle_recipient_from_card(sample["email"], sample)
        pump(app)
        page._toggle_recipient_from_card(sample["email"], sample)
        pump(app)

    window.close()
    pump(app)
    print("UI smoke OK")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
