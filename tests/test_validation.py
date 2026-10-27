from pathlib import Path

import pytest

from ecommerce.domain.errors import ValidationError
from ecommerce.repositories.sqlite import OrderRepository
from ecommerce.services.order_service import OrderService, read_orders_csv

DATA = Path(__file__).resolve().parents[1] / "data" / "orders.csv"


def test_invalid_rows_are_reported_not_imported(db):
    orders, issues, fetched = read_orders_csv(DATA)

    assert fetched == 21
    assert len(orders) == 8
    assert len(issues) == 8

    messages = [issue.message for issue in issues]
    assert any("2026-13-40" in message for message in messages)
    assert any("RMB" in message for message in messages)
    assert any("冲突" in message for message in messages)
    assert any("重复行" in message for message in messages)

    with db:
        OrderService(OrderRepository(db)).import_orders(orders)

    assert OrderRepository(db).count_orders() == 8


def test_missing_column_raises(tmp_path):
    path = tmp_path / "bad.csv"
    path.write_text("platform,order_id\nshopee_mock,O1\n", encoding="utf-8")

    with pytest.raises(ValidationError):
        read_orders_csv(path)
