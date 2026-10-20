"""本地 Mock 平台，只模拟端点形状；不验证 Lazada 真实签名。"""

import asyncio
from datetime import datetime

from fastapi import FastAPI, Query
from fastapi.responses import JSONResponse, Response
from pydantic import BaseModel

app = FastAPI(title="Mock Lazada")

# 故障注入状态：测试通过 POST /mock/fault 设置，消费 times 次后自动恢复。
FAULT = {"mode": None, "times": 0}
DRAFTS: dict[str, str] = {}

ORDERS = [
    {
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
            },
        ],
    },
    {
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
            },
        ],
    },
    {
        "order_id": "LZ1003",
        "status": "shipped",
        "currency": "CNY",
        "created_at": "2026-10-03T10:00:00+08:00",
        "updated_at": "2026-10-03T10:05:00+08:00",
        "items": [
            {
                "order_item_id": "1",
                "sku": "ACC-001",
                "qty": 1,
                "unit_price": "59.00",
                "currency": "CNY",
            },
        ],
    },
]


@app.post("/mock/fault")
def set_fault(mode: str | None = None, times: int = 1):
    FAULT.update(mode=mode, times=times)
    return {"ok": True, "fault": FAULT}


def _maybe_fault():
    if not FAULT["mode"] or FAULT["times"] <= 0:
        return None

    mode = FAULT["mode"]
    FAULT["times"] -= 1
    if mode == "401":
        return JSONResponse(
            {"code": "E_AUTH", "message": "unauthorized"}, status_code=401
        )
    if mode == "429":
        return JSONResponse(
            {"code": "E901", "message": "request too frequent"},
            status_code=429,
            headers={"Retry-After": "0"},
        )
    if mode == "500":
        return JSONResponse(
            {"code": "E500", "message": "server error"}, status_code=500
        )
    if mode == "bad_json":
        return Response(
            content="not json{", media_type="application/json", status_code=200
        )
    if mode == "timeout":
        return {"__sleep__": 2}
    return None


@app.post("/rest/auth/token/create")
def token_create(app_key: str = "mock"):
    # 真实 Lazada 要校验 sign；这里只检查参数存在，明确是 Mock。
    if not app_key:
        return JSONResponse(
            {"code": "E_PARAM", "message": "missing app_key"}, status_code=400
        )
    return {
        "code": "0",
        "access_token": "mock-token",
        "refresh_token": "mock-refresh",
        "expires_in": 3600,
    }


@app.get("/rest/orders/get")
async def orders_get(
    update_after: str | None = None,
    limit: int = Query(100, ge=1),
    offset: int = Query(0, ge=0),
):
    fault = _maybe_fault()
    if isinstance(fault, dict) and "__sleep__" in fault:
        await asyncio.sleep(fault["__sleep__"])
        fault = None
    if fault is not None:
        return fault

    if limit > 100:
        return JSONResponse(
            {"code": "E_PARAM", "message": "limit must be <= 100"}, status_code=400
        )

    candidates = ORDERS
    if update_after:
        try:
            cutoff = datetime.fromisoformat(update_after)
            if cutoff.tzinfo is None:
                raise ValueError("timezone required")
            candidates = [
                order
                for order in ORDERS
                if datetime.fromisoformat(order["updated_at"]) >= cutoff
            ]
        except (TypeError, ValueError):
            return JSONResponse(
                {"code": "E_PARAM", "message": "bad update_after"}, status_code=400
            )

    page = candidates[offset: offset + limit]
    return {
        "code": "0",
        "data": {
            "orders": page,
            "count": len(candidates),
            "offset": offset,
            "limit": limit,
        },
    }


class ProductCreate(BaseModel):
    seller_sku: str
    name: str
    price: str
    currency: str
    quantity: int
    category: str | None = None
    image_url: str
    idempotency_key: str


def _create_draft(payload: ProductCreate) -> str:
    if payload.seller_sku not in DRAFTS:
        DRAFTS[payload.seller_sku] = f"P{len(DRAFTS) + 1}"
    return DRAFTS[payload.seller_sku]


@app.post("/rest/product/create")
async def product_create(payload: ProductCreate):
    fault = _maybe_fault()
    if isinstance(fault, dict) and "__sleep__" in fault:
        # 先落库再睡：模拟“服务端已创建，但客户端超时”。
        product_id = _create_draft(payload)
        await asyncio.sleep(fault["__sleep__"])
        return {"code": "0", "data": {"product_id": product_id}}
    if fault is not None:
        return fault

    return {"code": "0", "data": {"product_id": _create_draft(payload)}}


@app.get("/rest/product/get")
def product_get(sku: str):
    product_id = DRAFTS.get(sku)
    if product_id is None:
        return JSONResponse(
            {"code": "E_NOT_FOUND", "message": "not found"}, status_code=404
        )
    return {"code": "0", "data": {"product_id": product_id}}
