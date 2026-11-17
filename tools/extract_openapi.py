"""从运行中的服务（或本地 OpenAPI 文件）抽取接口清单，产出 AI 生成用例的原材料。

用法：
    python tools/extract_openapi.py                      # 默认抓 http://127.0.0.1:8000/openapi.json
    python tools/extract_openapi.py openapi.json         # 从本地文件抽取
    OPENAPI_URL=http://host/openapi.json python tools/extract_openapi.py
"""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path

import requests
import yaml

ROOT = Path(__file__).resolve().parents[1]
OUT_PATH = ROOT / "data" / "generated" / "operations.yaml"
HTTP_METHODS = {"get", "post", "put", "patch", "delete"}
KEEP_KEYS = ("type", "format", "enum", "example", "default",
             "minimum", "maximum", "minLength", "maxLength", "pattern")


def load_spec() -> dict:
    src = sys.argv[1] if len(sys.argv) > 1 else os.getenv(
        "OPENAPI_URL", "http://127.0.0.1:8000/openapi.json"
    )
    if src.startswith("http"):
        resp = requests.get(src, timeout=15)
        resp.raise_for_status()
        return resp.json()
    return json.loads(Path(src).read_text(encoding="utf-8"))


def resolve(spec: dict, node: dict, depth: int = 0) -> dict:
    while isinstance(node, dict) and "$ref" in node and depth < 10:
        ref = node["$ref"]
        if not ref.startswith("#/"):
            return node
        current = spec
        for part in ref[2:].split("/"):
            current = current[part]
        node = current
        depth += 1
    return node


def summarize(spec: dict, schema: dict | None) -> dict | None:
    if schema is None:
        return None
    schema = resolve(spec, schema)
    if not isinstance(schema, dict):
        return schema
    out = {key: schema[key] for key in KEEP_KEYS if key in schema}
    if "required" in schema:
        out["required"] = schema["required"]
    if "properties" in schema:
        out["properties"] = {
            name: summarize(spec, prop) for name, prop in schema["properties"].items()
        }
    if "items" in schema:
        out["items"] = summarize(spec, schema["items"])
    if "additionalProperties" in schema:
        out["additionalProperties"] = schema["additionalProperties"]
    return out


def extract(spec: dict) -> list[dict]:
    operations = []
    for path, methods in sorted(spec.get("paths", {}).items()):
        for method, op in methods.items():
            if method.lower() not in HTTP_METHODS:
                continue
            params = []
            for raw in op.get("parameters", []):
                param = resolve(spec, raw)
                params.append(
                    {
                        "name": param.get("name"),
                        "in": param.get("in"),
                        "required": param.get("required", False),
                        "schema": summarize(spec, param.get("schema")),
                    }
                )
            body = (
                op.get("requestBody", {})
                .get("content", {})
                .get("application/json", {})
                .get("schema")
            )
            responses = {}
            for status, raw in op.get("responses", {}).items():
                resp = resolve(spec, raw)
                schema = resp.get("content", {}).get("application/json", {}).get("schema")
                responses[str(status)] = summarize(spec, schema)

            operations.append(
                {
                    "operationId": op.get("operationId", f"{method}_{path}"),
                    "method": method.upper(),
                    "path": path,
                    "summary": op.get("summary"),
                    "parameters": params,
                    "requestBody": summarize(spec, body),
                    "responses": responses,
                    "auth": "Authorization: Bearer <token>" if path.startswith("/api/") and
                    "login" not in path else "none",
                }
            )
    return operations


def main() -> None:
    spec = load_spec()
    operations = extract(spec)
    payload = {
        "source": spec.get("info", {}),
        "auth_notes": "先 POST /api/auth/login 获取 token，后续接口需 Authorization: Bearer <token>；"
                      "业务失败返回 HTTP 200 + code!=0，鉴权/越权/限流分别用 401/403/429。",
        "operations": operations,
    }
    OUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    OUT_PATH.write_text(
        yaml.safe_dump(payload, allow_unicode=True, sort_keys=False), encoding="utf-8"
    )
    print(f"已抽取 {len(operations)} 个接口 -> {OUT_PATH}")
    for op in operations:
        print(f"  {op['method']:6} {op['path']}")


if __name__ == "__main__":
    main()
