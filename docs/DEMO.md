# 演示脚本（约 3 分钟）

> 目标：用本地 Mock 展示“导入 → 同步 → 对账工作台 → 库存 → 测试”的闭环。
> 边界：平台是本地 Mock；不验证 Lazada 真实签名；商品只建草稿，不发布。

---

## 0. 最快方式（推荐）

Windows 双击项目根目录的 `start_demo.bat`：自动跑完演示、启动看板并打开浏览器。端口从 8000 开始自动选空闲的。

命令行方式：

```bash
./.venv/Scripts/python.exe scripts/demo.py
```

它会自动起 Mock、用 `data/demo_run.db`、跑订单导入/双平台同步/商品草稿/结构化提取/对账/库存/report，并写入 `outputs/`。

演示完启动看板：

```bash
ECOMMERCE_DB=data/demo_run.db ./.venv/Scripts/python.exe -m ecommerce.cli serve
```

下面的分步命令用于讲解细节，不是必须逐条执行。

---

## 0.1 准备（分步演示）

```bash
cd "X:/claudecodework/ecom-ops-tools"
./.venv/Scripts/python.exe check_env.py
```

终端 A 启动本地 Mock：

```bash
./.venv/Scripts/python.exe -m uvicorn mock_server.app:app --host 127.0.0.1 --port 8100
```

终端 B 跑下面的命令。为了数字干净，三个场景用不同的 SQLite 文件。

---

## 0.2 对账工作台（看板）

双击 `start_demo.bat`，打开看板后点侧边栏「对账工作台」。

1. 勾选「使用示例数据」，点「开始对账」。
2. 结果 13 行，顶部显示 `待处理 13`。
3. 把 O1014 的「金额不符」改成「已处理」，备注「已确认」，点保存。
4. 状态筛选「已处理」，只剩这一行。
5. 点「导出 CSV」，得到带状态和备注的工作清单。

讲解点：上传、对账、处理、导出全程在浏览器里完成；状态存本地 SQLite，不写回平台。

---

## 1. 订单 CSV 导入（幂等 + 脏数据）

```bash
rm -f data/demo_orders.db
./.venv/Scripts/python.exe -m ecommerce.cli import-orders --csv data/orders.csv --db data/demo_orders.db
```

预期：

```text
fetched=21 valid_orders=8 valid_lines=11 issues=8 inserted=8 updated=0
```

再跑一次：

```bash
./.venv/Scripts/python.exe -m ecommerce.cli import-orders --csv data/orders.csv --db data/demo_orders.db
```

预期：

```text
fetched=21 valid_orders=8 valid_lines=11 issues=8 inserted=0 updated=8
```

讲解点：

- 21 行里 8 个订单、11 条订单行合法；8 条 issue 对应坏日期、空 SKU、qty=0、负单价、未知币种、订单级字段不一致、同键冲突、完全重复行。
- 第二次 `inserted=0`，行数不变：自然键 + 唯一约束 + upsert。

---

## 2. Shopee-like 假适配器同步

```bash
rm -f data/demo_sync.db
./.venv/Scripts/python.exe -m ecommerce.cli sync-orders --platform shopee_mock --since 2026-10-01T00:00:00+00:00 --db data/demo_sync.db
```

预期：

```text
run_id=<uuid> fetched=2 inserted=2 updated=0
```

再跑一次：

```bash
./.venv/Scripts/python.exe -m ecommerce.cli sync-orders --platform shopee_mock --since 2026-10-01T00:00:00+00:00 --db data/demo_sync.db
```

预期：

```text
run_id=<uuid> fetched=2 inserted=0 updated=2
```

讲解点：

- `OrderService` 只依赖 `PlatformPort`；换平台不换业务代码。
- 每次同步写 `sync_runs`，逐项写 `sync_items`。

---

## 3. Lazada HTTP Mock 同步（分页 + 断点）

Mock 里有 3 个订单。用 `--page-size 2` 强制分两页：

```bash
./.venv/Scripts/python.exe -m ecommerce.cli sync-orders --platform lazada --since 2026-10-01T00:00:00+00:00 --base-url http://127.0.0.1:8100 --page-size 2 --db data/demo_sync.db
```

预期：

```text
run_id=<uuid> fetched=3 inserted=3 updated=0
```

讲解点：

- 服务循环取页；每页的数据、逐项状态、游标在同一事务里提交。
- `sync_state` 保存断点游标；成功后清空；失败时保留，下次从这里继续。
- 想看运行记录：

```bash
./.venv/Scripts/python.exe -m ecommerce.cli report --db data/demo_sync.db
```

预期能看到 `run=<前8位> platform=lazada status=succeeded fetched=3 ...`。

---

## 4. 商品草稿（幂等 + 超时回查）

```bash
rm -f data/demo_products.db
./.venv/Scripts/python.exe -m ecommerce.cli import-products --csv data/products.csv --platform shopee_mock --db data/demo_products.db
```

