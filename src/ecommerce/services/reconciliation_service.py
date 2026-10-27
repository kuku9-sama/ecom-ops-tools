import csv
from collections import Counter, defaultdict
from dataclasses import dataclass
from datetime import date
from decimal import Decimal, InvalidOperation
from pathlib import Path

import pandas as pd

from ecommerce.domain.models import BillLine, Order
from ecommerce.domain.money import (
    MINOR_UNITS,
    MoneyError,
    convert_minor,
    parse_amount_to_minor,
)

BILL_COLUMNS = (
    "platform", "bill_id", "order_id", "line_id", "settlement_currency",
    "gross", "fee", "refund", "net", "settled_at",
)
FX_COLUMNS = ("base", "quote", "rate", "effective_date", "source")
LINE_TOLERANCE = 1
AGGREGATE_TOLERANCE = 5


@dataclass(frozen=True, slots=True)
class ReconIssue:
    source: str
    row_number: int
    field: str
    message: str


@dataclass(frozen=True, slots=True)
class ReconRow:
    platform: str
    platform_order_id: str
    line_id: str
    category: str
    expected_minor: int | None
    actual_minor: int | None
    diff_minor: int | None
    fx_rate_used: str | None
    fx_rate_date: str | None
    fx_rate_source: str | None
    message: str


@dataclass(frozen=True, slots=True)
class ReconResult:
    rows: tuple[ReconRow, ...]
    counts: dict[str, int]


class MissingFxRate(Exception):
    pass


def read_bills_csv(
    path: str | Path,
) -> tuple[list[BillLine], list[ReconIssue], set[tuple[str, str, str]]]:
    bills: list[BillLine] = []
    issues: list[ReconIssue] = []
    duplicate_keys: set[tuple[str, str, str]] = set()
    first_rows: dict[tuple[str, str, str], int] = {}

    with open(path, newline="", encoding="utf-8-sig") as f:
        reader = csv.DictReader(f)
        missing = [c for c in BILL_COLUMNS if c not in (reader.fieldnames or [])]
        if missing:
            raise ValueError(f"CSV 缺少列: {missing}")

        for row_number, row in enumerate(reader, start=2):
            platform = (row.get("platform") or "").strip()
            bill_id = (row.get("bill_id") or "").strip()
            order_id = (row.get("order_id") or "").strip()
            line_id = (row.get("line_id") or "").strip()
            currency = (row.get("settlement_currency") or "").strip().upper()

            if not platform or not bill_id or not order_id:
                issues.append(
                    ReconIssue("bills", row_number, "key", "platform/bill_id/order_id 不能为空")
                )
                continue
            if currency not in MINOR_UNITS:
                issues.append(
                    ReconIssue("bills", row_number, "currency", f"不支持的币种: {currency}")
                )
                continue

            try:
                gross = parse_amount_to_minor(row.get("gross") or "", currency)
                fee = parse_amount_to_minor((row.get("fee") or "0") or "0", currency)
                refund = parse_amount_to_minor((row.get("refund") or "0") or "0", currency)
                net = parse_amount_to_minor(row.get("net") or "", currency)
            except MoneyError as exc:
                issues.append(ReconIssue("bills", row_number, "amount", str(exc)))
                continue
            if fee < 0 or refund < 0:
                issues.append(ReconIssue("bills", row_number, "fee/refund", "不能为负"))
                continue

            try:
                settled_at = date.fromisoformat((row.get("settled_at") or "").strip())
            except ValueError:
                issues.append(
                    ReconIssue("bills", row_number, "settled_at", "日期格式必须是 YYYY-MM-DD")
                )
                continue

            key = (platform, bill_id, line_id)
            if key in first_rows:
                issues.append(
                    ReconIssue(
                        "bills",
                        row_number,
                        "bill_id",
                        f"重复账单键，首次出现在第 {first_rows[key]} 行",
                    )
                )
                duplicate_keys.add(key)
            else:
                first_rows[key] = row_number

            bills.append(
                BillLine(
                    platform=platform,
                    bill_id=bill_id,
                    platform_order_id=order_id,
                    line_id=line_id,
                    settlement_currency=currency,
                    gross_minor=gross,
                    fee_minor=fee,
                    refund_minor=refund,
                    net_minor=net,
                    settled_at=settled_at,
                )
            )

    return bills, issues, duplicate_keys


