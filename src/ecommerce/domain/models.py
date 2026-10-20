from dataclasses import dataclass
from datetime import date, datetime


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


@dataclass(frozen=True, slots=True)
class ProductDraft:
    sku: str
    title: str
    price_minor: int
    currency: str
    stock: int
    category: str
    image_url: str


@dataclass(frozen=True, slots=True)
class BillLine:
    platform: str
    bill_id: str
    platform_order_id: str
    line_id: str
    settlement_currency: str
    gross_minor: int
    fee_minor: int
    refund_minor: int
    net_minor: int
    settled_at: date

    @property
    def is_internally_consistent(self) -> bool:
        return self.net_minor == self.gross_minor - self.fee_minor - self.refund_minor
