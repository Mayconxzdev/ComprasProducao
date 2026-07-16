from __future__ import annotations

"""Busca operacional de destinatários.

Este módulo fica fora da UI para que a mesma regra seja usada em Nova cotação,
Fornecedores e testes automatizados. A regra é propositalmente mais rígida que
um autocomplete comum: em compras, resultado irrelevante é pior do que nenhum
resultado, porque o usuário pode enviar uma cotação para o fornecedor errado.
"""

from dataclasses import dataclass
import re
import unicodedata
from typing import Iterable, Mapping, Sequence

try:
    from rapidfuzz import fuzz as _rapidfuzz
except Exception:  # pragma: no cover - fallback for minimal installs
    _rapidfuzz = None
    from difflib import SequenceMatcher

_EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
_WORD_RE = re.compile(r"[a-z0-9]+")
_WS_RE = re.compile(r"\s+")

STOPWORDS = {
    "a", "o", "as", "os", "e", "de", "da", "do", "das", "dos",
    "para", "por", "com", "em", "no", "na", "nos", "nas",
    # Termos empresariais comuns que atrapalham ranking de fornecedores.
    "ltda", "me", "eireli", "sa", "s", "comercio",
    "industrial", "industria", "empresa", "servicos", "servico",
}

SOURCE_WEIGHT = {
    "supplier_index": 90,
    "supplier": 90,
    "local_supplier": 80,
    "fornecedor": 80,
    "history": -80,
    "thunderbird": -80,
    "contact": -100,
    "contato": -100,
}

FIELD_WEIGHT = {
    "empresa": 420,
    "produto": 360,
    "email": 260,
    "contato_nome": 210,
    "telefone": 150,
}


@dataclass(frozen=True)
class RecipientSearchRow:
    empresa: str = ""
    email: str = ""
    contato_nome: str = ""
    telefone: str = ""
    produto: str = ""
    source: str = ""
    payload: object | None = None

    @classmethod
    def from_mapping(cls, row: Mapping[str, object]) -> "RecipientSearchRow":
        return cls(
            empresa=_clean(row.get("empresa")),
            email=_clean(row.get("email")),
            contato_nome=_clean(row.get("contato_nome") or row.get("contato") or row.get("contact")),
            telefone=_clean(row.get("telefone") or row.get("phone")),
            produto=_clean(row.get("produto") or row.get("product") or row.get("categoria") or row.get("material_produto")),
            source=_clean(row.get("source")),
            payload=row,
        )

    def to_dict(self) -> dict[str, str]:
        return {
            "empresa": self.empresa,
            "email": self.email,
            "contato_nome": self.contato_nome,
            "telefone": self.telefone,
            "produto": self.produto,
            "source": self.source,
        }


def _clean(value: object) -> str:
    return str(value or "").strip()


def normalize(value: object) -> str:
    text = _clean(value).casefold()
    text = unicodedata.normalize("NFKD", text)
    text = "".join(ch for ch in text if not unicodedata.combining(ch))
    # Mantém letras e números, mas separa pontuação, hífen, barra, @ e ponto.
    text = re.sub(r"[^a-z0-9]+", " ", text)
    return _WS_RE.sub(" ", text).strip()


def words(value: object) -> list[str]:
    return _WORD_RE.findall(normalize(value))


def meaningful_query_tokens(query: str) -> list[str]:
    tokens = [token for token in words(query) if token and token not in STOPWORDS]
    return [token for token in tokens if len(token) >= 2 or token.isdigit()]


def is_valid_email(email: str) -> bool:
    return bool(_EMAIL_RE.match(_clean(email)))


def _fuzzy_ratio(query: str, candidate: str) -> float:
    q = normalize(query)
    c = normalize(candidate)
    if not q or not c:
        return 0.0
    if _rapidfuzz is not None:
        try:
            return float(max(
                _rapidfuzz.WRatio(q, c, score_cutoff=0),
                _rapidfuzz.token_set_ratio(q, c, score_cutoff=0),
                _rapidfuzz.token_sort_ratio(q, c, score_cutoff=0),
            ))
        except Exception:
            return 0.0
    try:
        return float(SequenceMatcher(None, q, c).ratio() * 100.0)
    except Exception:
        return 0.0

