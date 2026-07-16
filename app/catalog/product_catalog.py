from __future__ import annotations

from dataclasses import dataclass, field
import json
import logging
import re
import sys
import unicodedata
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

from app.core.config import ensure_app_data_dir

from .product_usage_store import ProductUsageStore

logger = logging.getLogger(__name__)

CATALOG_FILE_NAME = "insumos_autocomplete.json"
MAX_SUGGESTIONS = 12

_WS_RE = re.compile(r"\s+")
_SEPARATORS_RE = re.compile(r"[\-|/\\]+")
_MEASURE_X_RE = re.compile(r"(?<=\d)\s*[xX×]\s*(?=\d)")


@dataclass
class CatalogValidationIssue:
    code: str
    message: str
    path: str = ""
    level: str = "error"
    context: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "code": self.code,
            "level": self.level,
            "path": self.path,
            "message": self.message,
            "context": dict(self.context),
        }


@dataclass
class CatalogValidationReport:
    valid: bool
    total_products: int = 0
    total_combinations: int = 0
    invalid_defaults: int = 0
    normalized_duplicates: int = 0
    issues: List[CatalogValidationIssue] = field(default_factory=list)
    generated_at: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat(timespec="seconds"))

    @property
    def has_errors(self) -> bool:
        return any((issue.level or "error").lower() == "error" for issue in self.issues)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "valid": bool(self.valid),
            "generated_at": self.generated_at,
            "total_products": int(self.total_products),
            "total_combinations": int(self.total_combinations),
            "invalid_defaults": int(self.invalid_defaults),
            "normalized_duplicates": int(self.normalized_duplicates),
            "issues": [issue.to_dict() for issue in self.issues],
        }

    @classmethod
    def empty(cls) -> "CatalogValidationReport":
        return cls(valid=True)


def normalize_catalog_text(value: str | None) -> str:
    """Normalize text for matching aliases and free-text input."""
    text = str(value or "").strip().lower()
    if not text:
        return ""

    text = text.replace("×", "x")
    text = unicodedata.normalize("NFKD", text)
    text = "".join(ch for ch in text if not unicodedata.combining(ch))

    # Normalize measure separators like `2x1`, `2 x 1`, `2×1`.
    text = _MEASURE_X_RE.sub(" x ", text)

    # Normalize generic separators for matching consistency.
    text = _SEPARATORS_RE.sub(" ", text)
    text = _WS_RE.sub(" ", text).strip()
    return text


def _clean(value: Any) -> str:
    return str(value or "").strip()


def _report_path_default() -> Path:
    return ensure_app_data_dir() / "reports" / "catalog_quality_report.json"


def _raw_is_mapping(raw: Any) -> bool:
    return isinstance(raw, dict)


