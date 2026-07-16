from __future__ import annotations
import sqlite3
from difflib import SequenceMatcher
from dataclasses import dataclass
from typing import Dict, List, Optional
from .models import Supplier
from .utils_text import normalize_text, token_set

@dataclass
class SearchResult:
    supplier: Supplier
    score: int

class SupplierIndex:
    def __init__(self) -> None:
        self._by_email: Dict[str, Supplier] = {}
        self._all: List[Supplier] = []
        self._unique_products: set[str] = set()  # Para autocomplete
        self._fts_conn = None

    def clear(self) -> None:
        self._by_email.clear()
        self._all.clear()
        self._unique_products.clear()
        self._fts_conn = None

    @property
    def suppliers(self) -> List[Supplier]:
        return list(self._all)

    @property
    def unique_products(self) -> list[str]:
        """Retorna lista única de produtos para autocomplete"""
        return sorted(self._unique_products)

    def add_many(self, suppliers: List[Supplier]) -> None:
        for s in suppliers:
            key = normalize_text(s.email)
            if not key:
                continue

            # Coletar produtos únicos para autocomplete
            if s.material_produto:
                # Dividir produtos compostos (separados por |)
                parts = s.material_produto.split("|")
                for part in parts:
                    clean = part.strip()
                    if clean:
                        self._unique_products.add(clean)

            if key in self._by_email:
                existing = self._by_email[key]
                existing.origins.extend(s.origins)
                existing.tokens |= s.tokens
                if s.material_produto and s.material_produto not in existing.material_produto:
                    existing.material_produto = f"{existing.material_produto} | {s.material_produto}"
            else:
                self._by_email[key] = s
        self._all = list(self._by_email.values())
        self._rebuild_fts()

    def get_by_email(self, email: str) -> Optional[Supplier]:
        return self._by_email.get(normalize_text(email))

    def search(self, query: str) -> list[Supplier]:
        """
        Busca fornecedores por produto/empresa/contato/email.
        Suporta busca multi-produto com vírgulas: "chapa inox, tubo pvc"
        Retorna fornecedores que têm QUALQUER um dos produtos (lógica OR)
        """
        if not query.strip():
            return self.suppliers

        # Detectar múltiplos produtos (separados por vírgula)
        if "," in query:
            # Busca multi-produto
            terms = [t.strip() for t in query.split(",") if t.strip()]
            results_dict = {}  # email -> Supplier (para evitar duplicatas)

            for term in terms:
                term_results = self._search_single(term)
                for s in term_results:
                    email_key = normalize_text(s.email)
                    if email_key not in results_dict:
                        results_dict[email_key] = s

            return list(results_dict.values())
        else:
            # Busca simples (único produto)
            return self._search_single(query)

    def _rebuild_fts(self) -> None:
        try:
            conn = sqlite3.connect(":memory:")
            conn.execute("CREATE VIRTUAL TABLE suppliers_fts USING fts5(email UNINDEXED, empresa, produto, contato, telefone)")
            conn.executemany(
                "INSERT INTO suppliers_fts(email, empresa, produto, contato, telefone) VALUES (?,?,?,?,?)",
                [(s.email, s.empresa, s.material_produto, s.contato_nome, s.telefone) for s in self._all],
            )
            conn.commit()
            self._fts_conn = conn
        except Exception:
            self._fts_conn = None

    def _supplier_text(self, s: Supplier) -> str:
        return " ".join([s.material_produto or "", s.empresa or "", s.contato_nome or "", s.email or "", s.telefone or ""])

    def _search_single(self, query: str) -> list[Supplier]:
        """Busca por termo único com prioridade para produto, FTS5 e fuzzy opcional."""
        q_norm = normalize_text(query)
        q_tokens = token_set(query)
        if not q_tokens:
            return self.suppliers

        scores: dict[str, tuple[Supplier, float]] = {}

        def add(s: Supplier, score: float) -> None:
            key = normalize_text(s.email)
            if not key:
                return
            current = scores.get(key)
            if current is None or score > current[1]:
                scores[key] = (s, score)

        # 1) SQLite FTS5 quando disponível: melhor para pesquisa textual rápida por produto.
        if self._fts_conn is not None:
            try:
                fts_query = " OR ".join(t + "*" for t in sorted(q_tokens) if t)
                for row in self._fts_conn.execute(
                    "SELECT email, bm25(suppliers_fts, 0.1, 1.5, 2.6, 0.8, 0.5) AS rank FROM suppliers_fts WHERE suppliers_fts MATCH ? ORDER BY rank LIMIT 120",
                    (fts_query,),
                ):
                    supplier = self.get_by_email(str(row[0]))
                    if supplier is not None:
                        add(supplier, 140 - float(row[1] or 0))
            except Exception:
                pass

        try:
            from rapidfuzz import fuzz  # type: ignore
            def fuzzy(a: str, b: str) -> float:
                return float(fuzz.partial_ratio(a, b))
        except Exception:
            def fuzzy(a: str, b: str) -> float:
                if not a or not b:
                    return 0.0
                if a in b:
                    return 100.0
                return 100.0 * SequenceMatcher(None, a, b).ratio()

        for s in self.suppliers:
            produto = normalize_text(s.material_produto)
            empresa = normalize_text(s.empresa)
            email = normalize_text(s.email)
            contato = normalize_text(s.contato_nome)
            full = normalize_text(self._supplier_text(s))
            score = 0.0
            # Produto é o principal: "chapa" deve achar fornecedor de chapa mesmo sem nome da empresa.
            if q_norm and q_norm in produto:
                score += 120
            if s.tokens and q_tokens.issubset(s.tokens):
                score += 100
            if q_norm and q_norm in empresa:
                score += 70
            if q_norm and q_norm in email:
                score += 60
            if q_norm and q_norm in contato:
                score += 50
            matched = len(q_tokens.intersection(token_set(full)))
            score += matched * 18
            fuzzy_score = max(fuzzy(q_norm, produto), fuzzy(q_norm, empresa), fuzzy(q_norm, email))
            if fuzzy_score >= 82:
                score += fuzzy_score * 0.55
            if score > 0:
                add(s, score)

        ordered = sorted(scores.values(), key=lambda item: (-item[1], normalize_text(item[0].empresa), normalize_text(item[0].email)))
        return [s for s, _score in ordered]
