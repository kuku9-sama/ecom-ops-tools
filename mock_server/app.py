"""本地 Mock 平台，只模拟端点形状；不验证 Lazada 真实签名。"""

import asyncio
from datetime import datetime

from fastapi import FastAPI, Query
from fastapi.responses import JSONResponse, Response
from pydantic import BaseModel, Field

app = FastAPI(
    title="本地 Mock 平台",
    description="模拟 Lazada 形状的订单与商品端点；不校验真实签名。",
)

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


INVENTORY = [
    {"sku": "MON-001", "stock": 18},
    {"sku": "KB-001", "stock": 5},
    {"sku": "ACC-001", "stock": 0},
    {"sku": "USB-001", "stock": 10},
]


@app.post(
    "/mock/fault",
    summary="注入故障",
    description="mode 支持 401/429/500/bad_json/timeout；times 是消费次数。",
)
def set_fault(
    mode: str | None = Query(None, description="故障类型"),
    times: int = Query(1, description="消费次数"),
):
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


@app.post(
    "/rest/auth/token/create",
    summary="创建访问令牌",
    description="Mock 只检查 app_key，不校验真实签名。",
)
def token_create(app_key: str = Query("mock", description="应用标识")):
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


@app.get(
    "/rest/orders/get",
    summary="拉取订单",
    description="支持 update_after、limit、offset 分页。",
)
async def orders_get(
    update_after: str | None = Query(None, description="ISO 时间，必须带时区"),
    limit: int = Query(100, ge=1, description="每页条数，最大 100"),
    offset: int = Query(0, ge=0, description="偏移量"),
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


@app.get(
    "/rest/inventory/get",
    summary="拉取库存",
    description="返回平台库存快照；只读，不写回。",
)
def inventory_get(
    limit: int = Query(100, ge=1, description="每页条数，最大 100"),
    offset: int = Query(0, ge=0, description="偏移量"),
):
    if limit > 100:
        return JSONResponse(
            {"code": "E_PARAM", "message": "limit must be <= 100"}, status_code=400
        )
    page = INVENTORY[offset: offset + limit]
    return {
        "code": "0",
        "data": {
            "items": page,
            "count": len(INVENTORY),
            "offset": offset,
            "limit": limit,
        },
    }


class ProductCreate(BaseModel):
    seller_sku: str = Field(description="卖家 SKU")
    name: str = Field(description="商品标题")
    price: str = Field(description="价格字符串")
    currency: str = Field(description="币种")
    quantity: int = Field(description="库存")
    category: str | None = Field(default=None, description="类目")
    image_url: str = Field(description="图片 URL")
    idempotency_key: str = Field(description="幂等键")


def _create_draft(payload: ProductCreate) -> str:
    if payload.seller_sku not in DRAFTS:
        DRAFTS[payload.seller_sku] = f"P{len(DRAFTS) + 1}"
    return DRAFTS[payload.seller_sku]


@app.post(
    "/rest/product/create",
    summary="创建商品草稿",
    description="按 seller_sku 幂等；只建草稿，不发布。",
)
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


@app.get("/rest/product/get", summary="按 SKU 查询商品")
def product_get(sku: str = Query(..., description="卖家 SKU")):
    product_id = DRAFTS.get(sku)
    if product_id is None:
        return JSONResponse(
            {"code": "E_NOT_FOUND", "message": "not found"}, status_code=404
        )
    return {"code": "0", "data": {"product_id": product_id}}
