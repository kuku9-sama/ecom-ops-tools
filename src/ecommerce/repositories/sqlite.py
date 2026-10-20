import sqlite3
from pathlib import Path

from ecommerce.domain.models import Order

SCHEMA_PATH = Path(__file__).with_name("schema.sql")


def connect(db_path: str | Path) -> sqlite3.Connection:
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    # SQLite 默认不检查外键，每个连接都要打开。
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def init_db(conn: sqlite3.Connection) -> None:
    conn.executescript(SCHEMA_PATH.read_text(encoding="utf-8"))
    conn.commit()


ORDER_UPSERT = """
INSERT INTO orders (
    platform, platform_order_id, status, currency, paid_at, updated_at, raw_json
) VALUES (?, ?, ?, ?, ?, ?, ?)
ON CONFLICT (platform, platform_order_id) DO UPDATE SET
    status     = excluded.status,
    currency   = excluded.currency,
    paid_at    = excluded.paid_at,
    updated_at = excluded.updated_at,
    raw_json   = excluded.raw_json
"""

LINE_UPSERT = """
INSERT INTO order_lines (
    platform, platform_order_id, line_id, sku, qty,
    unit_price_minor, discount_minor, refund_minor, currency
) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
ON CONFLICT (platform, platform_order_id, line_id) DO UPDATE SET
    sku              = excluded.sku,
    qty              = excluded.qty,
    unit_price_minor = excluded.unit_price_minor,
    discount_minor   = excluded.discount_minor,
    refund_minor     = excluded.refund_minor,
    currency         = excluded.currency
"""


class OrderRepository:
    def __init__(self, conn: sqlite3.Connection):
        self._conn = conn

    def upsert(self, order: Order) -> bool:
        """返回 True 表示新插入，False 表示已存在并更新。"""
        # 这个查询只用于统计 inserted/updated；幂等防线是主键约束。
        exists = self._conn.execute(
            "SELECT 1 FROM orders WHERE platform = ? AND platform_order_id = ?",
            (order.platform, order.platform_order_id),
        ).fetchone() is not None

        self._conn.execute(ORDER_UPSERT, (
            order.platform,
            order.platform_order_id,
            order.status,
            order.currency,
            order.paid_at.isoformat(),
            order.updated_at.isoformat(),
            order.raw_json,
        ))
        for line in order.lines:
            self._conn.execute(LINE_UPSERT, (
                line.platform,
                line.platform_order_id,
                line.line_id,
                line.sku,
                line.qty,
                line.unit_price_minor,
                line.discount_minor,
                line.refund_minor,
                line.currency,
            ))
        return not exists

    def count_orders(self) -> int:
        return self._conn.execute("SELECT COUNT(*) FROM orders").fetchone()[0]

    def count_order_lines(self) -> int:
        return self._conn.execute("SELECT COUNT(*) FROM order_lines").fetchone()[0]

    def status_counts(self) -> list[tuple[str, int]]:
        rows = self._conn.execute(
            "SELECT status, COUNT(*) AS n FROM orders GROUP BY status ORDER BY status"
        ).fetchall()
        return [(row["status"], row["n"]) for row in rows]