def validate_catalog(raw: Dict[str, Any], *, strict: bool = False) -> CatalogValidationReport:
    """Validate schema/integrity/defaults and collect quality metrics."""
    issues: List[CatalogValidationIssue] = []
    invalid_defaults = 0
    duplicate_count = 0
    total_combinations = 0

    if not _raw_is_mapping(raw):
        issues.append(
            CatalogValidationIssue(
                code="schema.raw",
                path="root",
                message="Catalog root must be a JSON object",
            )
        )
        report = CatalogValidationReport(valid=False, issues=issues)
        if strict:
            raise ValueError("Catalog validation failed: root is not a JSON object")
        return report

    version = raw.get("version")
    if not isinstance(version, int):
        issues.append(
            CatalogValidationIssue(
                code="schema.version",
                path="version",
                message="'version' must be an integer",
            )
        )

    groups = raw.get("groups")
    if not isinstance(groups, list):
        issues.append(
            CatalogValidationIssue(
                code="schema.groups",
                path="groups",
                message="'groups' must be a list",
            )
        )
        groups = []

    products = raw.get("products")
    if not isinstance(products, list):
        issues.append(
            CatalogValidationIssue(
                code="schema.products",
                path="products",
                message="'products' must be a list",
            )
        )
        products = []

    group_ids: set[str] = set()
    for idx, group in enumerate(groups):
        path = f"groups[{idx}]"
        if not isinstance(group, dict):
            issues.append(
                CatalogValidationIssue(
                    code="schema.group.item",
                    path=path,
                    message="Each group must be an object",
                )
            )
            continue
        group_id = _clean(group.get("group_id"))
        if not group_id:
            issues.append(
                CatalogValidationIssue(
                    code="schema.group.group_id",
                    path=f"{path}.group_id",
                    message="group_id is required",
                )
            )
            continue
        if group_id in group_ids:
            issues.append(
                CatalogValidationIssue(
                    code="integrity.group_id.duplicate",
                    path=f"{path}.group_id",
                    message=f"Duplicate group_id: {group_id}",
                )
            )
        group_ids.add(group_id)

    alias_index: Dict[str, set[str]] = {}
    combo_seen: set[str] = set()
    product_ids: set[str] = set()

    for idx, product in enumerate(products):
        path = f"products[{idx}]"
        if not isinstance(product, dict):
            issues.append(
                CatalogValidationIssue(
                    code="schema.product.item",
                    path=path,
                    message="Each product must be an object",
                )
            )
            continue

        product_id = _clean(product.get("product_id"))
        group_id = _clean(product.get("group_id"))
        canonical = _clean(product.get("canonical"))
        aliases = product.get("aliases")

        if not product_id:
            issues.append(
                CatalogValidationIssue(
                    code="schema.product.product_id",
                    path=f"{path}.product_id",
                    message="product_id is required",
                )
            )
            continue

        if product_id in product_ids:
            issues.append(
                CatalogValidationIssue(
                    code="integrity.product_id.duplicate",
                    path=f"{path}.product_id",
                    message=f"Duplicate product_id: {product_id}",
                )
            )
        product_ids.add(product_id)

        if not canonical:
            issues.append(
                CatalogValidationIssue(
                    code="schema.product.canonical",
                    path=f"{path}.canonical",
                    message="canonical is required",
                )
            )

        if not isinstance(aliases, list) or not [_clean(alias) for alias in aliases if _clean(alias)]:
            issues.append(
                CatalogValidationIssue(
                    code="schema.product.aliases",
                    path=f"{path}.aliases",
                    message="aliases must be a non-empty list",
                )
            )
            aliases = []

        if not group_id:
            issues.append(
                CatalogValidationIssue(
                    code="schema.product.group_id",
                    path=f"{path}.group_id",
                    message="group_id is required",
                )
            )
        elif group_id not in group_ids:
            issues.append(
                CatalogValidationIssue(
                    code="integrity.product.group_id",
                    path=f"{path}.group_id",
                    message=f"group_id '{group_id}' is not declared in groups[]",
                )
            )

        catalog = product.get("catalog")
        types = (catalog or {}).get("types") if isinstance(catalog, dict) else None
        if not isinstance(types, dict) or not types:
            issues.append(
                CatalogValidationIssue(
                    code="integrity.product.types",
                    path=f"{path}.catalog.types",
                    message="catalog.types must be a non-empty object",
                )
            )
            types = {}

        for type_name, type_node in list(types.items()):
            type_text = _clean(type_name)
            type_path = f"{path}.catalog.types[{type_text or '<empty>'}]"
            if not type_text:
                issues.append(
                    CatalogValidationIssue(
                        code="integrity.type.empty",
                        path=type_path,
                        message="Type key cannot be empty",
                    )
                )
                continue

            if not isinstance(type_node, dict):
                issues.append(
                    CatalogValidationIssue(
                        code="integrity.type.node",
                        path=type_path,
                        message="Type node must be an object",
                    )
                )
                continue

            thicknesses = type_node.get("thicknesses")
            if not isinstance(thicknesses, dict) or not thicknesses:
                issues.append(
                    CatalogValidationIssue(
                        code="integrity.type.thicknesses",
                        path=f"{type_path}.thicknesses",
                        message="Type must contain thicknesses",
                    )
                )
                continue

            for thick_name, thick_node in list(thicknesses.items()):
                thick_text = _clean(thick_name)
                thick_path = f"{type_path}.thicknesses[{thick_text or '<empty>'}]"
                if not thick_text:
                    issues.append(
                        CatalogValidationIssue(
                            code="integrity.thickness.empty",
                            path=thick_path,
                            message="Thickness key cannot be empty",
                        )
                    )
                    continue

                if not isinstance(thick_node, dict):
                    issues.append(
                        CatalogValidationIssue(
                            code="integrity.thickness.node",
                            path=thick_path,
                            message="Thickness node must be an object",
                        )
                    )
                    continue

                measures = thick_node.get("measures")
                if not isinstance(measures, list) or not measures:
                    issues.append(
                        CatalogValidationIssue(
                            code="integrity.thickness.measures",
                            path=f"{thick_path}.measures",
                            message="Thickness must contain at least one measure",
                        )
                    )
                    continue

                for measure_idx, measure in enumerate(measures):
                    measure_path = f"{thick_path}.measures[{measure_idx}]"
                    if isinstance(measure, str):
                        measure_value = _clean(measure)
                        length_value = ""
                    elif isinstance(measure, dict):
                        measure_value = _clean(measure.get("measure"))
                        length_value = _clean(measure.get("length"))
                    else:
                        measure_value = ""
                        length_value = ""

                    if not measure_value:
                        issues.append(
                            CatalogValidationIssue(
                                code="integrity.measure.empty",
                                path=measure_path,
                                message="Measure value cannot be empty",
                            )
                        )
                        continue

                    total_combinations += 1
                    combo_key = "|".join(
                        [
                            normalize_catalog_text(product_id),
                            normalize_catalog_text(type_text),
                            normalize_catalog_text(thick_text),
                            normalize_catalog_text(measure_value),
                            normalize_catalog_text(length_value),
                        ]
                    )
                    if combo_key in combo_seen:
                        duplicate_count += 1
                        issues.append(
                            CatalogValidationIssue(
                                code="quality.combination.duplicate",
                                level="warning",
                                path=measure_path,
                                message="Duplicate normalized combination found",
                            )
                        )
                    combo_seen.add(combo_key)

        defaults = product.get("defaults") if isinstance(product.get("defaults"), dict) else {}
        def_type = _clean(defaults.get("type"))
        def_thickness = _clean(defaults.get("thickness"))
        def_measure = _clean(defaults.get("measure"))
        def_length = _clean(defaults.get("length"))

        defaults_valid = True
        if not (def_type and def_thickness and def_measure):
            defaults_valid = False
        else:
            type_node = types.get(def_type) if isinstance(types, dict) else None
            if not isinstance(type_node, dict):
                defaults_valid = False
            else:
                thickness_node = (type_node.get("thicknesses") or {}).get(def_thickness)
                if not isinstance(thickness_node, dict):
                    defaults_valid = False
                else:
                    measures = list(thickness_node.get("measures") or [])
                    match_found = False
                    for measure in measures:
                        if isinstance(measure, str):
                            m_value = _clean(measure)
                            l_value = ""
                        elif isinstance(measure, dict):
                            m_value = _clean(measure.get("measure"))
                            l_value = _clean(measure.get("length"))
                        else:
                            continue
                        if normalize_catalog_text(m_value) != normalize_catalog_text(def_measure):
                            continue
                        if def_length and normalize_catalog_text(l_value) != normalize_catalog_text(def_length):
                            continue
                        match_found = True
                        break
                    if not match_found:
                        defaults_valid = False

        if not defaults_valid:
            invalid_defaults += 1
            issues.append(
                CatalogValidationIssue(
                    code="integrity.defaults.invalid",
                    path=f"{path}.defaults",
                    message="Defaults do not map to a valid catalog path",
                    context={
                        "product_id": product_id,
                        "type": def_type,
                        "thickness": def_thickness,
                        "measure": def_measure,
                        "length": def_length,
                    },
                )
            )

        names = [canonical] + [_clean(alias) for alias in aliases if _clean(alias)]
        for name in names:
            norm = normalize_catalog_text(name)
            if not norm:
                continue
            alias_index.setdefault(norm, set()).add(product_id)

    for norm_name, owners in alias_index.items():
        if len(owners) <= 1:
            continue
        duplicate_count += 1
        issues.append(
            CatalogValidationIssue(
                code="quality.alias.normalized_duplicate",
                level="warning",
                path="products[*].aliases",
                message=f"Normalized alias/canonical collision for '{norm_name}'",
                context={"owners": sorted(owners)},
            )
        )

    has_errors = any((issue.level or "error").lower() == "error" for issue in issues)
    report = CatalogValidationReport(
        valid=not has_errors,
        total_products=len(products),
        total_combinations=total_combinations,
        invalid_defaults=invalid_defaults,
        normalized_duplicates=duplicate_count,
        issues=issues,
    )

    if strict and report.has_errors:
        first = next((issue for issue in report.issues if issue.level.lower() == "error"), None)
        detail = first.message if first else "catalog validation failed"
        raise ValueError(f"Catalog validation failed: {detail}")

    return report


