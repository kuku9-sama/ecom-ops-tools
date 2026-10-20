import argparse
from datetime import datetime

from ecommerce import __version__
from ecommerce.adapters.lazada import LazadaAdapter
from ecommerce.adapters.shopee_mock import ShopeeMockAdapter
from ecommerce.domain.errors import EcommerceError
from ecommerce.logging_setup import log_event, setup_logging
from ecommerce.repositories.sqlite import (
    OrderRepository,
    ProductRepository,
    connect,
    init_db,
)
from ecommerce.services.order_service import OrderService, read_orders_csv
from ecommerce.services.product_service import ProductService, read_products_csv


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
    p_sync.add_argument("--db", default="data/ecommerce.db")

    p_products = sub.add_parser("import-products", help="从 CSV 创建商品草稿")
    p_products.add_argument("--csv", required=True)
    p_products.add_argument("--platform", default="shopee_mock")
    p_products.add_argument("--base-url", default="http://127.0.0.1:8100")
    p_products.add_argument("--db", default="data/ecommerce.db")

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
    if args.platform == "shopee_mock":
        adapter = ShopeeMockAdapter()
    elif args.platform == "lazada":
        adapter = LazadaAdapter(args.base_url)
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
        with conn:
            result = OrderService(OrderRepository(conn)).sync_orders(adapter, since)
    except EcommerceError as exc:
        print(f"sync failed: {exc}")
        return 1
    finally:
        conn.close()

    print(f"inserted={result.inserted} updated={result.updated}")
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


def cmd_report(args: argparse.Namespace) -> int:
    conn = connect(args.db)
    try:
        init_db(conn)
        repo = OrderRepository(conn)
        orders = repo.count_orders()
        lines = repo.count_order_lines()
        products = ProductRepository(conn).count_products()
        statuses = repo.status_counts()
    finally:
        conn.close()

    print(f"orders={orders} order_lines={lines} products={products}")
    for status, count in statuses:
        print(f"  status={status} count={count}")
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
    if args.command == "report":
        return cmd_report(args)
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
