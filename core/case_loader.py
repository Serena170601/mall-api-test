"""YAML 用例加载器：把 YAML 文件转成 pytest 参数，实现数据与代码分离。

用 pytest.param(..., id=case["id"]) 保证报告里用例名可读（product_list_page_invalid），
而不是 cases0/cases1。
"""
from __future__ import annotations

from pathlib import Path

import pytest
import yaml

DATA_DIR = Path(__file__).resolve().parents[1] / "data"


def load_cases(filename: str) -> list:
    path = DATA_DIR / filename
    if not path.exists():
        return []
    raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    cases = raw.get("cases", raw) if isinstance(raw, dict) else raw
    return [pytest.param(case, id=case["id"]) for case in cases]
