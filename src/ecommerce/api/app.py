import csv
import io
import json
import os
import sqlite3
import tempfile
from collections import Counter
from pathlib import Path
from typing import Annotated
from urllib.parse import urlencode

from fastapi import Depends, FastAPI, File, Form, Request, UploadFile
from fastapi.responses import HTMLResponse, RedirectResponse, Response
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from ecommerce.domain.errors import EcommerceError
from ecommerce.repositories.sqlite import (
    ReconciliationRepository,
    SyncRepository,
    connect,
    init_db,
)
from ecommerce.services.order_service import read_orders_csv
from ecommerce.services.reconciliation_service import (
    ReconReportPaths,
    read_bills_csv,
    read_fx_csv,
    reconcile,
    write_recon_reports,
)

app = FastAPI(
    title="ecom-ops-tools 本地看板",
    description="只读本地 SQLite 和 outputs；平台数据以平台为准。",
)
templates = Jinja2Templates(directory=str(Path(__file__).parent / "templates"))
app.mount(
    "/static",
    StaticFiles(directory=str(Path(__file__).parent / "static")),
    name="static",
)

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

STATUS_LABELS = {
    "pending": "待处理",
    "handled": "已处理",
    "ignored": "已忽略",
}
VALID_STATUSES = set(STATUS_LABELS)

WORKLIST_FIELDS = [
    "platform",
    "platform_order_id",
    "line_id",
    "category",
    "category_label",
    "status",
    "status_label",
    "note",
    "expected_minor",
    "actual_minor",
    "diff_minor",
    "fx_rate_used",
    "fx_rate_date",
    "fx_rate_source",
    "message",
]

PROJECT_ROOT = Path(__file__).resolve().parents[3]
DATA_DIR = PROJECT_ROOT / "data"
MAX_UPLOAD_BYTES = 20 * 1024 * 1024
SAMPLE_FILES = {
    "orders": "orders.csv",
    "bills": "bills.csv",
    "fx": "fx_rates.csv",
}


def get_conn():
    conn = connect(os.environ.get("ECOMMERCE_DB", "data/ecommerce.db"))
    init_db(conn)
    try:
        yield conn
    finally:
        conn.close()


def _recon_paths() -> ReconReportPaths:
    return ReconReportPaths(
        out=Path(os.environ.get("RECON_DETAIL_PATH", "outputs/reconciliation.csv")),
        summary=Path(
            os.environ.get("RECON_SUMMARY_PATH", "outputs/reconciliation_summary.json")
        ),
        unmatched=Path(os.environ.get("RECON_UNMATCHED_PATH", "outputs/unmatched.csv")),
        data_quality=Path(
            os.environ.get("RECON_DATA_QUALITY_PATH", "outputs/data_quality.csv")
        ),
    )


def _load_summary():
    path = _recon_paths().summary
    if not path.exists():
        return None
    return json.loads(path.read_text(encoding="utf-8"))


def _load_detail_rows() -> list[dict]:
    path = _recon_paths().out
    if not path.exists():
        return []
    with path.open(newline="", encoding="utf-8-sig") as f:
        return list(csv.DictReader(f))


def _sort_detail_rows(rows: list[dict]) -> list[dict]:
    return sorted(
        rows,
        key=lambda row: (
            row.get("category", ""),
            row.get("platform", ""),
            row.get("platform_order_id", ""),
            row.get("line_id", ""),
        ),
    )


def _load_worklist(conn: sqlite3.Connection, category: str = "", status: str = ""):
    rows = _sort_detail_rows(_load_detail_rows())
    statuses = ReconciliationRepository(conn).load_statuses()
    for row in rows:
        key = (
            row.get("platform", ""),
            row.get("platform_order_id", ""),
            row.get("line_id", ""),
            row.get("category", ""),
        )
        stored = statuses.get(key, {})
        row["status"] = stored.get("status", "pending")
        row["status_label"] = STATUS_LABELS.get(row["status"], row["status"])
        row["note"] = stored.get("note", "")
        row["category_label"] = CATEGORY_LABELS.get(
            row["category"], row["category"]
        )

    counts = Counter(row["status"] for row in rows)
    filtered = [
        row for row in rows
        if (not category or row["category"] == category)
        and (not status or row["status"] == status)
    ]
    return filtered, rows, counts


def _load_inventory_rows():
    path = Path(os.environ.get("INVENTORY_DIFF_PATH", "outputs/inventory_diff.csv"))
    if not path.exists():
        return []
    with path.open(newline="", encoding="utf-8-sig") as f:
        return list(csv.DictReader(f))


def _inventory_rows_with_labels():
    rows = _load_inventory_rows()
    for row in rows:
        row["category_label"] = INVENTORY_LABELS.get(
            row["category"], row["category"]
        )
    return rows


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


@app.get("/inventory", response_class=HTMLResponse)
def inventory_page(request: Request):
    return templates.TemplateResponse(
        request=request,
        name="inventory.html",
        context={
            "active_nav": "inventory",
            "inventory_rows": _inventory_rows_with_labels(),
        },
    )


