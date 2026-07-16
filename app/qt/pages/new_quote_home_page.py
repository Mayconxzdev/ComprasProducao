from __future__ import annotations

from typing import Callable
from PySide6.QtCore import Qt
from PySide6.QtWidgets import QFrame, QGridLayout, QHBoxLayout, QLabel, QPushButton, QVBoxLayout, QWidget

from app.application.context import AppContext
from app.core.smart_parser import REQUEST_FREIGHT, REQUEST_MATERIAL, REQUEST_PURCHASE_ORDER
REQUEST_EX_PANELS = "ex_panels"
from app.qt.ui_scale import scaled_px
from app.qt.icon_utils import get_icon_char, get_icon


class NewQuoteHomePage(QWidget):
    def __init__(
        self,
        app_context: AppContext,
        *,
        on_start: Callable[[str], None],
        on_open_history: Callable[[], None],
        on_open_suppliers: Callable[[], None],
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.app_context = app_context
        self._on_start = on_start
        self._on_open_history = on_open_history
        self._on_open_suppliers = on_open_suppliers
        self._build_ui()

    def _build_ui(self) -> None:
        root = QVBoxLayout(self)
        root.setContentsMargins(scaled_px(20), scaled_px(18), scaled_px(20), scaled_px(14))
        root.setSpacing(scaled_px(14))
        title = QLabel("Nova cotação")
        title.setObjectName("pageTitle")
        root.addWidget(title)
        subtitle = QLabel("Escolha o tipo de envio e preencha apenas o necessário.")
        subtitle.setObjectName("pageSubtitle")
        root.addWidget(subtitle)

        panel = QFrame(self)
        panel.setObjectName("dashboardHero")
        grid = QGridLayout(panel)
        grid.setContentsMargins(scaled_px(18), scaled_px(18), scaled_px(18), scaled_px(18))
        grid.setHorizontalSpacing(scaled_px(14))
        grid.setVerticalSpacing(scaled_px(14))
        specs = [
            ("material", "Cotação de material", "Enviar pedido de preço para fornecedores.", "Criar cotação", "blue", REQUEST_MATERIAL),
            ("ex_panels", "Painéis EX", "Cotação de painéis para áreas classificadas.", "Criar cotação", "green", REQUEST_EX_PANELS),
            ("freight", "Cotação de frete", "Solicitar frete para transportadoras.", "Criar cotação", "purple", REQUEST_FREIGHT),
            ("purchase_order", "Ordem de compra", "Enviar ordem de compra para fornecedor.", "Criar ordem", "orange", REQUEST_PURCHASE_ORDER),
        ]
        all_specs = list(specs)
        for custom in list(getattr(self.app_context.state.config, "custom_quote_types", []) or []):
            if not isinstance(custom, dict) or not custom.get("active", True):
                continue
            all_specs.append((
                str(custom.get("icon") or "material"),
                str(custom.get("name") or "Envio personalizado"),
                str(custom.get("description") or "Modelo criado pelo administrador."),
                "Criar envio",
                str(custom.get("color") or "blue"),
                "custom:" + str(custom.get("id") or ""),
            ))
        for i, spec in enumerate(all_specs):
            grid.addWidget(self._start_card(*spec), i // 4, i % 4)
        root.addWidget(panel, 0)

        guide = QGridLayout()
        guide.setHorizontalSpacing(scaled_px(14))
        guide.setVerticalSpacing(scaled_px(14))
        guide.addWidget(self._action_info_card(
            "Acompanhe sem abrir e-mail",
            "Depois do envio, as respostas ficam em Acompanhar. O app mostra quem respondeu e quem falta cobrar.",
            "Abrir Acompanhar",
            self._on_open_history,
        ), 0, 0)
        guide.addWidget(self._action_info_card(
            "Encontre fornecedor por produto",
            "Digite chapa, rolamento, painel, cabo ou frete. A busca mostra fornecedores relacionados, não só nomes de e-mail.",
            "Abrir Fornecedores",
            self._on_open_suppliers,
        ), 0, 1)
        root.addLayout(guide, 0)

        root.addStretch(1)


    def _action_info_card(self, title: str, text: str, button: str, callback: Callable[[], None]) -> QFrame:
        card = QFrame(self)
        card.setObjectName("dashboardCard")
        box = QVBoxLayout(card)
        box.setContentsMargins(scaled_px(18), scaled_px(16), scaled_px(18), scaled_px(16))
        box.setSpacing(scaled_px(8))
        t = QLabel(title)
        t.setObjectName("dashCardTitle")
        box.addWidget(t)
        p = QLabel(text)
        p.setObjectName("pageSubtitle")
        p.setWordWrap(True)
        box.addWidget(p)
        box.addStretch(1)
        btn = QPushButton(button + "  ›")
        btn.setObjectName("secondarySmall")
        btn.clicked.connect(callback)
        box.addWidget(btn, 0, Qt.AlignmentFlag.AlignLeft)
        return card

    def _start_card(self, icon_key: str, title: str, subtitle: str, action: str, color: str, request_type: str) -> QFrame:
        card = QFrame(self)
        card.setObjectName(f"quoteType_{color}")
        card.setCursor(Qt.CursorShape.PointingHandCursor)
        box = QVBoxLayout(card)
        box.setContentsMargins(scaled_px(16), scaled_px(18), scaled_px(16), scaled_px(16))
        box.setSpacing(scaled_px(10))
        ico = QLabel()
        ico.setObjectName(f"quoteIcon_{color}")
        ico.setAlignment(Qt.AlignmentFlag.AlignCenter)
        try:
            colors = {"blue": "#075C91", "green": "#15803D", "purple": "#6D28D9", "orange": "#EA580C", "cyan": "#0891B2", "indigo": "#4F46E5", "slate": "#475569", "red": "#DC2626"}
            qicon = get_icon(icon_key, color=colors.get(color, "#075C91"), scale_factor=1.1)
            if qicon and not qicon.isNull():
                ico.setPixmap(qicon.pixmap(scaled_px(26), scaled_px(26)))
            else:
                ico.setText(get_icon_char(icon_key))
        except Exception:
            ico.setText(get_icon_char(icon_key))
        box.addWidget(ico, 0, Qt.AlignmentFlag.AlignHCenter)
        t = QLabel(title)
        t.setObjectName(f"quoteTitle_{color}")
        t.setAlignment(Qt.AlignmentFlag.AlignCenter)
        box.addWidget(t)
        s = QLabel(subtitle)
        s.setObjectName("muted")
        s.setAlignment(Qt.AlignmentFlag.AlignCenter)
        s.setWordWrap(True)
        box.addWidget(s)
        btn = QPushButton(f"{action}   ›")
        btn.setObjectName(f"quoteButton_{color}")
        btn.clicked.connect(lambda _=False, rt=request_type: self._on_start(rt))
        box.addWidget(btn)
        card.mousePressEvent = lambda event, rt=request_type: self._on_start(rt)  # type: ignore[method-assign]
        return card

    def _card(self, title: str) -> QFrame:
        card = QFrame(self)
        card.setObjectName("dashboardCard")
        box = QVBoxLayout(card)
        box.setContentsMargins(scaled_px(16), scaled_px(14), scaled_px(16), scaled_px(14))
        box.setSpacing(scaled_px(10))
        label = QLabel(title)
        label.setObjectName("dashCardTitle")
        box.addWidget(label)
        return card

    def _shortcut_card(self) -> QFrame:
        card = self._card("Atalhos recentes")
        box = card.layout()
        for title in ["Cotação de flanges aço inox", "Cotação de frete - Rio de Janeiro", "Painel EX 440V"]:
            row = QLabel(f"› {title}\n   06/07/2026 • 13:19")
            row.setObjectName("recentTextRow")
            box.addWidget(row)
        btn = QPushButton("Ver todas as cotações recentes ›")
        btn.setObjectName("linkButton")
        btn.clicked.connect(self._on_open_history)
        box.addWidget(btn)
        return card

    def _how_card(self) -> QFrame:
        card = self._card("Como funciona")
        box = card.layout()
        steps = [
            "1  Escolher o tipo de envio\n   Selecione cotação ou ordem de compra.",
            "2  Preencher as informações\n   Informe apenas o essencial e anexe arquivos.",
            "3  Selecionar destinatários e enviar\n   O app monta assunto, texto e assinatura.",
        ]
        for step in steps:
            lbl = QLabel(step)
            lbl.setObjectName("recentTextRow")
            lbl.setWordWrap(True)
            box.addWidget(lbl)
        return card

    def _last_recipients_card(self) -> QFrame:
        card = self._card("Últimos destinatários usados")
        box = card.layout()
        for name, email in [
            ("EletroFlange Modelo", "vendas@eletroflange-modelo.invalid"),
            ("Metal Técnica Modelo", "comercial@metal-tecnica-modelo.invalid"),
            ("TransLog Demonstração", "fretes@translog-demo.invalid"),
        ]:
            lbl = QLabel(f"{name}\n{email}")
            lbl.setObjectName("recentTextRow")
            box.addWidget(lbl)
        btn = QPushButton("Ver todos os destinatários ›")
        btn.setObjectName("linkButton")
        btn.clicked.connect(self._on_open_suppliers)
        box.addWidget(btn)
        return card

    def _footer_card(self) -> QFrame:
        frame = QFrame(self)
        frame.setObjectName("dashboardFooter")
        row = QHBoxLayout(frame)
        row.setContentsMargins(scaled_px(14), scaled_px(8), scaled_px(14), scaled_px(8))
        row.addWidget(QLabel("◷ Base atualizada: 06/07/2026 07:30"))
        row.addWidget(QLabel("• Fornecedores: 698"))
        row.addWidget(QLabel("• Itens cadastrados: 343"))
        row.addStretch(1)
        return frame
