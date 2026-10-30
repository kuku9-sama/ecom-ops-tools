import sqlite3
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path

from ecommerce.domain.models import Order, ProductDraft

SCHEMA_PATH = Path(__file__).with_name("schema.sql")


def connect(db_path: str | Path) -> sqlite3.Connection:
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    # SQLite 默认不检查外键，每个连接都要打开。
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def init_db(conn: sqlite3.Connection) -> None:
    conn.executescript(SCHEMA_PATH.read_text(encoding="utf-8"))
    # 旧本地库可能还没有这个列；这是镜像库，补列即可。
    columns = {row["name"] for row in conn.execute("PRAGMA table_info(products)")}
    if "platform_product_id" not in columns:
        conn.execute("ALTER TABLE products ADD COLUMN platform_product_id TEXT")
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

PRODUCT_UPSERT = """
INSERT INTO products (
    platform, sku, title, price_minor, currency, stock, status,
    category, image_url, platform_product_id, updated_at
) VALUES (?, ?, ?, ?, ?, ?, 'draft', ?, ?, ?, ?)
ON CONFLICT (platform, sku) DO UPDATE SET
    title               = excluded.title,
    price_minor         = excluded.price_minor,
    currency            = excluded.currency,
    stock               = excluded.stock,
    status              = excluded.status,
    category            = excluded.category,
    image_url           = excluded.image_url,
    platform_product_id = excluded.platform_product_id,
    updated_at          = excluded.updated_at
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


class ProductRepository:
    def __init__(self, conn: sqlite3.Connection):
        self._conn = conn

    def upsert(
        self, platform: str, draft: ProductDraft, platform_product_id: str | None
    ) -> None:
        self._conn.execute(PRODUCT_UPSERT, (
            platform,
            draft.sku,
            draft.title,
            draft.price_minor,
            draft.currency,
            draft.stock,
            draft.category,
            draft.image_url,
            platform_product_id,
            datetime.now(UTC).isoformat(),
        ))

    def find_platform_id(self, platform: str, sku: str) -> str | None:
        row = self._conn.execute(
            "SELECT platform_product_id FROM products WHERE platform = ? AND sku = ?",
            (platform, sku),
        ).fetchone()
        if row is None:
            return None
        return row["platform_product_id"]

    def count_products(self) -> int:
        return self._conn.execute("SELECT COUNT(*) FROM products").fetchone()[0]


class SyncRepository:
    def __init__(self, conn: sqlite3.Connection):
        self._conn = conn

    @contextmanager
    def transaction(self):
        with self._conn:
            yield

    def create_run(
        self, run_id: str, platform: str, resource: str, cursor_before: str | None
    ) -> None:
        self._conn.execute(
            "INSERT INTO sync_runs "
            "(run_id, platform, resource, status, cursor_before, started_at) "
            "VALUES (?, ?, ?, 'processing', ?, ?)",
            (run_id, platform, resource, cursor_before, datetime.now(UTC).isoformat()),
        )
        self._conn.commit()

    def save_page(
        self,
        run_id: str,
        cursor_after: str | None,
        fetched: int,
        inserted: int,
        updated: int,
    ) -> None:
        self._conn.execute(
            "UPDATE sync_runs SET cursor_after = ?, "
            "fetched = fetched + ?, inserted = inserted + ?, updated = updated + ? "
            "WHERE run_id = ?",
            (cursor_after, fetched, inserted, updated, run_id),
        )

    def finish_run(self, run_id: str, status: str) -> None:
        self._conn.execute(
            "UPDATE sync_runs SET status = ?, finished_at = ? WHERE run_id = ?",
            (status, datetime.now(UTC).isoformat(), run_id),
        )
        self._conn.commit()

    def mark_item(
        self, run_id: str, item_key: str, status: str, error: str = ""
    ) -> None:
        self._conn.execute(
            "INSERT INTO sync_items (run_id, item_key, status, attempts, last_error) "
            "VALUES (?, ?, ?, 1, ?) "
            "ON CONFLICT (run_id, item_key) DO UPDATE SET "
            "status = excluded.status, "
            "attempts = sync_items.attempts + 1, "
            "last_error = excluded.last_error",
            (run_id, item_key, status, error),
        )

    def load_cursor(self, platform: str, resource: str) -> str | None:
        row = self._conn.execute(
            "SELECT cursor FROM sync_state WHERE platform = ? AND resource = ?",
            (platform, resource),
        ).fetchone()
        return row["cursor"] if row else None

    def save_cursor(self, platform: str, resource: str, cursor: str | None) -> None:
        self._conn.execute(
            "INSERT INTO sync_state (platform, resource, cursor, updated_at) "
            "VALUES (?, ?, ?, ?) "
            "ON CONFLICT (platform, resource) DO UPDATE SET "
            "cursor = excluded.cursor, updated_at = excluded.updated_at",
            (platform, resource, cursor, datetime.now(UTC).isoformat()),
        )

    def get_run(self, run_id: str) -> dict | None:
        row = self._conn.execute(
            "SELECT run_id, platform, resource, status, cursor_before, cursor_after, "
            "fetched, inserted, updated, failed, started_at, finished_at "
            "FROM sync_runs WHERE run_id = ?",
            (run_id,),
        ).fetchone()
        return dict(row) if row else None

    def latest_run(self, platform: str, resource: str) -> dict | None:
        row = self._conn.execute(
            "SELECT run_id, platform, resource, status, cursor_before, cursor_after, "
            "fetched, inserted, updated, failed, started_at, finished_at "
            "FROM sync_runs WHERE platform = ? AND resource = ? "
            "ORDER BY started_at DESC LIMIT 1",
            (platform, resource),
        ).fetchone()
        return dict(row) if row else None

    def recent_runs(self, limit: int = 5) -> list[dict]:
        rows = self._conn.execute(
            "SELECT run_id, platform, resource, status, fetched, inserted, updated, "
            "started_at FROM sync_runs ORDER BY started_at DESC LIMIT ?",
            (limit,),
        ).fetchall()
        return [dict(row) for row in rows]

    def item_count(self, run_id: str) -> int:
        return self._conn.execute(
            "SELECT COUNT(*) FROM sync_items WHERE run_id = ?", (run_id,)
        ).fetchone()[0]


class ExtractionRepository:
    def __init__(self, conn: sqlite3.Connection):
        self._conn = conn

    def record_failure(
        self, source_hash: str, raw_response: str, error: str
    ) -> None:
        self._conn.execute(
            "INSERT INTO extraction_failures "
            "(source_hash, raw_response, validation_error, created_at) "
            "VALUES (?, ?, ?, ?) "
            "ON CONFLICT (source_hash) DO UPDATE SET "
            "raw_response = excluded.raw_response, "
            "validation_error = excluded.validation_error, "
            "created_at = excluded.created_at",
            (
                source_hash,
                raw_response,
                error,
                datetime.now(UTC).isoformat(),
            ),
        )

    def count_failures(self) -> int:
        return self._conn.execute(
            "SELECT COUNT(*) FROM extraction_failures"
        ).fetchone()[0]


class ReconciliationRepository:
    def __init__(self, conn: sqlite3.Connection):
        self._conn = conn

    def upsert_status(
        self,
        platform: str,
        platform_order_id: str,
        line_id: str,
        category: str,
        status: str,
        note: str,
    ) -> None:
        self._conn.execute(
            "INSERT INTO reconciliation_exceptions "
            "(platform, platform_order_id, line_id, category, status, note, updated_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?) "
            "ON CONFLICT (platform, platform_order_id, line_id, category) DO UPDATE SET "
            "status = excluded.status, "
            "note = excluded.note, "
            "updated_at = excluded.updated_at",
            (
                platform,
                platform_order_id,
                line_id,
                category,
                status,
                note,
                datetime.now(UTC).isoformat(),
            ),
        )

    def load_statuses(self) -> dict[tuple[str, str, str, str], dict[str, str]]:
        rows = self._conn.execute(
            "SELECT platform, platform_order_id, line_id, category, status, note "
            "FROM reconciliation_exceptions"
        ).fetchall()
        return {
            (
                row["platform"],
                row["platform_order_id"],
                row["line_id"],
                row["category"],
            ): {"status": row["status"], "note": row["note"]}
            for row in rows
        }
