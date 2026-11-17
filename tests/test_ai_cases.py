"""AI 生成用例的执行入口。

执行门槛：只有人工审核通过（reviewed: true）的用例才会真正执行；
needs_review / reviewed: false 的草稿在报告里以 skip 呈现，避免“AI 说什么就测什么”。
"""
from __future__ import annotations

from pathlib import Path

import allure
import pytest
import yaml

from core.case_runner import execute_case

AI_CASES_PATH = Path(__file__).resolve().parents[1] / "data" / "generated" / "ai_cases.yaml"


def _load_cases() -> list[dict]:
    if not AI_CASES_PATH.exists():
        return []
    data = yaml.safe_load(AI_CASES_PATH.read_text(encoding="utf-8")) or {}
    return data.get("cases", []) if isinstance(data, dict) else data


def _params() -> list:
    cases = _load_cases()
    if not cases:
        return [
            pytest.param(
                None,
                id="no_ai_cases",
                marks=pytest.mark.skip(reason="尚未生成 AI 用例，先执行 tools/gen_cases.py"),
            )
        ]
    params = []
    for index, case in enumerate(cases):
        cid = case.get("id", f"ai_case_{index}")
        marks = []
        if case.get("reviewed") is not True:
            marks.append(pytest.mark.skip(reason="AI 草稿未人工审核（reviewed != true）"))
        params.append(pytest.param(case, id=cid, marks=marks))
    return params


@allure.epic("mini-mall")
@allure.feature("AI 生成用例")
@pytest.mark.ai
@pytest.mark.parametrize("case", _params())
def test_ai_generated_cases(clients, case):
    execute_case(clients, case)
