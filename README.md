# ecom-ops-tools

跨境电商运营自动化小工具。先从最薄的一条链路开始：读订单导出文件，写进 SQLite，再输出对账报告。

当前状态：早期开发。平台侧是本地 Mock，没有真实账号和密钥。

## 当前能做什么

- 从 CSV 导入订单行到 SQLite（幂等）。
- 从 CSV 导入商品草稿（校验、字段映射、幂等创建、逐行结果；只建草稿，不发布）。
- 从内置 Shopee-like 假适配器同步订单（内存数据，不走网络）。
- 从 Lazada 形状的 HTTP Mock 同步订单（认证、分页、429/5xx/坏 JSON/超时；不验证真实签名）。
- 订单同步支持分页、游标和断点恢复；每页数据与游标同一事务提交。
- 按订单行核对账单：多币种、汇率舍入、重复账单、退款/金额差异；输出逐行差异 CSV。
- 用 `report` 查看订单数、订单行数和状态分布。

## 计划中

- 订单级对账兜底（平台按订单汇总出账）。
- 对比平台库存和本地库存。
- 本地看板（FastAPI 薄层）。

## 不做什么

- 不调用真实平台账号。
- 不自动发布商品、退款、改价、写回库存。
- 这是本地小工具，不是多租户服务。

## 环境要求

- Python 3.12+
- Windows（下面命令用 `.venv/Scripts`；Linux/macOS 换成 `.venv/bin`）

## 安装

```bash
python -m venv .venv
./.venv/Scripts/python.exe -m pip install -r requirements.txt
./.venv/Scripts/python.exe -m pip install -e .
./.venv/Scripts/python.exe check_env.py
```

## 用法

```bash
./.venv/Scripts/python.exe -m ecommerce.cli --help
./.venv/Scripts/python.exe -m ecommerce.cli import-orders --csv data/orders.csv --db data/ecommerce.db
./.venv/Scripts/python.exe -m ecommerce.cli import-products --csv data/products.csv --platform shopee_mock --db data/ecommerce.db
./.venv/Scripts/python.exe -m ecommerce.cli sync-orders --platform shopee_mock --since 2026-10-01T00:00:00+00:00 --db data/ecommerce.db
./.venv/Scripts/python.exe -m ecommerce.cli reconcile --orders data/orders.csv --bills data/bills.csv --fx data/fx_rates.csv --out outputs/reconciliation.csv
./.venv/Scripts/python.exe -m ecommerce.cli report --db data/ecommerce.db
```

Lazada 形状的 HTTP Mock 需要先在一个终端启动服务：

```bash
./.venv/Scripts/python.exe -m uvicorn mock_server.app:app --host 127.0.0.1 --port 8100
```

然后在另一个终端同步：

```bash
./.venv/Scripts/python.exe -m ecommerce.cli sync-orders --platform lazada --since 2026-10-01T00:00:00+00:00 --base-url http://127.0.0.1:8100 --db data/ecommerce.db
```

更多命令随着链路补上。

## 测试

```bash
./.venv/Scripts/python.exe -m pytest -q
./.venv/Scripts/ruff.exe check .
```

## 说明

- 数据是合成的，不含真实客户信息。
- `data/README.md` 记录了每个脏数据用例的预期行为。
- 演示脚本见 `docs/DEMO.md`。
- Lazada Mock 不验证真实签名，不代表真实平台接入。
