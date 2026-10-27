import argparse
import csv
import json
from collections import Counter
from dataclasses import asdict
from datetime import datetime
from pathlib import Path

from ecommerce import __version__
from ecommerce.adapters.lazada import LazadaAdapter
from ecommerce.adapters.shopee_mock import ShopeeMockAdapter
from ecommerce.domain.errors import EcommerceError
from ecommerce.domain.models import InventoryDiff
from ecommerce.logging_setup import log_event, setup_logging
from ecommerce.repositories.sqlite import (
    OrderRepository,
    ProductRepository,
    SyncRepository,
    connect,
    init_db,
)
from ecommerce.services.inventory_service import InventoryService, read_local_stock_csv
from ecommerce.services.order_service import OrderService, read_orders_csv
from ecommerce.services.product_service import ProductService, read_products_csv
from ecommerce.services.reconciliation_service import (
    ReconRow,
    read_bills_csv,
    read_fx_csv,
    reconcile,
)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="ecommerce")
    parser.add_argument("--version", action="version", version=__version__)
    sub = parser.add_subparsers(dest="command", required=True)

    p_import = sub.add_parser("import-orders", help="从 CSV 导入订单")
    p_import.add_argument("--csv", required=True)
    p_import.add_argument("--db", default="data/ecommerce.db")

    p_report = sub.add_parser("report", help="打印数据库摘要")
    p_report.add_argument("--db", default="data/ecommerce.db")

    p_sync = sub.add_parser("sync-orders", help="从假适配器或 HTTP Mock 同步订单")
    p_sync.add_argument("--platform", default="shopee_mock")
    p_sync.add_argument("--since", required=True, help="ISO 时间，必须带时区")
    p_sync.add_argument("--base-url", default="http://127.0.0.1:8100")
    p_sync.add_argument("--page-size", type=int, default=100)
    p_sync.add_argument("--db", default="data/ecommerce.db")

    p_products = sub.add_parser("import-products", help="从 CSV 创建商品草稿")
    p_products.add_argument("--csv", required=True)
    p_products.add_argument("--platform", default="shopee_mock")
    p_products.add_argument("--base-url", default="http://127.0.0.1:8100")
    p_products.add_argument("--db", default="data/ecommerce.db")

    p_reconcile = sub.add_parser("reconcile", help="按订单行核对账单")
    p_reconcile.add_argument("--orders", default="data/orders.csv")
    p_reconcile.add_argument("--bills", default="data/bills.csv")
    p_reconcile.add_argument("--fx", default="data/fx_rates.csv")
    p_reconcile.add_argument("--out", default="outputs/reconciliation.csv")
    p_reconcile.add_argument("--summary", default="outputs/reconciliation_summary.json")
    p_reconcile.add_argument("--unmatched", default="outputs/unmatched.csv")
    p_reconcile.add_argument("--data-quality", default="outputs/data_quality.csv")

    p_inventory = sub.add_parser("inventory", help="比对平台库存与本地库存")
    p_inventory.add_argument("--platform", default="shopee_mock")
    p_inventory.add_argument("--base-url", default="http://127.0.0.1:8100")
    p_inventory.add_argument("--local", default="data/local_stock.csv")
    p_inventory.add_argument("--out", default="outputs/inventory_diff.csv")

    p_serve = sub.add_parser("serve", help="启动本地看板")
    p_serve.add_argument("--host", default="127.0.0.1")
    p_serve.add_argument("--port", type=int, default=8000)

    return parser


def cmd_import_orders(args: argparse.Namespace) -> int:
    orders, issues, fetched = read_orders_csv(args.csv)

    conn = connect(args.db)
    try:
        init_db(conn)
        # with conn 是事务：正常结束提交，异常回滚；它不会关闭连接。
        with conn:
            result = OrderService(OrderRepository(conn)).import_orders(orders)
    finally:
        conn.close()

    for issue in issues:
        log_event(
            "row_issue",
            row=issue.row_number,
            field=issue.field,
            message=issue.message,
        )

    print(
        f"fetched={fetched} valid_orders={len(orders)} "
        f"valid_lines={sum(len(o.lines) for o in orders)} issues={len(issues)} "
        f"inserted={result.inserted} updated={result.updated}"
    )
    return 0