def read_fx_csv(
    path: str | Path,
) -> tuple[dict[tuple[str, str, str], Decimal], list[ReconIssue]]:
    fx: dict[tuple[str, str, str], Decimal] = {}
    issues: list[ReconIssue] = []

    with open(path, newline="", encoding="utf-8-sig") as f:
        reader = csv.DictReader(f)
        missing = [c for c in FX_COLUMNS if c not in (reader.fieldnames or [])]
        if missing:
            raise ValueError(f"CSV 缺少列: {missing}")

        for row_number, row in enumerate(reader, start=2):
            base = (row.get("base") or "").strip().upper()
            quote = (row.get("quote") or "").strip().upper()
            if base not in MINOR_UNITS or quote not in MINOR_UNITS:
                issues.append(
                    ReconIssue("fx", row_number, "currency", f"不支持的币种: {base}->{quote}")
                )
                continue

            try:
                rate = Decimal((row.get("rate") or "").strip())
            except InvalidOperation:
                issues.append(ReconIssue("fx", row_number, "rate", "不是合法数字"))
                continue
            if rate <= 0:
                issues.append(ReconIssue("fx", row_number, "rate", "必须大于 0"))
                continue

            try:
                effective_date = date.fromisoformat((row.get("effective_date") or "").strip())
            except ValueError:
                issues.append(
                    ReconIssue("fx", row_number, "effective_date", "日期格式必须是 YYYY-MM-DD")
                )
                continue

            key = (base, quote, effective_date.isoformat())
            if key in fx:
                issues.append(ReconIssue("fx", row_number, "key", "重复汇率键，保留首次出现的值"))
                continue
            fx[key] = rate

    return fx, issues


