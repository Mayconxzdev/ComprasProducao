from app.core.recipient_search import recipient_match_score, search_recipient_rows

ROWS = [
    {
        "empresa": "Casa das Máquinas Demo",
        "contato_nome": "Operador A",
        "email": "vendas@casa-maquinas-demo.invalid",
        "telefone": "+55 11 4000-1000",
        "produto": "Ferramentas/Abrasivos",
        "source": "supplier_index",
    },
    {
        "empresa": "Saneamento Modelo",
        "contato_nome": "Operador B",
        "email": "contato@saneamento-modelo.invalid",
        "telefone": "",
        "produto": "Serviços de saneamento",
        "source": "contact",
    },
    {
        "empresa": "Bazar Casa Demo",
        "contato_nome": "",
        "email": "contato@bazar-casa-demo.invalid",
        "telefone": "+55 21 4000-2000",
        "produto": "Tapetes",
        "source": "supplier_index",
    },
    {
        "empresa": "Casa da Borracha Demo",
        "contato_nome": "",
        "email": "contato@borracha-demo.invalid",
        "telefone": "",
        "produto": "Borrachas e vedacoes",
        "source": "supplier_index",
    },
]


def names(query: str):
    return [row["empresa"] for row in search_recipient_rows(ROWS, query, limit=10)]


def test_search_phrase_partial_company():
    assert names("casa das maq")[0] == "Casa das Máquinas Demo"
    assert "Saneamento Modelo" not in names("casa das maq")


def test_search_single_token_prefers_real_company_over_inner_contact():
    results = names("maquinas")
    assert results[0] == "Casa das Máquinas Demo"
    assert "Saneamento Modelo" not in results


def test_search_by_contact_email_and_product():
    assert names("operador a")[0] == "Casa das Máquinas Demo"
    assert names("vendas@casa-maquinas")[0] == "Casa das Máquinas Demo"
    assert names("ferramentas abrasivos")[0] == "Casa das Máquinas Demo"


def test_search_broad_house_words_are_not_confused_with_specific_supplier():
    casa_results = names("casa")
    assert "Casa da Borracha Demo" in casa_results
    assert "Bazar Casa Demo" in casa_results
    assert "Casa das Máquinas Demo" in casa_results
    assert "Saneamento Modelo" not in casa_results


def test_score_zero_for_bad_inner_substring_contact():
    assert recipient_match_score("maquinas", ROWS[1]) == 0
    assert recipient_match_score("maquinas", ROWS[0]) > 0


def test_search_tolerates_small_typos_in_company():
    assert names("casa das maquina")[0] == "Casa das Máquinas Demo"
    assert names("ferramentas abrassivos")[0] == "Casa das Máquinas Demo"
