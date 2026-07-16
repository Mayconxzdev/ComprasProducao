"""
Professional search index for multi-sheet XLSX data
Supports searching across items, categories, synonyms, and services
"""
from __future__ import annotations
import logging
import math
from typing import List, Dict, Set
from dataclasses import dataclass
from .models_pro import SupplierPro, Item, ProDataSet
from .utils_text import normalize_text, token_set

logger = logging.getLogger(__name__)

@dataclass
class ProSearchResult:
    """Search result with matched items"""
    supplier: SupplierPro
    matched_items: List[Item]  # Items that matched the query
    score: int  # Number of matched tokens

    def __hash__(self):
        return hash(self.supplier.supplier_id)

class ProSearchIndex:
    """
    Professional search index with support for:
    - Items (ITEM, CATEGORIA, SUBTIPO)
    - Synonyms (TERMOS)
    - Supplier fields (EMPRESA, CONTATO, CIDADE, UF)
    - Services (CORTE, DOBRA, ENTREGA, RETIRA)
    """

    def __init__(self):
        self._suppliers: Dict[str, SupplierPro] = {}
        self._items: Dict[str, Item] = {}
        self._synonyms: Dict[str, Set[str]] = {}  # termo -> {item_ids}
        self._item_tokens: Dict[str, Set[str]] = {}  # item_id -> tokens

    def build_from_dataset(self, dataset: ProDataSet):
        """Build index from ProDataSet"""
        logger.info("Building professional search index...")

        self._suppliers = dataset.suppliers.copy()
        self._items = dataset.items.copy()

        # Build synonym map
        for syn in dataset.synonyms:
            termo_norm = normalize_text(syn.termo)
            if termo_norm not in self._synonyms:
                self._synonyms[termo_norm] = set()
            self._synonyms[termo_norm].add(syn.item_id)

        # Build item token index
        for item_id, item in self._items.items():
            text = f"{item.categoria} {item.subtipo} {item.item}"
            self._item_tokens[item_id] = token_set(text)

        logger.info(f"Index built: {len(self._suppliers)} suppliers, "
                    f"{len(self._items)} items, {len(self._synonyms)} synonym terms")

    def search(self, query: str) -> List[ProSearchResult]:
        """
        Search across all indexed data

        Match logic:
        - 1 token: must match at least 1
        - 2+ tokens: must match min(2, ceil(0.6 * N))

        Returns: List of ProSearchResult sorted by score desc, then empresa asc
        """
        if not query or not self._suppliers:
            return []

        # Normalize and tokenize query
        query_norm = normalize_text(query)
        query_tokens = self._tokenize(query_norm)

        if not query_tokens:
            return []

        # Minimum matches required
        required_matches = 1 if len(query_tokens) == 1 else min(2, math.ceil(0.6 * len(query_tokens)))

        # Check if query contains service keywords
        service_keywords = self._detect_service_keywords(query_tokens)

        # Expand query with synonyms
        expanded_item_ids = self._expand_with_synonyms(query_tokens)

        # Search suppliers
        results = []
        for supplier in self._suppliers.values():
            match_score, matched_items = self._match_supplier(
                supplier,
                query_tokens,
                required_matches,
                service_keywords,
                expanded_item_ids
            )

            if match_score > 0:
                results.append(ProSearchResult(
                    supplier=supplier,
                    matched_items=matched_items,
                    score=match_score
                ))

        # Sort by score desc, then empresa asc
        results.sort(key=lambda r: (-r.score, r.supplier.empresa.lower()))

        logger.info(f"Search '{query}' returned {len(results)} results")
        return results

    def _tokenize(self, text: str) -> Set[str]:
        """Tokenize normalized text"""
        tokens = set()
        for word in text.split():
            word = word.strip()
            # Keep numbers, ignore single chars (except numbers)
            if word and (len(word) > 1 or word.isdigit()):
                tokens.add(word)
        return tokens

    def _detect_service_keywords(self, query_tokens: Set[str]) -> Dict[str, bool]:
        """Detect service keywords in query"""
        services = {
            'corte': False,
            'dobra': False,
            'entrega': False,
            'retira': False
        }

        for token in query_tokens:
            if 'corte' in token:
                services['corte'] = True
            if 'dobra' in token:
                services['dobra'] = True
            if 'entrega' in token or 'entreg' in token:
                services['entrega'] = True
            if 'retira' in token or 'retire' in token:
                services['retira'] = True

        return services

    def _expand_with_synonyms(self, query_tokens: Set[str]) -> Set[str]:
        """Expand query tokens with synonyms to find item_ids"""
        item_ids = set()

        for token in query_tokens:
            # Check exact synonym match
            if token in self._synonyms:
                item_ids.update(self._synonyms[token])

            # Check if token is substring of any synonym
            for syn_term in self._synonyms:
                if token in syn_term or syn_term in token:  # Partial match
                    item_ids.update(self._synonyms[syn_term])

        return item_ids

    def _match_supplier(
        self,
        supplier: SupplierPro,
        query_tokens: Set[str],
        required_matches: int,
        service_keywords: Dict[str, bool],
        expanded_item_ids: Set[str]
    ) -> tuple[int, List[Item]]:
        """
        Match supplier against query

        Returns: (score, matched_items)
        """
        matched_tokens = set()
        matched_items = []

        # 1. Match supplier fields with substring matching
        supplier_text = f"{supplier.empresa} {getattr(supplier, 'contato', '')} {getattr(supplier, 'cidade', '')} {getattr(supplier, 'uf', '')}".lower()

        # Check substring matches in supplier fields
        for q_token in query_tokens:
            if q_token in supplier_text:
                matched_tokens.add(q_token)

        # Also check exact token matches
        supplier_tokens = supplier.tokens
        matched_tokens.update(query_tokens & supplier_tokens)

        # 2. Match items (by tokens and by synonym expansion)
        for item in supplier.items:
            item_matched = False

            # Get item text for partial matching
            item_text = f"{item.categoria} {item.subtipo} {item.item}".lower()

            # Check if ANY query token is a substring of item text
            for q_token in query_tokens:
                if q_token in item_text:  # Substring match!
                    item_matched = True
                    matched_tokens.add(q_token)

            # Also check direct token match (for exact word matches)
            item_tokens = self._item_tokens.get(item.item_id, set())
            if query_tokens & item_tokens:
                item_matched = True
                matched_tokens.update(query_tokens & item_tokens)

            # Check synonym match
            if item.item_id in expanded_item_ids:
                item_matched = True

            if item_matched:
                matched_items.append(item)

        # 3. Match services
        if service_keywords.get('corte') and supplier.serv_corte:
            matched_tokens.add('corte')
        if service_keywords.get('dobra') and supplier.serv_dobra:
            matched_tokens.add('dobra')
        if service_keywords.get('entrega') and supplier.serv_entrega:
            matched_tokens.add('entrega')
        if service_keywords.get('retira') and supplier.serv_retira:
            matched_tokens.add('retira')

        # Calculate score
        score = len(matched_tokens)

        # Check if meets minimum requirement
        if score < required_matches:
            return 0, []

        return score, matched_items

    def get_by_email(self, email: str) -> SupplierPro | None:
        """Get supplier by email (backward compatibility with simple index)"""
        if not email:
            return None
        email_norm = normalize_text(email)
        # Search in all suppliers
        for supplier in self._suppliers.values():
            if normalize_text(supplier.email) == email_norm:
                return supplier
        return None

    def get_all_suppliers(self) -> List[SupplierPro]:
        """Get all suppliers (for display without search)"""
        return list(self._suppliers.values())

    @property
    def supplier_count(self) -> int:
        """Number of indexed suppliers"""
        return len(self._suppliers)

    @property
    def item_count(self) -> int:
        """Number of indexed items"""
        return len(self._items)
