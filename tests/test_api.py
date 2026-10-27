import json
from pathlib import Path

from fastapi.testclient import TestClient

from ecommerce.api.app import app
from ecommerce.repositories.sqlite import OrderRepository, connect, init_db
from ecommerce.services.order_service import OrderService, read_orders_csv

DATA = Path(__file__).resolve().parents[1] / "data"


def _prepare_db(tmp_path, monkeypatch):
    db_path = tmp_path / "api.db"
    conn = connect(db_path)
    init_db(conn)
    orders, _, _ = read_orders_csv(DATA / "orders.csv")
    with conn:
        OrderService(OrderRepository(conn)).import_orders(orders)
    conn.close()
    monkeypatch.setenv("ECOMMERCE_DB", str(db_path))
    return db_path


def test_health_orders_and_index(tmp_path, monkeypatch):
    _prepare_db(tmp_path, monkeypatch)
    client = TestClient(app)

    assert client.get("/health").json() == {"ok": True}
    assert len(client.get("/orders").json()) == 8
    assert client.get("/runs").status_code == 200
    assert client.get("/").status_code == 200


def test_summary_and_inventory(tmp_path, monkeypatch):
    _prepare_db(tmp_path, monkeypatch)

    summary_path = tmp_path / "summary.json"
    summary_path.write_text(
        json.dumps({"counts": {"matched": 1}, "totals": {}, "rows": 1}),
        encoding="utf-8",
    )
    inventory_path = tmp_path / "inventory.csv"
    inventory_path.write_text(
        "sku,platform_stock,local_stock,diff,category\n"
        "A,1,2,-1,mismatch\n",
        encoding="utf-8",
    )
    monkeypatch.setenv("RECON_SUMMARY_PATH", str(summary_path))
    monkeypatch.setenv("INVENTORY_DIFF_PATH", str(inventory_path))
    client = TestClient(app)

    assert client.get("/reconciliation/summary").json()["counts"]["matched"] == 1
    assert len(client.get("/inventory/diff").json()) == 1
    assert client.get("/").status_code == 200
