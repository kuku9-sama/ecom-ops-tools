from datetime import datetime

import pytest

from ecommerce.domain.errors import TransientError
from ecommerce.domain.models import Order, OrderLine
from ecommerce.domain.ports import Page
from ecommerce.repositories.sqlite import OrderRepository, SyncRepository
from ecommerce.services.order_service import OrderService

SINCE = datetime.fromisoformat("2026-10-01T00:00:00+00:00")


def _order(order_id: str) -> Order:
    return Order(
        platform="paged",
        platform_order_id=order_id,
        status="paid",
        currency="CNY",
        paid_at=datetime.fromisoformat("2026-10-01T10:00:00+08:00"),
        updated_at=datetime.fromisoformat("2026-10-01T10:05:00+08:00"),
        lines=(
            OrderLine(
                platform="paged",
                platform_order_id=order_id,
                line_id="L1",
                sku=f"SKU-{order_id}",
                qty=1,
                unit_price_minor=1000,
                discount_minor=0,
                refund_minor=0,
                currency="CNY",
            ),
        ),
    )


class PagedAdapter:
    platform = "paged"

    def __init__(self):
        self.cursors = []

    def authenticate(self):
        pass

    def list_orders(self, updated_since, cursor):
        self.cursors.append(cursor)
        if cursor is None:
            return Page(items=(_order("P1"),), next_cursor="1")
        return Page(items=(_order("P2"),), next_cursor=None)


class FlakyAdapter(PagedAdapter):
    def list_orders(self, updated_since, cursor):
        self.cursors.append(cursor)
        if cursor is None:
            return Page(items=(_order("P1"),), next_cursor="1")
        raise TransientError("boom")


class ResumeAdapter(PagedAdapter):
    def list_orders(self, updated_since, cursor):
        self.cursors.append(cursor)
        return Page(items=(_order("P2"),), next_cursor=None)


def test_sync_pages_and_records_run(db):
    sync = SyncRepository(db)
    service = OrderService(OrderRepository(db), sync)

    result = service.sync_orders(PagedAdapter(), SINCE)

    assert result.fetched == 2
    assert result.inserted == 2
    assert OrderRepository(db).count_orders() == 2
    run = sync.get_run(result.run_id)
    assert run["status"] == "succeeded"
    assert run["cursor_after"] is None
    assert sync.item_count(result.run_id) == 2
    assert sync.load_cursor("paged", "orders") is None
    assert sync.recent_runs(5)[0]["run_id"] == result.run_id


def test_failure_keeps_cursor_and_resume_continues(db):
    sync = SyncRepository(db)
    service = OrderService(OrderRepository(db), sync)

    with pytest.raises(TransientError):
        service.sync_orders(FlakyAdapter(), SINCE)

    run = sync.latest_run("paged", "orders")
    assert run["status"] == "failed"
    assert sync.load_cursor("paged", "orders") == "1"
    assert OrderRepository(db).count_orders() == 1

    resume = ResumeAdapter()
    result = service.sync_orders(resume, SINCE)

    assert resume.cursors[0] == "1"
    assert result.fetched == 1
    assert OrderRepository(db).count_orders() == 2
    assert sync.load_cursor("paged", "orders") is None
