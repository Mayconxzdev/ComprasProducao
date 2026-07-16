"""
Professional data models for multi-sheet XLSX with relational data
"""
from __future__ import annotations
from dataclasses import dataclass, field
from typing import List, Dict

@dataclass
class Item:
    """Represents an item/product from ITENS sheet"""
    item_id: str
    categoria: str
    subtipo: str
    item: str
    ativo: bool

    def __str__(self):
        return f"{self.categoria} - {self.item}" if self.categoria else self.item

@dataclass
class SupplierItemLink:
    """Represents link between supplier and item from FORNECEDOR_ITENS sheet"""
    link_id: str
    supplier_id: str
    item_id: str
    serv_corte: bool = False
    serv_dobra: bool = False
    serv_entrega: bool = False
    serv_retira: bool = False
    obs_link: str = ""
    prioridade: int = 0
    prazo_entrega_dias: int = 0

@dataclass
class SupplierPro:
    """Professional supplier with linked items and services"""
    supplier_id: str
    empresa: str
    contato: str = ""
    telefone: str = ""
    email: str = ""
    endereco: str = ""
    bairro: str = ""
    cidade: str = ""
    uf: str = ""
    obs: str = ""

    # Linked data (computed from joins)
    items: List[Item] = field(default_factory=list)
    links: List[SupplierItemLink] = field(default_factory=list)

    # Aggregated services (True if ANY link has this service)
    serv_corte: bool = False
    serv_dobra: bool = False
    serv_entrega: bool = False
    serv_retira: bool = False

    # Validation
    is_valid: bool = True
    invalid_reason: str = ""

    # Search tokens (computed)
    tokens: set = field(default_factory=set)

    # Origin tracking
    source_file: str = ""
    is_local: bool = False  # True if from local.db overlay

@dataclass
class Synonym:
    """Synonym mapping from TERMOS sheet"""
    termo: str  # Search term
    item_id: str  # Maps to Item.item_id

@dataclass
class ProDataSet:
    """Complete dataset from professional XLSX"""
    suppliers: Dict[str, SupplierPro]  # supplier_id -> SupplierPro
    items: Dict[str, Item]  # item_id -> Item
    links: List[SupplierItemLink]
    synonyms: List[Synonym]
    warnings: List[str] = field(default_factory=list)
