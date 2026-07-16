from __future__ import annotations

from dataclasses import dataclass
from typing import Optional


@dataclass
class AppError(Exception):
    code: str
    user_message: str
    debug_details: str = ""
    cause: Optional[BaseException] = None

    def __str__(self) -> str:
        return f"{self.code}: {self.user_message}"


def wrap_error(
    *,
    code: str,
    user_message: str,
    debug_details: str = "",
    cause: Optional[BaseException] = None,
) -> AppError:
    return AppError(code=code, user_message=user_message, debug_details=debug_details, cause=cause)
