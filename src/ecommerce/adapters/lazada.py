import json
import random
from datetime import datetime

import requests
from requests.adapters import HTTPAdapter
from tenacity import (
    RetryCallState,
    retry,
    retry_if_exception_type,
    stop_after_attempt,
)

from ecommerce.domain.errors import (
    AuthError,
    ParseError,
    PermanentError,
    RateLimitError,
    TransientError,
    ValidationError,
)
from ecommerce.domain.models import InventoryItem, Order, OrderLine, ProductDraft
from ecommerce.domain.money import format_minor, parse_amount_to_minor
from ecommerce.domain.ports import Page


class LazadaAdapter:
    """Lazada 形状的 HTTP 适配器；Mock 不验证真实签名。"""

    platform = "lazada"

    def __init__(
        self,
        base_url: str,
        app_key: str = "mock",
        page_size: int = 100,
        timeout: tuple[float, float] = (3, 15),
    ):
        if not 1 <= page_size <= 100:
            raise ValueError("page_size must be between 1 and 100")
        self._base_url = base_url.rstrip("/")
        self._app_key = app_key
        self._page_size = page_size
        self._timeout = timeout
        self._session = requests.Session()
        # 关掉 requests 自带的隐藏重试，重试统一由 tenacity 控制。
        self._session.mount("http://", HTTPAdapter(max_retries=0))
        self._session.mount("https://", HTTPAdapter(max_retries=0))
        self._token: str | None = None

    def authenticate(self) -> None:
        try:
            resp = self._session.post(
                f"{self._base_url}/rest/auth/token/create",
                params={
                    "app_key": self._app_key,
                    "timestamp": int(datetime.now().timestamp()),
                },
                timeout=self._timeout,
            )
            _raise_for_status(resp, "/auth/token/create")
            data = _json_or_parse_error(resp)
        except requests.RequestException as exc:
            raise TransientError(f"auth request failed: {exc}") from exc

        token = data.get("access_token")
        if not token:
            raise ParseError("token response missing access_token")
        self._token = token

    def list_orders(self, updated_since: datetime, cursor: str | None) -> Page[Order]:
        offset = int(cursor or 0)
        data = get_with_retry(
            self,
            "/orders/get",
            {
                "update_after": updated_since.isoformat(),
                "limit": self._page_size,
                "offset": offset,
            },
        )

        payload = data.get("data")
        if not isinstance(payload, dict) or not isinstance(payload.get("orders"), list):
            raise ParseError("orders response missing data.orders")

        orders = tuple(_to_order(raw) for raw in payload["orders"])
        count = int(payload.get("count", len(orders)))
        next_offset = offset + len(orders)
        next_cursor = str(next_offset) if orders and next_offset < count else None
        return Page(items=orders, next_cursor=next_cursor)

    def _get(self, path: str, params: dict, allow_404: bool = False) -> dict:
        if self._token is None:
            raise AuthError("not authenticated")

        try:
            resp = self._session.get(
                f"{self._base_url}/rest{path}",
                params={
                    "app_key": self._app_key,
                    "access_token": self._token,
                    **params,
                },
                timeout=self._timeout,
            )
            if resp.status_code == 404 and allow_404:
                return {}
            _raise_for_status(resp, path)
            return _json_or_parse_error(resp)
        except requests.RequestException as exc:
            # 挂了重试适配器后，读超时可能表现为 ConnectionError；
            # 统一捕获父类，避免漏掉。
            raise TransientError(f"GET {path} failed: {exc}") from exc

    def _post(self, path: str, payload: dict) -> dict:
        if self._token is None:
            raise AuthError("not authenticated")

        try:
            resp = self._session.post(
                f"{self._base_url}/rest{path}",
                params={"app_key": self._app_key, "access_token": self._token},
                json=payload,
                timeout=self._timeout,
            )
            _raise_for_product_status(resp, path)
            return _json_or_parse_error(resp)
        except requests.RequestException as exc:
            raise TransientError(f"POST {path} failed: {exc}") from exc

    def create_product_draft(self, draft: ProductDraft, idempotency_key: str) -> str:
        payload = {
            "seller_sku": draft.sku,
            "name": draft.title,
            "price": format_minor(draft.price_minor, draft.currency),
            "currency": draft.currency,
            "quantity": draft.stock,
            "category": draft.category,
            "image_url": draft.image_url,
            "idempotency_key": idempotency_key,
        }
        data = self._post("/product/create", payload)
        payload_data = data.get("data")
        if not isinstance(payload_data, dict) or not payload_data.get("product_id"):
            raise ParseError("product create response missing product_id")
        return str(payload_data["product_id"])

    def find_product_by_sku(self, sku: str) -> str | None:
        data = get_with_retry(self, "/product/get", {"sku": sku}, allow_404=True)
        if not data:
            return None
        payload_data = data.get("data")
        if not isinstance(payload_data, dict):
            raise ParseError("product get response missing data")
        product_id = payload_data.get("product_id")
        return str(product_id) if product_id else None

    def list_inventory(self, cursor: str | None) -> Page[InventoryItem]:
        offset = int(cursor or 0)
        data = get_with_retry(
            self, "/inventory/get", {"limit": 100, "offset": offset}
        )
        payload = data.get("data")
        if not isinstance(payload, dict) or not isinstance(payload.get("items"), list):
            raise ParseError("inventory response missing data.items")

        items = []
        for item in payload["items"]:
            try:
                items.append(
                    InventoryItem(
                        platform="lazada",
                        sku=str(item["sku"]),
                        stock=int(item["stock"]),
                    )
                )
            except (KeyError, TypeError, ValueError) as exc:
                raise ParseError(f"bad inventory item: {exc}") from exc

        count = int(payload.get("count", len(items)))
        next_offset = offset + len(items)
        next_cursor = str(next_offset) if items and next_offset < count else None
        return Page(items=tuple(items), next_cursor=next_cursor)


