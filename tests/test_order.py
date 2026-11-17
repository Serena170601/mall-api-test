"""下单链路：状态机、越权、库存回滚、数据准备与清理。"""
from __future__ import annotations

import allure
import pytest

from core.assertion import (
    assert_business_error,
    assert_business_ok,
    assert_jsonpath,
    assert_not_null,
    assert_schema,
    assert_status,
    assert_three_layers,
)

FEATURE = "交易"


def _stock(clients, product_id: int) -> int:
    payload = assert_business_ok(clients["anonymous"].get(f"/api/products/{product_id}"))
    return payload["data"]["stock"]


def _create_order(clients, items: list[dict], address_id: int = 1):
    return clients["user"].post("/api/orders", json={"items": items, "addressId": address_id})


@allure.epic("mini-mall")
@allure.feature(FEATURE)
@pytest.mark.write
class TestOrder:
    @allure.story("正常下单")
    @allure.title("下单成功且库存被扣减")
    def test_create_order_deducts_stock(self, clients):
        before = _stock(clients, 1)
        resp = _create_order(clients, [{"productId": 1, "quantity": 2}])
        payload = assert_three_layers(resp, 200, "order", 0)
        order = payload["data"]
        try:
            assert_jsonpath(payload, "$.data.status", "CREATED")
            assert_jsonpath(payload, "$.data.totalAmount", 1998.0)
            assert_not_null(payload, "$.data.orderId")
            assert _stock(clients, 1) == before - 2
        finally:
            clients["user"].delete(f"/api/orders/{order['orderId']}")

    @allure.title("库存不足时业务码 2002，且不生成订单")
    def test_create_order_insufficient_stock(self, clients):
        resp = _create_order(clients, [{"productId": 3, "quantity": 1}])
        assert_status(resp, 200)
        assert_business_error(resp, 2002)
        assert_schema(resp.json(), "error")

    @allure.title("下架商品下单失败，业务码 1002")
    def test_create_order_off_sale_product(self, clients):
        resp = _create_order(clients, [{"productId": 4, "quantity": 1}])
        assert_business_error(resp, 1002)

    @allure.title("商品不存在返回 404/4004")
    def test_create_order_product_not_found(self, clients):
        resp = _create_order(clients, [{"productId": 999, "quantity": 1}])
        assert_status(resp, 404)
        assert_business_error(resp, 4004)

    @allure.title("未登录下单返回 401/4001")
    def test_create_order_requires_token(self, clients):
        resp = clients["anonymous"].post(
            "/api/orders", json={"items": [{"productId": 1, "quantity": 1}], "addressId": 1}
        )
        assert_status(resp, 401)
        assert_business_error(resp, 4001)

    @allure.title("购买数量 0 触发 422 参数校验")
    def test_create_order_invalid_quantity(self, clients):
        resp = _create_order(clients, [{"productId": 1, "quantity": 0}])
        assert_status(resp, 422)

    @allure.story("越权")
    @allure.title("他人订单不可见，返回 403/4003")
    def test_other_user_cannot_view_order(self, clients, created_order):
        resp = clients["other"].get(f"/api/orders/{created_order['orderId']}")
        assert_status(resp, 403)
        assert_business_error(resp, 4003)

    @allure.title("他人订单不可支付，返回 403/4003")
    def test_other_user_cannot_pay_order(self, clients, created_order):
        resp = clients["other"].post(f"/api/orders/{created_order['orderId']}/pay")
        assert_status(resp, 403)
        assert_business_error(resp, 4003)

    @allure.title("管理员可查看任意用户订单")
    def test_admin_can_view_any_order(self, clients, created_order):
        resp = clients["admin"].get(f"/api/orders/{created_order['orderId']}")
        payload = assert_three_layers(resp, 200, "order", 0)
        assert_jsonpath(payload, "$.data.orderId", created_order["orderId"])

    @allure.title("普通用户访问管理端返回 403/4003")
    def test_user_cannot_access_admin_api(self, clients):
        resp = clients["user"].get("/api/admin/orders")
        assert_status(resp, 403)
        assert_business_error(resp, 4003)

    @allure.story("状态机")
    @allure.title("重复支付返回 409/3001")
    def test_pay_twice_conflict(self, clients, created_order):
        first = clients["user"].post(f"/api/orders/{created_order['orderId']}/pay")
        payload = assert_business_ok(first)
        assert_jsonpath(payload, "$.data.status", "PAID")

        second = clients["user"].post(f"/api/orders/{created_order['orderId']}/pay")
        assert_status(second, 409)
        assert_business_error(second, 3001)

    @allure.title("已支付订单不可取消，返回 409/3002")
    def test_cancel_paid_order_conflict(self, clients):
        created = assert_business_ok(_create_order(clients, [{"productId": 2, "quantity": 1}]))
        order_id = created["data"]["orderId"]
        assert_business_ok(clients["user"].post(f"/api/orders/{order_id}/pay"))
        resp = clients["user"].delete(f"/api/orders/{order_id}")
        assert_status(resp, 409)
        assert_business_error(resp, 3002)

    @allure.title("取消订单回滚库存")
    def test_cancel_restores_stock(self, clients):
        before = _stock(clients, 1)
        created = assert_business_ok(_create_order(clients, [{"productId": 1, "quantity": 3}]))
        order_id = created["data"]["orderId"]
        assert _stock(clients, 1) == before - 3

        resp = clients["user"].delete(f"/api/orders/{order_id}")
        payload = assert_three_layers(resp, 200, "order", 0)
        assert_jsonpath(payload, "$.data.status", "CANCELED")
        assert _stock(clients, 1) == before

    @allure.title("购物车金额汇总正确")
    def test_cart_total_amount(self, clients):
        assert_business_ok(
            clients["other"].post("/api/cart/items", json={"productId": 1, "quantity": 2})
        )
        payload = assert_business_ok(clients["other"].get("/api/cart"))
        assert_jsonpath(payload, "$.data.count", 1)
        assert_jsonpath(payload, "$.data.totalAmount", 1998.0)

    # ===== D4 新增 3 条用例 =====
    @allure.story("状态机")
    @allure.title("已取消订单再次取消 → 409/3002（状态机幂等性验证）")
    def test_cancel_already_canceled_order(self, clients):
        """先创建并取消，再取消一次，应触发 409/3002。

        这条用例专门验证「订单状态机」的一个易错点：CANCELED 状态是
        终态，不允许再被取消，也不允许支付。很多项目这里会漏测，导致
        脏数据（比如取消的订单又被支付，库存重复扣减）。
        """
        # 1. 创建并取消
        created = assert_business_ok(_create_order(clients, [{"productId": 1, "quantity": 1}]))
        order_id = created["data"]["orderId"]
        assert_business_ok(clients["user"].delete(f"/api/orders/{order_id}"))

        # 2. 再次取消 → 409 / 3002
        resp = clients["user"].delete(f"/api/orders/{order_id}")
        assert_status(resp, 409)
        assert_business_error(resp, 3002)

    @allure.story("越权")
    @allure.title("管理员无权执行用户写操作（取消他人订单 → 403/4003）")
    def test_admin_cannot_cancel_other_order(self, clients, created_order):
        """管理员能看任意订单，但「取消订单」属于用户本人的写操作，
        必须只有订单所有者能执行。这条用例防止「权限放大」。"""
        resp = clients["admin"].delete(f"/api/orders/{created_order['orderId']}")
        assert_status(resp, 403)
        assert_business_error(resp, 4003)

    @allure.title("混合商品下单：totalAmount 等于单价×数量之和")
    def test_mixed_order_total_amount(self, clients):
        product1_before = _stock(clients, 1)
        product2_before = _stock(clients, 2)

        # 商品1：红米 Note 13，999 × 2 = 1998
        # 商品2：机械键盘，399 × 1 = 399
        # 合计：1998 + 399 = 2397.0
        resp = _create_order(
            clients,
            [
                {"productId": 1, "quantity": 2},
                {"productId": 2, "quantity": 1},
            ],
        )
        payload = assert_three_layers(resp, 200, "order", 0)

        # 1. 金额精确断言（浮点数用 == 是安全的，因为靶场计算用 round 到 2 位）
        assert_jsonpath(payload, "$.data.totalAmount", 2397.0)

        # 2. 库存分别扣减
        assert _stock(clients, 1) == product1_before - 2
        assert _stock(clients, 2) == product2_before - 1

        # 清理：取消订单，回滚两类商品库存
        clients["user"].delete(f"/api/orders/{payload['data']['orderId']}")
