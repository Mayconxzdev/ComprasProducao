from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def read(rel: str) -> str:
    return (ROOT / rel).read_text(encoding="utf-8")


def test_recipient_card_selection_is_not_checkbox_only():
    src = read("app/qt/pages/new_request_page.py")
    assert "class RecipientRowFrame" in src
    assert "wrapper.clicked.connect(self._toggle_recipient_from_card)" in src
    assert "WA_TransparentForMouseEvents" in src
    assert "def _toggle_recipient_from_card" in src


def test_suppliers_viewport_click_toggles_row_once():
    src = read("app/qt/pages/suppliers_page.py")
    assert "self.table.viewport().installEventFilter(self)" in src
    assert "def _handle_view_mouse_release" in src
    assert "self._toggle_proxy_row_selection(index.row())" in src
    assert "return True" in src


def test_dark_theme_has_explicit_hover_and_checked_states():
    src = read("app/qt/theme/theme_manager.py")
    assert "Tema escuro 3.6" in src
    assert "QFrame#recipientRow:hover QLabel#recipientEmail" in src
    assert "QFrame#recipientRow[checked=\"true\"] QLabel#recipientEmail" in src
    assert "QListWidget#recipientList::item:hover" in src


def test_recipient_toggle_does_not_rebuild_list_for_regular_recipients():
    src = read("app/qt/pages/new_request_page.py")
    assert "def _refresh_recipient_selection_state" in src
    toggle_block = src.split("def _toggle_recipient(", 1)[1].split("def _refresh_recipient_selection_state", 1)[0]
    assert "_refresh_recipient_selection_state(target_key=key)" in toggle_block
    # Frete usa itens nativos e pode refazer apenas a lista pequena de transportadoras;
    # destinatários comuns continuam sem reconstrução pesada nem prévia.
    assert "else:\n            self._refresh_recipient_selection_state(target_key=key)" in toggle_block
    assert "_refresh_preview()" not in toggle_block


def test_file_lock_is_centralized_and_cross_platform():
    assert "def cross_process_file_lock" in read("app/core/file_lock.py")
    assert "import fcntl" in read("app/core/file_lock.py")
    for rel in [
        "app/core/history_store.py",
        "app/core/config_sync.py",
        "app/core/supplier_meta_store_nas.py",
        "app/core/xlsx_master_writer.py",
    ]:
        assert "from .file_lock import cross_process_file_lock" in read(rel)


def test_recipient_card_has_no_duplicate_email_label_creation():
    src = read("app/qt/pages/new_request_page.py")
    assert src.count('mail_label = QLabel("  •  ".join(meta_parts) if meta_parts else email)') == 1


def test_operational_shell_has_no_legacy_quote_window():
    src = read("app/qt/main_window.py")
    assert "class _QuoteWindow" not in src
    assert "QuotePage" not in src
    assert "DashboardPage" not in src
    assert "Home legada" not in src


def test_suppliers_bulk_selection_is_batched():
    src_model = read("app/qt/models/supplier_table_model.py")
    src_page = read("app/qt/pages/suppliers_page.py")
    assert "def set_selected_rows" in src_model
    assert "selectedEmailsChanged.emit" in src_model
    assert "def _set_visible_selected" in src_page
    assert "self._model.set_selected_rows(source_rows, selected)" in src_page


def test_unused_legacy_quote_chat_page_removed():
    assert not (ROOT / "app/qt/pages/quote_chat_page.py").exists()


def test_material_typing_does_not_refresh_recipient_panel_or_steal_focus():
    src = read("app/qt/pages/new_request_page.py")
    schedule_block = src.split("def _schedule_analysis", 1)[1].split("def _schedule_suggestions", 1)[0]
    assert "_schedule_suggestions" not in schedule_block
    analysis_block = src.split("def _analyze_and_refresh", 1)[1].split("def _apply_analysis_to_fields", 1)[0]
    assert "had_material_focus = self.smart_input.hasFocus()" in analysis_block
    assert "not had_material_focus" in analysis_block
    assert "self.smart_input.setFocus" in analysis_block
    assert "_refresh_supplier_suggestions" not in analysis_block
    assert "self._refresh_drop_zones()" not in analysis_block


def test_recipient_search_is_explicit_not_material_driven():
    src = read("app/qt/pages/new_request_page.py")
    rows_block = src.split("def _recipient_rows", 1)[1].split("def _default_search_text", 1)[0]
    assert "effective_query = clean_text(query)" in rows_block
    assert "_default_search_text" not in rows_block
    default_block = src.split("def _default_search_text", 1)[1].split("def _populate_recipient_list", 1)[0]
    assert "return \"\"" in default_block