def cmd_sync_orders(args: argparse.Namespace) -> int:
    if not 1 <= args.page_size <= 100:
        print("--page-size 必须在 1–100 之间")
        return 2

    if args.platform == "shopee_mock":
        adapter = ShopeeMockAdapter()
    elif args.platform == "lazada":
        adapter = LazadaAdapter(args.base_url, page_size=args.page_size)
    else:
        print(f"unsupported platform: {args.platform}")
        return 2

    try:
        since = datetime.fromisoformat(args.since)
    except ValueError:
        print("--since 不是合法 ISO 时间")
        return 2
    if since.tzinfo is None:
        print("--since 必须带时区，例如 2026-10-01T00:00:00+00:00")
        return 2

    conn = connect(args.db)
    try:
        init_db(conn)
        result = OrderService(
            OrderRepository(conn), SyncRepository(conn)
        ).sync_orders(adapter, since)
    except EcommerceError as exc:
        print(f"sync failed: {exc}")
        return 1
    finally:
        conn.close()

    print(
        f"run_id={result.run_id} fetched={result.fetched} "
        f"inserted={result.inserted} updated={result.updated}"
    )
    return 0


def cmd_import_products(args: argparse.Namespace) -> int:
    items, issues = read_products_csv(args.csv)

    if args.platform == "shopee_mock":
        adapter = ShopeeMockAdapter()
    elif args.platform == "lazada":
        adapter = LazadaAdapter(args.base_url)
    else:
        print(f"unsupported platform: {args.platform}")
        return 2

    conn = connect(args.db)
    try:
        init_db(conn)
        service = ProductService(ProductRepository(conn), adapter)
        with conn:
            results = service.import_drafts(items)
    except EcommerceError as exc:
        print(f"import failed: {exc}")
        return 1
    finally:
        conn.close()

    for issue in issues:
        log_event(
            "product_issue",
            row=issue.row_number,
            field=issue.field,
            message=issue.message,
        )
    for result in results:
        log_event(
            "product_result",
            row=result.row_number,
            sku=result.sku,
            status=result.status,
            platform_id=result.platform_id,
            message=result.message,
        )

    created = sum(1 for r in results if r.status == "created")
    exists = sum(1 for r in results if r.status == "exists")
    failed = sum(1 for r in results if r.status == "failed")
    print(
        f"parsed={len(items) + len(issues)} valid={len(items)} invalid={len(issues)} "
        f"created={created} exists={exists} failed={failed}"
    )
    return 0


def cmd_inventory(args: argparse.Namespace) -> int:
    if args.platform == "shopee_mock":
        adapter = ShopeeMockAdapter()
    elif args.platform == "lazada":
        adapter = LazadaAdapter(args.base_url)
    else:
        print(f"unsupported platform: {args.platform}")
        return 2

    try:
        local_stock = read_local_stock_csv(args.local)
        rows = InventoryService(adapter).diff(local_stock)
    except (ValueError, EcommerceError) as exc:
        print(f"inventory failed: {exc}")
        return 1

    out_path = Path(args.out)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    field_names = list(InventoryDiff.__dataclass_fields__)
    with out_path.open("w", newline="", encoding="utf-8-sig") as f:
        writer = csv.DictWriter(f, fieldnames=field_names)
        writer.writeheader()
        for row in rows:
            writer.writerow(asdict(row))

    counts = Counter(row.category for row in rows)
    print(f"skus={len(rows)} wrote={out_path}")
    for category, count in sorted(counts.items()):
        print(f"  {category}={count}")
    return 0


