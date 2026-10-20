from pathlib import Path

from ecommerce.repositories.sqlite import OrderRepository
from ecommerce.services.order_service import OrderService, read_orders_csv

DATA = Path(__file__).resolve().parents[1] / "data" / "orders.csv"


def test_invalid_rows_are_reported_not_imported(db):
    orders, issues, fetched = read_orders_csv(DATA)

    assert fetched == 20
    assert len(orders) == 7
    assert len(issues) == 8

    messages = [issue.message for issue in issues]
    assert any("2026-13-40" in message for message in messages)
    assert any("RMB" in message for message in messages)
    assert any("冲突" in message for message in messages)
    assert any("重复行" in message for message in messages)

    with db:
        OrderService(OrderRepository(db)).import_orders(orders)

    assert OrderRepository(db).count_orders() == 7
