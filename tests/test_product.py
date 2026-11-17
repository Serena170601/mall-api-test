"""商品查询链路：全部来自 YAML，纯读，可 -n 并行。"""
from __future__ import annotations

import allure
import pytest
from core.case_loader import load_cases
from core.case_runner import execute_case


@allure.epic("mini-mall")
@allure.feature("商品查询")
@pytest.mark.read
@pytest.mark.parametrize("case", load_cases("product.yaml"))
def test_product_cases(clients, case):
    execute_case(clients, case)
