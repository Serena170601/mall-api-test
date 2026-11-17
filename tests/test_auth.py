"""认证链路：YAML 数据驱动 + 登录失败限流。"""
from __future__ import annotations

import allure
import pytest
from core.assertion import assert_business_error, assert_schema, assert_status
from core.case_loader import load_cases
from core.case_runner import execute_case


@allure.epic("mini-mall")
@allure.feature("认证")
@pytest.mark.write
@pytest.mark.parametrize("case", load_cases("auth.yaml"))
def test_auth_cases(clients, case):
    execute_case(clients, case)


@allure.story("登录失败限流")
@allure.title("同一账号连续失败 5 次后触发 429")
@pytest.mark.write
def test_login_rate_limit(clients):
    cfg_user = {"username": "carol", "password": "carol123"}
    for i in range(5):
        resp = clients["anonymous"].post(
            "/api/auth/login", json={"username": cfg_user["username"], "password": f"wrong-{i}"}
        )
        assert_business_error(resp, 1001)

    resp = clients["anonymous"].post(
        "/api/auth/login", json={"username": cfg_user["username"], "password": cfg_user["password"]}
    )
    assert_status(resp, 429)
    assert_business_error(resp, 4029)
    assert_schema(resp.json(), "error")
