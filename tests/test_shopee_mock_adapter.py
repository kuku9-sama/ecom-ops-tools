from datetime import datetime

from ecommerce.adapters.shopee_mock import ShopeeMockAdapter
from ecommerce.repositories.sqlite import OrderRepository
from ecommerce.services.order_service import OrderService


def test_list_orders_filters_by_updated_since():
    adapter = ShopeeMockAdapter()
    adapter.authenticate()

    since = datetime.fromisoformat("2026-10-01T00:00:00+00:00")
    page = adapter.list_orders(since, cursor=None)
    assert len(page.items) == 2
    assert page.next_cursor is None

    later = datetime.fromisoformat("2026-10-02T00:00:00+00:00")
    page = adapter.list_orders(later, cursor=None)
    assert [o.platform_order_id for o in page.items] == ["S1002"]


def test_sync_orders_is_idempotent(db):
    adapter = ShopeeMockAdapter()
    repo = OrderRepository(db)
    service = OrderService(repo)
    since = datetime.fromisoformat("2026-10-01T00:00:00+00:00")

    with db:
        first = service.sync_orders(adapter, since)
    assert first.inserted == 2
    assert first.updated == 0

    with db:
        second = service.sync_orders(adapter, since)
    assert second.inserted == 0
    assert second.updated == 2

    assert repo.count_orders() == 2
    assert repo.count_order_lines() == 2
