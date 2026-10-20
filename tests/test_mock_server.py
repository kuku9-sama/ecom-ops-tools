import pytest
from fastapi.testclient import TestClient

from mock_server.app import DRAFTS, FAULT, app

client = TestClient(app)


@pytest.fixture(autouse=True)
def reset_state():
    FAULT.update(mode=None, times=0)
    DRAFTS.clear()
    yield


def test_token_create():
    resp = client.post("/rest/auth/token/create", params={"app_key": "mock"})

    assert resp.status_code == 200
    assert resp.json()["access_token"] == "mock-token"


def test_orders_filter_and_pagination():
    resp = client.get(
        "/rest/orders/get",
        params={
            "update_after": "2026-10-02T00:00:00+00:00",
            "limit": 10,
            "offset": 0,
        },
    )

    assert resp.status_code == 200
    data = resp.json()["data"]
    assert data["count"] == 2
    assert [o["order_id"] for o in data["orders"]] == ["LZ1002", "LZ1003"]


def test_limit_validation():
    resp = client.get(
        "/rest/orders/get",
        params={"update_after": "2026-10-01T00:00:00+00:00", "limit": 101},
    )

    assert resp.status_code == 400


def test_fault_429_then_recover():
    client.post("/mock/fault", params={"mode": "429", "times": 1})

    first = client.get(
        "/rest/orders/get", params={"update_after": "2026-10-01T00:00:00+00:00"}
    )
    assert first.status_code == 429
    assert first.headers["Retry-After"] == "0"

    second = client.get(
        "/rest/orders/get", params={"update_after": "2026-10-01T00:00:00+00:00"}
    )
    assert second.status_code == 200


def test_fault_bad_json():
    client.post("/mock/fault", params={"mode": "bad_json", "times": 1})

    resp = client.get(
        "/rest/orders/get", params={"update_after": "2026-10-01T00:00:00+00:00"}
    )

    assert resp.status_code == 200
    assert resp.text == "not json{"


PRODUCT_PAYLOAD = {
    "seller_sku": "MON-001",
    "name": "27 inch monitor",
    "price": "1299.00",
    "currency": "CNY",
    "quantity": 20,
    "category": "Monitors",
    "image_url": "https://example.com/mon.jpg",
    "idempotency_key": "key",
}


def test_product_create_is_idempotent():
    first = client.post("/rest/product/create", json=PRODUCT_PAYLOAD)
    second = client.post("/rest/product/create", json=PRODUCT_PAYLOAD)

    assert first.status_code == 200
    assert second.status_code == 200
    assert first.json()["data"]["product_id"] == second.json()["data"]["product_id"]


def test_product_get():
    created = client.post("/rest/product/create", json=PRODUCT_PAYLOAD)
    product_id = created.json()["data"]["product_id"]

    found = client.get("/rest/product/get", params={"sku": "MON-001"})
    missing = client.get("/rest/product/get", params={"sku": "NOPE"})

    assert found.status_code == 200
    assert found.json()["data"]["product_id"] == product_id
    assert missing.status_code == 404