def cmd_reconcile(args: argparse.Namespace) -> int:
    orders, order_issues, _ = read_orders_csv(args.orders)
    bills, bill_issues, duplicate_keys = read_bills_csv(args.bills)
    fx, fx_issues = read_fx_csv(args.fx)

    result = reconcile(orders, bills, fx, duplicate_keys)

    field_names = list(ReconRow.__dataclass_fields__)

    def write_rows(path: str, rows) -> Path:
        out = Path(path)
        out.parent.mkdir(parents=True, exist_ok=True)
        with out.open("w", newline="", encoding="utf-8-sig") as f:
            writer = csv.DictWriter(f, fieldnames=field_names)
            writer.writeheader()
            for row in rows:
                writer.writerow(asdict(row))
        return out

    diff_path = write_rows(args.out, result.rows)
    unmatched_path = write_rows(
        args.unmatched,
        [r for r in result.rows if r.category in {"missing_order", "orphan_bill"}],
    )
    data_quality_path = write_rows(
        args.data_quality,
        [
            r for r in result.rows
            if r.category in {
                "duplicate_bill", "multiple_bills_for_line",
                "bill_inconsistent", "fx_missing",
            }
        ],
    )

    totals = {
        "expected_minor": sum(r.expected_minor or 0 for r in result.rows),
        "actual_minor": sum(r.actual_minor or 0 for r in result.rows),
        "diff_minor": sum(r.diff_minor or 0 for r in result.rows),
    }
    summary_path = Path(args.summary)
    summary_path.parent.mkdir(parents=True, exist_ok=True)
    summary_path.write_text(
        json.dumps(
            {"counts": result.counts, "totals": totals, "rows": len(result.rows)},
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )

    for issue in order_issues:
        log_event("recon_issue", source="orders", row=issue.row_number,
                  field=issue.field, message=issue.message)
    for issue in bill_issues + fx_issues:
        log_event("recon_issue", source=issue.source, row=issue.row_number,
                  field=issue.field, message=issue.message)

    print(
        f"orders={len(orders)} order_lines={sum(len(o.lines) for o in orders)} "
        f"bills={len(bills)} rows={len(result.rows)}"
    )
    print(
        f"issues: orders={len(order_issues)} bills={len(bill_issues)} fx={len(fx_issues)}"
    )
    print(f"wrote={diff_path} summary={summary_path}")
    print(f"unmatched={unmatched_path} data_quality={data_quality_path}")
    for category, count in sorted(result.counts.items()):
        print(f"  {category}={count}")
    return 0


def cmd_serve(args: argparse.Namespace) -> int:
    import uvicorn

    uvicorn.run(
        "ecommerce.api.app:app",
        host=args.host,
        port=args.port,
        log_level="info",
    )
    return 0


def cmd_report(args: argparse.Namespace) -> int:
    conn = connect(args.db)
    try:
        init_db(conn)
        repo = OrderRepository(conn)
        orders = repo.count_orders()
        lines = repo.count_order_lines()
        products = ProductRepository(conn).count_products()
        statuses = repo.status_counts()
        runs = SyncRepository(conn).recent_runs(5)
    finally:
        conn.close()

    print(f"orders={orders} order_lines={lines} products={products}")
    for status, count in statuses:
        print(f"  status={status} count={count}")
    for run in runs:
        print(
            f"  run={run['run_id'][:8]} platform={run['platform']} "
            f"status={run['status']} fetched={run['fetched']} "
            f"inserted={run['inserted']} updated={run['updated']}"
        )
    return 0


def main(argv: list[str] | None = None) -> int:
    setup_logging()
    args = build_parser().parse_args(argv)
    if args.command == "import-orders":
        return cmd_import_orders(args)
    if args.command == "sync-orders":
        return cmd_sync_orders(args)
    if args.command == "import-products":
        return cmd_import_products(args)
    if args.command == "reconcile":
        return cmd_reconcile(args)
    if args.command == "inventory":
        return cmd_inventory(args)
    if args.command == "serve":
        return cmd_serve(args)
    if args.command == "report":
        return cmd_report(args)
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
