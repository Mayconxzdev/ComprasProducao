from email.message import Message

from app.core.companies import CompanyProfile, COMPANIES
from app.core.imap_monitor import _reply_ref_from_message, _match_reply_to_event
from app.core.dashboard_insights import visible_history_rows


# 1. Teste de display_name em CompanyProfile
def test_company_profile_display_name():
    profile = CompanyProfile(
        key="test_key",
        label="Test Company",
        smtp_profile="test_smtp",
        email="test@empresa-a.invalid",
        razao_social="Test Social",
        cnpj="00.000.000/0001-00",
        endereco_linha="Street 1",
        cep="00000-000",
        subject_prefix="COTACAO",
    )
    assert profile.display_name == "Test Company"
    assert COMPANIES["vesper"].display_name == "Empresa A"


# 2. Teste de identificação de tracking_id pelo monitor IMAP
def test_imap_monitor_reply_ref_extraction():
    subject = "Re: Solicitação de Cotação CV-2026-000123"
    body = "Prezados,\nSegue anexo o orçamento solicitado."

    # Rastreamento pelo assunto
    ref = _reply_ref_from_message(subject, body)
    assert ref == "CV-2026-000123"

    # Rastreamento pelo corpo
    subject_no_ref = "Re: Solicitação de Cotação"
    body_with_ref = "Algum texto no corpo\nRef. interna: CV-2026-AB12CD34\nFim."
    ref2 = _reply_ref_from_message(subject_no_ref, body_with_ref)
    assert ref2 == "CV-2026-AB12CD34"

    # Rastreamento pelo header In-Reply-To ou References
    msg = Message()
    msg["In-Reply-To"] = "<CV-2026-999999.1@empresa-a.invalid>"
    ref3 = _reply_ref_from_message("Re: Assunto", "Corpo", msg)
    assert ref3 == "CV-2026-999999"


# 3. Teste de pareamento IMAP
def test_match_reply_to_event():
    events = [
        {
            "event_id": "ev_1",
            "ts": "2026-07-07T10:00:00",
            "event_type": "material_request",
            "status": "sent",
            "product_query": "chapa",
            "subject": "Solicitação de cotação de chapa",
            "recipients": [{"empresa": "Fornecedor 1", "email": "fornecedor-1@teste.invalid"}],
            "extra": {"rfq_id": "CV-2026-000001"}
        }
    ]

    # Match por Referência exata
    matched, by = _match_reply_to_event(
        sender="fornecedor-1@teste.invalid",
        subject="Re: Resposta",
        body="Ref. interna: CV-2026-000001",
        date_iso="2026-07-07T12:00:00",
        events=events
    )
    assert matched is not None
    assert matched["event_id"] == "ev_1"
    assert by == "referencia"


# 4. Teste de isolamento de histórico por arquivamento explícito
class MockHistoryStore:
    def __init__(self, events):
        self.events = events

    def get_global_history(self, query):
        return self.events


def test_history_explicit_archive_state():
    events = [
        {
            "event_id": "ev_archived",
            "ts": "2026-07-06T15:00:00",
            "event_type": "material_request",
            "status": "sent",
            "product_query": "chapa",
            "subject": "Cotação Arquivada",
            "recipients": [{"empresa": "A", "email": "a@empresa-teste.invalid"}],
            "extra": {"is_archived": True, "archived_at": "2026-07-07T16:00:00"},
        },
        {
            "event_id": "ev_active_old",
            "ts": "2026-07-06T15:00:00",
            "event_type": "material_request",
            "status": "sent",
            "product_query": "chapa antiga ativa",
            "subject": "Cotação Antiga Ativa",
            "recipients": [{"empresa": "A", "email": "a@empresa-teste.invalid"}],
        },
        {
            "event_id": "ev_new",
            "ts": "2026-07-07T09:00:00",
            "event_type": "material_request",
            "status": "sent",
            "product_query": "tubo",
            "subject": "Cotação Nova",
            "recipients": [{"empresa": "B", "email": "b@empresa-teste.invalid"}],
        },
    ]

    history_store = MockHistoryStore(events)

    active_rows = visible_history_rows(history_store, include_archived=False)
    assert {row["event_id"] for row in active_rows} == {"ev_active_old", "ev_new"}

    archived_rows = visible_history_rows(history_store, include_archived=True)
    assert len(archived_rows) == 1
    assert archived_rows[0]["event_id"] == "ev_archived"

from app.core.response_analyzer import extract_commercial_table, quote_quality_label


def test_response_analyzer_detects_price_deadline_and_payment():
    text = 'Flange aço carbono 2" - R$ 123,45\nPrazo: pronta entrega\nPagamento: 28 ddl'
    rows = extract_commercial_table(text)
    assert rows
    assert rows[0]["preco"] == "R$ 123,45"
    assert "pronta entrega" in rows[0]["prazo"].lower()
    assert "28" in rows[0]["pagamento"]
    assert quote_quality_label(text) == "Cotação válida provável"


def test_response_analyzer_keeps_pending_reply_invalid():
    assert quote_quality_label("Bom dia, vou verificar e retorno em breve.") == "Sem cotação válida"


def test_response_analyzer_ignores_quoted_original_request_dates_and_stock():
    from app.core.response_analyzer import extract_commercial_table, quote_quality_label, split_supplier_reply

    text = """ESTA 15,00 REAIS

POREM 10 UNIDADES SAIR A 120,00 REAIS
Em 08/07/2026 16:46, compras@empresa-b.invalid escreveu:
> Prezados,
> - Disponibilidade em estoque;
> Horário de funcionamento:
> Segunda a quinta-feira: das 8h00 às 17h30;
> Ref. interna: CV-2026-2588431F
"""
    answer, quoted = split_supplier_reply(text)
    assert "CHAPA" not in answer
    assert "08/07" in quoted
    rows = extract_commercial_table(text)
    assert [r["preco"] for r in rows] == ["R$ 15,00", "R$ 120,00"]
    assert rows[0]["prazo"] == "não informado"
    assert rows[0]["pagamento"] == "não informado"
    assert rows[1]["item"] == "10 unidade(s)"
    assert quote_quality_label(text) == "Cotação válida provável"


def test_response_analyzer_does_not_treat_calendar_date_as_payment():
    from app.core.response_analyzer import extract_commercial_table

    rows = extract_commercial_table("Valor unitário R$ 10,00\nEm 08/07/2026 alguém escreveu:")
    assert rows[0]["pagamento"] == "não informado"
