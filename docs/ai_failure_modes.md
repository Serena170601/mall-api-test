# AI 生成接口用例的失效模式

数据来源：本轮 `tools/gen_cases.py` 用 `deepseek-ai/DeepSeek-V4.1-Flash` 对 15 个接口一次性生成 **88 条草稿**，人工逐条审核后保留 15 条（11 条进入回归、4 条留作草稿）。
下面每条失效模式都来自这批草稿，期望值均对本机靶场实测核对过。

---

## 失效模式 1：虚构账号与凭据

- 现象：`ai_login_success` 生成 `{"username": "testuser", "password": "password123"}`，期望 `code: 0`。
- 实测：`HTTP 200 + code 1001`（用户不存在）。
- 为什么 AI 会这样：提示词里没有账号清单，模型按训练语料里最常见的示例账号补齐。
- 后果：用例必然失败，且失败原因是"用例写错"而不是"系统有缺陷"，会占用排查时间。
- 兜底：把可用账号写进 prompt 上下文（`config.yaml` 的 users），或对含凭据的用例一律 `needs_review: true`，由人工替换。

## 失效模式 2：臆造信封字段

- 现象：`ai_health_success` 期望 `{"status": 200, "code": 0}`。
- 实测：`/health` 返回 `{"status": "ok"}`，根本没有 `code` 字段。
- 为什么 AI 会这样：项目里绝大多数接口是 `{code, message, data}` 信封，模型把这一模式套到了所有接口上。
- 后果：断言层读不到 `$.code` 直接失败。
- 兜底：在 prompt 中明确"非信封接口只有 /health"；这类接口不允许套 `error` / 业务 schema，人工审核时单列。

## 失效模式 3：凭空假设鉴权

- 现象：`ai_products_list_no_token`、`ai_get_product_no_token` 都期望 `401 + 4001`。
- 实测：商品查询是公开接口，不带 token 返回 `200 + code 0`。
- 为什么 AI 会这样：`extract_openapi.py` 输出的 `auth` 字段是按"路径以 /api/ 开头"推断的，spec 里并没有 `security` 定义；模型据此认为所有 `/api/*` 都要登录。
- 后果：把"公开接口"误判为"漏鉴权"，产生假缺陷。
- 兜底：抽取阶段不要伪造 `auth` 语义，改为 `"auth_in_spec": null`；鉴权类用例统一 `needs_review: true`。

## 失效模式 4：依赖不存在的预置状态

- 现象：`ai_get_order_success`、`ai_cancel_order_success`、`ai_pay_order_success`、`ai_pay_order_repeat_conflict` 都直接写死 `order_id: 1`。
- 实测：干净数据下订单列表为空，访问订单 1 得到 `404 + 4005`，这些用例全部挂。
- 为什么 AI 会这样：提示词要求"每条用例只做一次请求、不依赖前一条"，模型就默认实体已存在，用最小 ID 占位。
- 后果：用例在空库环境必然失败；如果换成真实系统，还可能删掉别人的订单。
- 兜底：这类用例强制 `needs_review: true`；真正要测的状态机由人工用例 + `created_order` / `paid_order` fixture 覆盖。

## 失效模式 5：边界值靠猜

- 现象：`ai_add_cart_item_quantity_boundary_max` 认为 `quantity=99` 是合法上限且库存充足，期望 `200 + code 0`；同一批里 `ai_add_cart_item_stock_insufficient` 又用 `quantity=99` 期望 `2002`，两条自相矛盾。
- 实测：商品 1 库存为 10，`quantity=99` 返回 `200 + 2002`（库存不足）。
- 为什么 AI 会这样：spec 里没有 `maximum` 和 `example`，模型只能按常见电商经验取 99。
- 后果：边界用例互相打架，不可信。
- 兜底：边界值必须来自 spec 的 `minimum/maximum` 或实测；涉及库存、余额这类动态量时，先查询再断言，不要写死。

## 失效模式 6：schema 选错（结构对不上）

- 现象：`ai_list_orders_success`、`ai_admin_orders_success` 都选了 `schema: order`。
- 实测：两个列表接口返回 `{"code":0,"data":{"total":0,"items":[]}}`，而 `config/schemas/order.json` 要求 `data` 里有 `orderId/status/...`。
- 为什么 AI 会这样：schema 白名单只校验"名字在不在允许集合里"，不校验"这个接口到底返回哪种结构"。
- 后果：结构断言必然失败，而且报错信息指向 `data.orderId` 缺失，容易被误读成后端 bug。
- 兜底：白名单只能防瞎编，不能防错配；列表接口需要独立的 `order_list` schema，并由人工确认映射。

## 失效模式 7：只断言状态码（且绕过了闸门）

- 现象：15 条 422 参数校验用例都写成 `expected: {status: 422, code: null, schema: null}`。
- 问题：`sanitize()` 原来用 `if "code" not in expected` 判断，而模型会把 `code` 显式写成 `null`——键存在、值为空，闸门没拦住。
- 后果：一批只断言 HTTP 状态的"假严格"用例混进候选集。
- 兜底：闸门改为 `expected.get("code") is None and not expected.get("schema")` 才丢弃；本次已修复，下一轮会直接丢掉这类草稿。

## 失效模式 8：生成破坏性操作

- 现象：`ai_test_reset_success` 直接 POST `/api/_test/reset` 清库；`ai_create_order_success` 每执行一次就真实扣一次库存，且没有清理逻辑。
- 实测：`/api/_test/reset` 在测试模式下确实会重置全部数据。
- 后果：会把同一会话里其他用例的数据清空，或让库存越跑越少，最终导致无关用例随机失败。
- 兜底：内部/测试专用接口不进入 AI 生成范围（从 `operations.yaml` 里排除 `/api/_test/*`）；写接口用例必须 `needs_review: true`，且只能通过带清理的 fixture 执行。

---

## 审核后的处理结果

| 处理 | 数量 | 说明 |
| --- | --- | --- |
| 直接删除 | 73 条 | 期望与实测不符、只断言状态码、破坏性操作、重复用例 |
| 保留为草稿（skip） | 4 条 | 依赖预置订单 / 限流计数，无法自证 |
| 审核通过（进入回归） | 11 条 | 每条期望值均已实测：401/4002 鉴权、403/4003 越权、400/1100、400/1101、404/4004、404/4005、200/2002 |

改 `reviewed: true` 的 11 条覆盖 4 个接口族（认证、购物车、商品、订单、管理端），全部通过，说明"生成—审核—执行"闭环可用；
但审核淘汰率约 83%（73/88），也说明当前 prompt 与 spec 质量下，AI 产出只能当候选，不能当结论。
