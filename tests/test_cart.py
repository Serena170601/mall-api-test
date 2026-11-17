"""购物车链路：YAML 驱动。

为什么只有两行？因为数据驱动把「用例逻辑」和「测试代码」解耦了：
- 用例是什么？写在 data/cart.yaml 里；
- 怎么跑？复用 core/case_runner.execute_case；
- 断言在哪？在 core/assertion.py 的三层断言里。

新增一个购物车接口的用例 → 只改 YAML，不用动这个文件。
"""
from __future__ import annotations

import allure
import pytest

from core.case_loader import load_cases
from core.case_runner import execute_case


@allure.epic("mini-mall")
@allure.feature("购物车")
@pytest.mark.write  # 写操作，串行执行，避免互相污染库存/购物车状态
@pytest.mark.parametrize("case", load_cases("cart.yaml"))
def test_cart_cases(clients, case):
    execute_case(clients, case)
