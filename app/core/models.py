from __future__ import annotations
from dataclasses import dataclass, field
from typing import List, Optional

@dataclass
class SupplierOrigin:
    source_file: str
    source_row: int

@dataclass
class Supplier:
    empresa: str
    material_produto: str
    email: str
    telefone: str = ""
    contato_nome: str = ""
    endereco: str = ""
    bairro_cidade: str = ""
    # Derived / internal
    origins: List[SupplierOrigin] = field(default_factory=list)
    tokens: set[str] = field(default_factory=set)
    # Validation
    is_valid: bool = True
    invalid_reason: str = ""

@dataclass
class QuoteItem:
    line_text: str

@dataclass
class QuoteRecipient:
    empresa: str
    contato_nome: str
    email: str
    telefone: str
    material_produto: str
    source_file: str
    source_row: int

@dataclass
class Quote:
    id: Optional[int]
    created_at: str
    product_query: str
    subject: str
    body: str
    user_pc: str
    status: str
    items: List[QuoteItem] = field(default_factory=list)
    recipients: List[QuoteRecipient] = field(default_factory=list)
