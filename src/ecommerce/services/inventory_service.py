import csv
from datetime import UTC, datetime
from pathlib import Path

from ecommerce.domain.models import InventoryDiff, InventoryItem
from ecommerce.domain.ports import PlatformPort


def read_local_stock_csv(path: str | Path) -> dict[str, int]:
    stock: dict[str, int] = {}
    with open(path, newline="", encoding="utf-8-sig") as f:
        reader = csv.DictReader(f)
        required = ("sku", "stock")
        missing = [c for c in required if c not in (reader.fieldnames or [])]
        if missing:
            raise ValueError(f"CSV 缺少列: {missing}")

        for row_number, row in enumerate(reader, start=2):
            sku = (row.get("sku") or "").strip()
            if not sku:
                raise ValueError(f"第 {row_number} 行 SKU 为空")
            if sku in stock:
                raise ValueError(f"第 {row_number} 行 SKU 重复: {sku}")
            try:
                value = int((row.get("stock") or "").strip())
            except ValueError as exc:
                raise ValueError(f"第 {row_number} 行库存不是整数") from exc
            if value < 0:
                raise ValueError(f"第 {row_number} 行库存不能为负")
            stock[sku] = value
    return stock


def diff_inventory(
    platform: str,
    platform_items: list[InventoryItem],
    local_stock: dict[str, int],
) -> list[InventoryDiff]:
    platform_by_sku = {item.sku: item.stock for item in platform_items}
    snapshot_at = datetime.now(UTC).isoformat()
    rows: list[InventoryDiff] = []
    for sku in sorted(set(platform_by_sku) | set(local_stock)):
        platform_value = platform_by_sku.get(sku)
        local_value = local_stock.get(sku)
        if platform_value is None:
            category = "missing_on_platform"
            diff = None
        elif local_value is None:
            category = "missing_locally"
            diff = None
        else:
            diff = platform_value - local_value
            category = "match" if diff == 0 else "mismatch"
        rows.append(
            InventoryDiff(
                platform=platform,
                sku=sku,
                platform_stock=platform_value,
                local_stock=local_value,
                diff=diff,
                category=category,
                snapshot_at=snapshot_at,
            )
        )
    return rows


class InventoryService:
    def __init__(self, platform: PlatformPort):
        self._platform = platform

    def fetch_snapshot(self) -> list[InventoryItem]:
        self._platform.authenticate()
        items: list[InventoryItem] = []
        cursor = None
        while True:
            page = self._platform.list_inventory(cursor)
            items.extend(page.items)
            cursor = page.next_cursor
            if cursor is None:
                break
        return items

    def diff(self, local_stock: dict[str, int]) -> list[InventoryDiff]:
        return diff_inventory(
            self._platform.platform, self.fetch_snapshot(), local_stock
        )