@app.get("/reconciliation", response_class=HTMLResponse)
def reconciliation_page(
    request: Request,
    conn: Annotated[sqlite3.Connection, Depends(get_conn)],
    category: str = "",
    status: str = "",
    msg: str = "",
    error: str = "",
):
    if status not in VALID_STATUSES:
        status = ""
    rows, all_rows, status_counts = _load_worklist(conn, category, status)
    return templates.TemplateResponse(
        request=request,
        name="reconciliation.html",
        context={
            "active_nav": "reconciliation",
            "rows": rows,
            "total_rows": len(all_rows),
            "status_counts": status_counts,
            "category_options": sorted(
                CATEGORY_LABELS.items(), key=lambda item: item[1]
            ),
            "status_options": list(STATUS_LABELS.items()),
            "selected_category": category,
            "selected_status": status,
            "export_query": urlencode(
                {
                    key: value
                    for key, value in {"category": category, "status": status}.items()
                    if value
                }
            ),
            "summary": _load_summary(),
            "msg": msg,
            "error": error,
        },
    )


@app.post("/reconciliation/run")
async def reconciliation_run(
    orders: Annotated[UploadFile | None, File()] = None,
    bills: Annotated[UploadFile | None, File()] = None,
    fx: Annotated[UploadFile | None, File()] = None,
    use_sample: Annotated[str, Form()] = "",
):
    paths = _recon_paths()
    try:
        with tempfile.TemporaryDirectory() as tmp:
            inputs: dict[str, Path] = {}
            for name, upload in (
                ("orders", orders), ("bills", bills), ("fx", fx),
            ):
                if upload is not None and upload.filename:
                    data = await upload.read()
                    if len(data) > MAX_UPLOAD_BYTES:
                        raise ValueError(f"{name} 文件超过 20 MB")
                    target = Path(tmp) / f"{name}.csv"
                    target.write_bytes(data)
                    inputs[name] = target
                elif use_sample:
                    inputs[name] = DATA_DIR / SAMPLE_FILES[name]
                else:
                    raise ValueError("请上传三个 CSV，或勾选使用示例数据")

            order_list, _, _ = read_orders_csv(inputs["orders"])
            bill_list, _, duplicate_keys = read_bills_csv(inputs["bills"])
            fx_rates, _ = read_fx_csv(inputs["fx"])
            result = reconcile(order_list, bill_list, fx_rates, duplicate_keys)
            write_recon_reports(result, paths)
    except (EcommerceError, OSError, ValueError) as exc:
        return RedirectResponse(
            "/reconciliation?" + urlencode({"error": str(exc)}),
            status_code=303,
        )

    return RedirectResponse(
        "/reconciliation?"
        + urlencode({"msg": f"对账完成，共 {len(result.rows)} 行"}),
        status_code=303,
    )


@app.post("/reconciliation/action")
def reconciliation_action(
    conn: Annotated[sqlite3.Connection, Depends(get_conn)],
    platform: Annotated[str, Form()],
    platform_order_id: Annotated[str, Form()],
    category: Annotated[str, Form()],
    line_id: Annotated[str, Form()] = "",
    status: Annotated[str, Form()] = "pending",
    note: Annotated[str, Form()] = "",
    next_category: Annotated[str, Form()] = "",
    next_status: Annotated[str, Form()] = "",
):
    if not platform or not platform_order_id or not category:
        error = "缺少异常标识"
    elif status not in VALID_STATUSES:
        error = "无效的处理状态"
    elif len(note) > 500:
        error = "备注不能超过 500 字"
    else:
        error = ""

    if error:
        return RedirectResponse(
            "/reconciliation?" + urlencode({"error": error}),
            status_code=303,
        )

    with conn:
        ReconciliationRepository(conn).upsert_status(
            platform, platform_order_id, line_id, category, status, note.strip()
        )

    return RedirectResponse(
        "/reconciliation?"
        + urlencode(
            {
                "category": next_category,
                "status": next_status,
                "msg": "已保存",
            }
        ),
        status_code=303,
    )


@app.get("/reconciliation/export")
def reconciliation_export(
    conn: Annotated[sqlite3.Connection, Depends(get_conn)],
    category: str = "",
    status: str = "",
):
    if status not in VALID_STATUSES:
        status = ""
    rows, _, _ = _load_worklist(conn, category, status)
    buf = io.StringIO()
    writer = csv.DictWriter(
        buf, fieldnames=WORKLIST_FIELDS, extrasaction="ignore"
    )
    writer.writeheader()
    writer.writerows(rows)
    return Response(
        content=buf.getvalue().encode("utf-8-sig"),
        media_type="text/csv",
        headers={
            "Content-Disposition": 'attachment; filename="reconciliation_worklist.csv"'
        },
    )


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

    _, _, status_counts = _load_worklist(conn)

    return templates.TemplateResponse(
        request=request,
        name="index.html",
        context={
            "active_nav": "overview",
            "order_count": order_count,
            "line_count": line_count,
            "product_count": product_count,
            "runs": SyncRepository(conn).recent_runs(5),
            "recon": recon,
            "recon_rows": recon_rows,
            "pending_count": status_counts.get("pending", 0),
        },
    )
