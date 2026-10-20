from datetime import datetime

from ecommerce.adapters.shopee_mock import ShopeeMockAdapter
from ecommerce.repositories.sqlite import OrderRepository, SyncRepository
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
    sync = SyncRepository(db)
    service = OrderService(repo, sync)
    since = datetime.fromisoformat("2026-10-01T00:00:00+00:00")

    first = service.sync_orders(adapter, since)
    assert first.fetched == 2
    assert first.inserted == 2
    assert first.updated == 0

    second = service.sync_orders(adapter, since)
    assert second.inserted == 0
    assert second.updated == 2

    assert repo.count_orders() == 2
    assert repo.count_order_lines() == 2
    run = sync.get_run(first.run_id)
    assert run["status"] == "succeeded"
    assert sync.item_count(first.run_id) == 2
    assert sync.load_cursor("shopee_mock", "orders") is None