def _best_business_fuzzy(query: str, row: RecipientSearchRow) -> tuple[str, float]:
    # Fuzzy só olha campos que representam o fornecedor/produto/e-mail.
    # Contato fica fora para não transformar “macho” em “Camacho”.
    fields = [row.empresa, row.produto, row.email]
    best_text = ""
    best_score = 0.0
    for text in fields:
        score = _fuzzy_ratio(query, text)
        if score > best_score:
            best_score = score
            best_text = str(text or "")
    return best_text, best_score


def _field_words(row: RecipientSearchRow) -> dict[str, list[str]]:
    return {
        "empresa": words(row.empresa),
        "produto": words(row.produto),
        "email": words(row.email),
        "contato_nome": words(row.contato_nome),
        "telefone": words(row.telefone),
    }


def _field_texts(row: RecipientSearchRow) -> dict[str, str]:
    return {
        "empresa": normalize(row.empresa),
        "produto": normalize(row.produto),
        "email": normalize(row.email),
        "contato_nome": normalize(row.contato_nome),
        "telefone": normalize(row.telefone),
    }


def _token_match(token: str, field_name: str, field_words: Sequence[str], *, last_token: bool) -> tuple[bool, int]:
    """Retorna (bateu, qualidade).

    Qualidade maior significa match mais confiável. Não aceitamos substring interna
    em empresa/produto/contato para evitar caso ruim: "macho" encontrar "Camacho".
    Em e-mail/telefone, substring interna é aceita porque o usuário costuma digitar
    pedaços de domínio, DDD ou número.
    """
    if not token:
        return False, 0
    if token in field_words:
        return True, 100
    if any(word.startswith(token) for word in field_words):
        # Prefixo é importante enquanto o usuário digita: "mac" -> "machos".
        return True, 92 if last_token else 78
    if field_name in {"email", "telefone"} and len(token) >= 3 and any(token in word for word in field_words):
        return True, 72
    return False, 0


def recipient_match_score(query: str, row_data: Mapping[str, object] | RecipientSearchRow) -> int:
    row = row_data if isinstance(row_data, RecipientSearchRow) else RecipientSearchRow.from_mapping(row_data)
    if not is_valid_email(row.email):
        return 0
    q = normalize(query)
    tokens = meaningful_query_tokens(query)
    if not q or not tokens:
        return 1

    field_texts = _field_texts(row)
    field_words = _field_words(row)
    all_text = " ".join(field_texts.values()).strip()
    if not all_text:
        return 0

    # Frase inteira: alta precisão. Isso faz "casa dos mac" encontrar "CASA DOS MACHOS"
    # mesmo ignorando "dos" como conectivo no token score.
    phrase_score = 0
    if len(q) >= 3:
        for field, text in field_texts.items():
            if not text:
                continue
            weight = FIELD_WEIGHT[field]
            if q == text:
                phrase_score = max(phrase_score, weight + 6500)
            elif text.startswith(q):
                phrase_score = max(phrase_score, weight + 5600)
            elif q in text:
                # Substring em contato é menos confiável; em empresa/produto/e-mail é mais útil.
                if field in {"empresa", "produto", "email"}:
                    phrase_score = max(phrase_score, weight + 4400)
                elif field == "contato_nome" and len(q) >= 6:
                    phrase_score = max(phrase_score, weight + 2200)

    # Token coverage: todos os termos úteis precisam bater em algum campo. Isso evita
    # resultado tipo "Casacivil" + "dos Santos" para busca "casa dos machos".
    matched_tokens: dict[str, tuple[str, int]] = {}
    for i, token in enumerate(tokens):
        best: tuple[str, int] | None = None
        for field, parts in field_words.items():
            ok, quality = _token_match(token, field, parts, last_token=(i == len(tokens) - 1))
            if not ok:
                continue
            weighted_quality = quality + FIELD_WEIGHT[field]
            if best is None or weighted_quality > best[1]:
                best = (field, weighted_quality)
        if best is not None:
            matched_tokens[token] = best

    # Busca composta exige todos os termos úteis, exceto quando empresa/produto/e-mail
    # tem correspondência fuzzy muito forte. Isso corrige digitação pequena sem misturar
    # contatos parecidos, como “macho” versus “Camacho”.
    fuzzy_text, fuzzy_phrase = _best_business_fuzzy(q, row)
    if len(tokens) >= 2 and len(matched_tokens) < len(tokens):
        if phrase_score >= 4200:
            pass
        elif len(q) >= 6 and fuzzy_phrase >= 86:
            return max(int(4200 + fuzzy_phrase * 18 + SOURCE_WEIGHT.get(normalize(row.source), 0)), 0)
        else:
            return 0
    if len(tokens) == 1 and not matched_tokens and not phrase_score:
        # Token único precisa ser mais rígido para não criar ruído em listas grandes.
        if len(q) >= 6 and fuzzy_phrase >= 92:
            return max(int(2500 + fuzzy_phrase * 14 + SOURCE_WEIGHT.get(normalize(row.source), 0)), 0)
        return 0

    score = phrase_score
    for _token, (field, quality) in matched_tokens.items():
        score += quality
        if field in {"empresa", "produto"}:
            score += 120

    # Bônus quando todos os termos batem dentro de empresa/produto. Essa é a regra que
    # coloca "CASA DOS MACHOS" acima de qualquer contato/e-mail parecido.
    if tokens and all(_token_match(t, "empresa", field_words["empresa"], last_token=(i == len(tokens) - 1))[0] for i, t in enumerate(tokens)):
        score += 1600
    if tokens and field_words["produto"] and all(_token_match(t, "produto", field_words["produto"], last_token=(i == len(tokens) - 1))[0] for i, t in enumerate(tokens)):
        score += 1300

    # Fuzzy complementa resultado que já bateu por token/frase, melhorando ordenação
    # sem deixar ruído passar como resultado principal.
    if score > 0 and fuzzy_phrase >= 78:
        score += int(fuzzy_phrase * 3)
        if fuzzy_text and normalize(fuzzy_text) == q:
            score += 450

    score += SOURCE_WEIGHT.get(normalize(row.source), 0)
    return max(int(score), 0)