def test_runtime_package_cleaning_and_version_contract():
    assert read("version.txt").startswith("4.8.0")
    assert "app.tools.static_clean_audit" in read("app/tools/quality_gate.py")
    assert "def main()" in read("app/tools/clean_runtime_artifacts.py")


def test_global_visual_polish_is_applied_to_all_loaded_pages():
    src_theme = read("app/qt/theme/theme_manager.py")
    src_main = read("app/qt/main_window.py")
    assert "def _apply_global_layout_metrics" in src_theme
    assert "card_names" in src_theme and "row_names" in src_theme and "summaryCell" in src_theme
    assert "QFrame#dashboardCard" in src_theme and "QFrame#recipientRow" in src_theme
    assert "QTimer.singleShot(0, self.theme_manager.repolish)" in src_main


def test_response_browser_uses_global_theme_detection_not_widget_base_only():
    src = read("app/qt/pages/history_page.py")
    block = src.split("def _browser_html_colors", 1)[1].split("def _render_response_preview", 1)[0]
    assert "QApplication.instance()" in block
    assert "QPalette.ColorRole.Window" in block
    assert "QPalette.ColorRole.Base" not in block


def test_deep_global_visual_hardening_for_top_combos_and_popups():
    src = read("app/qt/theme/theme_manager.py")
    assert "QComboBox#topCombo:hover" in src
    assert "QFrame#topControlWrap QComboBox#topCombo" in src
    assert "QComboBox#topCombo::drop-down" in src
    assert "QListView#comboPopup::item:hover" in src
    assert "background: @INPUT@" in src and "color: @TEXT@" in src
    main = read("app/qt/main_window.py")
    assert "box.setContentsMargins(scaled_px(12), scaled_px(8), scaled_px(12), scaled_px(10))" in main


def test_no_inline_light_preview_styles_remaining():
    src = read("app/qt/pages/models_signatures_page.py")
    assert "background-color: #f8f9fa" not in src
    assert 'browser.setObjectName("previewBrowser")' in src


def test_history_page_auto_sync_and_clean_reply_view_contract():
    src = read("app/qt/pages/history_page.py")
    assert "def _auto_sync_if_stale" in src
    assert "self._sync_replies_now(auto=True)" in src
    assert "split_supplier_reply" in src
    assert "Histórico citado ocultado" in src
    assert "Resposta do fornecedor" in src

def test_imap_monitor_uses_uid_range_after_last_uid_contract():
    src = read("app/core/imap_monitor.py")
    assert 'client.uid("search", None, f"UID {scan_floor + 1}:*")' in src
    assert 'client.uid("fetch", uid, "(BODY.PEEK[HEADER.FIELDS' in src


def test_history_page_master_detail_splitter_layout_contract():
    src = read("app/qt/pages/history_page.py")
    assert "self.tracking_splitter = QSplitter(Qt.Orientation.Horizontal" in src
    assert "self.left_splitter = QSplitter(Qt.Orientation.Vertical" in src
    assert "self.response_splitter = QSplitter(Qt.Orientation.Vertical" in src
    assert "trackingListPanel" in src
    assert "trackingContextPanel" in src
    assert "responseCard" in src
    assert "analysisCard" in src
    assert "self.analysis_browser" in src
    assert "self.tracking_splitter.setSizes" in src
    assert "self.left_splitter.setSizes" in src
    assert "self.response_splitter.setSizes" in src
    render_block = src.split("def _render_response_preview", 1)[1].split("def _on_response_link_clicked", 1)[0]
    assert "self.response_browser.setHtml" in render_block
    assert "self.analysis_browser.setHtml" in render_block
    assert "Dados encontrados" in src


def test_history_page_primary_actions_are_reduced_contract():
    src = read("app/qt/pages/history_page.py")
    build_block = src.split("def _build_ui", 1)[1].split("def _set_filter", 1)[0]
    assert 'QPushButton("Copiar e-mails")' not in build_block
    assert 'QPushButton("Registrar resposta")' not in build_block
    assert 'QPushButton("Reabrir cotação")' not in build_block
    assert 'self.btn_followup.setObjectName("quietAction")' in build_block
    assert 'self.btn_archive.setObjectName("primaryAction")' in build_block


def test_history_page_left_panel_has_three_scrollable_sections_and_full_email_action():
    src = read("app/qt/pages/history_page.py")
    assert "trackingRecipientsPanel" in src
    assert "trackingContextScroll" in src
    assert "self.left_splitter.addWidget(self.recipients_panel)" in src
    assert "self.recipients_count_label" in src
    assert "Ver e-mail completo" in src
    assert "def _show_full_email_dialog" in src
    assert "self.btn_full_email.setEnabled(True)" in src
    assert "history/left_splitter" in src and "saveState()" in src and "restoreState" in src


