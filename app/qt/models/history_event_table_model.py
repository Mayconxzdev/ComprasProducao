from __future__ import annotations

from typing import Any, Iterable

from PySide6.QtCore import QAbstractTableModel, QModelIndex, Qt


class HistoryEventTableModel(QAbstractTableModel):
    HEADERS = (
        "Data/Hora",
        "Pedido",
        "Solicitante",
        "Liberador",
        "Enviado para",
        "Conta",
        "Status",
        "Assinatura",
    )

    STATUS_LABELS = {
        "sent_smtp_ok": "Enviado",
        "sent_smtp_fail": "Falha no envio",
        "opened_thunderbird": "Aberto no Thunderbird",
        "thunderbird_fail": "Falha Thunderbird",
        "generated": "Gerado",
        "quote_generated": "Gerado",
        "sent_smtp_cancelled": "Cancelado",
        "workflow_em_aprovacao": "Em aprovacao",
        "workflow_aprovada": "Aprovada",
        "workflow_requisitada": "Requisitada",
        "workflow_ordem_emitida": "Ordem emitida",
    }

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self._rows: list[dict] = []

    def set_rows(self, rows: Iterable[dict]) -> None:
        self.beginResetModel()
        self._rows = [row for row in rows if isinstance(row, dict)]
        self.endResetModel()

    def rows(self) -> list[dict]:
        return list(self._rows)

    def row_at(self, row: int) -> dict | None:
        if row < 0 or row >= len(self._rows):
            return None
        return self._rows[row]

    def rowCount(self, parent: QModelIndex = QModelIndex()) -> int:  # noqa: N802
        if parent.isValid():
            return 0
        return len(self._rows)

    def columnCount(self, parent: QModelIndex = QModelIndex()) -> int:  # noqa: N802
        if parent.isValid():
            return 0
        return len(self.HEADERS)

    def headerData(self, section: int, orientation: Qt.Orientation, role: int = Qt.ItemDataRole.DisplayRole) -> Any:  # noqa: N802
        if role != Qt.ItemDataRole.DisplayRole:
            return None
        if orientation == Qt.Orientation.Horizontal and 0 <= section < len(self.HEADERS):
            return self.HEADERS[section]
        return None

    def data(self, index: QModelIndex, role: int = Qt.ItemDataRole.DisplayRole) -> Any:  # noqa: N802
        if not index.isValid():
            return None
        row = self.row_at(index.row())
        if row is None:
            return None

        if role == Qt.ItemDataRole.UserRole:
            return row
        if role == Qt.ItemDataRole.UserRole + 1 and index.column() == 6:
            return str(row.get("status") or "").strip()
        if role not in (Qt.ItemDataRole.DisplayRole, Qt.ItemDataRole.ToolTipRole):
            return None

        ts = str(row.get("ts") or "").strip()
        extra = row.get("extra") or {}
        if not isinstance(extra, dict):
            extra = {}
        product = str(extra.get("request_text") or row.get("product_query") or "").strip()
        raw_status = str(row.get("status") or "").strip()
        status = self.STATUS_LABELS.get(raw_status, raw_status.replace("_", " ").strip().capitalize())
        requester = str(extra.get("requester_name") or "").strip()
        approver = str(extra.get("approver_name") or "").strip()
        recipients = row.get("recipients") or []
        recipient_summary = "-"
        if isinstance(recipients, list) and recipients:
            labels: list[str] = []
            for recipient in recipients[:3]:
                if not isinstance(recipient, dict):
                    continue
                company = str(recipient.get("empresa") or "").strip()
                email = str(recipient.get("email") or "").strip()
                contact = str(recipient.get("contato_nome") or "").strip()
                if company and contact:
                    labels.append(f"{company} ({contact})")
                elif company:
                    labels.append(company)
                elif contact:
                    labels.append(contact)
                elif email:
                    labels.append(email)
            if labels:
                extra_count = max(0, len(recipients) - len(labels))
                recipient_summary = " | ".join(labels)
                if extra_count:
                    recipient_summary = f"{recipient_summary} | +{extra_count}"
        sender = str(extra.get("sender") or "").strip().casefold()
        if "@empresa-a." in sender:
            account = "Empresa A"
        elif "@empresa-b." in sender:
            account = "Empresa B"
        else:
            account = str(row.get("user") or "").strip()
        signature_owner = str(extra.get("signature_owner") or row.get("user") or "").strip()

        if index.column() == 0:
            return ts
        if index.column() == 1:
            return product
        if index.column() == 2:
            return requester
        if index.column() == 3:
            return approver
        if index.column() == 4:
            return recipient_summary
        if index.column() == 5:
            return account
        if index.column() == 6:
            return status
        if index.column() == 7:
            return signature_owner
        return None
