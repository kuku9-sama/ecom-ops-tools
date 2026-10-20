from dataclasses import dataclass
from datetime import datetime
from typing import Protocol

from ecommerce.domain.models import Order


@dataclass(frozen=True, slots=True)
class Page[T]:
    items: tuple[T, ...]
    next_cursor: str | None


class PlatformPort(Protocol):
    """平台适配器要实现的最小接口。"""

    platform: str

    def authenticate(self) -> None: ...

    def list_orders(
        self, updated_since: datetime, cursor: str | None
    ) -> Page[Order]: ...
