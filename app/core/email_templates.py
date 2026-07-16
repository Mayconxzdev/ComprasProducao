from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Iterable

from app.core.companies import CompanyProfile


EMAIL_RE = re.compile(r"^[^@\s<>;,]+@[^@\s<>;,]+\.[^@\s<>;,]+$")


@dataclass(frozen=True)
class FreightCarrier:
    label: str
    email: str


DEFAULT_FREIGHT_CARRIERS: tuple[FreightCarrier, ...] = (
    FreightCarrier("Transportadora Horizonte", "cotacao@transportadora-horizonte.invalid"),
    FreightCarrier("Logística Ponto Sul", "atendimento@logistica-ponto-sul.invalid"),
    FreightCarrier("Carga Certa", "comercial@carga-certa.invalid"),
    FreightCarrier("Rota Industrial", "vendas@rota-industrial.invalid"),
)


def clean_text(value: object) -> str:
    return str(value or "").strip()


def dedupe_emails(emails: Iterable[str]) -> list[str]:
    out: list[str] = []
    seen: set[str] = set()
    for raw in emails:
        email = clean_text(raw).lower()
        if not email or email in seen:
            continue
        if not is_valid_email(email):
            continue
        seen.add(email)
        out.append(email)
    return out


def split_emails(raw: str) -> list[str]:
    text = clean_text(raw)
    if not text:
        return []
    chunks = re.split(r"[\s,;/]+", text)
    return dedupe_emails(chunks)


def is_valid_email(email: str) -> bool:
    return bool(EMAIL_RE.match(clean_text(email)))


def summarize_subject(text: str, max_len: int = 55) -> str:
    raw = clean_text(text)
    if not raw:
        return "MATERIAL"
    # Prefer the first useful line, then remove common field prefixes.
    first = next((line.strip() for line in raw.splitlines() if line.strip()), raw)
    first = re.sub(r"^(descri[cç][aã]o do material|material|produto|item)\s*:\s*", "", first, flags=re.I)
    first = re.sub(r"\s+", " ", first).strip(" -–:\t")
    if not first:
        first = "MATERIAL"
    if len(first) > max_len:
        first = first[: max_len - 3].rstrip() + "..."
    return first.upper()


def build_material_email(company: CompanyProfile, items_text: str, *, ex_required: bool = False) -> tuple[str, str]:
    summary = summarize_subject(items_text)
    subject = f"{company.subject_prefix} <> COTAÇÃO <> {summary}"
    ex_line = ""
    if ex_required:
        ex_line = "\n- Certificado ou documentação que comprove que o produto é adequado para utilização em área classificada ou para instalação em invólucro Ex;"
    body = f"""Prezados,

Solicito, por gentileza, o envio de cotação para o(s) item(ns) abaixo, conforme as especificações:

{clean_text(items_text)}

Solicito, por gentileza, que a proposta comercial contenha as seguintes informações:

- Valor unitário e valor total;
- Prazo de entrega;
- Condições de pagamento;
- Disponibilidade em estoque;
- Informações sobre o frete, se aplicável;{ex_line}

Endereço para entrega/coleta:

Av. Exemplo, 100
Centro – Cidade/UF
CEP: 00000-000

Horário de funcionamento:

Atendimento em horário comercial.

Desde já, agradeço a atenção e fico no aguardo do retorno."""
    return subject, body


def parse_freight_fields(raw_text: str) -> dict[str, str]:
    text = clean_text(raw_text)
    fields = {
        "descricao": "",
        "volumes": "",
        "peso": "",
        "valor_nf": "",
        "medidas": "",
    }
    if not text:
        return fields

    patterns = {
        "descricao": r"descri[cç][aã]o\s+do\s+material\s*:\s*(.+)",
        "volumes": r"quantidade\s+de\s+volumes\s*:\s*(.+)",
        "peso": r"peso\s+total\s*:\s*(.+)",
        "valor_nf": r"valor\s+da\s+nota\s+fiscal\s*:\s*(.+)",
        "medidas": r"medidas(?:\s*\([^)]*\))?\s*:\s*(.+)",
    }
    for line in text.splitlines():
        clean_line = line.strip()
        if not clean_line:
            continue
        for key, pattern in patterns.items():
            if fields[key]:
                continue
            match = re.search(pattern, clean_line, flags=re.I)
            if match:
                fields[key] = match.group(1).strip()
    return fields


def build_freight_email(
    company: CompanyProfile,
    *,
    descricao: str,
    volumes: str,
    peso: str,
    valor_nf: str,
    medidas: str,
    observacao: str = "",
    destino_observacao: str = "",  # compatibilidade com versões anteriores
) -> tuple[str, str]:
    desc = clean_text(descricao) or "MATERIAL"
    subject = f"{company.subject_prefix} <> COTAÇÃO DE FRETE <> {summarize_subject(desc)}"
    obs = clean_text(observacao) or clean_text(destino_observacao)
    obs_block = f"\n\nObservação:\n{obs}" if obs else ""
    body = f"""Prezados,

Por gentileza, cotar o frete abaixo:

CNPJ do pagador do frete:
{company.razao_social} – CNPJ: {company.cnpj}

Empresa remetente:

{company.razao_social}
CNPJ: {company.cnpj}
Endereço: {company.endereco_linha}
CEP: {company.cep}

Dados do material:

Descrição do material: {clean_text(descricao)}
Quantidade de volumes: {clean_text(volumes)}
Peso total: {clean_text(peso)}
Valor da nota fiscal: {clean_text(valor_nf)}
Medidas (comp x larg x alt): {clean_text(medidas)}{obs_block}

Fico no aguardo."""
    return subject, body


def build_purchase_order_email(
    company: CompanyProfile,
    *,
    supplier_name: str = "",
    oc_number: str,
    observacao: str = "",
) -> tuple[str, str]:
    oc = clean_text(oc_number) or ""
    subject = f"{company.subject_prefix} <> ORDEM DE COMPRA N° {oc}"
    obs = clean_text(observacao)
    obs_block = f"\n\nObservação:\n{obs}" if obs else ""
    body = f"""Prezados,

Segue em anexo nossa Ordem de Compra nº {oc}.

Solicitamos a emissão da Nota Fiscal e da Duplicata em nome de {company.razao_social} – CNPJ: {company.cnpj}, conforme a Ordem de Compra anexa.

Solicitamos, por gentileza, a confirmação do recebimento deste e-mail, bem como a confirmação dos seguintes itens:

- Prazo de entrega;
- Forma de pagamento;
- Disponibilidade em estoque.

Endereço para entrega e coleta:

Av. Exemplo, 100
Centro – Cidade/UF
CEP: 00000-000

Horário de funcionamento:

Em horário comercial{obs_block}

Ficamos no aguardo de seu retorno."""
    return subject, body
