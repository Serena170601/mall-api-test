"""pytest 全局夹具：被测服务生命周期、登录态客户端、数据准备与清理。

设计要点：
1. 服务未启动时自动拉起 uvicorn，保证“clone 下来 pytest 就能跑”；
2. 账号只在 session 级登录一次，token 挂在 Session 上复用，避免用例重复登录；
3. created_order 用 yield 做数据准备/清理，取消订单同时回滚库存，保证用例可重复执行。
"""
from __future__ import annotations

import os
import subprocess
import sys
import time
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

import pytest
import requests

from core.assertion import assert_business_ok
from core.config import Settings, settings
from core.logger import log
from core.request_client import RequestClient

ROOT = Path(__file__).resolve().parent


# --------------------------------------------------------------------------
# 被测服务
# --------------------------------------------------------------------------
def _port(base_url: str) -> int:
    return urlparse(base_url).port or 8000


def _healthy(base_url: str) -> bool:
    try:
        # 禁用环境代理：被测服务在本机，走系统代理会拿到代理的 404
        return requests.get(
            f"{base_url}/health", timeout=1, proxies={"http": None, "https": None}
        ).status_code == 200
    except requests.RequestException:
        return False


def _wait_health(base_url: str, seconds: int = 30) -> bool:
    deadline = time.time() + seconds
    while time.time() < deadline:
        if _healthy(base_url):
            return True
        time.sleep(0.5)
    return False


def _start_service(base_url: str) -> subprocess.Popen:
    port = _port(base_url)
    log.info("被测服务未运行，自动启动 uvicorn (port=%s, test_mode=1)", port)
    env = {**os.environ, "MALL_MOCK_TEST_MODE": "1"}
    return subprocess.Popen(
        [sys.executable, "-m", "uvicorn", "target_service.app:app",
         "--host", "127.0.0.1", "--port", str(port), "--log-level", "warning"],
        cwd=str(ROOT),
        env=env,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.STDOUT,
    )


@pytest.fixture(scope="session")
def service() -> str:
    base_url = settings.base_url
    if not _healthy(base_url):
        pytest.exit(f"被测服务不可用：{base_url}", returncode=1)
    return base_url


# --------------------------------------------------------------------------
# 服务生命周期：必须由主进程统一拉起/关闭
# xdist 下每个 worker 都会执行 conftest，如果各自启动就会抢端口、互相把服务杀掉
# --------------------------------------------------------------------------
_SERVICE_PROC: subprocess.Popen | None = None


def pytest_configure(config: pytest.Config) -> None:
    global _SERVICE_PROC
    base_url = settings.base_url

    if os.getenv("PYTEST_XDIST_WORKER"):
        # worker 进程只等待主进程把服务起好
        if not _wait_health(base_url, 60):
            pytest.exit(f"worker 等待被测服务超时：{base_url}", returncode=1)
        return

    if not _healthy(base_url):
        _SERVICE_PROC = _start_service(base_url)
        if not _wait_health(base_url, 30):
            _SERVICE_PROC.terminate()
            _SERVICE_PROC = None
            pytest.exit(f"被测服务启动失败，请检查 {base_url} 端口是否被占用", returncode=1)

    # 每次会话开始重置靶场数据，避免库存/限流计数被上一次运行污染
    try:
        resp = requests.post(f"{base_url}/api/_test/reset", timeout=3,
                             proxies={"http": None, "https": None})
        if resp.status_code == 200:
            log.info("靶场数据已重置")
        else:
            log.warning("靶场未开启测试模式，跳过重置：%s", resp.status_code)
    except requests.RequestException as exc:
        log.warning("重置靶场失败：%s", exc)


def pytest_unconfigure(config: pytest.Config) -> None:
    global _SERVICE_PROC
    if _SERVICE_PROC is not None:
        _SERVICE_PROC.terminate()
        try:
            _SERVICE_PROC.wait(timeout=5)
        except subprocess.TimeoutExpired:
            _SERVICE_PROC.kill()
        _SERVICE_PROC = None


@pytest.fixture(scope="session")
def cfg() -> Settings:
    return settings


# --------------------------------------------------------------------------
# 客户端与登录态
# --------------------------------------------------------------------------
def _login(base_url: str, username: str, password: str) -> str:
    resp = requests.post(
        f"{base_url}/api/auth/login",
        json={"username": username, "password": password},
        timeout=5,
        proxies={"http": None, "https": None},
    )
    payload = assert_business_ok(resp)
    return payload["data"]["token"]


@pytest.fixture(scope="session")
def clients(service: str) -> dict[str, RequestClient]:
    tokens = {
        "user": _login(service, **settings.user("user")),
        "other": _login(service, **settings.user("other")),
        "admin": _login(service, **settings.user("admin")),
    }
    mapping: dict[str, RequestClient] = {}
    for name, token in tokens.items():
        client = RequestClient(settings)
        client.set_token(token)
        mapping[name] = client
    mapping["anonymous"] = RequestClient(settings)
    mapping["bad_token"] = RequestClient(settings)
    mapping["bad_token"].set_token("forged.payload.signature")
    yield mapping
    for client in mapping.values():
        client.close()


@pytest.fixture(scope="session")
def token(clients: dict[str, RequestClient]) -> str:
    return clients["user"].session.headers["Authorization"].split(" ", 1)[1]


# --------------------------------------------------------------------------
# 数据准备与清理
# --------------------------------------------------------------------------
@pytest.fixture
def created_order(clients: dict[str, RequestClient]) -> Any:
    """下单前准备、用后取消：yield 前创建订单，yield 后取消并回滚库存。"""
    resp = clients["user"].post(
        "/api/orders",
        json={"items": [{"productId": 1, "quantity": 1}], "addressId": 1},
    )
    payload = assert_business_ok(resp)
    order = payload["data"]
    log.info("准备测试订单 orderId=%s", order["orderId"])
    yield order
    cleanup = clients["user"].delete(f"/api/orders/{order['orderId']}")
    log.info("清理测试订单 orderId=%s -> %s", order["orderId"], cleanup.status_code)


@pytest.fixture
def paid_order(clients: dict[str, RequestClient]) -> Any:
    """已支付订单 fixture：创建后立即支付，yield 后**不清理**。

    为什么不清理？已支付订单状态机不允许取消（返回 409 / 3002），
    而真正的退款接口不在当前靶场 scope 内。该 fixture 产生的脏数据靠
    pytest_configure 里的 /api/_test/reset 在每次测试会话开始时批量清理，
    而不是由 fixture 的 yield 后逻辑承担。
    """
    resp = clients["user"].post(
        "/api/orders",
        json={"items": [{"productId": 2, "quantity": 1}], "addressId": 1},
    )
    payload = assert_business_ok(resp)
    order = payload["data"]
    assert_business_ok(clients["user"].post(f"/api/orders/{order['orderId']}/pay"))
    log.info("准备已支付测试订单 orderId=%s", order["orderId"])
    yield order
    # 注意：这里没有 yield 后的清理逻辑，解释见 docstring
