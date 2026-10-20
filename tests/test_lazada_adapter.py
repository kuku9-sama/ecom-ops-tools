import re
from datetime import datetime

import pytest
import requests
import responses

from ecommerce.adapters.lazada import LazadaAdapter
from ecommerce.domain.errors import AuthError, ParseError, TransientError

BASE = "https://mock.lazada"
TOKEN_URL = re.compile(r"https://mock\.lazada/rest/auth/token/create.*")
ORDERS_URL = re.compile(r"https://mock\.lazada/rest/orders/get.*")
SINCE = datetime.fromisoformat("2026-10-01T00:00:00+00:00")

ORDER_1 = {
    "order_id": "LZ1001",
    "status": "paid",
    "currency": "USD",
    "created_at": "2026-10-01T10:00:00+08:00",
    "updated_at": "2026-10-01T10:05:00+08:00",
    "items": [
        {
            "order_item_id": "1",
            "sku": "MON-001",
            "qty": 1,
            "unit_price": "129.90",
            "currency": "USD",
        }
    ],
}
ORDER_2 = {
    "order_id": "LZ1002",
    "status": "paid",
    "currency": "CNY",
    "created_at": "2026-10-02T10:00:00+08:00",
    "updated_at": "2026-10-02T10:05:00+08:00",
    "items": [
        {
            "order_item_id": "1",
            "sku": "KB-001",
            "qty": 2,
            "unit_price": "199.00",
            "currency": "CNY",
        }
    ],
}


def _add_token():
    responses.add(responses.POST, TOKEN_URL, json={"access_token": "t"})


def _adapter(page_size: int = 100) -> LazadaAdapter:
    adapter = LazadaAdapter(BASE, page_size=page_size)
    adapter.authenticate()
    return adapter


@responses.activate
def test_maps_payload_and_pagination():
    _add_token()
    responses.add(
        responses.GET,
        ORDERS_URL,
        json={
            "code": "0",
            "data": {"orders": [ORDER_1, ORDER_2], "count": 3, "offset": 0, "limit": 2},
        },
    )

    page = _adapter(page_size=2).list_orders(SINCE, None)

    assert len(page.items) == 2
    assert page.next_cursor == "2"
    assert page.items[0].platform_order_id == "LZ1001"
    assert page.items[0].lines[0].unit_price_minor == 12990


@responses.activate
def test_429_then_success(monkeypatch):
    monkeypatch.setattr("ecommerce.adapters.lazada._wait", lambda state: 0)
    _add_token()
    responses.add(responses.GET, ORDERS_URL, status=429, headers={"Retry-After": "0"})
    responses.add(responses.GET, ORDERS_URL, status=429, headers={"Retry-After": "0"})
    responses.add(
        responses.GET,
        ORDERS_URL,
        json={"code": "0", "data": {"orders": [ORDER_1], "count": 1, "offset": 0, "limit": 100}},
    )

    page = _adapter().list_orders(SINCE, None)

    assert len(page.items) == 1
    assert len([c for c in responses.calls if c.request.method == "GET"]) == 3


@responses.activate
def test_500_exhausts_budget(monkeypatch):
    monkeypatch.setattr("ecommerce.adapters.lazada._wait", lambda state: 0)
    _add_token()
    for _ in range(4):
        responses.add(responses.GET, ORDERS_URL, status=500)

    with pytest.raises(TransientError):
        _adapter().list_orders(SINCE, None)

    assert len([c for c in responses.calls if c.request.method == "GET"]) == 4


@responses.activate
def test_401_raises_auth_error():
    _add_token()
    responses.add(responses.GET, ORDERS_URL, status=401)

    with pytest.raises(AuthError):
        _adapter().list_orders(SINCE, None)


@responses.activate
def test_bad_json_raises_parse_error():
    _add_token()
    responses.add(responses.GET, ORDERS_URL, body="not json{", status=200)

    with pytest.raises(ParseError):
        _adapter().list_orders(SINCE, None)


@responses.activate
def test_timeout_raises_transient_error(monkeypatch):
    monkeypatch.setattr("ecommerce.adapters.lazada._wait", lambda state: 0)
    _add_token()

    def timeout_callback(request):
        raise requests.exceptions.ReadTimeout("timeout")

    responses.add_callback(responses.GET, ORDERS_URL, callback=timeout_callback)

    with pytest.raises(TransientError):
        _adapter().list_orders(SINCE, None)

    assert len([c for c in responses.calls if c.request.method == "GET"]) == 4


def test_list_orders_requires_authenticate():
    with pytest.raises(AuthError):
        LazadaAdapter(BASE).list_orders(SINCE, None)


def test_page_size_bounds():
    with pytest.raises(ValueError):
        LazadaAdapter(BASE, page_size=0)
    with pytest.raises(ValueError):
        LazadaAdapter(BASE, page_size=101)
