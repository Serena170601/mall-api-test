"""配置加载：YAML 为底，环境变量优先（CI 里用 BASE_URL 覆盖）。"""
from __future__ import annotations

import os
from pathlib import Path
from typing import Any

import yaml

ROOT = Path(__file__).resolve().parents[1]
CONFIG_PATH = ROOT / "config" / "config.yaml"


class Settings:
    def __init__(self, data: dict[str, Any]):
        self._data = data

    @classmethod
    def load(cls, path: Path = CONFIG_PATH) -> "Settings":
        with path.open(encoding="utf-8") as f:
            data = yaml.safe_load(f) or {}
        data["base_url"] = os.getenv("BASE_URL", data.get("base_url", "http://127.0.0.1:8000"))
        data["timeout"] = float(os.getenv("HTTP_TIMEOUT", data.get("timeout", 10)))
        return cls(data)

    def get(self, key: str, default: Any = None) -> Any:
        return self._data.get(key, default)

    @property
    def base_url(self) -> str:
        return self._data["base_url"].rstrip("/")

    @property
    def timeout(self) -> float:
        return self._data["timeout"]

    @property
    def retry(self) -> dict[str, Any]:
        return self._data.get("retry", {})

    def user(self, name: str) -> dict[str, str]:
        return self._data["users"][name]


settings = Settings.load()