def reconcile(
    orders: list[Order],
    bills: list[BillLine],
    fx: dict[tuple[str, str, str], Decimal],
    duplicate_keys: set[tuple[str, str, str]],
) -> ReconResult:
    rows: list[ReconRow] = []

    first_bill_by_key: dict[tuple[str, str, str], BillLine] = {}
    for bill in bills:
        first_bill_by_key.setdefault((bill.platform, bill.bill_id, bill.line_id), bill)

    for key in duplicate_keys:
        bill = first_bill_by_key.get(key)
        if bill is None:
            continue
        rows.append(
            ReconRow(
                platform=bill.platform,
                platform_order_id=bill.platform_order_id,
                line_id=bill.line_id,
                category="duplicate_bill",
                expected_minor=None,
                actual_minor=None,
                diff_minor=None,
                fx_rate_used=None,
                fx_rate_date=None,
                fx_rate_source=None,
                message="账单键重复，未参与匹配",
            )
        )

    filtered_bills = [
        bill for bill in bills
        if (bill.platform, bill.bill_id, bill.line_id) not in duplicate_keys
    ]

    match_key_counts = Counter(
        (bill.platform, bill.platform_order_id, bill.line_id)
        for bill in filtered_bills
        if bill.line_id
    )
    multi_keys = {key for key, count in match_key_counts.items() if count > 1}
    for platform, order_id, line_id in multi_keys:
        rows.append(
            ReconRow(
                platform=platform,
                platform_order_id=order_id,
                line_id=line_id,
                category="multiple_bills_for_line",
                expected_minor=None,
                actual_minor=None,
                diff_minor=None,
                fx_rate_used=None,
                fx_rate_date=None,
                fx_rate_source=None,
                message="同一订单行有多条不同账单，未参与匹配",
            )
        )
    filtered_bills = [
        bill for bill in filtered_bills
        if (bill.platform, bill.platform_order_id, bill.line_id) not in multi_keys
    ]

    order_records = []
    for order in orders:
        for line in order.lines:
            order_records.append(
                {
                    "platform": line.platform,
                    "platform_order_id": line.platform_order_id,
                    "line_id": line.line_id,
                    "sku": line.sku,
                    "order_net_minor": line.net_minor,
                    "order_refund_minor": line.refund_minor,
                    "currency": line.currency,
                    "paid_at": order.paid_at.date().isoformat(),
                }
            )

    bill_records = [
        {
            "platform": bill.platform,
            "platform_order_id": bill.platform_order_id,
            "line_id": bill.line_id,
            "bill_id": bill.bill_id,
            "settlement_currency": bill.settlement_currency,
            "gross_minor": bill.gross_minor,
            "fee_minor": bill.fee_minor,
            "bill_refund_minor": bill.refund_minor,
            "bill_net_minor": bill.net_minor,
            "settled_at": bill.settled_at.isoformat(),
        }
        for bill in filtered_bills
    ]

    if not order_records:
        for record in bill_records:
            rows.append(_orphan_row(record))
        return _result(rows)

    if not bill_records:
        for record in order_records:
            rows.append(_missing_row(record))
        return _result(rows)

    merged = pd.DataFrame(order_records).merge(
        pd.DataFrame(bill_records),
        on=["platform", "platform_order_id", "line_id"],
        how="outer",
        validate="one_to_one",
        indicator=True,
    )

    left_by_order: dict[tuple[str, str], list] = defaultdict(list)
    right_by_order: dict[tuple[str, str], list] = defaultdict(list)
    for _, row in merged.iterrows():
        key = (str(row["platform"]), str(row["platform_order_id"]))
        if row["_merge"] == "left_only":
            left_by_order[key].append(row)
        elif row["_merge"] == "right_only":
            right_by_order[key].append(row)

    # 行级没匹配上，但订单两侧各有一方时，才做订单级兜底。
    aggregate_keys = {
        key for key in left_by_order
        if len(right_by_order.get(key, [])) == 1
    }

    for _, row in merged.iterrows():
        key = (str(row["platform"]), str(row["platform_order_id"]))
        if row["_merge"] == "left_only":
            if key in aggregate_keys:
                continue
            rows.append(_missing_row(row))
            continue
        if row["_merge"] == "right_only":
            if key in aggregate_keys:
                continue
            rows.append(_orphan_row(row))
            continue
        rows.append(_compare_row(row, fx))

    for key in aggregate_keys:
        rows.append(_aggregate_row(left_by_order[key], right_by_order[key][0], fx))

    return _result(rows)


def _rate_for(
    base: str, quote: str, settled_at: str, paid_at: str,
    fx: dict[tuple[str, str, str], Decimal],
) -> tuple[Decimal, str, str]:
    if base == quote:
        return Decimal("1"), settled_at, "same_currency"

    rate = fx.get((base, quote, settled_at))
    if rate is not None:
        return rate, settled_at, "settlement_date"

    rate = fx.get((base, quote, paid_at))
    if rate is not None:
        return rate, paid_at, "order_date_fallback"

    raise MissingFxRate(f"缺少汇率 {base}->{quote}，结算日 {settled_at}，订单日 {paid_at}")


