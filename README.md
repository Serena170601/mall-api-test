# mall-api-test

接口自动化测试项目：**Python + Requests + Pytest + YAML 数据驱动 + jsonschema/jsonpath 三层断言 + Allure + GitHub Actions**。
仓库内置一个自建的轻量被测系统 `target_service/`（mini-mall），clone 下来即可跑通完整链路，不依赖 Docker。

![api-test](https://github.com/<OWNER>/mall-api-test/actions/workflows/test.yml/badge.svg)

- 在线报告（GitHub Pages）：https://<OWNER>.github.io/mall-api-test/
- 用例规模：33 条（32 通过 + 1 条待审核 AI 草稿），覆盖登录 / 查询 / 下单 / 越权 / 状态机 / 限流

## 快速开始

```bash
python -m venv .venv
.venv\Scripts\activate            # macOS/Linux: source .venv/bin/activate
pip install -r requirements.txt -i https://pypi.tuna.tsinghua.edu.cn/simple

pytest                            # conftest 会自动拉起被测服务
pytest -m read -n 4               # 只跑查询类用例，4 进程并行
pytest -m write                   # 串行跑写接口，避免状态互相污染

# 生成并查看 Allure 报告
allure generate allure-results -o allure-report --clean
allure open allure-report
```

Windows 安装 Allure CLI（GitHub Release 下载常失败，走 Maven 镜像）：

```powershell
$dir = "D:\tools"; New-Item -ItemType Directory -Force -Path $dir | Out-Null
Invoke-WebRequest -Uri "https://maven.aliyun.com/repository/public/io/qameta/allure/allure-commandline/2.29.0/allure-commandline-2.29.0.zip" -OutFile "$dir\allure.zip"
Expand-Archive "$dir\allure.zip" -DestinationPath $dir
$env:Path += ";$dir\allure-2.29.0\bin"
allure --version
```

## 目录结构

```
.
├── config/
│   ├── config.yaml            # base_url / 超时 / 重试 / 测试账号，CI 用 BASE_URL 覆盖
│   └── schemas/               # jsonschema：product / order / login / error ...
├── core/
│   ├── request_client.py      # 请求封装：Session、token、超时、重试、日志、Allure 附件
│   ├── assertion.py           # 三层断言 + 组合断言
│   ├── case_loader.py         # YAML -> pytest.param
│   ├── case_runner.py         # YAML 用例执行器
│   ├── config.py              # 配置加载（YAML + 环境变量）
│   └── logger.py
├── data/
│   ├── product.yaml           # 查询类用例（纯读，可并行）
│   ├── auth.yaml              # 认证类用例（写限流计数，串行）
│   └── generated/             # OpenAPI 抽取结果 / AI 生成的用例草稿
├── tests/
│   ├── test_auth.py           # YAML 驱动 + 登录限流 429
│   ├── test_product.py        # YAML 驱动
│   ├── test_order.py          # 下单、越权、状态机、库存回滚、数据准备与清理
│   └── test_ai_cases.py       # 只执行人工审核通过的 AI 用例
├── target_service/            # 自建被测系统（FastAPI），CI 与本地同一套
├── tools/
│   ├── extract_openapi.py     # OpenAPI -> operations.yaml
│   └── gen_cases.py           # operations.yaml -> LLM -> ai_cases.yaml
├── docs/business_codes.md     # 业务错误码约定，也是 AI 生成的上下文
└── .github/workflows/test.yml # push 自动回归 -> Allure -> Pages
```

## 设计决策（面试能脱稿说的版本）

**1. 请求封装层：不让用例里出现重复劳动**
用例里只会写 `resp = clients["user"].get("/api/orders")`，不会有：
- URL 拼接（统一 `base_url`）
- headers 里塞 token（Session 上挂一次）
- 超时时间散落各处（config 里集中配）
- 重试（只对 GET 的 502/503/504 开，**写接口重试会掩盖幂等缺陷**——比如重试一次下单就重复扣库存）
- 日志（每一步自动打，Allure 附件自动落盘，失败时直接打开报告看请求/响应，不用翻控制台）

**2. 三层断言：把"假绿"干掉**
这个靶场故意设计成：**业务失败也是 HTTP 200 + code!=0**（比如密码错了返回 HTTP 200 + `{"code":1001,"message":"用户名或密码错误"}`）。
- 只断 `status_code==200` → 会把业务失败当成通过（假绿）
- 只断字段值 → 字段新增/删除/类型变了发现不了（比如后端把 `stock` 从 `integer` 改成 `string`，只断值会漏）
- 所以固定顺序：HTTP 状态码（传输层）→ JSON Schema（结构层）→ jsonpath 业务字段（业务层）

**3. YAML 数据驱动：新增接口用例只改 YAML，不动 Python**
`data/*.yaml` 里每条就是一个用例，结构是 `method / path / auth / params / json / expected`。比如要加一个"支付后订单状态变成 PAID"的用例，写完 YAML 就能跑，`tests/test_order.py` 不需要改一行。
好处：测试同学和开发同学都能看懂 YAML，Review 成本低；出 bug 时 YAML 就是复现步骤。

**4. 为什么先生成中间 YAML 而不是让 AI 直接写 .py**
YAML 是人和机器都能审的格式：AI 输出的用例草稿先写成 YAML，然后**人工逐条审核**（删掉"只断状态码"的废用例、删掉"让 AI 重复下单 100 次"的危险用例），最后 `reviewed: true` 才会被 pytest 执行。直接让 AI 写 `.py`，审查成本太高，而且很难统一约束断言。

## 我踩过的坑（真实经历，面试加分）

**坑 1：xdist 下被测服务生命周期**
一开始没把服务启动逻辑放到 `pytest_configure` 里，而是放在 session fixture 里。结果一跑 `pytest -n 4`，4 个 worker 进程**各自启动了一个服务**（4 个进程抢端口），还互相把对方的服务杀掉，导致 ConnectionRefused。
**解决**：服务只由主进程（非 worker）在 `pytest_configure` 里统一启动，worker 只等 health check 通过。现在 `conftest.py` 里有 `PYTEST_XDIST_WORKER` 环境变量判断，就是这个坑的产物。

**坑 2：JSON 不支持注释**
改 `config/schemas/product.json` 测试 Schema 校验时，顺手加了一行 `# 故意改成 string`，结果 `json.loads` 直接抛 JSONDecodeError——因为 JSON 标准里没有注释这个特性！Python 的 json 模块不支持注释，所以 Schema 文件里不能加任何 `//` 或 `#`。
**教训**：YAML 可以加注释，JSON 不行，别搞混。

**坑 3：JSON 里 type 字段的字符串值**
刚才改 Schema 里 `stock` 的 type 从 `integer` 改成 `string`（为了故意制造 Schema 失败），第一次加了 `, "minimum": 0` 同时也改成 string 类型的约束——结果 jsonschema 对 `type: string` 的 schema 里出现 `minimum`（这是 integer 的约束）直接报错。后来把多余约束删掉才真正得到"类型校验失败"的报错。
**教训**：改 Schema 里的类型时，要同步把依赖该类型的约束字段也改掉。

**坑 4：pip 默认源连接被 TLS 中断**
第一次装依赖用默认 `pip install`，反复出现 `Connection interrupted`，下载了半天 0 字节。换成清华镜像 `pip install -i https://pypi.tuna.tsinghua.edu.cn/simple ...` 才瞬间装完。
**教训**：国内网络环境下，`-i https://pypi.tuna.tsinghua.edu.cn/simple` 基本是必加。

## AI 辅助用例生成

```bash
set LLM_API_KEY=sk-xxx
set LLM_BASE_URL=https://api.deepseek.com/v1
set LLM_MODEL=deepseek-chat

python tools/extract_openapi.py                 # 抓 /openapi.json -> operations.yaml
python tools/gen_cases.py --dry-run             # 先看 prompt，不花钱
python tools/gen_cases.py                       # 生成 ai_cases.yaml（全部 reviewed: false）
```

产物默认全部 `reviewed: false`，`tests/test_ai_cases.py` 会把这些草稿显示为 skip；人工逐条审核后把 `reviewed` 改成 `true` 才会真正执行。

脚本内置三道闸门：编造 `path` 的草稿直接丢弃、只断言状态码的草稿直接丢弃、`schema` 不在白名单时置空并标记待审。

## 已知边界

- 被测系统为自建靶场，数据存内存，重启即复位；`/api/_test/reset` 仅在 `MALL_MOCK_TEST_MODE=1` 时可用。
- 写接口用例必须串行（`-m write`），并行只在查询类用例上开启。
- AI 生成的用例无法自证“重复支付 / 并发 / 过期 token”等需要预置状态的场景，这部分由 `tests/test_order.py` 中的人工用例兜底。
