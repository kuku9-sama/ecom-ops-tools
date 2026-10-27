from dataclasses import dataclass
from datetime import datetime
from typing import Protocol

from ecommerce.domain.models import InventoryItem, Order, ProductDraft


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

    def create_product_draft(
        self, draft: ProductDraft, idempotency_key: str
    ) -> str: ...

    def find_product_by_sku(self, sku: str) -> str | None: ...

    def list_inventory(self, cursor: str | None) -> Page[InventoryItem]: ...


class ExtractionPort(Protocol):
    def extract(self, text: str, repair_error: str | None = None) -> str: ...
