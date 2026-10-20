import csv
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

from ecommerce.domain.errors import ValidationError
from ecommerce.domain.models import Order, OrderLine
from ecommerce.domain.money import MINOR_UNITS, MoneyError, parse_amount_to_minor
from ecommerce.domain.ports import PlatformPort
from ecommerce.repositories.sqlite import OrderRepository

REQUIRED_COLUMNS = (
    "platform", "order_id", "line_id", "sku", "qty", "unit_price",
    "currency", "status", "paid_at", "updated_at",
)
OPTIONAL_DEFAULTS = {"discount": "0", "refund": "0"}


@dataclass(frozen=True, slots=True)
class RowIssue:
    row_number: int
    field: str
    message: str


@dataclass(frozen=True, slots=True)
class ImportResult:
    inserted: int
    updated: int


class OrderService:
    def __init__(self, repo: OrderRepository):
        self._repo = repo

    def import_orders(self, orders: list[Order]) -> ImportResult:
        inserted = updated = 0
        for order in orders:
            if self._repo.upsert(order):
                inserted += 1
            else:
                updated += 1
        return ImportResult(inserted=inserted, updated=updated)

    def sync_orders(self, platform: PlatformPort, updated_since: datetime) -> ImportResult:
        platform.authenticate()
        # Day 7 会在这里加分页和游标；现在先跑通一页。
        page = platform.list_orders(updated_since, cursor=None)
        return self.import_orders(list(page.items))


def read_orders_csv(path: str | Path) -> tuple[list[Order], list[RowIssue], int]:
    """读取订单宽表：一行一条订单行，订单级字段在多行里重复。"""
    issues: list[RowIssue] = []
    groups: dict[tuple[str, str], list[tuple[int, dict[str, str]]]] = {}
    fetched = 0

    with open(path, newline="", encoding="utf-8-sig") as f:
        reader = csv.DictReader(f)
        missing = [c for c in REQUIRED_COLUMNS if c not in (reader.fieldnames or [])]
        if missing:
            raise ValidationError(f"CSV 缺少列: {missing}")

        for row_number, row in enumerate(reader, start=2):
            fetched += 1
            for key, default in OPTIONAL_DEFAULTS.items():
                value = row.get(key)
                if value is None or not value.strip():
                    row[key] = default

            platform = (row.get("platform") or "").strip()
            order_id = (row.get("order_id") or "").strip()
            if not platform or not order_id:
                issues.append(RowIssue(row_number, "platform/order_id", "不能为空"))
                continue
            groups.setdefault((platform, order_id), []).append((row_number, row))

    orders: list[Order] = []
    for (platform, order_id), rows in groups.items():
        order = _build_order(platform, order_id, rows, issues)
        if order is not None:
            orders.append(order)
    return orders, issues, fetched


def _build_order(
    platform: str,
    order_id: str,
    rows: list[tuple[int, dict[str, str]]],
    issues: list[RowIssue],
) -> Order | None:
    def fail(row_number: int, field: str, message: str) -> None:
        issues.append(RowIssue(row_number, field, f"订单 {order_id}: {message}"))

    # 订单级字段必须一致；不一致说明源数据有问题，无法判断哪个是真相，整单跳过。
    first_number, first = rows[0]
    for field in ("status", "currency", "paid_at", "updated_at"):
        values = {(row.get(field) or "").strip() for _, row in rows}
        if len(values) != 1:
            fail(first_number, field, f"同一订单的 {field} 不一致: {sorted(values)}")
            return None

    currency = (first.get("currency") or "").strip().upper()
    if currency not in MINOR_UNITS:
        fail(first_number, "currency", f"不支持的币种: {currency}")
        return None

    try:
        paid_at = _parse_datetime(first.get("paid_at") or "", "paid_at")
        updated_at = _parse_datetime(first.get("updated_at") or "", "updated_at")
    except ValueError as exc:
        fail(first_number, "datetime", str(exc))
        return None

    lines: list[OrderLine] = []
    seen: dict[str, tuple[int, dict[str, str]]] = {}
    for row_number, row in rows:
        line_id = (row.get("line_id") or "").strip()
        if not line_id:
            fail(row_number, "line_id", "不能为空")
            return None

        if line_id in seen:
            prev_number, prev = seen[line_id]
            same = all((prev.get(k) or "") == (row.get(k) or "") for k in prev)
            if same:
                issues.append(RowIssue(
                    row_number, "line_id", f"重复行，已忽略（同订单第 {prev_number} 行）"
                ))
                continue
            fail(row_number, "line_id", f"与第 {prev_number} 行冲突")
            return None

        sku = (row.get("sku") or "").strip()
        if not sku:
            fail(row_number, "sku", "不能为空")
            return None

        try:
            qty = int((row.get("qty") or "").strip())
        except ValueError:
            fail(row_number, "qty", "必须是整数")
            return None
        if qty <= 0:
            fail(row_number, "qty", "必须大于 0")
            return None

        try:
            unit_price_minor = parse_amount_to_minor(row.get("unit_price") or "", currency)
            discount_minor = parse_amount_to_minor(row.get("discount") or "0", currency)
            refund_minor = parse_amount_to_minor(row.get("refund") or "0", currency)
        except MoneyError as exc:
            fail(row_number, "amount", str(exc))
            return None

        if unit_price_minor < 0:
            fail(row_number, "unit_price", "不能为负")
            return None
        if discount_minor < 0 or refund_minor < 0:
            fail(row_number, "discount/refund", "不能为负")
            return None

        seen[line_id] = (row_number, row)
        lines.append(OrderLine(
            platform=platform,
            platform_order_id=order_id,
            line_id=line_id,
            sku=sku,
            qty=qty,
            unit_price_minor=unit_price_minor,
            discount_minor=discount_minor,
            refund_minor=refund_minor,
            currency=currency,
        ))

    if not lines:
        fail(first_number, "lines", "订单没有任何有效行")
        return None

    return Order(
        platform=platform,
        platform_order_id=order_id,
        status=(first.get("status") or "").strip().lower(),
        currency=currency,
        paid_at=paid_at,
        updated_at=updated_at,
        lines=tuple(lines),
    )


def _parse_datetime(text: str, field: str) -> datetime:
    value = text.strip()
    if not value:
        raise ValueError(f"{field} 不能为空")
    try:
        dt = datetime.fromisoformat(value)
    except ValueError as exc:
        raise ValueError(f"{field} 不是合法 ISO 时间: {value!r}") from exc
    if dt.tzinfo is None:
        # 没有时区就无法比较先后、无法按日期取汇率，宁可报错也不猜。
        raise ValueError(f"{field} 必须带时区，例如 2026-10-01T10:00:00+08:00")
    return dt.astimezone(UTC)
