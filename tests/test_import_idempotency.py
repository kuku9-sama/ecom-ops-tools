from pathlib import Path

from ecommerce.repositories.sqlite import OrderRepository
from ecommerce.services.order_service import OrderService, read_orders_csv

DATA = Path(__file__).resolve().parents[1] / "data" / "orders.csv"


def test_import_twice_does_not_duplicate(db):
    orders, issues, fetched = read_orders_csv(DATA)

    assert fetched == 21
    assert len(orders) == 8
    assert sum(len(order.lines) for order in orders) == 11
    assert len(issues) == 8

    repo = OrderRepository(db)
    service = OrderService(repo)

    with db:
        first = service.import_orders(orders)
    assert first.inserted == 8
    assert first.updated == 0

    with db:
        second = service.import_orders(orders)
    assert second.inserted == 0
    assert second.updated == 8

    assert repo.count_orders() == 8
    assert repo.count_order_lines() == 11


def test_paid_at_is_stored_in_utc():
    orders, _, _ = read_orders_csv(DATA)
    order = next(o for o in orders if o.platform_order_id == "O1001")

    assert order.paid_at.isoformat() == "2026-10-01T02:00:00+00:00"
