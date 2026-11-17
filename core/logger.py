"""统一日志：控制台输出，格式带时间/级别/模块，便于定位失败用例。"""
from __future__ import annotations

import logging
import sys


def get_logger(name: str = "mall-api-test") -> logging.Logger:
    logger = logging.getLogger(name)
    if logger.handlers:
        return logger
    logger.setLevel(logging.INFO)
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(
        logging.Formatter("%(asctime)s | %(levelname)-7s | %(name)s | %(message)s", "%H:%M:%S")
    )
    logger.addHandler(handler)
    logger.propagate = False
    return logger


log = get_logger()
