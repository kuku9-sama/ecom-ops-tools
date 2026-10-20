from datetime import datetime

from ecommerce.domain.models import Order, OrderLine
from ecommerce.domain.ports import Page


class ShopeeMockAdapter:
    """模拟现有的 Shopee-like 框架：数据在内存里，不访问网络。"""

    platform = "shopee_mock"

    def __init__(self) -> None:
        self._authenticated = False
        self._orders = [
            Order(
                platform=self.platform,
                platform_order_id="S1001",
                status="paid",
                currency="CNY",
                paid_at=datetime.fromisoformat("2026-10-01T10:00:00+08:00"),
                updated_at=datetime.fromisoformat("2026-10-01T10:05:00+08:00"),
                lines=(
                    OrderLine(
                        platform=self.platform,
                        platform_order_id="S1001",
                        line_id="L1",
                        sku="MON-001",
                        qty=1,
                        unit_price_minor=129900,
                        discount_minor=0,
                        refund_minor=0,
                        currency="CNY",
                    ),
                ),
            ),
            Order(
                platform=self.platform,
                platform_order_id="S1002",
                status="paid",
                currency="USD",
                paid_at=datetime.fromisoformat("2026-10-02T09:00:00+08:00"),
                updated_at=datetime.fromisoformat("2026-10-02T09:05:00+08:00"),
                lines=(
                    OrderLine(
                        platform=self.platform,
                        platform_order_id="S1002",
                        line_id="L1",
                        sku="USB-001",
                        qty=2,
                        unit_price_minor=2500,
                        discount_minor=0,
                        refund_minor=0,
                        currency="USD",
                    ),
                ),
            ),
        ]

    def authenticate(self) -> None:
        self._authenticated = True

    def list_orders(self, updated_since: datetime, cursor: str | None) -> Page[Order]:
        if not self._authenticated:
            # 认证失败是调用方的编程错误，不是平台返回的 401。
            raise RuntimeError("authenticate() must be called first")

        items = tuple(
            order for order in self._orders if order.updated_at >= updated_since
        )
        return Page(items=items, next_cursor=None)