def _raise_for_status(resp: requests.Response, path: str) -> None:
    if resp.status_code in (401, 403):
        raise AuthError(f"{path} unauthorized")
    if resp.status_code == 429:
        raise RateLimitError(f"{path} rate limited", _retry_after(resp))
    if resp.status_code >= 500:
        raise TransientError(f"{path} server error {resp.status_code}")
    if 400 <= resp.status_code < 500:
        raise PermanentError(f"{path} client error {resp.status_code}")


def _raise_for_product_status(resp: requests.Response, path: str) -> None:
    if resp.status_code in (401, 403):
        raise AuthError(f"{path} unauthorized")
    if resp.status_code == 429:
        raise RateLimitError(f"{path} rate limited", _retry_after(resp))
    if resp.status_code >= 500:
        raise TransientError(f"{path} server error {resp.status_code}")
    if resp.status_code in (400, 422):
        raise ValidationError(f"{path} validation failed")
    if 400 <= resp.status_code < 500:
        raise PermanentError(f"{path} client error {resp.status_code}")


def _json_or_parse_error(resp: requests.Response) -> dict:
    try:
        data = resp.json()
    except ValueError as exc:
        raise ParseError("response is not JSON") from exc
    if not isinstance(data, dict):
        raise ParseError("response JSON is not an object")
    return data


def _retry_after(resp: requests.Response) -> float | None:
    value = resp.headers.get("Retry-After")
    try:
        return float(value) if value is not None else None
    except ValueError:
        return None


def _to_order(raw: dict) -> Order:
    try:
        order_id = str(raw["order_id"])
        currency = str(raw["currency"]).upper()
        status = str(raw["status"]).lower()
        paid_at = datetime.fromisoformat(raw["created_at"])
        updated_at = datetime.fromisoformat(raw["updated_at"])
        items = raw["items"]
    except (KeyError, TypeError, ValueError) as exc:
        raise ParseError(f"bad order payload: {exc}") from exc

    if paid_at.tzinfo is None or updated_at.tzinfo is None:
        raise ParseError(f"order {order_id} timestamps must include timezone")
    if not isinstance(items, list) or not items:
        raise ParseError(f"order {order_id} has no items")

    lines = []
    for item in items:
        if not isinstance(item, dict):
            raise ParseError(f"order {order_id} has a non-object item")
        try:
            line_currency = str(item.get("currency") or currency).upper()
            if line_currency != currency:
                raise ValueError("item currency mismatch")
            qty = int(item["qty"])
            if qty <= 0:
                raise ValueError("qty must be > 0")
            unit_price_minor = parse_amount_to_minor(str(item["unit_price"]), currency)
            if unit_price_minor < 0:
                raise ValueError("unit_price must be >= 0")
            lines.append(
                OrderLine(
                    platform="lazada",
                    platform_order_id=order_id,
                    line_id=str(item["order_item_id"]),
                    sku=str(item["sku"]),
                    qty=qty,
                    unit_price_minor=unit_price_minor,
                    discount_minor=0,
                    refund_minor=0,
                    currency=currency,
                )
            )
        except (KeyError, TypeError, ValueError) as exc:
            raise ParseError(f"bad item in order {order_id}: {exc}") from exc

    return Order(
        platform="lazada",
        platform_order_id=order_id,
        status=status,
        currency=currency,
        paid_at=paid_at,
        updated_at=updated_at,
        lines=tuple(lines),
        raw_json=json.dumps(raw, ensure_ascii=False),
    )


def _wait(retry_state: RetryCallState) -> float:
    exc = retry_state.outcome.exception()
    if isinstance(exc, RateLimitError) and exc.retry_after is not None:
        return min(exc.retry_after, 8.0)
    base = min(0.5 * (2 ** (retry_state.attempt_number - 1)), 8.0)
    return base + random.uniform(0, 0.5)


def get_with_retry(
    adapter: LazadaAdapter, path: str, params: dict, allow_404: bool = False
) -> dict:
    @retry(
        retry=retry_if_exception_type((RateLimitError, TransientError)),
        stop=stop_after_attempt(4),
        wait=_wait,
        reraise=True,
    )
    def _call() -> dict:
        return adapter._get(path, params, allow_404=allow_404)

    return _call()
