# ecom-ops-tools

跨境电商运营自动化小工具。统一商品/订单/库存/账单模型，用适配器接不同平台；先把一条可运行的链路做稳，再扩展。

当前状态：可本地演示。平台侧是 Mock，没有真实账号和密钥。

## 当前能做什么

- 一条命令演示完整链路：`scripts/demo.py`。
- 订单 CSV 导入 SQLite（幂等，含脏数据校验）。
- 商品草稿：CSV 校验、字段映射、幂等创建、超时回查；只建草稿。
- Shopee-like 内存适配器 + Lazada 形状 HTTP Mock 适配器。
- 订单同步：分页、游标、断点恢复；每页数据与游标同一事务。
- 多币种对账：行级匹配、订单级兜底、重复/多账单、退款/金额/汇率差异。
- 库存比对：平台快照 vs 本地库存，输出差异 CSV；只读。
- 本地 FastAPI 看板：订单、同步运行、对账汇总、库存差异。
- 结构化提取：文本 → 商品草稿；默认 mock，失败进 `extraction_failures`；可选 DeepSeek。

## 不做什么 / 已知限制

- 不调用真实平台；Lazada 签名、QPS、Finance 端点未验证。
- 不自动发布商品、退款、改价、写回库存。
- 商品只创建草稿。
- 对账目前读 CSV；SQLite 版本还没接。
- 汇总 JSON 的金额合计是原始求和，不是配对后的合计。
- 一个订单多条聚合账单、混合币种聚合行还没完全处理。
- 真实 DeepSeek 调用未在无 key 环境验证。
- 本地单用户工具，不是多租户服务。

## 环境要求

- Python 3.12+
- Windows（命令用 `.venv/Scripts`；Linux/macOS 换成 `.venv/bin`）

## 安装

```bash
python -m venv .venv
./.venv/Scripts/python.exe -m pip install -r requirements.txt
./.venv/Scripts/python.exe -m pip install -e .
./.venv/Scripts/python.exe check_env.py
```

## 一条命令演示

### 最简方式（Windows）

双击项目根目录的 `start_demo.bat`：自动跑完整演示、启动看板并打开浏览器。端口从 8000 开始自动选空闲的，终端里会打印地址。浏览器没自动弹出时，按终端里的地址手动打开。

下面的命令用于手动分步执行。

```bash
./.venv/Scripts/python.exe scripts/demo.py
```

自动起本地 Mock、用 `data/demo_run.db`、跑订单导入 → 双平台同步 → 商品草稿 → 结构化提取 → 对账 → 库存 → report，结果写到 `outputs/`。

启动看板（bash）：

```bash
ECOMMERCE_DB=data/demo_run.db ./.venv/Scripts/python.exe -m ecommerce.cli serve
```

PowerShell 里环境变量要单独设：

```powershell
$env:ECOMMERCE_DB = "data/demo_run.db"
./.venv/Scripts/python.exe -m ecommerce.cli serve
```

打开 <http://127.0.0.1:8000/>；端口被占用时加 `--port 8010`。

## 分步命令

```bash
./.venv/Scripts/python.exe -m ecommerce.cli --help
./.venv/Scripts/python.exe -m ecommerce.cli import-orders --csv data/orders.csv --db data/ecommerce.db
./.venv/Scripts/python.exe -m ecommerce.cli import-products --csv data/products.csv --platform shopee_mock --db data/ecommerce.db
./.venv/Scripts/python.exe -m ecommerce.cli sync-orders --platform shopee_mock --since 2026-10-01T00:00:00+00:00 --db data/ecommerce.db
./.venv/Scripts/python.exe -m ecommerce.cli reconcile --orders data/orders.csv --bills data/bills.csv --fx data/fx_rates.csv --out outputs/reconciliation.csv
./.venv/Scripts/python.exe -m ecommerce.cli inventory --platform shopee_mock --local data/local_stock.csv --out outputs/inventory_diff.csv
./.venv/Scripts/python.exe -m ecommerce.cli extract --input data/raw_products.jsonl --provider mock --out outputs/extracted_products.csv --db data/ecommerce.db
./.venv/Scripts/python.exe -m ecommerce.cli report --db data/ecommerce.db
```

Lazada 形状的 HTTP Mock 需要先启动服务：

```bash
./.venv/Scripts/python.exe -m uvicorn mock_server.app:app --host 127.0.0.1 --port 8100
```

然后在另一个终端同步或比对库存：

```bash
./.venv/Scripts/python.exe -m ecommerce.cli sync-orders --platform lazada --since 2026-10-01T00:00:00+00:00 --base-url http://127.0.0.1:8100 --page-size 2 --db data/ecommerce.db
./.venv/Scripts/python.exe -m ecommerce.cli inventory --platform lazada --base-url http://127.0.0.1:8100 --local data/local_stock.csv --out outputs/inventory_diff.csv
```

## 结构

```text
CLI / FastAPI 薄层
  → 应用服务（订单 / 商品 / 库存 / 对账）
  → 领域模型 + Ports
  → 适配器（Shopee-like / Lazada / Mock） + SQLite Repository
```

- 领域层不 import `requests` / `fastapi` / `sqlite3`。
- 服务层只依赖 `PlatformPort`；换平台只换适配器。
- 对账是纯函数：输入订单/账单/汇率，输出差异行。
- `data/` 是合成脏数据；`outputs/` 是差异/汇总报告。

## 测试

```bash
./.venv/Scripts/python.exe -m pytest -q
./.venv/Scripts/ruff.exe check .
```

验证记录见 `docs/VERIFICATION.md`；演示脚本见 `docs/DEMO.md`；数据契约见 `docs/CONTRACT.md`。

## 数据与边界

- 数据是合成的，不含真实客户信息。
- `data/README.md` 记录每个脏数据用例的预期行为。
- 库存比对只读；商品只建草稿；不自动退款/改价/写回库存。
- 可选：复制 `.env.example` 为 `.env`，填 `DEEPSEEK_API_KEY`；`.env` 不进 git。
- 看板布局使用 Tabler 1.7.1（MIT），许可证随文件放在 `src/ecommerce/api/static/vendor/tabler/`。
- Mock 不是真实接入。
