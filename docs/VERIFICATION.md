# 验证记录

> 日期：2026-10-27
> 环境：Windows 11；Python 3.14.7；`.venv`
> 结论：58 个测试通过；`ruff` 干净；新虚拟环境可按 README 复现；一条命令演示跑通。

---

## 1. 自动化测试

| 文件 | 覆盖 |
|---|---|
| `tests/test_money.py` | 金额清洗、半入舍入、0 位小数币种、最小单位格式化 |
| `tests/test_import_idempotency.py` | 同一批订单导入两次、时间转 UTC |
| `tests/test_validation.py` | 非法行报告、缺列报错 |
| `tests/test_shopee_mock_adapter.py` | 内存适配器过滤、同步幂等 |
| `tests/test_lazada_adapter.py` | 401 / 429 / 500 / 坏 JSON / 超时 / 400 / 分页 / 库存分页 |
| `tests/test_lazada_product.py` | 商品创建/查询、400、超时、POST 429 不重试 |
| `tests/test_mock_server.py` | token、订单分页、故障注入、商品、库存 |
| `tests/test_product_service.py` | CSV 校验、重复 SKU、幂等、部分失败、超时回查 |
| `tests/test_sync.py` | 多页同步、失败保留游标、恢复继续 |
| `tests/test_reconciliation.py` | 行级/订单级对账、汇率回退、CLI 报告输出 |
| `tests/test_inventory.py` | 本地库存读取、差异分类、重复 SKU |
| `tests/test_api.py` | `/health`、`/orders`、首页、对账汇总、库存差异 |
| `tests/test_smoke.py` | 包导入 |

命令与结果：

```text
pytest -q        → 58 passed
ruff check .     → All checks passed
```

---

## 2. 故障注入

| 场景 | 预期 | 测试 |
|---|---|---|
| 401 | 抛 `AuthError`，不重试 | `test_401_raises_auth_error` |
| GET 429 | 重试后成功，尝试次数正确 | `test_429_then_success` |
| GET 500 | 最多 4 次后抛 `TransientError` | `test_500_exhausts_budget` |
| 200 + 非法 JSON | 抛 `ParseError` | `test_bad_json_raises_parse_error` |
| 读超时 | 抛 `TransientError` | `test_timeout_raises_transient_error` |
| GET 400 | 抛 `PermanentError` | `test_400_raises_permanent_error` |
| POST 429 | 不自动重试，抛 `RateLimitError` | `test_create_rate_limit_is_not_retried` |
| 商品创建 400 | 抛 `ValidationError` | `test_create_validation_error` |

说明：

- GET 重试由 `tenacity` 统一控制，最多 4 次尝试，尊重 `Retry-After`，指数退避 + 抖动。
- POST 默认不重试；商品创建带幂等键，服务层在超时后按 SKU 回查，再决定是否补偿。
- 401/403 统一翻译为 `AuthError`；刷新 token 还没实现，Mock 只发固定 token。

---

## 3. 幂等与恢复

| 场景 | 结果 |
|---|---|
| 同一批订单导入两次 | 第一次 `inserted=8`，第二次 `inserted=0 updated=8`；订单 8、订单行 11 不变 |
| 同一平台同步两次 | 第二次 `inserted=0`，只更新已存在订单 |
| 第 1 页成功、第 2 页失败 | `sync_state.cursor` 保留为 `"1"`，run 标为 failed |
| 从断点继续 | 新 run 从游标 `"1"` 开始，订单总数正确，无重复 |
| 商品草稿导入两次 | 第一次 `created=1`，第二次 `exists=1`；本地 `platform_product_id` 生效 |
| 商品创建超时 | 先按 SKU 回查，已创建则视为成功，不重复创建 |

---

## 4. 对账结果

数据：8 个有效订单、11 条订单行、13 条账单、6 条汇率。

```text
orders=8 order_lines=11 bills=13 rows=13
  aggregated_bill=2
  amount_mismatch=1
  bill_inconsistent=1
  duplicate_bill=1
  fx_missing=1
  fx_or_rounding_diff=1
  matched=1
  missing_order=2
  multiple_bills_for_line=1
  orphan_bill=1
  refund_mismatch=1
```

输出：

- `outputs/reconciliation.csv`：逐行差异
- `outputs/reconciliation_summary.json`：分类计数、金额合计
- `outputs/unmatched.csv`：缺失订单 / 孤立账单
- `outputs/data_quality.csv`：重复账单、多账单、账单内部不一致、汇率缺失

G2 追溯性：

- 每行都有 `platform`、`platform_order_id`、`line_id`。
- 每行都有 `expected_minor`、`actual_minor`、`diff_minor`。
- 汇率相关行记录 `fx_rate_used`、`fx_rate_date`、`fx_rate_source`。
- 重复账单、同一行多账单显式进入数据质量清单，不静默合并。
- 行级没匹配上的订单，订单级金额能对上时标记 `aggregated_bill`。

---

## 5. 一条命令演示

```bash
./.venv/Scripts/python.exe scripts/demo.py
```

结果摘要：

```text
fetched=21 valid_orders=8 valid_lines=11 issues=8 inserted=8
Shopee 同步：fetched=2 inserted=2
Lazada 同步：fetched=3 inserted=3（page size 2）
商品草稿：created=1
对账：rows=13
库存：mismatch=3 missing_locally=1
report：orders=13 order_lines=16 products=1
```

脚本会自动起 Mock、使用 `data/demo_run.db`、写 `outputs/`，结束后停服务。

看板：

```bash
ECOMMERCE_DB=data/demo_run.db ./.venv/Scripts/python.exe -m ecommerce.cli serve
```

---

## 6. 新环境复现

在临时目录新建虚拟环境，按 README 安装依赖并运行：

```text
python -m venv <temp>
pip install -r requirements.txt
pip install setuptools
pip install -e . --no-build-isolation
python check_env.py          → OK: environment ready
pytest -q                    → 58 passed
ruff check .                 → All checks passed
python scripts/demo.py       → 跑通
```

结论：新环境可以按 README 复现（G5）。

---

## 7. G2 / G3 清单

G2：

- [x] 每笔差异可追溯到订单/订单行
- [x] 重复账单显式报错
- [x] 同一订单行多账单显式报错
- [x] 未匹配单独输出
- [x] 订单级兜底标记 `aggregated_bill`

G3：

- [x] 401 有测试，抛 `AuthError`
- [x] GET 429 有测试，重试成功
- [x] 5xx 有测试，重试到上限后失败
- [x] 非法 JSON 有测试
- [x] 超时有测试
- [x] POST 429 有测试，不自动重试

---

## 8. 已知限制

- 平台是本地 Mock，没有真实账号；Lazada 签名、QPS、Finance 端点未验证。
- 401 刷新 token 未实现。
- 一个订单多条聚合账单、混合币种聚合行还没完全处理。
- 对账目前读 CSV，SQLite 版本还没接。
- 汇总 JSON 的金额合计是原始求和，不是配对后的合计。
- 结构化提取还没实现。
- 本地单用户，不是多租户服务。
