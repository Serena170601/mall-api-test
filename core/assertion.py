"""三层断言：HTTP 状态码 -> 响应结构(jsonschema) -> 业务字段(jsonpath)。

为什么分三层：只断 200 会把“接口 200 但业务失败”的缺陷全部漏掉（本项目故意用
HTTP 200 + code!=0 表达业务失败）；只断字段不做结构校验，则字段增删、类型变化无法发现。
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import allure
import jsonschema
import requests
from jsonpath_ng.ext import parse

ROOT = Path(__file__).resolve().parents[1]
SCHEMA_DIR = ROOT / "config" / "schemas"

UNSET = object()


def _payload(response: requests.Response) -> dict[str, Any]:
    try:
        return response.json()
    except ValueError as exc:
        raise AssertionError(
            f"响应不是合法 JSON：status={response.status_code}, body={response.text[:500]}"
        ) from exc


# ------------------------------ 第一层 ------------------------------------
def assert_status(response: requests.Response, expected: int = 200) -> None:
    assert response.status_code == expected, (
        f"HTTP 状态码不符：期望 {expected}，实际 {response.status_code}，"
        f"body={response.text[:500]}"
    )


# ------------------------------ 第二层 ------------------------------------
def assert_schema(payload: dict[str, Any], schema_name: str) -> None:
    schema_path = SCHEMA_DIR / f"{schema_name}.json"
    assert schema_path.exists(), f"schema 文件不存在：{schema_path}"
    schema = json.loads(schema_path.read_text(encoding="utf-8"))
    try:
        jsonschema.validate(instance=payload, schema=schema)
    except jsonschema.ValidationError as exc:
        raise AssertionError(
            f"响应结构不符合 schema[{schema_name}]：{exc.message} "
            f"@ {'/'.join(str(p) for p in exc.absolute_path)}"
        ) from exc


# ------------------------------ 第三层 ------------------------------------
def assert_jsonpath(payload: Any, expr: str, expected: Any = UNSET) -> None:
    matches = [m.value for m in parse(expr).find(payload)]
    assert matches, f"jsonpath 未命中：{expr}，payload={json.dumps(payload, ensure_ascii=False)[:500]}"
    if expected is UNSET:
        return
    assert matches[0] == expected, f"jsonpath {expr} 期望 {expected!r}，实际 {matches[0]!r}"


def assert_not_null(payload: Any, expr: str) -> None:
    matches = [m.value for m in parse(expr).find(payload)]
    assert matches and matches[0] not in (None, "", [], {}), f"jsonpath {expr} 为空：{matches!r}"


def assert_length(payload: Any, expr: str, expected: int) -> None:
    matches = [m.value for m in parse(expr).find(payload)]
    assert matches, f"jsonpath 未命中：{expr}"
    assert isinstance(matches[0], (list, dict, str)), f"jsonpath {expr} 不是可计数类型"
    assert len(matches[0]) == expected, f"jsonpath {expr} 长度期望 {expected}，实际 {len(matches[0])}"


def assert_business_ok(response: requests.Response) -> dict[str, Any]:
    """业务层断言：HTTP 200 且 code==0。"""
    assert_status(response, 200)
    payload = _payload(response)
    assert_jsonpath(payload, "$.code", 0)
    return payload


def assert_business_error(response: requests.Response, expected_code: int) -> dict[str, Any]:
    payload = _payload(response)
    assert_jsonpath(payload, "$.code", expected_code)
    assert_not_null(payload, "$.message")
    return payload


# ------------------------------ 组合断言 ----------------------------------
@allure.step("三层断言：HTTP {expected_status} / schema {schema_name} / 业务 code {expected_code}")
def assert_three_layers(
    response: requests.Response,
    expected_status: int = 200,
    schema_name: str | None = None,
    expected_code: int = 0,
) -> dict[str, Any]:
    assert_status(response, expected_status)
    payload = _payload(response)
    assert_jsonpath(payload, "$.code", expected_code)
    if schema_name:
        assert_schema(payload, schema_name)
    return payload
