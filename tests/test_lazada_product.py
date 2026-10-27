import re

import pytest
import requests
import responses

from ecommerce.adapters.lazada import LazadaAdapter
from ecommerce.domain.errors import (
    RateLimitError,
    TransientError,
    ValidationError,
)
from ecommerce.domain.models import ProductDraft

BASE = "https://mock.lazada"
TOKEN_URL = re.compile(r"https://mock\.lazada/rest/auth/token/create.*")
CREATE_URL = re.compile(r"https://mock\.lazada/rest/product/create.*")
GET_URL = re.compile(r"https://mock\.lazada/rest/product/get.*")

DRAFT = ProductDraft(
    sku="MON-001",
    title="27 inch monitor",
    price_minor=129900,
    currency="CNY",
    stock=20,
    category="Monitors",
    image_url="https://example.com/mon.jpg",
)


def _adapter() -> LazadaAdapter:
    responses.add(responses.POST, TOKEN_URL, json={"access_token": "t"})
    adapter = LazadaAdapter(BASE, timeout=(1, 1))
    adapter.authenticate()
    return adapter


@responses.activate
def test_create_product_draft():
    adapter = _adapter()
    responses.add(
        responses.POST,
        CREATE_URL,
        json={"code": "0", "data": {"product_id": "P1"}},
    )

    product_id = adapter.create_product_draft(DRAFT, "key")

    assert product_id == "P1"
    body = responses.calls[-1].request.body
    if isinstance(body, bytes):
        body = body.decode("utf-8")
    assert "seller_sku" in body
    assert "1299.00" in body


@responses.activate
def test_find_product_not_found():
    adapter = _adapter()
    responses.add(responses.GET, GET_URL, status=404)

    assert adapter.find_product_by_sku("MON-001") is None


@responses.activate
def test_find_product_found():
    adapter = _adapter()
    responses.add(
        responses.GET,
        GET_URL,
        json={"code": "0", "data": {"product_id": "P1"}},
    )

    assert adapter.find_product_by_sku("MON-001") == "P1"


@responses.activate
def test_create_validation_error():
    adapter = _adapter()
    responses.add(responses.POST, CREATE_URL, status=400)

    with pytest.raises(ValidationError):
        adapter.create_product_draft(DRAFT, "key")


@responses.activate
def test_create_timeout_raises_transient_error():
    adapter = _adapter()

    def timeout_callback(request):
        raise requests.exceptions.ReadTimeout("timeout")

    responses.add_callback(responses.POST, CREATE_URL, callback=timeout_callback)

    with pytest.raises(TransientError):
        adapter.create_product_draft(DRAFT, "key")


@responses.activate
def test_create_rate_limit_is_not_retried():
    adapter = _adapter()
    responses.add(
        responses.POST, CREATE_URL, status=429, headers={"Retry-After": "0"}
    )

    with pytest.raises(RateLimitError):
        adapter.create_product_draft(DRAFT, "key")

    create_calls = [c for c in responses.calls if "product/create" in c.request.url]
    assert len(create_calls) == 1