def dedupe_recipient_rows(rows: Iterable[Mapping[str, object] | RecipientSearchRow]) -> list[RecipientSearchRow]:
    best: dict[str, RecipientSearchRow] = {}
    for item in rows:
        row = item if isinstance(item, RecipientSearchRow) else RecipientSearchRow.from_mapping(item)
        if not is_valid_email(row.email):
            continue
        key = normalize(row.email)
        if not key:
            continue
        current = best.get(key)
        if current is None:
            best[key] = row
            continue
        # Preserva a linha mais rica: empresa/produto/contato da base ganha de histórico.
        current_richness = sum(bool(x) for x in [current.empresa, current.produto, current.contato_nome, current.telefone]) + SOURCE_WEIGHT.get(normalize(current.source), 0) / 1000
        new_richness = sum(bool(x) for x in [row.empresa, row.produto, row.contato_nome, row.telefone]) + SOURCE_WEIGHT.get(normalize(row.source), 0) / 1000
        if new_richness > current_richness:
            best[key] = row
    return list(best.values())


def search_recipient_rows(
    rows: Iterable[Mapping[str, object] | RecipientSearchRow],
    query: str,
    *,
    selected_emails: Iterable[str] = (),
    limit: int = 10,
) -> list[dict[str, str]]:
    q = normalize(query)
    selected = {normalize(email) for email in selected_emails if email}
    base = dedupe_recipient_rows(rows)
    if not q:
        ordered = sorted(base, key=lambda row: (0 if normalize(row.email) in selected else 1, normalize(row.empresa), normalize(row.email)))
        return [row.to_dict() for row in ordered[: max(1, limit)]]

    scored: list[tuple[int, RecipientSearchRow]] = []
    for row in base:
        score = recipient_match_score(query, row)
        if score > 0:
            scored.append((score, row))
    scored.sort(key=lambda item: (0 if normalize(item[1].email) in selected else 1, -item[0], normalize(item[1].empresa), normalize(item[1].email)))
    return [row.to_dict() for _score, row in scored[: max(1, limit)]]
