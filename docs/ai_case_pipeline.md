# AI 辅助接口用例生成：一页纸

## 数据流

```
docs/openapi.json                 （FastAPI 导出的接口契约，只描述结构）
        │
        ├── openapi-spec-validator 校验
        ▼
tools/extract_openapi.py
        │  只保留 method / path / 参数 / 请求体 / 响应结构，去掉 $ref 噪音
        ▼
data/generated/operations.yaml    （15 个接口）
        │
        │   ＋ docs/business_codes.md（错误码表，spec 里没有）
        │   ＋ 固定输出结构约束（JSON）
        ▼
tools/gen_cases.py  ──LLM──▶  草稿
        │  闸门 1/2/3
        ▼
data/generated/ai_cases.yaml      （全部 reviewed: false）
        │
        │  人工逐条审核：删错例、删危险操作、补业务断言
        ▼
tests/test_ai_cases.py ──pytest -m ai──▶ Allure（与人工用例在同一份报告里）
```

## 三道闸门

| 闸门 | 位置 | 规则 | 拦下的东西 |
| --- | --- | --- | --- |
| 1. path 白名单 | `sanitize()` | `path` 不在 `operations.yaml` 里直接丢弃 | 模型编造的接口，例如不存在的 `/api/wishlist` |
| 2. 业务断言门槛 | `sanitize()` | `code` 为空且没有 `schema` 才丢弃（`expected.get("code") is None`） | 只断言 422/200 的"假严格"用例 |
| 3. 人工审核 | `reviewed` 字段 | 生成时强制写 `reviewed: false`，只有人工改成 `true` 才执行 | 依赖预置状态、破坏性写操作、期望值错误 |

第 3 道闸门是唯一不可自动化的：前两道保证"用例至少是可执行的语法与接口"，第三道保证"用例的期望值是对的"。

## 关键设计

1. **生成中间 YAML，不生成 `.py`**
   YAML 人和机器都能审，diff 干净；直接生成代码会把断言写法也交给模型，审查成本反而更高。

2. **业务错误码必须单独喂**
   OpenAPI 只描述"长什么样"，不描述"为什么失败"。1001/2002/3001 这类语义只存在于业务文档里，不喂给模型它就只能编。

3. **`needs_review` 与 `reviewed` 分离**
   `needs_review` 是模型自评（"我不确定"），`reviewed` 是人类结论。用人的字段控制执行，而不是相信模型的自评。

4. **状态机与写操作不交给 AI**
   下单、支付、取消依赖前置数据，必须用 `created_order` / `paid_order` fixture 构造并清理；AI 草稿只保留为 skip 样例。

## 本轮的量化结果

- 输入：15 个接口 + 18 个业务错误码
- 输出：88 条草稿，闸门 1/2 未丢弃（模型没编 path，但大量把 `code` 写成 `null`）
- 人工审核：删除 73 条、留 4 条草稿、通过 11 条
- 执行：`pytest -m ai` → 11 passed / 4 skipped；全量 `pytest` → 52 条，48 passed / 4 skipped
- 淘汰率 83%，与本轮识别出的 8 类失效模式（见 `docs/ai_failure_modes.md`）一一对应

## 复现命令

```powershell
.\.venv\Scripts\python tools\extract_openapi.py docs\openapi.json
.\.venv\Scripts\python tools\gen_cases.py --dry-run    # 先看 prompt
.\.venv\Scripts\python tools\gen_cases.py              # 需要 .env 里的 LLM_API_KEY
.\.venv\Scripts\python -m pytest -m ai -v
```

## 已知边界

- 模型对"公开接口 / 需要登录"的判断不可信，因为 spec 没有 `security` 定义；
- 成功响应的 schema 在 spec 里只有 `additionalProperties: true`，字段级断言只能靠人工补；
- 列表类接口没有独立的 schema，`schema: order` 这类错配目前只能靠审核拦住；
- `/api/_test/*` 属于测试专用接口，应在上游抽取阶段就排除，不能进入生成范围。
