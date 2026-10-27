import csv
import json
import os
import sqlite3
from pathlib import Path
from typing import Annotated

from fastapi import Depends, FastAPI, Request
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates

from ecommerce.repositories.sqlite import SyncRepository, connect, init_db

app = FastAPI(
    title="ecom-ops-tools 本地看板",
    description="只读本地 SQLite 和 outputs；平台数据以平台为准。",
)
templates = Jinja2Templates(directory=str(Path(__file__).parent / "templates"))

CATEGORY_LABELS = {
    "matched": "已匹配",
    "amount_mismatch": "金额不符",
    "refund_mismatch": "退款不符",
    "fx_missing": "缺汇率",
    "fx_or_rounding_diff": "汇率或舍入差异",
    "duplicate_bill": "重复账单",
    "multiple_bills_for_line": "同一行多账单",
    "bill_inconsistent": "账单内部不一致",
    "missing_order": "缺订单",
    "orphan_bill": "孤立账单",
    "aggregated_bill": "聚合账单（订单级兜底）",
}

INVENTORY_LABELS = {
    "mismatch": "库存不一致",
    "missing_locally": "本地缺失",
}


def get_conn():
    conn = connect(os.environ.get("ECOMMERCE_DB", "data/ecommerce.db"))
    init_db(conn)
    try:
        yield conn
    finally:
        conn.close()


def _load_summary():
    path = Path(
        os.environ.get("RECON_SUMMARY_PATH", "outputs/reconciliation_summary.json")
    )
    if not path.exists():
        return None
    return json.loads(path.read_text(encoding="utf-8"))


def _load_inventory_rows():
    path = Path(os.environ.get("INVENTORY_DIFF_PATH", "outputs/inventory_diff.csv"))
    if not path.exists():
        return []
    with path.open(newline="", encoding="utf-8-sig") as f:
        return list(csv.DictReader(f))


@app.get("/health")
def health():
    return {"ok": True}


@app.get("/runs")
def runs(
    conn: Annotated[sqlite3.Connection, Depends(get_conn)], limit: int = 20
):
    return SyncRepository(conn).recent_runs(limit)


@app.get("/orders")
def orders(
    conn: Annotated[sqlite3.Connection, Depends(get_conn)], limit: int = 50
):
    rows = conn.execute(
        "SELECT platform, platform_order_id, status, currency, paid_at, updated_at "
        "FROM orders ORDER BY updated_at DESC LIMIT ?",
        (limit,),
    ).fetchall()
    return [dict(row) for row in rows]


@app.get("/reconciliation/summary")
def reconciliation_summary():
    summary = _load_summary()
    if summary is None:
        return {"ok": False, "message": "暂无对账结果"}
    return summary


@app.get("/inventory/diff")
def inventory_diff():
    return _load_inventory_rows()


@app.get("/", response_class=HTMLResponse)
def index(
    request: Request,
    conn: Annotated[sqlite3.Connection, Depends(get_conn)],
):
    order_count = conn.execute("SELECT COUNT(*) FROM orders").fetchone()[0]
    line_count = conn.execute("SELECT COUNT(*) FROM order_lines").fetchone()[0]
    product_count = conn.execute("SELECT COUNT(*) FROM products").fetchone()[0]

    recon = _load_summary()
    recon_rows = []
    if recon:
        recon_rows = sorted(
            (
                {
                    "key": key,
                    "label": CATEGORY_LABELS.get(key, key),
                    "count": count,
                }
                for key, count in recon["counts"].items()
            ),
            key=lambda row: (row["key"] == "matched", -row["count"], row["key"]),
        )

    inventory_rows = _load_inventory_rows()
    for row in inventory_rows:
        row["category_label"] = INVENTORY_LABELS.get(
            row["category"], row["category"]
        )

    return templates.TemplateResponse(
        request=request,
        name="index.html",
        context={
            "order_count": order_count,
            "line_count": line_count,
            "product_count": product_count,
            "runs": SyncRepository(conn).recent_runs(5),
            "recon": recon,
            "recon_rows": recon_rows,
            "inventory_rows": inventory_rows,
        },
    )
