from pathlib import Path

import pytest

from ecommerce.adapters.shopee_mock import ShopeeMockAdapter
from ecommerce.domain.models import InventoryItem
from ecommerce.services.inventory_service import (
    InventoryService,
    diff_inventory,
    read_local_stock_csv,
)

DATA = Path(__file__).resolve().parents[1] / "data"


def test_read_local_stock_csv():
    assert read_local_stock_csv(DATA / "local_stock.csv") == {
        "MON-001": 20,
        "KB-001": 50,
        "ACC-001": 100,
    }


def test_diff_inventory_categories():
    items = [
        InventoryItem(platform="p", sku="A", stock=10),
        InventoryItem(platform="p", sku="B", stock=5),
        InventoryItem(platform="p", sku="C", stock=7),
    ]
    local = {"A": 10, "B": 9, "D": 3}

    rows = {row.sku: row for row in diff_inventory("p", items, local)}

    assert rows["A"].category == "match"
    assert rows["B"].category == "mismatch"
    assert rows["B"].diff == -4
    assert rows["C"].category == "missing_locally"
    assert rows["D"].category == "missing_on_platform"


def test_inventory_service_with_shopee_mock():
    rows = InventoryService(ShopeeMockAdapter()).diff(
        {"MON-001": 20, "KB-001": 50, "ACC-001": 100}
    )
    counts = {}
    for row in rows:
        counts[row.category] = counts.get(row.category, 0) + 1

    assert counts["match"] == 1
    assert counts["missing_locally"] == 1
    assert counts["missing_on_platform"] == 2


def test_local_stock_rejects_duplicate_sku(tmp_path):
    path = tmp_path / "stock.csv"
    path.write_text("sku,stock\nA,1\nA,2\n", encoding="utf-8")

    with pytest.raises(ValueError):
        read_local_stock_csv(path)
