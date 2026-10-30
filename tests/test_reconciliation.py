import json
from argparse import Namespace
from datetime import date, datetime
from decimal import Decimal
from pathlib import Path

from ecommerce.cli import cmd_reconcile
from ecommerce.domain.models import BillLine, Order, OrderLine
from ecommerce.services.order_service import read_orders_csv
from ecommerce.services.reconciliation_service import (
    ReconReportPaths,
    read_bills_csv,
    read_fx_csv,
    reconcile,
    write_recon_reports,
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
    assert counts.get("aggregated_bill") == 2
    assert counts.get("missing_order") == 2
    assert counts.get("orphan_bill") == 1
    assert len(result.rows) == 13

    aggregated = sorted(
        r.platform_order_id for r in result.rows if r.category == "aggregated_bill"
    )
    assert aggregated == ["O1011", "O1012"]

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


def test_order_level_amount_mismatch():
    order = Order(
        platform="p",
        platform_order_id="O2",
        status="paid",
        currency="CNY",
        paid_at=datetime.fromisoformat("2026-10-01T10:00:00+08:00"),
        updated_at=datetime.fromisoformat("2026-10-01T10:05:00+08:00"),
        lines=(
            OrderLine(
                platform="p", platform_order_id="O2", line_id="L1", sku="S1",
                qty=1, unit_price_minor=1000, discount_minor=0, refund_minor=0,
                currency="CNY",
            ),
            OrderLine(
                platform="p", platform_order_id="O2", line_id="L2", sku="S2",
                qty=1, unit_price_minor=2000, discount_minor=0, refund_minor=0,
                currency="CNY",
            ),
        ),
    )
    bill = BillLine(
        platform="p",
        bill_id="B2",
        platform_order_id="O2",
        line_id="",
        settlement_currency="CNY",
        gross_minor=2990,
        fee_minor=0,
        refund_minor=0,
        net_minor=2990,
        settled_at=date(2026, 10, 2),
    )

    result = reconcile([order], [bill], {}, set())

    row = result.rows[0]
    assert row.category == "amount_mismatch"
    assert row.line_id == ""
    assert row.diff_minor == -10


def test_cli_writes_reports(tmp_path):
    args = Namespace(
        orders=str(DATA / "orders.csv"),
        bills=str(DATA / "bills.csv"),
        fx=str(DATA / "fx_rates.csv"),
        out=str(tmp_path / "recon.csv"),
        summary=str(tmp_path / "summary.json"),
        unmatched=str(tmp_path / "unmatched.csv"),
        data_quality=str(tmp_path / "data_quality.csv"),
    )

    assert cmd_reconcile(args) == 0

    summary = json.loads((tmp_path / "summary.json").read_text(encoding="utf-8"))
    assert summary["counts"]["aggregated_bill"] == 2
    assert summary["counts"]["missing_order"] == 2
    assert (tmp_path / "unmatched.csv").exists()
    assert (tmp_path / "data_quality.csv").exists()


def test_write_recon_reports_writes_four_files(tmp_path):
    orders, _, _ = read_orders_csv(DATA / "orders.csv")
    bills, _, duplicate_keys = read_bills_csv(DATA / "bills.csv")
    fx, _ = read_fx_csv(DATA / "fx_rates.csv")
    result = reconcile(orders, bills, fx, duplicate_keys)

    paths = ReconReportPaths(
        out=tmp_path / "recon.csv",
        summary=tmp_path / "summary.json",
        unmatched=tmp_path / "unmatched.csv",
        data_quality=tmp_path / "data_quality.csv",
    )
    write_recon_reports(result, paths)

    assert paths.out.read_bytes().startswith(b"\xef\xbb\xbf")
    summary = json.loads(paths.summary.read_text(encoding="utf-8"))
    assert summary["rows"] == len(result.rows)
    assert summary["counts"] == result.counts
    assert paths.unmatched.exists()
    assert paths.data_quality.exists()
