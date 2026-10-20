from dataclasses import dataclass
from datetime import datetime


@dataclass(frozen=True, slots=True)
class OrderLine:
    platform: str
    platform_order_id: str
    line_id: str
    sku: str
    qty: int
    unit_price_minor: int
    discount_minor: int
    refund_minor: int
    currency: str

    @property
    def net_minor(self) -> int:
        # 净额只在这里定义一次，避免各处重复算导致口径不一致。
        return self.qty * self.unit_price_minor - self.discount_minor - self.refund_minor


@dataclass(frozen=True, slots=True)
class Order:
    platform: str
    platform_order_id: str
    status: str
    currency: str
    paid_at: datetime
    updated_at: datetime
    lines: tuple[OrderLine, ...]
    raw_json: str | None = None
