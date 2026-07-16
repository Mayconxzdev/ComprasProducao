from __future__ import annotations

from dataclasses import dataclass
from typing import Dict


@dataclass(frozen=True)
class CompanyProfile:
    key: str
    label: str
    smtp_profile: str
    email: str
    razao_social: str
    cnpj: str
    endereco_linha: str
    cep: str
    subject_prefix: str

    @property
    def display_name(self) -> str:
        return self.label


COMPANIES: Dict[str, CompanyProfile] = {
    "vesper": CompanyProfile(
        key="vesper",
        label="Empresa A",
        smtp_profile="vesper",
        email="compras@empresa-a.invalid",
        razao_social="Empresa A Equipamentos Ltda.",
        cnpj="00.000.000/0001-00",
        endereco_linha="Av. Exemplo, 100 - Centro - Cidade/UF",
        cep="00000-000",
        subject_prefix="EMPRESA A",
    ),
    "ventrio": CompanyProfile(
        key="ventrio",
        label="Empresa B",
        smtp_profile="ventrio",
        email="compras@empresa-b.invalid",
        razao_social="Empresa B Industrial Ltda.",
        cnpj="11.111.111/0001-11",
        endereco_linha="Rua Modelo, 200 - Distrito Industrial - Cidade/UF",
        cep="11111-111",
        subject_prefix="EMPRESA B",
    ),
}


def normalize_company_key(value: str | None) -> str:
    raw = str(value or "").strip().lower().replace(" ", "").replace("_", "")
    if raw in {"ventrio", "ventrioequipamentos", "producaoventrio", "producao"}:
        return "ventrio"
    if raw in {"vesper", "vesperequipamentos"}:
        return "vesper"
    return "vesper" if raw not in COMPANIES else raw


def company_for_key(value: str | None) -> CompanyProfile:
    return COMPANIES[normalize_company_key(value)]
