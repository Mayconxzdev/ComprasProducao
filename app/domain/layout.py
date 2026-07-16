from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class LayoutSpec:
    mode: str
    show_contact: bool
    show_items: bool


def decide_layout_mode(width: int, height: int, dpi_scale: float = 1.0) -> LayoutSpec:
    width = max(1, int(width))
    height = max(1, int(height))
    scale = max(0.5, min(3.0, float(dpi_scale)))
    logical_width = width / scale

    if logical_width >= 1200:
        return LayoutSpec(mode="full", show_contact=True, show_items=True)
    if logical_width >= 900:
        return LayoutSpec(mode="mid", show_contact=False, show_items=True)
    return LayoutSpec(mode="compact", show_contact=False, show_items=False)