class ProductCatalog:
    def __init__(
        self,
        *,
        usage_store: ProductUsageStore | None = None,
        catalog_path: str | Path | None = None,
        strict_validation: bool = False,
        quality_report_path: str | Path | None = None,
        write_quality_report: bool = False,
    ):
        self.usage_store = usage_store
        self.catalog_path = Path(catalog_path) if catalog_path else None
        self.strict_validation = bool(strict_validation)
        self.quality_report_path = Path(quality_report_path) if quality_report_path else None
        self.write_quality_report = bool(write_quality_report)

        self._loaded = False
        self.load_error = ""

        self._version = 1
        self._groups_by_id: Dict[str, str] = {}
        self._products_by_id: Dict[str, Dict[str, Any]] = {}
        self._normalized_aliases: Dict[str, set[str]] = {}

        self.catalog_quality: CatalogValidationReport = CatalogValidationReport.empty()

    # -----------------------------
    # Public API requested by spec
    # -----------------------------
    def resolve_product(self, text: str) -> Optional[Dict[str, Any]]:
        self._ensure_loaded()
        query = normalize_catalog_text(text)
        if not query:
            return None

        best: tuple[float, str] | None = None
        for pid, product in self._products_by_id.items():
            score = self._match_score(query, product)
            if score <= 0:
                continue
            key = (score, product["canonical"].casefold())
            if best is None or key > (best[0], self._products_by_id[best[1]]["canonical"].casefold()):
                best = (score, pid)

        if best is None:
            return None
        return dict(self._products_by_id[best[1]])

    def list_products(self, query: str, limit: int = MAX_SUGGESTIONS) -> List[Dict[str, Any]]:
        self._ensure_loaded()
        q = normalize_catalog_text(query)
        out: List[tuple[Any, Dict[str, Any]]] = []

        for pid, product in self._products_by_id.items():
            prefix_hit, contains_hit = self._match_flags(q, product)
            if q and not contains_hit:
                continue
            usage = self._product_usage_score(pid)
            has_defaults = 1 if any(_clean(product["defaults"].get(k)) for k in ("type", "thickness", "measure", "length")) else 0
            rank_key = (
                usage,
                has_defaults,
                1 if prefix_hit else 0,
                1 if contains_hit else 0,
            )
            out.append((rank_key, dict(product)))

        out.sort(key=lambda row: (-row[0][0], -row[0][1], -row[0][2], -row[0][3], row[1]["canonical"].casefold()))
        return [row[1] for row in out[: max(1, int(limit or MAX_SUGGESTIONS))]]

    def list_types(self, product_id: str) -> List[str]:
        p = self._product(product_id)
        if not p:
            return []
        types = list(p["catalog"]["types"].keys())
        return self._sort_options(p["product_id"], "type", types, base={})

    def list_thicknesses(self, product_id: str, type: str) -> List[str]:
        p = self._product(product_id)
        if not p:
            return []
        selected_type = self._resolve_type_name(p, type)
        bucket = p["catalog"]["types"]
        values: set[str] = set()
        if selected_type:
            values.update(bucket[selected_type]["thicknesses"].keys())
        else:
            for tnode in bucket.values():
                values.update(tnode["thicknesses"].keys())
        return self._sort_options(
            p["product_id"],
            "thickness",
            list(values),
            base={"type": selected_type or _clean(type)},
        )

    def list_measures(self, product_id: str, type: str, thickness: str) -> List[Dict[str, Any]]:
        p = self._product(product_id)
        if not p:
            return []

        selected_type = self._resolve_type_name(p, type)
        selected_thickness = self._resolve_thickness_name(p, selected_type, thickness)
        rows: List[Dict[str, Any]] = []

        for type_name, tnode in p["catalog"]["types"].items():
            if selected_type and type_name != selected_type:
                continue
            for thick_name, th_node in tnode["thicknesses"].items():
                if selected_thickness and thick_name != selected_thickness:
                    continue
                for m in th_node["measures"]:
                    rows.append({"measure": _clean(m.get("measure")), "length": _clean(m.get("length")) or None})

        seen: set[str] = set()
        dedup: List[Dict[str, Any]] = []
        for row in rows:
            key = f"{normalize_catalog_text(row.get('measure'))}|{normalize_catalog_text(row.get('length'))}"
            if key in seen:
                continue
            seen.add(key)
            dedup.append(row)

        dedup.sort(
            key=lambda row: self._measure_rank_key(
                p["product_id"],
                selected_type or _clean(type),
                selected_thickness or _clean(thickness),
                row,
            ),
            reverse=True,
        )
        return dedup

    def get_defaults(self, product_id: str) -> Dict[str, Any]:
        p = self._product(product_id)
        if not p:
            return {"type": "", "thickness": "", "measure": "", "length": None}
        return dict(p["defaults"])

    def is_value_valid(self, product_id: str, type: str, thickness: str, measure: str, length: str | None) -> bool:
        p = self._product(product_id)
        if not p:
            return False

        type_name = self._resolve_type_name(p, type)
        if _clean(type) and not type_name:
            return False

        thick_name = self._resolve_thickness_name(p, type_name, thickness)
        if _clean(thickness) and not thick_name:
            return False

        if not _clean(measure) and not _clean(length):
            return True

        candidates = self.list_measures(product_id, type_name or type, thick_name or thickness)
        wanted_measure = normalize_catalog_text(measure)
        wanted_length = normalize_catalog_text(length)

        for row in candidates:
            if wanted_measure and normalize_catalog_text(row.get("measure")) != wanted_measure:
                continue
            if wanted_length and normalize_catalog_text(row.get("length")) != wanted_length:
                continue
            return True
        return False

    # -----------------------------
    # Helper APIs used by UI layer
    # -----------------------------
    def list_lengths(self, product_id: str, type: str, thickness: str, measure: str) -> List[str]:
        rows = self.list_measures(product_id, type, thickness)
        wanted = normalize_catalog_text(measure)
        values: set[str] = set()
        for row in rows:
            if wanted and normalize_catalog_text(row.get("measure")) != wanted:
                continue
            length = _clean(row.get("length"))
            if length:
                values.add(length)
        p = self._product(product_id)
        if not p:
            return sorted(values, key=str.casefold)
        return self._sort_options(
            p["product_id"],
            "length",
            list(values),
            base={"type": type, "thickness": thickness, "measure": measure},
        )

    # -----------------------------
    # Internal
    # -----------------------------
    def _product(self, product_id: str) -> Optional[Dict[str, Any]]:
        self._ensure_loaded()
        return self._products_by_id.get(_clean(product_id))

    def _ensure_loaded(self) -> None:
        if self._loaded:
            return
        self._loaded = True
        self._load_raw_catalog()

    def _build_invalid_catalog_report(self, message: str, *, code: str) -> CatalogValidationReport:
        return CatalogValidationReport(
            valid=False,
            total_products=0,
            total_combinations=0,
            invalid_defaults=0,
            normalized_duplicates=0,
            issues=[CatalogValidationIssue(code=code, path=CATALOG_FILE_NAME, message=message)],
        )

    def _load_raw_catalog(self) -> None:
        raw: Dict[str, Any] = {}
        try:
            raw_text = self._read_catalog_text().lstrip("\ufeff")
            raw = json.loads(raw_text)
        except Exception as exc:
            self.load_error = f"Catalogo indisponivel/invalido: {exc}"
            logger.warning(self.load_error)
            self.catalog_quality = self._build_invalid_catalog_report(self.load_error, code="catalog.load_error")
            self._write_quality_report()
            if self.strict_validation:
                raise ValueError(self.load_error) from exc
            raw = {"version": 1, "groups": [], "products": []}

        try:
            self.catalog_quality = validate_catalog(raw, strict=self.strict_validation)
        except Exception as exc:
            self.load_error = f"Catalogo invalido: {exc}"
            logger.warning(self.load_error)
            self.catalog_quality = self._build_invalid_catalog_report(self.load_error, code="catalog.validation_error")
            self._write_quality_report()
            if self.strict_validation:
                raise
            raw = {"version": 1, "groups": [], "products": []}

        if not self.catalog_quality.valid:
            if not self.load_error:
                self.load_error = "Catalogo invalido: modo manual habilitado"
            logger.warning(self.load_error)
            raw = {"version": int(raw.get("version", 1) or 1), "groups": [], "products": []}

        self._write_quality_report()

        self._version = int(raw.get("version", 1) or 1)
        self._groups_by_id = {}
        for g in list(raw.get("groups") or []):
            gid = _clean(g.get("group_id"))
            if not gid:
                continue
            self._groups_by_id[gid] = _clean(g.get("name")) or gid

        self._products_by_id = {}
        self._normalized_aliases = {}
        for row in list(raw.get("products") or []):
            product = self._normalize_product_row(row)
            if not product:
                continue
            pid = product["product_id"]
            self._products_by_id[pid] = product
            aliases = set(product.get("aliases") or [])
            aliases.add(product["canonical"])
            self._normalized_aliases[pid] = {normalize_catalog_text(x) for x in aliases if _clean(x)}

    def _write_quality_report(self) -> None:
        if not self.write_quality_report:
            return
        try:
            path = self.quality_report_path or _report_path_default()
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(json.dumps(self.catalog_quality.to_dict(), ensure_ascii=False, indent=2), encoding="utf-8")
        except Exception:
            logger.exception("Falha ao escrever catalog_quality_report")

    def _normalize_product_row(self, row: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        pid = _clean(row.get("product_id"))
        canonical = _clean(row.get("canonical"))
        if not pid or not canonical:
            return None

        group_id = _clean(row.get("group_id"))
        group_name = self._groups_by_id.get(group_id, group_id)
        defaults = dict(row.get("defaults") or {})
        norm_defaults = {
            "type": _clean(defaults.get("type")),
            "thickness": _clean(defaults.get("thickness")),
            "measure": _clean(defaults.get("measure")),
            "length": _clean(defaults.get("length")) or None,
        }
        aliases = [_clean(x) for x in list(row.get("aliases") or []) if _clean(x)]

        catalog = {"types": {}}
        raw_types = dict((row.get("catalog") or {}).get("types") or {})
        for type_name, type_node in raw_types.items():
            tname = _clean(type_name)
            if not tname:
                continue
            thicknesses: Dict[str, Dict[str, Any]] = {}
            raw_thicknesses = dict((type_node or {}).get("thicknesses") or {})
            for thick_name, thick_node in raw_thicknesses.items():
                th_name = _clean(thick_name)
                if not th_name:
                    continue
                measures: List[Dict[str, Any]] = []
                for raw_measure in list((thick_node or {}).get("measures") or []):
                    if isinstance(raw_measure, str):
                        measures.append({"measure": _clean(raw_measure), "length": None})
                    elif isinstance(raw_measure, dict):
                        m = _clean(raw_measure.get("measure"))
                        if not m:
                            continue
                        measures.append(
                            {
                                "measure": m,
                                "length": _clean(raw_measure.get("length")) or None,
                            }
                        )
                thicknesses[th_name] = {"measures": measures}
            catalog["types"][tname] = {"thicknesses": thicknesses}

        return {
            "product_id": pid,
            "group_id": group_id,
            "group_name": group_name,
            "canonical": canonical,
            "aliases": aliases,
            "defaults": norm_defaults,
            "catalog": catalog,
        }

    def _read_catalog_text(self) -> str:
        candidates: List[Path] = []
        if self.catalog_path:
            candidates.append(self.catalog_path)
            try:
                if self.catalog_path.exists():
                    return self.catalog_path.read_text(encoding="utf-8")
            except Exception:
                pass

        try:
            from importlib import resources

            resource = resources.files("app.assets.catalog").joinpath(CATALOG_FILE_NAME)
            if resource.is_file():
                return resource.read_text(encoding="utf-8")
        except Exception:
            pass

        candidates.append(Path(__file__).resolve().parents[1] / "assets" / "catalog" / CATALOG_FILE_NAME)
        if getattr(sys, "_MEIPASS", None):
            candidates.append(Path(str(sys._MEIPASS)) / "app" / "assets" / "catalog" / CATALOG_FILE_NAME)

        for path in candidates:
            try:
                if path.exists():
                    return path.read_text(encoding="utf-8")
            except Exception:
                continue
        raise FileNotFoundError(f"Catalogo nao encontrado: {CATALOG_FILE_NAME}")

    def _match_flags(self, q: str, product: Dict[str, Any]) -> tuple[bool, bool]:
        if not q:
            return False, True
        aliases = self._normalized_aliases.get(product["product_id"], set())
        prefix_hit = any(a.startswith(q) for a in aliases if a)
        contains_hit = any(q in a for a in aliases if a)
        return prefix_hit, contains_hit

    def _match_score(self, q: str, product: Dict[str, Any]) -> float:
        aliases = self._normalized_aliases.get(product["product_id"], set())
        if q in aliases:
            return 1000.0
        prefix_hit = any(a.startswith(q) for a in aliases if a)
        contains_hit = any(q in a for a in aliases if a)
        if prefix_hit:
            return 700.0
        if contains_hit:
            return 400.0
        q_tokens = q.split()
        if q_tokens and any(all(token in a for token in q_tokens) for a in aliases):
            return 200.0
        return 0.0

    def _product_usage_score(self, product_id: str) -> float:
        if not self.usage_store:
            return 0.0
        return self.usage_store.rank_boost({"product_id": product_id})

    def _sort_options(self, product_id: str, field_name: str, values: List[str], *, base: Dict[str, Any]) -> List[str]:
        unique = {v.strip(): None for v in values if _clean(v)}
        sorted_values = list(unique.keys())
        sorted_values.sort(
            key=lambda v: self._option_rank_key(
                product_id=product_id,
                field_name=field_name,
                value=v,
                base=base,
            ),
            reverse=True,
        )
        return sorted_values

    def _option_rank_key(self, *, product_id: str, field_name: str, value: str, base: Dict[str, Any]) -> tuple[float, str]:
        boost = 0.0
        if self.usage_store:
            sample = {"product_id": product_id}
            sample.update(base)
            sample[field_name] = value
            boost = self.usage_store.rank_boost(sample)
        return (boost, value.casefold())

    def _measure_rank_key(self, product_id: str, type_name: str, thick_name: str, row: Dict[str, Any]) -> tuple[float, str, str]:
        boost = 0.0
        if self.usage_store:
            boost = self.usage_store.rank_boost(
                {
                    "product_id": product_id,
                    "type": type_name,
                    "thickness": thick_name,
                    "measure": row.get("measure"),
                    "length": row.get("length"),
                }
            )
        return (boost, _clean(row.get("measure")).casefold(), _clean(row.get("length")).casefold())

    def _resolve_type_name(self, product: Dict[str, Any], type_value: str) -> str:
        wanted = normalize_catalog_text(type_value)
        if not wanted:
            return ""
        for tname in product["catalog"]["types"].keys():
            if normalize_catalog_text(tname) == wanted:
                return tname
        return ""

    def _resolve_thickness_name(self, product: Dict[str, Any], type_name: str, thickness: str) -> str:
        wanted = normalize_catalog_text(thickness)
        if not wanted:
            return ""
        if type_name:
            tnode = product["catalog"]["types"].get(type_name, {})
            for thick_name in tnode.get("thicknesses", {}).keys():
                if normalize_catalog_text(thick_name) == wanted:
                    return thick_name
            return ""
        for tnode in product["catalog"]["types"].values():
            for thick_name in tnode.get("thicknesses", {}).keys():
                if normalize_catalog_text(thick_name) == wanted:
                    return thick_name
        return ""


def generate_catalog_quality_report(
    *,
    strict: bool = True,
    catalog_path: str | Path | None = None,
    report_path: str | Path | None = None,
) -> Dict[str, Any]:
    """Run strict catalog validation and write a quality report file."""
    catalog = ProductCatalog(
        catalog_path=catalog_path,
        strict_validation=strict,
        quality_report_path=Path(report_path) if report_path else _report_path_default(),
        write_quality_report=True,
    )
    # Trigger loading/validation.
    _ = catalog.list_products("", limit=1)
    return catalog.catalog_quality.to_dict()


_DEFAULT_CATALOG: ProductCatalog | None = None


def _default_catalog() -> ProductCatalog:
    global _DEFAULT_CATALOG
    if _DEFAULT_CATALOG is None:
        _DEFAULT_CATALOG = ProductCatalog()
    return _DEFAULT_CATALOG


def resolve_product(text: str) -> Optional[Dict[str, Any]]:
    return _default_catalog().resolve_product(text)


def list_products(query: str, limit: int = MAX_SUGGESTIONS) -> List[Dict[str, Any]]:
    return _default_catalog().list_products(query=query, limit=limit)


def list_types(product_id: str) -> List[str]:
    return _default_catalog().list_types(product_id)


def list_thicknesses(product_id: str, type: str) -> List[str]:
    return _default_catalog().list_thicknesses(product_id, type)


def list_measures(product_id: str, type: str, thickness: str) -> List[Dict[str, Any]]:
    return _default_catalog().list_measures(product_id, type, thickness)


def get_defaults(product_id: str) -> Dict[str, Any]:
    return _default_catalog().get_defaults(product_id)


def is_value_valid(product_id: str, type: str, thickness: str, measure: str, length: str | None) -> bool:
    return _default_catalog().is_value_valid(product_id, type, thickness, measure, length)
