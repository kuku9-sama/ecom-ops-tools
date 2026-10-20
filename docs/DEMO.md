# 演示脚本（约 3 分钟）

> 目标：用本地 Mock 展示“导入 → 同步 → 商品草稿 → 测试”的闭环。
> 边界：平台是本地 Mock；不验证 Lazada 真实签名；商品只建草稿，不发布。

---

## 0. 准备

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

## 1. 订单 CSV 导入（幂等 + 脏数据）

```bash
rm -f data/demo_orders.db
./.venv/Scripts/python.exe -m ecommerce.cli import-orders --csv data/orders.csv --db data/demo_orders.db
```

预期：

```text
fetched=20 valid_orders=7 valid_lines=10 issues=8 inserted=7 updated=0
```

再跑一次：

```bash
./.venv/Scripts/python.exe -m ecommerce.cli import-orders --csv data/orders.csv --db data/demo_orders.db
```

预期：

```text
fetched=20 valid_orders=7 valid_lines=10 issues=8 inserted=0 updated=7
```

讲解点：

- 20 行里 7 个订单、10 条订单行合法；8 条 issue 对应坏日期、空 SKU、qty=0、负单价、未知币种、订单级字段不一致、同键冲突、完全重复行。
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

## 5. 报告与测试

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

37 passed
All checks passed
```

---

## 6. Swagger 页面

浏览器打开 <http://127.0.0.1:8100/docs>。

可以看到中文的接口标题和参数说明：

- 创建访问令牌
- 拉取订单
- 创建商品草稿
- 按 SKU 查询商品
- 注入故障

路径和字段名仍是英文（`/rest/orders/get`、`seller_sku` 等），这是代码标识符，保留英文。

---

## 7. 三分钟讲解顺序

| 时间 | 内容 | 说什么 |
|---|---|---|
| 0:00–0:20 | 问题 | 多平台重复劳动、漏单/重单、对账不可追溯 |
| 0:20–0:50 | 架构 | CLI → 服务层 → Port ← 适配器 → Mock/SQLite |
| 0:50–1:40 | 订单导入 | 20 行脏数据、7 个合法订单、第二次 0 新增 |
| 1:40–2:20 | 平台同步 | 同一服务换 Shopee/Lazada 适配器；分页 + 断点 |
| 2:20–2:50 | 商品草稿 | 只建草稿；单行失败不影响整批；超时回查 |
| 2:50–3:00 | 证据与限制 | 39 个测试；Mock 不是真实接入；对账/库存还没做 |

---

## 8. 边界与已知限制

- 平台是本地 Mock，没有真实账号；Lazada 签名、QPS、Finance 端点未验证。
- 订单同步覆盖分页、游标和断点恢复；库存比对、多币种对账、本地看板还没做。
- 商品只创建草稿，不发布。
- 不自动退款、改价、写回库存。
- 数据是合成的，不含真实客户信息。
