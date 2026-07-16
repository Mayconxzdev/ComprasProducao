from __future__ import annotations
from dataclasses import dataclass
from typing import Dict, Optional
from .utils_text import header_key

@dataclass
class ColumnMap:
    empresa: int
    material_produto: int
    email: int
    telefone: Optional[int] = None
    contato_nome: Optional[int] = None
    endereco: Optional[int] = None
    bairro_cidade: Optional[int] = None

def _find_col(headers: Dict[str, int], aliases: list[str]) -> Optional[int]:
    for a in aliases:
        k = header_key(a)
        if k in headers:
            return headers[k]
    return None

def map_columns(header_row: list[str]) -> ColumnMap:
    # Build normalized header map -> index
    headers: Dict[str, int] = {}
    for idx, h in enumerate(header_row):
        if h is None:
            continue
        k = header_key(str(h))
        if not k:
            continue
        if k not in headers:
            headers[k] = idx

    empresa = _find_col(headers, ["EMPRESA", "RAZAO SOCIAL", "FORNECEDOR"])
    material = _find_col(headers, ["MATERIAL / PRODUTO", "MATERIAL", "PRODUTO", "MATERIAL PRODUTO"])
    email = _find_col(headers, ["EMAIL", "E-MAIL", "MAIL"])
    if empresa is None or material is None or email is None:
        missing = []
        if empresa is None: missing.append("EMPRESA")
        if material is None: missing.append("MATERIAL / PRODUTO")
        if email is None: missing.append("EMAIL")
        raise ValueError(f"Colunas obrigatórias ausentes: {', '.join(missing)}")

    telefone = _find_col(headers, ["TELEFONE", "FONE", "CELULAR", "WHATS", "WHATSAPP"])
    contato = _find_col(headers, ["NOME DO CONTATO", "CONTATO", "NOME", "RESPONSAVEL"])
    endereco = _find_col(headers, ["ENDEREÇO", "ENDERECO", "RUA", "LOGRADOURO"])
    bairro_cidade = _find_col(headers, ["BAIRRO / CIDADE", "BAIRRO CIDADE", "CIDADE", "BAIRRO", "MUNICIPIO"])

    return ColumnMap(
        empresa=empresa,
        material_produto=material,
        email=email,
        telefone=telefone,
        contato_nome=contato,
        endereco=endereco,
        bairro_cidade=bairro_cidade,
    )