预期：

```text
parsed=5 valid=1 invalid=4 created=1 exists=0 failed=0
```

再跑一次：

```bash
./.venv/Scripts/python.exe -m ecommerce.cli import-products --csv data/products.csv --platform shopee_mock --db data/demo_products.db
```

预期：

```text
parsed=5 valid=1 invalid=4 created=0 exists=1 failed=0
```

讲解点：

- 只建草稿，不发布；单行失败不影响整批。
- 幂等三层：本地 `platform_product_id` → 平台按 SKU 查询 → 幂等键。
- 超时后先按 SKU 回查，确认服务端是否已创建，避免重复。

Lazada 版本（可选，Mock 在跑）：

```bash
./.venv/Scripts/python.exe -m ecommerce.cli import-products --csv data/products.csv --platform lazada --base-url http://127.0.0.1:8100 --db data/demo_products.db
```

---

### 结构化提取（mock）

```bash
./.venv/Scripts/python.exe -m ecommerce.cli extract --input data/raw_products.jsonl --provider mock --out outputs/extracted_products.csv --db data/demo_products.db
```

预期：

```text
texts=4 success=3 failed=1 attempts=6 failures_in_db=1 wrote=outputs/extracted_products.csv
```

讲解点：

- 正常 2 条、修复 1 条、坏 JSON 1 条进失败队列。
- 默认 mock 是确定性正则解析，不调用模型；设置 `DEEPSEEK_API_KEY` 可接 DeepSeek（可选）。
- 失败只记录 hash、原始响应和校验错误，不写真实客户信息。

---

## 5. 多币种行级对账

```bash
./.venv/Scripts/python.exe -m ecommerce.cli reconcile --orders data/orders.csv --bills data/bills.csv --fx data/fx_rates.csv --out outputs/reconciliation.csv
```

预期：

```text
orders=8 order_lines=11 bills=13 rows=13
issues: orders=8 bills=1 fx=2
wrote=outputs/reconciliation.csv summary=outputs/reconciliation_summary.json
unmatched=outputs/unmatched.csv data_quality=outputs/data_quality.csv
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

讲解点：

- 重复账单键、同一订单行多账单先排除，不静默合并。
- 同币种汇率 = 1；结算日优先，订单日回退，缺失报 `fx_missing`。
- 先校验账单自洽，再比较金额；差异按容差分类。
- O1011/B007、O1012/B008 行级没对上，但订单级金额能对上，标记 `aggregated_bill`。
- 输出四份文件：逐行差异、汇总 JSON、未匹配清单、数据质量清单。

---

## 6. 库存比对

```bash
./.venv/Scripts/python.exe -m ecommerce.cli inventory --platform lazada --base-url http://127.0.0.1:8100 --local data/local_stock.csv --out outputs/inventory_diff.csv
```

预期：

```text
skus=4 wrote=outputs/inventory_diff.csv
  mismatch=3
  missing_locally=1
```

讲解点：

- 平台快照 vs 本地库存；只读，不写回。
- MON-001 18 vs 20、KB-001 5 vs 50、ACC-001 0 vs 100 是 mismatch。
- USB-001 平台有、本地没有，标记 missing_locally。
- 差异 CSV 给运营补货/调库存，但系统不自动写回。

---

## 7. 报告与测试

```bash
./.venv/Scripts/python.exe -m ecommerce.cli report --db data/demo_sync.db
./.venv/Scripts/python.exe -m pytest -q
./.venv/Scripts/ruff.exe check .
```

预期：

```text
orders=5 order_lines=5 products=0
  status=paid count=4
  status=shipped count=1
  run=<...> platform=lazada status=succeeded fetched=3 ...
  run=<...> platform=shopee_mock status=succeeded fetched=2 ...

