from __future__ import annotations

from dataclasses import dataclass


@dataclass
class PaginationState:
    total_items: int = 0
    page_size: int = 100
    page_index: int = 0

    def clamp(self) -> None:
        if self.page_size <= 0:
            self.page_size = 100
        if self.total_items < 0:
            self.total_items = 0
        max_page = self.max_page_index
        if self.page_index < 0:
            self.page_index = 0
        if self.page_index > max_page:
            self.page_index = max_page

    @property
    def total_pages(self) -> int:
        if self.total_items <= 0:
            return 1
        return ((self.total_items - 1) // self.page_size) + 1

    @property
    def max_page_index(self) -> int:
        return max(0, self.total_pages - 1)

    @property
    def start(self) -> int:
        self.clamp()
        return self.page_index * self.page_size

    @property
    def end(self) -> int:
        self.clamp()
        return min(self.total_items, self.start + self.page_size)

    def set_total(self, total: int) -> None:
        self.total_items = int(total)
        self.clamp()

    def set_page_size(self, size: int) -> None:
        self.page_size = int(size)
        self.page_index = 0
        self.clamp()

    def first(self) -> None:
        self.page_index = 0

    def last(self) -> None:
        self.page_index = self.max_page_index

    def next(self) -> None:
        self.page_index = min(self.max_page_index, self.page_index + 1)

    def prev(self) -> None:
        self.page_index = max(0, self.page_index - 1)

    def page_label(self) -> str:
        self.clamp()
        return f"{self.page_index + 1}/{self.total_pages} ({self.start + 1}-{self.end} de {self.total_items})" if self.total_items else "0/0"