def test_ex_panels_uses_explicit_new_panel_button_not_menu_arrow():
    src = read("app/qt/pages/new_request_page.py")
    assert 'self.btn_new_ex_panel = QPushButton("Painel novo")' in src
    assert "self.btn_more_options" not in src
    assert "Cadastrar painel novo" not in src


def test_global_visual_final_rules_cover_sidebar_sendbar_and_combo_popup():
    src = read("app/qt/theme/theme_manager.py")
    assert "4.1.0: correção global final" in src or "4.2.0" in src
    assert 'QPushButton#sideNavButton[active="false"]' in src
    assert "QFrame#sendBar, QFrame#actionBar" in src
    assert "QFrame#trackingRecipientsPanel" in src
    assert "QComboBox QAbstractItemView" in src


def test_freight_opening_is_born_in_target_mode_and_has_no_temp_row_parent():
    src = read("app/qt/main_window.py")
    block = src[src.index("    def _open_task"):src.index("    def _refresh_nav_state")]
    assert "self._pending_composer_request_type = request_type" in block
    assert block.index("set_request_type_public(request_type)") < block.index('self._set_page("composer")')
    assert "initial_request_type=self._pending_composer_request_type" in src
    page_src = read("app/qt/pages/new_request_page.py")
    assert "Preparando transportadoras" not in page_src
    assert "QTimer.singleShot(60, self._refresh_supplier_suggestions)" not in page_src
    assert "RecipientRowFrame(email=email, row=row, checked=checked, parent=None)" not in page_src
    assert "parent=self.supplier_results.viewport()" in page_src
    assert "self._freight_refresh_timer" in page_src


def test_setup_prewarm_syncs_master_and_custom_types_sync():
    prewarm = read("app/tools/prewarm.py")
    assert "from app.core.config_sync import sync_from_master" in prewarm
    assert "sync_from_master(cfg)" in prewarm
    sync = read("app/core/config_sync.py")
    assert '"custom_quote_types"' in sync
    assert '"custom_quote_types": list(config.custom_quote_types or [])' in sync
    assert "config.custom_quote_types = clean_types[:24]" in sync


def test_freight_opens_without_default_carriers_and_uses_manual_action():
    src = read("app/qt/pages/new_request_page.py")
    assert "self._carrier_selected: set[str] = set()" in src
    assert "btn_add_default_carriers" in src
    assert "def _add_default_freight_carriers" in src
    assert "Adicionar padrão" in src
    assert "Frete abre leve" in src
    assert "Nenhuma transportadora selecionada" in src


def test_supplier_duplicate_logs_are_normalized_and_not_warning_spam():
    src = read("app/core/data_merger.py")
    assert "def _email_key" in src
    assert ".casefold()" in src
    assert 'logger.debug("Duplicate email in master' in src
    assert "Supplier email dedupe" in src
    assert 'logger.warning(f"Duplicate email' not in src


def test_freight_defaults_render_delegate_cards_not_widget_batch():
    src = read("app/qt/pages/new_request_page.py")
    assert "class FreightRecipientDelegate" in src
    assert "ROLE_FREIGHT_CARD" in src
    assert "self.supplier_results.setItemDelegate(FreightRecipientDelegate" in src
    add_block = src.split("def _add_default_freight_carriers", 1)[1].split("def _refresh_freight_defaults_button", 1)[0]
    assert "self._carrier_selected.update(self._default_freight_carrier_keys())" in add_block
    assert "self._refresh_supplier_suggestions()" in add_block
    populate_block = src.split("def _populate_recipient_list", 1)[1].split("def _recipient_row_widget", 1)[0]
    assert "if self._request_type == REQUEST_FREIGHT" in populate_block
    assert "item.setData(FreightRecipientDelegate.ROLE_FREIGHT_CARD, True)" in populate_block
    assert "self.supplier_results.setItemWidget(item, widget)" in populate_block
    assert "Card visual por delegate" in src


def test_freight_search_uses_global_recipient_base():
    src = read("app/qt/pages/new_request_page.py")
    carrier_block = src.split("def _carrier_rows", 1)[1].split("def _is_reliable_query", 1)[0]
    assert "self._search_recipient_rows(query)" in carrier_block
    assert "Fornecedor da base" in carrier_block
    assert "Buscar transportadora, fornecedor ou e-mail" in src
    toggle_block = src.split("def _toggle_recipient(", 1)[1].split("def _refresh_recipient_selection_state", 1)[0]
    assert '"kind": "supplier"' in toggle_block
    assert "self._extra_carriers[key]" in toggle_block