80 passed
All checks passed
```

---

## 8. Swagger 页面

浏览器打开 <http://127.0.0.1:8100/docs>。

可以看到中文的接口标题和参数说明：

- 创建访问令牌
- 拉取订单
- 创建商品草稿
- 按 SKU 查询商品
- 注入故障

路径和字段名仍是英文（`/rest/orders/get`、`seller_sku` 等），这是代码标识符，保留英文。

---

## 8.1 加演：故障注入与恢复（可选，约 40 秒）

Mock 支持注入故障，可以现场演示「失败 → 重跑 → 不重复」。终端 A 先起 Mock（8100），终端 B 执行。

先让下一次 GET 返回 429：

```bash
rm -f data/demo_fault.db
curl -s -X POST "http://127.0.0.1:8100/mock/fault?mode=429&times=1"
./.venv/Scripts/python.exe -m ecommerce.cli sync-orders --platform lazada --since 2026-10-01T00:00:00+00:00 --base-url http://127.0.0.1:8100 --page-size 2 --db data/demo_fault.db
```

预期仍是成功：

```text
run_id=<uuid> fetched=3 inserted=3 updated=0
```

再让服务端连续 500，耗尽 GET 的 4 次重试：

```bash
rm -f data/demo_fault.db
curl -s -X POST "http://127.0.0.1:8100/mock/fault?mode=500&times=4"
./.venv/Scripts/python.exe -m ecommerce.cli sync-orders --platform lazada --since 2026-10-01T00:00:00+00:00 --base-url http://127.0.0.1:8100 --page-size 2 --db data/demo_fault.db
```

预期失败、退出码 1：

```text
sync failed: /orders/get server error 500
```

故障耗尽后再跑两次：

```bash
./.venv/Scripts/python.exe -m ecommerce.cli sync-orders --platform lazada --since 2026-10-01T00:00:00+00:00 --base-url http://127.0.0.1:8100 --page-size 2 --db data/demo_fault.db
./.venv/Scripts/python.exe -m ecommerce.cli sync-orders --platform lazada --since 2026-10-01T00:00:00+00:00 --base-url http://127.0.0.1:8100 --page-size 2 --db data/demo_fault.db
./.venv/Scripts/python.exe -m ecommerce.cli report --db data/demo_fault.db
```

预期：

```text
run_id=<uuid> fetched=3 inserted=3 updated=0
run_id=<uuid> fetched=3 inserted=0 updated=3
orders=3 order_lines=3 products=0
  run=<uuid> platform=lazada status=succeeded ...
  run=<uuid> platform=lazada status=succeeded ...
  run=<uuid> platform=lazada status=failed ...
```

讲解点：

- GET 最多重试 4 次、尊重 `Retry-After`；POST 默认不重试。
- 失败的 run 留在 `sync_runs`，重跑按自然键 upsert，不产生重复订单。
- 页级断点（游标保留、下次从断点续跑）由测试证明：

```bash
./.venv/Scripts/python.exe -m pytest tests/test_sync.py -v
```

预期 2 passed，其中 `test_failure_keeps_cursor_and_resume_continues`。

---

## 9. 三分钟讲解顺序

| 时间 | 内容 | 说什么 |
|---|---|---|
| 0:00–0:20 | 问题 | 多平台重复劳动、漏单/重单、对账不可追溯 |
| 0:20–0:50 | 架构 | CLI → 服务层 → Port ← 适配器 → Mock/SQLite |
| 0:50–1:30 | 订单导入 | 21 行脏数据、8 个合法订单、第二次 0 新增 |
| 1:30–2:10 | 平台同步 | 同一服务换 Shopee/Lazada 适配器；分页 + 断点 |
| 2:10–2:40 | 对账工作台 | 上传三份 CSV → 13 行异常 → 标记处理 → 导出 |
| 2:40–2:55 | 库存 + 草稿/提取 | 库存只读比对；商品只建草稿；失败进提取队列 |
| 2:55–3:00 | 证据与限制 | 80 个测试；Mock 不是真实接入；不写回平台 |

---

## 9.1 亮点与现场证据

| 亮点 | 现场证据 |
|---|---|
| 自然键幂等导入 | 同一 CSV 再跑一次：`inserted=0 updated=8`，行数不变 |
| 换平台不换业务代码 | 同一条 `sync-orders` 只换 `--platform shopee_mock/lazada` |
| 分页 + 每页与游标同事务 | Lazada `--page-size 2` 仍 `fetched=3`；`test_sync.py` |
| 断点恢复 | `test_failure_keeps_cursor_and_resume_continues` |
| 重试边界 | 429 重试后成功；500 耗尽 4 次失败；POST 429 不重试 |
| 失败后可恢复且不重复 | 500 失败 → 重跑 `inserted=3` → 再跑 `inserted=0 updated=3` |
| 行级对账 + 订单级兜底 | `aggregated_bill=2` |
| 不静默合并问题账单 | `duplicate_bill`、`multiple_bills_for_line` 进数据质量清单 |
| 提取失败可修复、可追溯 | `texts=4 success=3 failed=1 attempts=6`，失败进 `extraction_failures` |
| 只建草稿 + 超时回查 | 第一次 `created=1`，再跑 `exists=1`；`test_product_service.py` |
| 金额不用 float | 最小货币单位整数 + `Decimal` 汇率；缺失汇率报 `fx_missing` |
| 平台边界清楚 | Mock 端点、Swagger 中文说明；不自动发布/退款/改价/写回库存 |

---

## 10. 边界与已知限制

- 平台是本地 Mock，没有真实账号；Lazada 签名、QPS、Finance 端点未验证。
- 订单同步覆盖分页、游标和断点恢复；对账覆盖行级和订单级兜底；库存比对、本地看板、结构化提取已覆盖；真实 DeepSeek 未验证。
- 商品只创建草稿，不发布。
- 不自动退款、改价、写回库存。
- 数据是合成的，不含真实客户信息。
