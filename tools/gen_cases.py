"""OpenAPI -> LLM -> 用例草稿（YAML），产物必须人工审核后才会被执行。

用法：
    set LLM_API_KEY=sk-xxx
    set LLM_BASE_URL=https://api.deepseek.com/v1   （任意 OpenAI 兼容网关）
    set LLM_MODEL=deepseek-chat
    python tools/extract_openapi.py
    python tools/gen_cases.py
    python tools/gen_cases.py --dry-run            # 只落 prompt，不花钱

红线：
1. 所有生成结果强制 needs_review=true / reviewed=false，人工改成 reviewed: true 才执行；
2. 编造出来的 path 直接丢弃，并在控制台列出；
3. 不允许“只断言 200”的用例，expected 至少包含 code 或 schema。
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from collections import Counter
from pathlib import Path

import requests
import yaml

ROOT = Path(__file__).resolve().parents[1]
OPS_PATH = ROOT / "data" / "generated" / "operations.yaml"
OUT_PATH = ROOT / "data" / "generated" / "ai_cases.yaml"
PROMPT_PATH = ROOT / "data" / "generated" / "prompt_preview.txt"
CODES_PATH = ROOT / "docs" / "business_codes.md"
KNOWN_SCHEMAS = {"product", "product_list", "login", "profile", "order", "error"}

SYSTEM_PROMPT = """你是一名资深接口测试工程师，负责根据 OpenAPI 定义设计可执行的接口测试用例。
硬性要求：
1. 只使用输入中出现的 path 与字段，禁止发明接口、字段、错误码；
2. 覆盖：正常路径、必填字段缺失、类型错误、边界值（0/负数/超上限）、未登录、越权、业务错误码；
3. 每条用例只做一次请求，不得依赖前一条用例产生的数据；
4. 只输出 JSON，不要输出解释性文字、不要输出 Markdown。"""

USER_PROMPT_TEMPLATE = """## 接口定义
{operations}

## 业务错误码
{codes}

## 输出 JSON 结构
{{"cases": [{{
  "id": "ai_<snake_case>",
  "title": "中文用例标题",
  "method": "GET|POST|DELETE",
  "path": "只能取自接口定义",
  "auth": "anonymous|user|other|admin|bad_token",
  "params": {{}},
  "json": {{}},
  "expected": {{"status": 200, "code": 0, "schema": "product_list|product|login|profile|order|error", "jsonpath": {{}}, "not_null": []}},
  "needs_review": true,
  "review_reason": "为什么需要人工确认（无则空字符串）"
}}]}}

## 约束
- 业务失败是 HTTP 200 + code!=0，鉴权缺失 401、越权 403、状态机冲突 409、限流 429、参数校验 422；
- schema 只能从 {schemas} 中选择；无法判断结构的用例把 schema 设为 null 并说明原因；
- 无法自证（需要预置已支付订单、需要并发、需要过期 token）的用例必须 needs_review=true。"""


def load_operations() -> list[dict]:
    if not OPS_PATH.exists():
        sys.exit("缺少 data/generated/operations.yaml，请先运行 tools/extract_openapi.py")
    data = yaml.safe_load(OPS_PATH.read_text(encoding="utf-8")) or {}
    return data.get("operations", [])


def build_prompt(operations: list[dict]) -> str:
    codes = CODES_PATH.read_text(encoding="utf-8") if CODES_PATH.exists() else "（无）"
    return USER_PROMPT_TEMPLATE.format(
        operations=json.dumps(operations, ensure_ascii=False, indent=2),
        codes=codes,
        schemas="|".join(sorted(KNOWN_SCHEMAS)),
    )


def load_env_file() -> None:
    """读取项目根目录 .env（已 gitignore），已存在的环境变量优先。"""
    env_file = ROOT / ".env"
    if not env_file.exists():
        return
    for line in env_file.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        os.environ.setdefault(key.strip(), value.strip().strip("\"'"))


def call_llm(prompt: str) -> dict:
    api_key = os.getenv("LLM_API_KEY") or os.getenv("OPENAI_API_KEY")
    base_url = os.getenv("LLM_BASE_URL", "https://api.openai.com/v1").rstrip("/")
    model = os.getenv("LLM_MODEL", "gpt-4o-mini")
    if not api_key:
        sys.exit("未配置 LLM_API_KEY / OPENAI_API_KEY；可先加 --dry-run 查看 prompt")

    resp = requests.post(
        f"{base_url}/chat/completions",
        headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
        json={
            "model": model,
            "temperature": 0.2,
            "response_format": {"type": "json_object"},
            "messages": [
                {"role": "system", "content": SYSTEM_PROMPT},
                {"role": "user", "content": prompt},
            ],
        },
        timeout=180,
    )
    resp.raise_for_status()
    content = resp.json()["choices"][0]["message"]["content"]
    return json.loads(content)


def sanitize(raw: dict, operations: list[dict]) -> tuple[list[dict], list[str]]:
    valid_paths = {op["path"] for op in operations}
    dropped: list[str] = []
    cases: list[dict] = []
    for index, case in enumerate(raw.get("cases", [])):
        case_id = case.get("id") or f"ai_case_{index}"
        if case.get("path") not in valid_paths:
            dropped.append(f"{case_id}: 编造 path {case.get('path')!r}")
            continue
        expected = case.get("expected") or {}
        if "code" not in expected and not expected.get("schema"):
            dropped.append(f"{case_id}: 只断言状态码，缺少业务断言")
            continue
        if expected.get("schema") not in KNOWN_SCHEMAS:
            expected["schema"] = None
        case["expected"] = expected
        case["id"] = case_id
        case["auth"] = case.get("auth") if case.get("auth") in {
            "anonymous", "user", "other", "admin", "bad_token"} else "user"
        # 强制人工审核闸门
        case["needs_review"] = bool(case.get("needs_review", True))
        case["reviewed"] = False
        cases.append(case)
    return cases, dropped


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--dry-run", action="store_true", help="只生成 prompt 预览")
    parser.add_argument("--out", default=str(OUT_PATH))
    args = parser.parse_args()

    load_env_file()
    operations = load_operations()
    prompt = build_prompt(operations)
    PROMPT_PATH.parent.mkdir(parents=True, exist_ok=True)
    PROMPT_PATH.write_text(prompt, encoding="utf-8")

    if args.dry_run:
        print(f"prompt 已写入 {PROMPT_PATH}（{len(prompt)} 字符，接口数 {len(operations)}）")
        return

    cases, dropped = sanitize(call_llm(prompt), operations)
    header = (
        "# 由 tools/gen_cases.py 生成：全部 needs_review=true / reviewed=false，"
        "人工审核后把 reviewed 改为 true 才会被 tests/test_ai_cases.py 执行\n"
    )
    Path(args.out).write_text(
        header + yaml.safe_dump({"cases": cases}, allow_unicode=True, sort_keys=False),
        encoding="utf-8",
    )

    print(f"生成 {len(cases)} 条草稿 -> {args.out}")
    print("覆盖分布：", dict(Counter(c["path"] for c in cases)))
    if dropped:
        print("已丢弃：")
        for item in dropped:
            print("  -", item)
    print("下一步：逐条审核，把可执行的 reviewed 改成 true，再运行 pytest -m ai")


if __name__ == "__main__":
    main()
