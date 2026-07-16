from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, List

from .product_catalog import ProductCatalog, normalize_catalog_text


def _clean(value: str | None) -> str:
    return str(value or "").strip()


@dataclass
class ItemCascadeState:
    product_text: str = ""
    product_id: str = ""
    group_name: str = ""
    canonical_product: str = ""

    type_value: str = ""
    thickness_value: str = ""
    measure_value: str = ""
    length_value: str = ""

    manual: Dict[str, bool] = field(
        default_factory=lambda: {
            "product": False,
            "type": False,
            "thickness": False,
            "measure": False,
            "length": False,
        }
    )
    options: Dict[str, List[str]] = field(
        default_factory=lambda: {
            "types": [],
            "thicknesses": [],
            "measures": [],
            "lengths": [],
        }
    )


class ItemCascadeEngine:
    def __init__(self, catalog: ProductCatalog):
        self.catalog = catalog

    def new_state(self) -> ItemCascadeState:
        return ItemCascadeState()

    def set_product(self, state: ItemCascadeState, text: str, *, from_catalog: bool = False) -> ItemCascadeState:
        text = _clean(text)
        state.product_text = text
        resolved = self.catalog.resolve_product(text)

        if not resolved:
            state.product_id = ""
            state.group_name = ""
            state.canonical_product = ""
            state.manual["product"] = bool(text)
            state.options["types"] = []
            state.options["thicknesses"] = []
            state.options["measures"] = []
            state.options["lengths"] = []
            return state

        state.product_id = resolved["product_id"]
        state.group_name = _clean(resolved.get("group_name"))
        state.canonical_product = _clean(resolved.get("canonical"))
        if from_catalog:
            state.product_text = state.canonical_product
        state.manual["product"] = False
        state.manual["type"] = False
        state.manual["thickness"] = False
        state.manual["measure"] = False
        state.manual["length"] = False

        defaults = self.catalog.get_defaults(state.product_id)

        state.options["types"] = self.catalog.list_types(state.product_id)
        state.type_value = self._coerce_value(
            current=state.type_value,
            options=state.options["types"],
            default=defaults.get("type"),
            keep_manual=False,
        )
        state.manual["type"] = self._is_manual(state.type_value, state.options["types"])

        self._refresh_after_type(state, defaults=defaults)
        return state

    def set_type(self, state: ItemCascadeState, value: str) -> ItemCascadeState:
        state.type_value = _clean(value)
        if not state.product_id:
            state.manual["type"] = bool(state.type_value)
            state.options["thicknesses"] = []
            state.options["measures"] = []
            state.options["lengths"] = []
            return state

        defaults = self.catalog.get_defaults(state.product_id)
        state.options["types"] = self.catalog.list_types(state.product_id)
        state.type_value = self._canonical_value(state.type_value, state.options["types"]) or state.type_value
        state.manual["type"] = self._is_manual(state.type_value, state.options["types"])

        self._refresh_after_type(state, defaults=defaults)
        return state

    def set_thickness(self, state: ItemCascadeState, value: str) -> ItemCascadeState:
        state.thickness_value = _clean(value)
        if not state.product_id:
            state.manual["thickness"] = bool(state.thickness_value)
            state.options["measures"] = []
            state.options["lengths"] = []
            return state

        defaults = self.catalog.get_defaults(state.product_id)
        state.options["thicknesses"] = self.catalog.list_thicknesses(state.product_id, state.type_value)
        state.thickness_value = self._canonical_value(state.thickness_value, state.options["thicknesses"]) or state.thickness_value
        state.manual["thickness"] = self._is_manual(state.thickness_value, state.options["thicknesses"])

        self._refresh_after_thickness(state, defaults=defaults)
        return state

    def set_measure(self, state: ItemCascadeState, value: str) -> ItemCascadeState:
        state.measure_value = _clean(value)
        if not state.product_id:
            state.manual["measure"] = bool(state.measure_value)
            state.options["lengths"] = []
            return state

        defaults = self.catalog.get_defaults(state.product_id)
        measure_rows = self.catalog.list_measures(state.product_id, state.type_value, state.thickness_value)
        state.options["measures"] = self._measure_values(measure_rows)
        state.measure_value = self._canonical_value(state.measure_value, state.options["measures"]) or state.measure_value
        state.manual["measure"] = self._is_manual(state.measure_value, state.options["measures"])

        self._refresh_after_measure(state, defaults=defaults)
        return state

    def set_length(self, state: ItemCascadeState, value: str) -> ItemCascadeState:
        state.length_value = _clean(value)
        if not state.product_id:
            state.manual["length"] = bool(state.length_value)
            return state

        state.options["lengths"] = self.catalog.list_lengths(
            state.product_id,
            state.type_value,
            state.thickness_value,
            state.measure_value,
        )
        state.length_value = self._canonical_value(state.length_value, state.options["lengths"]) or state.length_value
        state.manual["length"] = self._is_manual(state.length_value, state.options["lengths"])
        return state

    def selection_payload(self, state: ItemCascadeState) -> Dict[str, str]:
        return {
            "product_id": _clean(state.product_id),
            "type": _clean(state.type_value),
            "thickness": _clean(state.thickness_value),
            "measure": _clean(state.measure_value),
            "length": _clean(state.length_value),
        }

    def _refresh_after_type(self, state: ItemCascadeState, *, defaults: Dict[str, str]) -> None:
        state.options["thicknesses"] = self.catalog.list_thicknesses(state.product_id, state.type_value)
        state.thickness_value = self._coerce_value(
            current=state.thickness_value,
            options=state.options["thicknesses"],
            default=defaults.get("thickness"),
            keep_manual=state.manual["thickness"],
        )
        state.manual["thickness"] = self._is_manual(state.thickness_value, state.options["thicknesses"])
        self._refresh_after_thickness(state, defaults=defaults)

    def _refresh_after_thickness(self, state: ItemCascadeState, *, defaults: Dict[str, str]) -> None:
        measure_rows = self.catalog.list_measures(state.product_id, state.type_value, state.thickness_value)
        state.options["measures"] = self._measure_values(measure_rows)
        state.measure_value = self._coerce_value(
            current=state.measure_value,
            options=state.options["measures"],
            default=defaults.get("measure"),
            keep_manual=state.manual["measure"],
        )
        state.manual["measure"] = self._is_manual(state.measure_value, state.options["measures"])
        self._refresh_after_measure(state, defaults=defaults)

    def _refresh_after_measure(self, state: ItemCascadeState, *, defaults: Dict[str, str]) -> None:
        state.options["lengths"] = self.catalog.list_lengths(
            state.product_id,
            state.type_value,
            state.thickness_value,
            state.measure_value,
        )
        state.length_value = self._coerce_value(
            current=state.length_value,
            options=state.options["lengths"],
            default=defaults.get("length"),
            keep_manual=state.manual["length"],
        )
        state.manual["length"] = self._is_manual(state.length_value, state.options["lengths"])

    def _measure_values(self, rows: List[Dict[str, str]]) -> List[str]:
        seen: set[str] = set()
        out: List[str] = []
        for row in rows:
            measure = _clean(row.get("measure"))
            if not measure:
                continue
            key = normalize_catalog_text(measure)
            if key in seen:
                continue
            seen.add(key)
            out.append(measure)
        return out

    def _coerce_value(self, *, current: str, options: List[str], default: str | None, keep_manual: bool) -> str:
        current = _clean(current)
        if keep_manual and current and not self._canonical_value(current, options):
            return current

        default = _clean(default)
        if default:
            default_match = self._canonical_value(default, options)
            if default_match:
                return default_match
        if len(options) == 1:
            return options[0]
        current_match = self._canonical_value(current, options)
        if current_match:
            return current_match
        if keep_manual and current:
            return current
        return ""

    def _canonical_value(self, value: str, options: List[str]) -> str:
        wanted = normalize_catalog_text(value)
        if not wanted:
            return ""
        for option in options:
            if normalize_catalog_text(option) == wanted:
                return option
        return ""

    def _is_manual(self, value: str, options: List[str]) -> bool:
        if not _clean(value):
            return False
        return not bool(self._canonical_value(value, options))