def _compare_row(row, fx: dict[tuple[str, str, str], Decimal]) -> ReconRow:
    platform = str(row["platform"])
    order_id = str(row["platform_order_id"])
    line_id = str(row["line_id"])
    gross = int(row["gross_minor"])
    fee = int(row["fee_minor"])
    bill_refund = int(row["bill_refund_minor"])
    bill_net = int(row["bill_net_minor"])
    order_net = int(row["order_net_minor"])
    order_refund = int(row["order_refund_minor"])

    try:
        rate, rate_date, rate_source = _rate_for(
            str(row["currency"]),
            str(row["settlement_currency"]),
            str(row["settled_at"]),
            str(row["paid_at"]),
            fx,
        )
    except MissingFxRate as exc:
        return ReconRow(
            platform, order_id, line_id, "fx_missing",
            None, gross, None, None, None, None, str(exc),
        )

    expected = convert_minor(order_net, rate)
    expected_refund = convert_minor(order_refund, rate)
    diff = gross - expected

    if bill_net != gross - fee - bill_refund:
        category = "bill_inconsistent"
        message = "账单 net != gross - fee - refund"
    elif abs(bill_refund - expected_refund) > LINE_TOLERANCE:
        category = "refund_mismatch"
        message = f"账单退款 {bill_refund}，订单退款换算后 {expected_refund}"
    elif diff == 0:
        category = "matched"
        message = ""
    elif abs(diff) <= LINE_TOLERANCE:
        category = "fx_or_rounding_diff"
        message = f"差异 {diff}，在容差内"
    else:
        category = "amount_mismatch"
        message = f"账单金额 {gross}，订单换算后 {expected}"

    return ReconRow(
        platform, order_id, line_id, category,
        expected, gross, diff,
        str(rate), rate_date, rate_source, message,
    )


def _aggregate_row(left_rows, bill_row, fx: dict[tuple[str, str, str], Decimal]) -> ReconRow:
    platform = str(bill_row["platform"])
    order_id = str(bill_row["platform_order_id"])
    settlement_currency = str(bill_row["settlement_currency"])
    settled_at = str(bill_row["settled_at"])
    bill_id = str(bill_row["bill_id"])
    bill_line_id = str(bill_row["line_id"])
    bill_ref = bill_id if not bill_line_id else f"{bill_id}/{bill_line_id}"

    gross = int(bill_row["gross_minor"])
    fee = int(bill_row["fee_minor"])
    bill_refund = int(bill_row["bill_refund_minor"])
    bill_net = int(bill_row["bill_net_minor"])

    expected = 0
    expected_refund = 0
    first_rate = None
    first_date = None
    first_source = None
    try:
        for row in left_rows:
            rate, rate_date, rate_source = _rate_for(
                str(row["currency"]),
                settlement_currency,
                settled_at,
                str(row["paid_at"]),
                fx,
            )
            if first_rate is None:
                first_rate = str(rate)
                first_date = rate_date
                first_source = rate_source
            expected += convert_minor(int(row["order_net_minor"]), rate)
            expected_refund += convert_minor(int(row["order_refund_minor"]), rate)
    except MissingFxRate as exc:
        return ReconRow(
            platform, order_id, "", "fx_missing",
            None, gross, None, None, None, None, str(exc),
        )

    diff = gross - expected
    message = f"订单级兜底（账单 {bill_ref}）"
    if bill_net != gross - fee - bill_refund:
        category = "bill_inconsistent"
        message += " net != gross - fee - refund"
    elif abs(bill_refund - expected_refund) > AGGREGATE_TOLERANCE:
        category = "refund_mismatch"
        message += " 退款不一致"
    elif abs(diff) <= AGGREGATE_TOLERANCE:
        category = "aggregated_bill"
    else:
        category = "amount_mismatch"
        message += f" 金额差异 {diff}"

    return ReconRow(
        platform, order_id, "", category,
        expected, gross, diff,
        first_rate, first_date, first_source, message,
    )


def _missing_row(row) -> ReconRow:
    return ReconRow(
        str(row["platform"]), str(row["platform_order_id"]), str(row["line_id"]),
        "missing_order", None, None, None, None, None, None,
        "有订单行，没有对应账单",
    )


def _orphan_row(row) -> ReconRow:
    line_id = str(row["line_id"])
    message = "订单级聚合账单，待订单级兜底" if not line_id else "有账单，没有对应订单行"
    return ReconRow(
        str(row["platform"]), str(row["platform_order_id"]), line_id,
        "orphan_bill", None, int(row["gross_minor"]), None, None, None, None,
        message,
    )


def _result(rows: list[ReconRow]) -> ReconResult:
    counts = dict(Counter(row.category for row in rows))
    return ReconResult(rows=tuple(rows), counts=counts)
