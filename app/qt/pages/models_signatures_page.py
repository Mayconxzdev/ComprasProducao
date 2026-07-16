from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QFrame, QHBoxLayout, QLabel, QPushButton, QVBoxLayout, QWidget, QMessageBox, QDialog, QLineEdit, QTextEdit, QComboBox, QCheckBox, QScrollArea, QFormLayout, QTabWidget

from app.application.context import AppContext
from app.core.dashboard_insights import item_count, supplier_count
from app.core.email_signature import first_signature_owner, load_signature_html, resolve_signature_html_path, signature_owner_options
from app.core.companies import company_for_key
from app.core.email_templates import build_freight_email, build_material_email, build_purchase_order_email
from app.qt.ui_scale import scaled_px
from app.qt.icon_utils import get_icon_char


class ModelsSignaturesPage(QWidget):
    """Modelos e assinaturas com dados reais da configuração, sem mock técnico."""

    def __init__(self, app_context: AppContext, *, on_status=None, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.app_context = app_context
        self._on_status = on_status
        self._selected_owner = first_signature_owner(app_context.state.config) or ""
        self._build_ui()

    def on_page_activated(self) -> None:
        self._rebuild_dynamic()

    def _build_ui(self) -> None:
        self.root = QVBoxLayout(self)
        self.root.setContentsMargins(scaled_px(18), scaled_px(18), scaled_px(18), scaled_px(12))
        self.root.setSpacing(scaled_px(14))
        title = QLabel("Modelos e assinaturas")
        title.setObjectName("pageTitle")
        self.root.addWidget(title)
        subtitle = QLabel("Padronize os textos e garanta que cada pessoa envie com a assinatura correta.")
        subtitle.setObjectName("pageSubtitle")
        self.root.addWidget(subtitle)
        self.dynamic_area = QWidget(self)
        self.root.addWidget(self.dynamic_area, 1)
        self._rebuild_dynamic()

    def _clear_layout(self, layout) -> None:
        while layout.count():
            item = layout.takeAt(0)
            widget = item.widget()
            if widget is not None:
                widget.setParent(None)
                widget.deleteLater()
            child = item.layout()
            if child is not None:
                self._clear_layout(child)

    def _rebuild_dynamic(self) -> None:
        if self.dynamic_area.layout() is None:
            layout = QVBoxLayout(self.dynamic_area)
        else:
            layout = self.dynamic_area.layout()
            self._clear_layout(layout)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(scaled_px(14))

        tabs = QTabWidget(self.dynamic_area)
        tabs.setObjectName("adminTabs")
        tabs.addTab(self._signatures_card(), "Assinaturas")
        tabs.addTab(self._models_card(), "Modelos padrão")
        tabs.addTab(self._custom_types_card(), "Tipos de envio")
        layout.addWidget(tabs, 1)
        layout.addWidget(self._mapping_card(), 0)

    def _card(self, title: str, subtitle: str = "") -> QFrame:
        card = QFrame(self)
        card.setObjectName("dashboardCard")
        box = QVBoxLayout(card)
        box.setContentsMargins(scaled_px(16), scaled_px(14), scaled_px(16), scaled_px(14))
        box.setSpacing(scaled_px(10))
        top = QHBoxLayout()
        t = QLabel(title)
        t.setObjectName("dashCardTitle")
        top.addWidget(t)
        top.addStretch(1)
        box.addLayout(top)
        if subtitle:
            s = QLabel(subtitle)
            s.setObjectName("muted")
            s.setWordWrap(True)
            box.addWidget(s)
        return card

    def _models_card(self) -> QFrame:
        card = self._card("Modelos padrão", "Textos usados automaticamente em Material, Painéis EX, Frete e Ordem de compra.")
        box = card.layout()
        for icon, key, title, preview in [
            (get_icon_char("material"), "material", "Cotação de material", "Solicita preço, prazo, pagamento, estoque e frete quando aplicável."),
            (get_icon_char("ex_panels"), "ex", "Painéis EX", "Inclui documentação/certificado para área classificada."),
            (get_icon_char("freight"), "freight", "Cotação de frete", "Usa dados do material, NF, medidas e transportadoras padrão."),
            (get_icon_char("purchase_order"), "po", "Ordem de compra", "Solicita confirmação de recebimento, prazo e disponibilidade."),
        ]:
            box.addWidget(self._model_row(icon, title, preview, key))
        return card

    def _model_row(self, icon: str, title: str, preview: str, template_key: str) -> QFrame:
        row = QFrame(self)
        row.setObjectName("modelRow")
        lay = QHBoxLayout(row)
        lay.setContentsMargins(scaled_px(12), scaled_px(10), scaled_px(12), scaled_px(10))
        ico = QLabel(icon)
        ico.setObjectName("recentIcon")
        ico.setAlignment(Qt.AlignmentFlag.AlignCenter)
        lay.addWidget(ico)
        texts = QVBoxLayout()
        name = QLabel(f"{title}   Ativo")
        name.setObjectName("recentTitle")
        sub = QLabel(preview)
        sub.setObjectName("muted")
        sub.setWordWrap(True)
        texts.addWidget(name)
        texts.addWidget(sub)
        lay.addLayout(texts, 1)
        btn_view = QPushButton("Visualizar")
        btn_view.setObjectName("secondarySmall")
        btn_view.clicked.connect(lambda _=False, t=title, k=template_key: self._show_template_preview_dialog(t, k))
        lay.addWidget(btn_view)
        return row

    def _signatures_card(self) -> QFrame:
        cfg = self.app_context.state.config
        owners = signature_owner_options(cfg) or [self._selected_owner or "Operador demo"]
        if self._selected_owner not in owners:
            self._selected_owner = owners[0] if owners else ""
        card = self._card("Assinaturas", "Assinaturas reais configuradas para a equipe.")
        box = card.layout()
        for owner in owners:
            row = QPushButton(f"{owner}   {'✓' if owner == self._selected_owner else ''}")
            row.setObjectName("signatureRow")
            row.clicked.connect(lambda _=False, o=owner: self._select_owner(o))
            box.addWidget(row)
        preview_text = self._signature_preview_text(self._selected_owner)
        preview = QLabel("Prévia da assinatura selecionada\n\n" + preview_text)
        preview.setObjectName("signaturePreview")
        preview.setWordWrap(True)
        box.addWidget(preview, 1)
        btn = QPushButton("Gerenciar assinaturas")
        btn.setObjectName("accent")
        btn.clicked.connect(self._open_signature_dialog)
        box.addWidget(btn)
        return card

    def _template_preview(self, template_key: str) -> str:
        company = company_for_key(getattr(self.app_context.state.config, "smtp_active_profile", "vesper"))
        if template_key == "freight":
            subject, body = build_freight_email(
                company,
                descricao="FLANGES",
                volumes="02 VOLUMES",
                peso="100 kg",
                valor_nf="R$ 6.840,00",
                medidas="133 x 48 x 48 cm",
                observacao="",
            )
        elif template_key == "po":
            subject, body = build_purchase_order_email(company, oc_number="123", observacao="")
        else:
            subject, body = build_material_email(company, "PAINEL/ELE TR/EX TUV161484\nMotor 7,5 CV – 440 V", ex_required=(template_key == "ex"))
        return f"Assunto:\n{subject}\n\nCorpo:\n{body[:1800]}"

    def _show_template_preview_dialog(self, title: str, template_key: str) -> None:
        from PySide6.QtWidgets import QDialog, QVBoxLayout, QTextBrowser
        from app.core.utils_text import clean_text
        from app.core.email_signature import build_html_email_body

        company = company_for_key(getattr(self.app_context.state.config, "smtp_active_profile", "vesper"))
        if template_key == "freight":
            subject, body = build_freight_email(
                company,
                descricao="FLANGES",
                volumes="02 VOLUMES",
                peso="100 kg",
                valor_nf="R$ 6.840,00",
                medidas="133 x 48 x 48 cm",
                observacao="",
            )
        elif template_key == "po":
            subject, body = build_purchase_order_email(company, oc_number="123", observacao="")
        else:
            subject, body = build_material_email(company, "PAINEL/ELE TR/EX TUV161484\nMotor 7,5 CV – 440 V", ex_required=(template_key == "ex"))

        owner = self._selected_owner or first_signature_owner(self.app_context.state.config)
        cfg = self.app_context.state.config
        profile_key = clean_text(cfg.smtp_active_profile)
        profile = cfg.get_active_profile()
        profile_label = clean_text(getattr(profile, "label", ""))
        sig_path = resolve_signature_html_path(owner, profile_key, profile_label)
        sig_html = load_signature_html(sig_path) if sig_path else ""

        body_html = build_html_email_body(body, sig_html) if sig_html else body.replace("\n", "<br>")

        dialog = QDialog(self)
        dialog.setWindowTitle(f"Visualizar modelo: {title}")
        dialog.resize(scaled_px(650), scaled_px(500))
        layout = QVBoxLayout(dialog)

        header = QLabel(f"<b>Assunto:</b> {subject}")
        header.setWordWrap(True)
        header.setStyleSheet("font-size: 14px; padding-bottom: 8px;")
        layout.addWidget(header)

        browser = QTextBrowser(dialog)
        browser.setHtml(body_html)
        browser.setObjectName("previewBrowser")
        layout.addWidget(browser, 1)

        dialog.exec()

    def _signature_preview_text(self, owner: str) -> str:
        cfg = self.app_context.state.config
        profile_key = str(getattr(cfg, "smtp_active_profile", "vesper") or "vesper")
        profile = cfg.get_active_profile()
        path = resolve_signature_html_path(owner, profile_key, str(getattr(profile, "label", "") or ""))
        if not path:
            return "Nenhuma assinatura configurada para este usuário."
        try:
            import re, html
            raw = load_signature_html(path)
            text = re.sub(r"(?i)<br\s*/?>", "\n", raw)
            text = re.sub(r"(?i)</(p|div|tr|li|table|h[1-6])>", "\n", text)
            text = re.sub(r"<[^>]+>", "", text)
            lines = [line.strip() for line in html.unescape(text).splitlines() if line.strip()]
            return "\n".join(lines[:10]) or "Assinatura vazia."
        except Exception:
            return f"Assinatura: {path}"

    def _select_owner(self, owner: str) -> None:
        self._selected_owner = owner
        self._rebuild_dynamic()

    def _custom_types_card(self) -> QFrame:
        card = self._card("Tipos de envio personalizados", "Área de administrador: crie cards novos na tela Nova cotação sem mexer no código.")
        box = card.layout()
        cfg = self.app_context.state.config
        items = [it for it in list(getattr(cfg, "custom_quote_types", []) or []) if isinstance(it, dict)]
        if not items:
            empty = QLabel("Nenhum tipo personalizado criado. Os 4 tipos padrão continuam ativos.")
            empty.setObjectName("muted")
            box.addWidget(empty)
        else:
            for item in items:
                row = QFrame(self)
                row.setObjectName("modelRow")
                lay = QHBoxLayout(row)
                lay.setContentsMargins(scaled_px(12), scaled_px(10), scaled_px(12), scaled_px(10))
                ico = QLabel(get_icon_char(str(item.get("icon") or "material")))
                ico.setObjectName("recentIcon")
                ico.setAlignment(Qt.AlignmentFlag.AlignCenter)
                lay.addWidget(ico)
                texts = QVBoxLayout()
                name = QLabel(f"{item.get('name', 'Envio personalizado')}   {'Ativo' if item.get('active', True) else 'Oculto'}")
                name.setObjectName("recentTitle")
                desc = QLabel(str(item.get("description") or ""))
                desc.setObjectName("muted")
                desc.setWordWrap(True)
                texts.addWidget(name)
                texts.addWidget(desc)
                lay.addLayout(texts, 1)
                btn_edit = QPushButton("Editar")
                btn_edit.setObjectName("secondarySmall")
                btn_edit.clicked.connect(lambda _=False, tid=str(item.get("id") or ""): self._edit_custom_type(tid))
                lay.addWidget(btn_edit)
                btn_preview = QPushButton("Prévia")
                btn_preview.setObjectName("secondarySmall")
                btn_preview.clicked.connect(lambda _=False, tid=str(item.get("id") or ""): self._preview_custom_type(tid))
                lay.addWidget(btn_preview)
                btn_hide = QPushButton("Ocultar" if item.get("active", True) else "Ativar")
                btn_hide.setObjectName("secondarySmall")
                btn_hide.clicked.connect(lambda _=False, tid=str(item.get("id") or ""): self._toggle_custom_type(tid))
                lay.addWidget(btn_hide)
                btn_del = QPushButton("Remover")
                btn_del.setObjectName("secondarySmall")
                btn_del.clicked.connect(lambda _=False, tid=str(item.get("id") or ""): self._remove_custom_type(tid))
                lay.addWidget(btn_del)
                box.addWidget(row)
        btn = QPushButton("Novo tipo de envio")
        btn.setObjectName("accent")
        btn.clicked.connect(self._add_custom_type)
        box.addWidget(btn)
        note = QLabel("Admin: até 8 campos por tipo, 8 cores fixas, variáveis validadas e prévia completa antes de salvar.")
        note.setObjectName("muted")
        note.setWordWrap(True)
        box.addWidget(note)
        return card

    def _custom_type_by_id(self, type_id: str) -> dict | None:
        for item in list(getattr(self.app_context.state.config, "custom_quote_types", []) or []):
            if isinstance(item, dict) and str(item.get("id") or "") == str(type_id):
                return item
        return None

    def _add_custom_type(self) -> None:
        self._open_custom_type_editor(None)

    def _edit_custom_type(self, type_id: str) -> None:
        self._open_custom_type_editor(self._custom_type_by_id(type_id))

    def _preview_custom_type(self, type_id: str) -> None:
        item = self._custom_type_by_id(type_id)
        if not item:
            return
        sample = self._sample_custom_preview(item)
        QMessageBox.information(self, "Prévia do tipo de envio", sample[:4000])

    def _sample_custom_preview(self, item: dict) -> str:
        fields = [f for f in item.get("fields", []) if isinstance(f, dict)] or [{"label":"Conteúdo","var":"CONTEUDO"}]
        values = {}
        lines = []
        for f in fields[:8]:
            var = str(f.get("var") or f.get("label") or "CAMPO").upper()
            label = str(f.get("label") or var)
            value = f"Exemplo de {label.lower()}"
            values[var] = value
            lines.append(f"{label}: {value}")
        values.setdefault("CONTEUDO", "\n".join(lines))
        company = company_for_key(getattr(self.app_context.state.config, "default_company_key", "vesper"))
        replacements = {
            "EMPRESA": getattr(company, "subject_prefix", "VESPER"),
            "TIPO": str(item.get("name") or "Envio personalizado"),
            "TITULO": "EXEMPLO",
            "CONTEUDO": values.get("CONTEUDO", ""),
            "ASSINATURA": "[assinatura selecionada]",
            "ASSUNTO": "[assunto original]",
            **values,
        }
        def apply(text: str) -> str:
            out = str(text or "")
            for k, v in replacements.items():
                out = out.replace("{" + k + "}", str(v))
            return out.strip()
        return "Assunto:\n" + apply(str(item.get("subject_template") or "")) + "\n\nCorpo:\n" + apply(str(item.get("body_template") or "")) + "\n\nCobrança:\n" + apply(str(item.get("followup_template") or ""))

    def _open_custom_type_editor(self, existing: dict | None) -> None:
        dialog = QDialog(self)
        dialog.setWindowTitle("Tipo de envio" if existing else "Novo tipo de envio")
        dialog.resize(scaled_px(780), scaled_px(720))
        root = QVBoxLayout(dialog)
        scroll = QScrollArea(dialog)
        scroll.setWidgetResizable(True)
        content = QWidget(scroll)
        form = QVBoxLayout(content)
        form.setSpacing(scaled_px(10))
        basic = QFrame(content); basic.setObjectName("dashboardCard")
        b = QFormLayout(basic)
        name = QLineEdit(str((existing or {}).get("name") or "")); name.setPlaceholderText("Ex.: Cotação de serviço")
        desc = QLineEdit(str((existing or {}).get("description") or "")); desc.setPlaceholderText("Ex.: Solicitar preço e prazo de serviço")
        icon = QComboBox(); icon.addItems(["material", "ex_panels", "freight", "purchase_order", "settings"]); icon.setCurrentText(str((existing or {}).get("icon") or "material"))
        color = QComboBox(); color.addItems(["blue", "green", "purple", "orange", "cyan", "indigo", "slate", "red"]); color.setCurrentText(str((existing or {}).get("color") or "blue"))
        active = QCheckBox("Aparecer na tela Nova cotação"); active.setChecked(bool((existing or {}).get("active", True)))
        b.addRow("Nome do card", name); b.addRow("Descrição", desc); b.addRow("Ícone", icon); b.addRow("Cor", color); b.addRow("Status", active)
        form.addWidget(basic)

        fields_card = QFrame(content); fields_card.setObjectName("dashboardCard")
        fbox = QVBoxLayout(fields_card)
        fbox.addWidget(QLabel("Campos do formulário — até 8"))
        field_rows = []
        raw_fields = (existing or {}).get("fields") if isinstance((existing or {}).get("fields"), list) else []
        if not raw_fields:
            raw_fields = [{"label":"Conteúdo","var":"CONTEUDO","required":True,"multiline":True,"placeholder":"Digite o conteúdo."}]
        for i in range(8):
            row = QHBoxLayout()
            label = QLineEdit(str(raw_fields[i].get("label") if i < len(raw_fields) and isinstance(raw_fields[i], dict) else "")); label.setPlaceholderText(f"Campo {i+1}")
            var = QLineEdit(str(raw_fields[i].get("var") if i < len(raw_fields) and isinstance(raw_fields[i], dict) else "")); var.setPlaceholderText("VARIAVEL")
            req = QCheckBox("Obrig."); req.setChecked(bool(raw_fields[i].get("required", True)) if i < len(raw_fields) and isinstance(raw_fields[i], dict) else i == 0)
            multi = QCheckBox("Texto grande"); multi.setChecked(bool(raw_fields[i].get("multiline", False)) if i < len(raw_fields) and isinstance(raw_fields[i], dict) else i == 0)
            row.addWidget(label, 2); row.addWidget(var, 1); row.addWidget(req, 0); row.addWidget(multi, 0)
            fbox.addLayout(row)
            field_rows.append((label, var, req, multi))
        form.addWidget(fields_card)

        tmpl = QFrame(content); tmpl.setObjectName("dashboardCard")
        tbox = QVBoxLayout(tmpl)
        subject = QLineEdit(str((existing or {}).get("subject_template") or "{EMPRESA} <> {TIPO} <> {TITULO}"))
        body = QTextEdit(str((existing or {}).get("body_template") or "Prezados,\n\nSolicito cotação conforme abaixo:\n\n{CONTEUDO}\n\nFico no aguardo.\n\n{ASSINATURA}")); body.setMinimumHeight(scaled_px(150))
        follow = QTextEdit(str((existing or {}).get("followup_template") or "Prezados,\n\nPoderiam, por gentileza, nos retornar sobre {TIPO}?\n\n{ASSUNTO}\n\nFico no aguardo.")); follow.setMinimumHeight(scaled_px(105))
        variable_combo = QComboBox(); variable_combo.addItems(["{EMPRESA}", "{TIPO}", "{TITULO}", "{CONTEUDO}", "{ASSINATURA}", "{ASSUNTO}"])
        btn_insert_subject = QPushButton("Inserir variável no assunto")
        btn_insert_body = QPushButton("Inserir variável no corpo")
        btn_insert_follow = QPushButton("Inserir variável na cobrança")
        btn_insert_subject.clicked.connect(lambda: subject.insert(variable_combo.currentText()))
        btn_insert_body.clicked.connect(lambda: body.insertPlainText(variable_combo.currentText()))
        btn_insert_follow.clicked.connect(lambda: follow.insertPlainText(variable_combo.currentText()))
        tbox.addWidget(QLabel("Assunto")); tbox.addWidget(subject)
        rowv = QHBoxLayout(); rowv.addWidget(variable_combo); rowv.addWidget(btn_insert_subject); rowv.addWidget(btn_insert_body); rowv.addWidget(btn_insert_follow); tbox.addLayout(rowv)
        tbox.addWidget(QLabel("Modelo do e-mail")); tbox.addWidget(body)
        tbox.addWidget(QLabel("Modelo de cobrança")); tbox.addWidget(follow)
        form.addWidget(tmpl)
        scroll.setWidget(content)
        root.addWidget(scroll, 1)
        actions = QHBoxLayout()
        btn_preview = QPushButton("Prévia")
        btn_save = QPushButton("Salvar tipo"); btn_save.setObjectName("accent")
        btn_cancel = QPushButton("Cancelar")
        actions.addStretch(1); actions.addWidget(btn_preview); actions.addWidget(btn_cancel); actions.addWidget(btn_save)
        root.addLayout(actions)

        def collect() -> tuple[dict | None, str]:
            nm = str(name.text() or "").strip()
            if not nm:
                return None, "Informe o nome do tipo."
            fields = []
            vars_seen = set()
            for label, var, req, multi in field_rows:
                lab = str(label.text() or "").strip()
                if not lab:
                    continue
                vv = str(var.text() or lab.upper()).strip().upper()
                vv = "".join(ch if ch.isalnum() else "_" for ch in vv).strip("_")[:32] or "CAMPO"
                if vv in vars_seen:
                    return None, f"Variável repetida: {{{vv}}}"
                vars_seen.add(vv)
                fields.append({"label": lab[:36], "var": vv, "required": req.isChecked(), "multiline": multi.isChecked(), "placeholder": ""})
            if not fields:
                return None, "Crie pelo menos um campo."
            allowed = {"EMPRESA", "TIPO", "TITULO", "CONTEUDO", "ASSINATURA", "ASSUNTO", *vars_seen}
            import re
            all_text = subject.text() + "\n" + body.toPlainText() + "\n" + follow.toPlainText()
            unknown = sorted({m.group(1).upper() for m in re.finditer(r"\{([A-Z0-9_]+)\}", all_text, re.I) if m.group(1).upper() not in allowed})
            if unknown:
                return None, "Variável não existe: " + ", ".join("{" + u + "}" for u in unknown)
            raw_id = str((existing or {}).get("id") or "").strip() or ''.join(ch if ch.isalnum() else '_' for ch in nm.lower()).strip('_') or 'tipo'
            return {
                "id": raw_id,
                "name": nm[:48],
                "description": str(desc.text() or "").strip()[:120] or "Envio personalizado.",
                "icon": icon.currentText(),
                "color": color.currentText(),
                "subject_template": subject.text().strip(),
                "body_template": body.toPlainText().strip(),
                "followup_template": follow.toPlainText().strip(),
                "fields": fields,
                "active": active.isChecked(),
            }, ""

        def preview() -> None:
            item, error = collect()
            if error:
                QMessageBox.warning(dialog, "Tipo de envio", error); return
            QMessageBox.information(dialog, "Prévia", self._sample_custom_preview(item)[:4000])
        btn_preview.clicked.connect(preview)
        btn_cancel.clicked.connect(dialog.reject)
        def save() -> None:
            item, error = collect()
            if error:
                QMessageBox.warning(dialog, "Tipo de envio", error); return
            cfg = self.app_context.state.config
            items = [it for it in list(getattr(cfg, "custom_quote_types", []) or []) if isinstance(it, dict)]
            existing_ids = {str(it.get("id") or "") for it in items if not existing or str(it.get("id") or "") != str(existing.get("id") or "")}
            base_id = str(item["id"])
            if base_id in existing_ids:
                n = 2
                while f"{base_id}_{n}" in existing_ids:
                    n += 1
                item["id"] = f"{base_id}_{n}"
            replaced = False
            for idx, it in enumerate(items):
                if existing and str(it.get("id") or "") == str(existing.get("id") or ""):
                    items[idx] = item; replaced = True; break
            if not replaced:
                items.append(item)
            cfg.custom_quote_types = items[:24]
            try: cfg.save()
            except Exception: pass
            dialog.accept()
        btn_save.clicked.connect(save)
        if dialog.exec():
            if self._on_status:
                self._on_status("Tipo de envio salvo.")
            self._rebuild_dynamic()

    def _toggle_custom_type(self, type_id: str) -> None:
        cfg = self.app_context.state.config
        for item in list(getattr(cfg, "custom_quote_types", []) or []):
            if isinstance(item, dict) and str(item.get("id") or "") == type_id:
                item["active"] = not bool(item.get("active", True))
                break
        try:
            cfg.save()
        except Exception:
            pass
        self._rebuild_dynamic()

    def _remove_custom_type(self, type_id: str) -> None:
        if QMessageBox.question(self, "Remover tipo", "Remover este tipo personalizado?", QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No, QMessageBox.StandardButton.No) != QMessageBox.StandardButton.Yes:
            return
        cfg = self.app_context.state.config
        cfg.custom_quote_types = [it for it in list(getattr(cfg, "custom_quote_types", []) or []) if not (isinstance(it, dict) and str(it.get("id") or "") == type_id)]
        try:
            cfg.save()
        except Exception:
            pass
        self._rebuild_dynamic()

    def _mapping_card(self) -> QFrame:
        cfg = self.app_context.state.config
        card = self._card("Assinatura automática", "O app escolhe a assinatura pelo usuário do Windows/PC.")
        box = card.layout()
        row = QHBoxLayout()
        mapping = dict(getattr(cfg, "signature_auto_map", {}) or {})
        if mapping:
            for user, owner in list(mapping.items())[:4]:
                row.addWidget(QLabel(f"{user}  →  {owner}"))
        else:
            row.addWidget(QLabel("Nenhum mapeamento configurado. O app usará a última assinatura escolhida."))
        box.addLayout(row)
        return card

    def _footer_card(self) -> QFrame:
        frame = QFrame(self)
        frame.setObjectName("dashboardFooter")
        row = QHBoxLayout(frame)
        row.setContentsMargins(scaled_px(14), scaled_px(8), scaled_px(14), scaled_px(8))
        row.addWidget(QLabel(f"Fornecedores: {supplier_count(self.app_context.state.index)}"))
        row.addWidget(QLabel(f"• Itens cadastrados: {item_count(self.app_context.state.index)}"))
        row.addStretch(1)
        return frame

    def _open_signature_dialog(self) -> None:
        from .signature_dialog import SignatureConfigDialog
        dialog = SignatureConfigDialog(self, self.app_context.state)
        if dialog.exec():
            if self._on_status:
                self._on_status("Assinaturas atualizadas.")
            self._rebuild_dynamic()
