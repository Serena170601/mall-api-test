"""mini-mall：被测系统（自建靶场）。

设计目标（为接口自动化与 AI 用例生成服务）：
1. 有真实登录态（HMAC 签名 token），可测 401 / token 过期 / 角色权限 403；
2. 有业务错误码与 HTTP 状态码分离（业务失败返回 200 + code!=0），可做 HTTP/结构/业务三层断言；
3. 有库存与订单状态机（CREATED -> PAID / CANCELED），可测重复支付、越权、取消回滚库存；
4. 有登录失败限流（60 秒 5 次），可测 429；
5. 暴露 /openapi.json，供第 3 周 AI 用例生成环节直接消费。
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os
import time
from typing import Any

from fastapi import Depends, FastAPI, Header
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

SECRET = "mall-mock-secret"
TOKEN_TTL = 7200
TEST_MODE = os.getenv("MALL_MOCK_TEST_MODE") == "1"
MAX_LOGIN_FAILS = 5
LOGIN_FAIL_WINDOW = 60


# --------------------------------------------------------------------------
# 状态
# --------------------------------------------------------------------------
def build_state() -> dict[str, Any]:
    return {
        "users": {
            "alice": {"password": "alice123", "userId": 1001, "role": "user", "nickname": "Alice"},
            "bob": {"password": "bob123", "userId": 1002, "role": "user", "nickname": "Bob"},
            "carol": {"password": "carol123", "userId": 1003, "role": "user", "nickname": "Carol"},
            "admin": {"password": "admin123", "userId": 1000, "role": "admin", "nickname": "Admin"},
        },
        "products": {
            1: {"id": 1, "name": "红米 Note 13", "price": 999.0, "stock": 10, "onSale": True, "category": "手机"},
            2: {"id": 2, "name": "机械键盘 87 键", "price": 399.0, "stock": 3, "onSale": True, "category": "外设"},
            3: {"id": 3, "name": "限量大衣", "price": 899.0, "stock": 0, "onSale": True, "category": "服饰"},
            4: {"id": 4, "name": "停售耳机", "price": 199.0, "stock": 5, "onSale": False, "category": "外设"},
        },
        "orders": {},
        "nextOrderId": 10001,
        "cart": {},
        "loginFails": {},
    }


STATE: dict[str, Any] = build_state()


class ApiError(Exception):
    """业务异常：统一转换为 {"code", "message", "data"} 信封。"""

    def __init__(self, status: int, code: int, message: str):
        super().__init__(message)
        self.status = status
        self.code = code
        self.message = message


def ok(data: Any = None) -> dict[str, Any]:
    return {"code": 0, "message": "ok", "data": data}


# --------------------------------------------------------------------------
# token
# --------------------------------------------------------------------------
def _sign(raw: str) -> str:
    return hmac.new(SECRET.encode(), raw.encode(), hashlib.sha256).hexdigest()[:32]


def issue_token(username: str, user: dict[str, Any]) -> str:
    payload = {
        "sub": username,
        "uid": user["userId"],
        "role": user["role"],
        "exp": int(time.time()) + TOKEN_TTL,
    }
    raw = base64.urlsafe_b64encode(
        json.dumps(payload, separators=(",", ":")).encode()
    ).decode().rstrip("=")
    return f"{raw}.{_sign(raw)}"


def decode_token(token: str) -> dict[str, Any] | None:
    try:
        raw, sig = token.rsplit(".", 1)
    except ValueError:
        return None
    if not hmac.compare_digest(sig, _sign(raw)):
        return None
    try:
        payload = json.loads(base64.urlsafe_b64decode(raw + "=" * (-len(raw) % 4)))
    except Exception:
        return None
    if payload.get("exp", 0) < time.time():
        return None
    if payload.get("sub") not in STATE["users"]:
        return None
    return payload


def require_user(authorization: str | None = Header(default=None)) -> dict[str, Any]:
    if not authorization or not authorization.lower().startswith("bearer "):
        raise ApiError(401, 4001, "未登录或缺少 token")
    payload = decode_token(authorization.split(" ", 1)[1].strip())
    if payload is None:
        raise ApiError(401, 4002, "token 无效或已过期")
    return payload


def require_admin(payload: dict[str, Any] = Depends(require_user)) -> dict[str, Any]:
    if payload["role"] != "admin":
        raise ApiError(403, 4003, "无权限访问，需要管理员角色")
    return payload


# --------------------------------------------------------------------------
# 请求模型
# --------------------------------------------------------------------------
class LoginBody(BaseModel):
    username: str = Field(min_length=1, max_length=32)
    password: str = Field(min_length=1, max_length=64)


class CartItemBody(BaseModel):
    productId: int = Field(ge=1)
    quantity: int = Field(ge=1, le=99)


class OrderItemBody(BaseModel):
    productId: int
    quantity: int = Field(ge=1, le=99)


class CreateOrderBody(BaseModel):
    items: list[OrderItemBody] = Field(min_length=1)
    addressId: int = 1


# --------------------------------------------------------------------------
# 应用
# --------------------------------------------------------------------------
app = FastAPI(title="mini-mall mock", version="1.0.0")


@app.exception_handler(ApiError)
async def api_error_handler(_request, exc: ApiError) -> JSONResponse:
    return JSONResponse(
        status_code=exc.status,
        content={"code": exc.code, "message": exc.message, "data": None},
    )


@app.get("/health")
def health() -> dict[str, Any]:
    return {"status": "ok"}


@app.post("/api/_test/reset")
def reset_state() -> dict[str, Any]:
    """靶场专用：重置数据。仅在 MALL_MOCK_TEST_MODE=1 时可用。"""
    global STATE
    if not TEST_MODE:
        raise ApiError(403, 4009, "测试模式未开启")
    STATE = build_state()
    return ok({"reset": True})


# ---------------------------- 认证 ----------------------------------------
@app.post("/api/auth/login")
def login(body: LoginBody) -> dict[str, Any]:
    now = time.time()
    fails = [t for t in STATE["loginFails"].get(body.username, []) if now - t < LOGIN_FAIL_WINDOW]
    STATE["loginFails"][body.username] = fails
    if len(fails) >= MAX_LOGIN_FAILS:
        raise ApiError(429, 4029, "登录失败次数过多，请稍后再试")

    user = STATE["users"].get(body.username)
    if user is None or user["password"] != body.password:
        fails.append(now)
        STATE["loginFails"][body.username] = fails
        # 业务失败用 HTTP 200，靠 code 区分，这是本项目三层断言的关键差异点
        raise ApiError(200, 1001, "用户名或密码错误")

    STATE["loginFails"].pop(body.username, None)
    return ok(
        {
            "token": issue_token(body.username, user),
            "userId": user["userId"],
            "username": body.username,
            "role": user["role"],
            "expiresIn": TOKEN_TTL,
        }
    )


@app.get("/api/auth/profile")
def profile(payload: dict[str, Any] = Depends(require_user)) -> dict[str, Any]:
    user = STATE["users"][payload["sub"]]
    return ok(
        {
            "userId": user["userId"],
            "username": payload["sub"],
            "nickname": user["nickname"],
            "role": user["role"],
        }
    )


# ---------------------------- 商品 ----------------------------------------
@app.get("/api/products")
def list_products(
    keyword: str | None = None,
    page: int = 1,
    size: int = 10,
) -> dict[str, Any]:
    if page < 1:
        raise ApiError(400, 1100, "page 必须大于等于 1")
    if not 1 <= size <= 50:
        raise ApiError(400, 1101, "size 取值范围为 1-50")

    items = [p for p in STATE["products"].values() if p["onSale"]]
    if keyword:
        items = [p for p in items if keyword.lower() in p["name"].lower()]
    total = len(items)
    start = (page - 1) * size
    return ok({"total": total, "page": page, "size": size, "items": items[start:start + size]})


@app.get("/api/products/{product_id}")
def get_product(product_id: int) -> dict[str, Any]:
    product = STATE["products"].get(product_id)
    if product is None:
        raise ApiError(404, 4004, "商品不存在")
    return ok(product)


# ---------------------------- 购物车 --------------------------------------
@app.post("/api/cart/items")
def add_cart_item(
    body: CartItemBody,
    payload: dict[str, Any] = Depends(require_user),
) -> dict[str, Any]:
    product = STATE["products"].get(body.productId)
    if product is None:
        raise ApiError(404, 4004, "商品不存在")
    if not product["onSale"]:
        raise ApiError(200, 1002, "商品已下架")
    if product["stock"] < body.quantity:
        raise ApiError(200, 2002, "库存不足")

    items = STATE["cart"].setdefault(payload["sub"], [])
    items.append({"productId": product["id"], "name": product["name"],
                  "price": product["price"], "quantity": body.quantity})
    return ok({"count": len(items), "items": items})


@app.get("/api/cart")
def get_cart(payload: dict[str, Any] = Depends(require_user)) -> dict[str, Any]:
    items = STATE["cart"].get(payload["sub"], [])
    total = sum(i["price"] * i["quantity"] for i in items)
    return ok({"count": len(items), "items": items, "totalAmount": round(total, 2)})


# ---------------------------- 订单 ----------------------------------------
def _find_order(order_id: int) -> dict[str, Any]:
    order = STATE["orders"].get(order_id)
    if order is None:
        raise ApiError(404, 4005, "订单不存在")
    return order


@app.post("/api/orders")
def create_order(
    body: CreateOrderBody,
    payload: dict[str, Any] = Depends(require_user),
) -> dict[str, Any]:
    total = 0.0
    snapshot = []
    for item in body.items:
        product = STATE["products"].get(item.productId)
        if product is None:
            raise ApiError(404, 4004, f"商品 {item.productId} 不存在")
        if not product["onSale"]:
            raise ApiError(200, 1002, f"商品 {product['name']} 已下架")
        if product["stock"] < item.quantity:
            raise ApiError(200, 2002, f"商品 {product['name']} 库存不足")
        total += product["price"] * item.quantity
        snapshot.append({"productId": product["id"], "name": product["name"],
                         "price": product["price"], "quantity": item.quantity})

    for item in body.items:
        STATE["products"][item.productId]["stock"] -= item.quantity

    order_id = STATE["nextOrderId"]
    STATE["nextOrderId"] += 1
    order = {
        "orderId": order_id,
        "userId": payload["uid"],
        "status": "CREATED",
        "items": snapshot,
        "totalAmount": round(total, 2),
        "addressId": body.addressId,
        "createdAt": time.strftime("%Y-%m-%d %H:%M:%S"),
        "paidAt": None,
    }
    STATE["orders"][order_id] = order
    return ok(order)


@app.get("/api/orders")
def list_my_orders(payload: dict[str, Any] = Depends(require_user)) -> dict[str, Any]:
    items = [o for o in STATE["orders"].values() if o["userId"] == payload["uid"]]
    return ok({"total": len(items), "items": items})


@app.get("/api/orders/{order_id}")
def get_order(order_id: int, payload: dict[str, Any] = Depends(require_user)) -> dict[str, Any]:
    order = _find_order(order_id)
    if order["userId"] != payload["uid"] and payload["role"] != "admin":
        raise ApiError(403, 4003, "无权查看他人订单")
    return ok(order)


@app.post("/api/orders/{order_id}/pay")
def pay_order(order_id: int, payload: dict[str, Any] = Depends(require_user)) -> dict[str, Any]:
    order = _find_order(order_id)
    if order["userId"] != payload["uid"]:
        raise ApiError(403, 4003, "无权操作他人订单")
    if order["status"] != "CREATED":
        raise ApiError(409, 3001, f"订单当前状态 {order['status']} 不允许支付")
    order["status"] = "PAID"
    order["paidAt"] = time.strftime("%Y-%m-%d %H:%M:%S")
    return ok(order)


@app.delete("/api/orders/{order_id}")
def cancel_order(order_id: int, payload: dict[str, Any] = Depends(require_user)) -> dict[str, Any]:
    order = _find_order(order_id)
    if order["userId"] != payload["uid"]:
        raise ApiError(403, 4003, "无权操作他人订单")
    if order["status"] != "CREATED":
        raise ApiError(409, 3002, f"订单当前状态 {order['status']} 不允许取消")
    order["status"] = "CANCELED"
    for item in order["items"]:
        STATE["products"][item["productId"]]["stock"] += item["quantity"]
    return ok(order)


# ---------------------------- 管理端 --------------------------------------
@app.get("/api/admin/orders")
def admin_list_orders(_admin: dict[str, Any] = Depends(require_admin)) -> dict[str, Any]:
    items = sorted(STATE["orders"].values(), key=lambda o: o["orderId"], reverse=True)
    return ok({"total": len(items), "items": items})


@app.get("/api/admin/stats")
def admin_stats(_admin: dict[str, Any] = Depends(require_admin)) -> dict[str, Any]:
    orders = list(STATE["orders"].values())
    paid = [o for o in orders if o["status"] == "PAID"]
    return ok(
        {
            "orderCount": len(orders),
            "paidCount": len(paid),
            "paidAmount": round(sum(o["totalAmount"] for o in paid), 2),
        }
    )
