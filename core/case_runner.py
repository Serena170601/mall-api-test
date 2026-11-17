"""YAML 用例执行器：按 auth/method/params/json/expected 驱动一次请求并做分层断言。

新增接口用例时，绝大多数场景只需要在 data/*.yaml 里加一条数据，不需要写 Python 代码。
"""
from __future__ import annotations

from typing import Any

import allure
import requests

from core.assertion import (
    assert_jsonpath,
    assert_length,
    assert_not_null,
    assert_schema,
    assert_status,
)
from core.request_client import RequestClient


def execute_case(clients: dict[str, RequestClient], case: dict[str, Any]) -> requests.Response:
    client = clients[case.get("auth", "user")]
    method = case.get("method", "GET").lower()
    kwargs: dict[str, Any] = {}
    for key in ("params", "json"):
        if key in case:
            kwargs[key] = case[key]

    with allure.step(f"{case.get('title', case['id'])}"):
        resp = client.request(method, case["path"], **kwargs)
        expected = case.get("expected", {})

        assert_status(resp, expected.get("status", 200))
        payload = resp.json()

        if expected.get("detail"):
            assert "detail" in payload, f"期望框架校验错误 detail，实际 {payload}"
        else:
            assert_jsonpath(payload, "$.code", expected.get("code", 0))

        if expected.get("schema"):
            assert_schema(payload, expected["schema"])
        for expr, value in (expected.get("jsonpath") or {}).items():
            assert_jsonpath(payload, expr, value)
        for expr in expected.get("not_null") or []:
            assert_not_null(payload, expr)
        for expr, size in (expected.get("length") or {}).items():
            assert_length(payload, expr, size)
    return resp
