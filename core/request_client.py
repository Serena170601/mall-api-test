"""请求封装层：统一 base_url、token 注入、超时、重试、日志与 Allure 附件。

封装层解决的问题：
- 用例里不再出现 url 拼接、headers 拼装、超时/重试散落；
- token 只在 Session 上设置一次，避免每条用例重复登录；
- 请求与响应统一落盘到 Allure，失败时无需翻控制台即可复现；
- 只对 502/503/504 这类网关瞬时错误重试，业务失败绝不重试（否则会掩盖幂等缺陷）。
"""
from __future__ import annotations

import json
import time
from typing import Any

import allure
import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

from core.config import Settings
from core.logger import log

SENSITIVE_HEADERS = {"authorization"}


class RequestClient:
    def __init__(self, settings: Settings):
        self.base_url = settings.base_url
        self.timeout = settings.timeout
        self.session = requests.Session()
        # 被测服务在本机/内网时不应走系统代理，否则健康检查会被代理返回 404
        self.session.trust_env = False
        retry_cfg = settings.retry
        retry = Retry(
            total=int(retry_cfg.get("total", 2)),
            backoff_factor=float(retry_cfg.get("backoff", 0.3)),
            status_forcelist=retry_cfg.get("status_forcelist", [502, 503, 504]),
            allowed_methods=frozenset({"GET", "HEAD", "OPTIONS"}),
            raise_on_status=False,
        )
        adapter = HTTPAdapter(max_retries=retry, pool_connections=20, pool_maxsize=20)
        self.session.mount("http://", adapter)
        self.session.mount("https://", adapter)
        self.session.headers.update({"Accept": "application/json"})

    # ---------------- token ----------------
    def set_token(self, token: str | None) -> None:
        if token:
            self.session.headers["Authorization"] = f"Bearer {token}"
        else:
            self.session.headers.pop("Authorization", None)

    # ---------------- 核心请求 ----------------
    def request(self, method: str, path: str, **kwargs: Any) -> requests.Response:
        url = path if path.startswith("http") else f"{self.base_url}{path}"
        kwargs.setdefault("timeout", self.timeout)

        started = time.perf_counter()
        log.info("→ %s %s params=%s json=%s", method.upper(), url,
                 kwargs.get("params"), kwargs.get("json"))
        resp = self.session.request(method.upper(), url, **kwargs)
        cost_ms = (time.perf_counter() - started) * 1000
        log.info("← %s %s %s %.1fms", method.upper(), url, resp.status_code, cost_ms)

        self._attach(method, url, kwargs, resp, cost_ms)
        return resp

    def _attach(self, method: str, url: str, kwargs: dict[str, Any],
                resp: requests.Response, cost_ms: float) -> None:
        safe_headers = {
            k: ("***" if k.lower() in SENSITIVE_HEADERS else v)
            for k, v in resp.request.headers.items()
        }
        req_dump = {
            "method": method.upper(),
            "url": url,
            "headers": safe_headers,
            "params": kwargs.get("params"),
            "json": kwargs.get("json"),
        }
        body: Any
        try:
            body = resp.json()
        except ValueError:
            body = resp.text
        resp_dump = {
            "status_code": resp.status_code,
            "elapsed_ms": round(cost_ms, 1),
            "body": body,
        }
        allure.attach(json.dumps(req_dump, ensure_ascii=False, indent=2),
                      name="request", attachment_type=allure.attachment_type.JSON)
        allure.attach(json.dumps(resp_dump, ensure_ascii=False, indent=2),
                      name="response", attachment_type=allure.attachment_type.JSON)

    # ---------------- 便捷方法 ----------------
    def get(self, path: str, **kwargs: Any) -> requests.Response:
        return self.request("GET", path, **kwargs)

    def post(self, path: str, **kwargs: Any) -> requests.Response:
        return self.request("POST", path, **kwargs)

    def put(self, path: str, **kwargs: Any) -> requests.Response:
        return self.request("PUT", path, **kwargs)

    def delete(self, path: str, **kwargs: Any) -> requests.Response:
        return self.request("DELETE", path, **kwargs)

    def close(self) -> None:
        self.session.close()
