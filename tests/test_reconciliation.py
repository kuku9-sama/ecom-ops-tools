from datetime import date, datetime
from decimal import Decimal
from pathlib import Path

from ecommerce.domain.models import BillLine, Order, OrderLine
from ecommerce.services.order_service import read_orders_csv
from ecommerce.services.reconciliation_service import (
    read_bills_csv,
    read_fx_csv,
    reconcile,
)

DATA = Path(__file__).resolve().parents[1] / "data"


def test_read_bills_detects_duplicate_key():
    bills, issues, duplicate_keys = read_bills_csv(DATA / "bills.csv")

    assert len(bills) == 13
    assert ("shopee_mock", "B001", "L1") in duplicate_keys
    assert any("重复账单键" in issue.message for issue in issues)


def test_read_fx_reports_invalid_and_duplicate():
    fx, issues = read_fx_csv(DATA / "fx_rates.csv")

    assert len(issues) == 2
    assert ("USD", "CNY", "2026-10-05") in fx
    assert any("不是合法数字" in issue.message for issue in issues)
    assert any("重复汇率键" in issue.message for issue in issues)


def test_line_level_reconciliation_counts():
    orders, _, _ = read_orders_csv(DATA / "orders.csv")
    bills, _, duplicate_keys = read_bills_csv(DATA / "bills.csv")
    fx, _ = read_fx_csv(DATA / "fx_rates.csv")

    result = reconcile(orders, bills, fx, duplicate_keys)
    counts = result.counts

    assert counts.get("matched") == 1
    assert counts.get("fx_or_rounding_diff") == 1
    assert counts.get("amount_mismatch") == 1
    assert counts.get("bill_inconsistent") == 1
    assert counts.get("refund_mismatch") == 1
    assert counts.get("fx_missing") == 1
    assert counts.get("duplicate_bill") == 1
    assert counts.get("multiple_bills_for_line") == 1
    assert counts.get("missing_order") == 5
    assert counts.get("orphan_bill") == 3

    matched = next(r for r in result.rows if r.category == "matched")
    assert matched.platform_order_id == "O1015"
    assert matched.expected_minor == 3000
    assert matched.actual_minor == 3000

    rounding = next(r for r in result.rows if r.category == "fx_or_rounding_diff")
    assert rounding.platform_order_id == "O1008"
    assert rounding.line_id == "L1"
    assert rounding.diff_minor == -1

    inconsistent = next(r for r in result.rows if r.category == "bill_inconsistent")
    assert inconsistent.platform_order_id == "O1013"

    refund = next(r for r in result.rows if r.category == "refund_mismatch")
    assert refund.platform_order_id == "O1007"

    missing_fx = next(r for r in result.rows if r.category == "fx_missing")
    assert missing_fx.platform_order_id == "O1008"
    assert missing_fx.line_id == "L2"


def test_fx_falls_back_to_order_date():
    order = Order(
        platform="p",
        platform_order_id="O1",
        status="paid",
        currency="USD",
        paid_at=datetime.fromisoformat("2026-10-01T10:00:00+08:00"),
        updated_at=datetime.fromisoformat("2026-10-01T10:05:00+08:00"),
        lines=(
            OrderLine(
                platform="p",
                platform_order_id="O1",
                line_id="L1",
                sku="S1",
                qty=1,
                unit_price_minor=1000,
                discount_minor=0,
                refund_minor=0,
                currency="USD",
            ),
        ),
    )
    bill = BillLine(
        platform="p",
        bill_id="B1",
        platform_order_id="O1",
        line_id="L1",
        settlement_currency="CNY",
        gross_minor=7250,
        fee_minor=0,
        refund_minor=0,
        net_minor=7250,
        settled_at=date(2026, 10, 10),
    )
    fx = {("USD", "CNY", "2026-10-01"): Decimal("7.25")}

    result = reconcile([order], [bill], fx, set())

    row = result.rows[0]
    assert row.category == "matched"
    assert row.fx_rate_source == "order_date_fallback"
    assert row.fx_rate_date == "2026-10-01"
