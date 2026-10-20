# 数据契约 v0.1

> 项目：ecom-ops-tools
> 日期：2026-10-20
> 状态：初版
>
> 这份文档是 CSV、领域模型、SQLite 表、平台适配器、对账规则之间的共同约定。
> 改字段、改键、改金额语义，先改这里，再改代码。

---

## 1. 范围

v0.1 覆盖：

- 商品草稿（Product）
- 订单头（Order）
- 订单行（OrderLine）
- 账单行（BillLine）
- 汇率（FxRate）
- 同步运行与逐项状态（SyncRun / SyncItem）

不在 v0.1：

- 库存快照（后续补）
- LLM 提取结果（后续补）
- 真实平台签名、限流、分页细节（属于适配器内部实现，对外仍映射到本契约）

---

## 2. 通用规则

### 2.1 编码与格式

- CSV 输入用 UTF-8；读取时用 `utf-8-sig`，兼容带 BOM 的文件。
- 输出 CSV 用 `utf-8-sig`，方便 Windows Excel 直接打开。
- 第一行必须是表头；列顺序不强制，按列名读取。
- 未知列忽略并记录一条 warning；缺少必需列直接报错。

### 2.2 金额

- 一律用最小货币单位整数，字段名以 `_minor` 结尾。
- 禁止 `float`。
- CSV 里金额可以写成 `"¥1,234.50"`、`"1 234.50"`、`"199.00"`；导入时清洗成整数最小单位。
- 清洗规则：去掉货币符号、千分位逗号、空格；用 `Decimal` 解析；乘以 10^minor；`ROUND_HALF_UP`。

### 2.3 币种

ISO 4217 大写三字母。v0.1 支持：

| 币种 | 最小单位位数 | 例子 |
|---|---|---|
| CNY | 2 | 123450 = ¥1,234.50 |
| USD | 2 | 123450 = $1,234.50 |
| SGD | 2 | 123450 = S$1,234.50 |
| MYR | 2 | 123450 = RM1,234.50 |
| THB | 2 | 123450 = ฿1,234.50 |
| PHP | 2 | 123450 = ₱1,234.50 |
| IDR | 0 | 123450 = Rp123,450 |
| VND | 0 | 123450 = 123,450₫ |

不支持的币种 → 校验错误，不猜测。

### 2.4 汇率

- `rate` 是 Decimal 字符串，存 TEXT，不用 float。
- 方向：`1 base = rate quote`。例如 `USD,CNY,7.2500` 表示 1 USD = 7.25 CNY。
- 必须 > 0。
- v0.1 只支持直接货币对，不做三角换算。

### 2.5 时间与日期

- 时间：ISO 8601，必须带时区，例如 `2026-10-01T10:00:00+08:00`。
- 导入时解析成带时区的 datetime，存库时统一转 UTC（`2026-10-01T02:00:00+00:00`）。
- 日期：`YYYY-MM-DD`，不带时间。
- 没有时区的时间 → 校验错误，不默认 UTC。

### 2.6 文本与 ID

- 所有 ID 都是字符串，不假设是数字。
- 读取时去掉首尾空格；平台名转小写，币种转大写。
- 空字符串表示“未提供”；必填字段为空 → 校验错误。
- 唯一例外：`bill_lines.line_id = ""` 表示“平台按订单汇总出账”。

---

## 3. 实体与字段

### 3.1 Product（商品草稿）

| 字段 | CSV 类型 | 领域/DB 类型 | 必填 | 规则 |
|---|---|---|---|---|
| platform | string | str | 是 | 适配器键，如 shopee_mock / lazada |
| sku | string | str | 是 | 同一平台内唯一；去空格后非空 |
| title | string | str | 是 | 1–200 字符 |
| price | amount | price_minor:int | 是 | > 0 |
| currency | string | str | 是 | 支持的币种 |
| stock | int | int | 是 | >= 0 |
| category | string | str | 否 | 类目编码或名称 |
| image_url | string | str | 是 | 以 http:// 或 https:// 开头 |
| updated_at | datetime | datetime | 是 | ISO 8601 带时区 |

- 商品 CSV 不包含 `platform`；导入命令用 `--platform` 指定。
- v0.1 只创建 `draft`，不发布。
- 唯一键：`(platform, sku)`。

### 3.2 Order（订单头）

| 字段 | CSV 列名 | 领域/DB 类型 | 必填 | 规则 |
|---|---|---|---|---|
| platform | platform | str | 是 | 平台键 |
| platform_order_id | order_id | str | 是 | 平台订单号；与 platform 组合唯一 |
| status | status | str | 是 | 转小写；已知值：pending/paid/shipped/delivered/cancelled/refunded |
| currency | currency | str | 是 | 订单币种 |
| paid_at | paid_at | datetime | 是 | 支付时间；带时区 |
| updated_at | updated_at | datetime | 是 | 平台更新时间；带时区 |
| raw_json | — | str/text | 否 | 原始响应，便于排查；CSV 导入时为空 |

- 订单总额不单独存，由订单行净额汇总。
- 唯一键：`(platform, platform_order_id)`。

### 3.3 OrderLine（订单行）

| 字段 | CSV 列名 | 领域/DB 类型 | 必填 | 规则 |
|---|---|---|---|---|
| platform | platform | str | 是 | 平台键 |
| platform_order_id | order_id | str | 是 | 所属订单 |
| line_id | line_id | str | 是 | 订单内唯一 |
| sku | sku | str | 是 | 商品 SKU |
| qty | qty | int | 是 | > 0 |
| unit_price | unit_price | unit_price_minor:int | 是 | >= 0 |
| discount | discount | discount_minor:int | 否 | 默认 0；>= 0 |
| refund | refund | refund_minor:int | 否 | 默认 0；>= 0 |
| currency | currency | str | 是 | 必须等于订单币种 |

- 净额：`net_minor = qty * unit_price_minor - discount_minor - refund_minor`。
- 唯一键：`(platform, platform_order_id, line_id)`。
- 外键：`(platform, platform_order_id)` → `orders`。

### 3.4 BillLine（账单行）

| 字段 | CSV 列名 | 领域/DB 类型 | 必填 | 规则 |
|---|---|---|---|---|
| platform | platform | str | 是 | 平台键 |
| bill_id | bill_id | str | 是 | 平台账单号 |
| platform_order_id | order_id | str | 是 | 账单对应的订单 |
| line_id | line_id | str | 是 | 空字符串 = 订单级汇总账单 |
| settlement_currency | settlement_currency | str | 是 | 结算币种 |
| gross | gross | gross_minor:int | 是 | 结算币种下的订单金额（扣费/退款前） |
| fee | fee | fee_minor:int | 否 | 默认 0；>= 0 |
| refund | refund | refund_minor:int | 否 | 默认 0；>= 0 |
| net | net | net_minor:int | 是 | 平台实际打款金额 |
| settled_at | settled_at | date | 是 | 结算日 |

- 必须满足：`net_minor == gross_minor - fee_minor - refund_minor`。
  - 不满足 → `bill_inconsistent`，进数据质量/人工队列，不拿去和订单比。
- 唯一键：`(platform, bill_id, line_id)`。
- 匹配键：`(platform, platform_order_id, line_id)`。

### 3.5 FxRate（汇率）

| 字段 | CSV 列名 | 领域/DB 类型 | 必填 | 规则 |
|---|---|---|---|---|
| base | base | str | 是 | 基础币种 |
| quote | quote | str | 是 | 报价币种 |
| rate | rate | Decimal/text | 是 | > 0；1 base = rate quote |
| effective_date | effective_date | date | 是 | 生效日 |
| source | source | str | 是 | manual / mock / provider |

- 唯一键：`(base_currency, quote_currency, effective_date)`。
- 取汇率顺序：
  1. `base == quote` → 1，source=`same_currency`。
  2. 结算日精确匹配。
  3. 回退到订单支付日。
  4. 仍缺失 → `fx_missing`，进人工；**不能默认 1**。

### 3.6 SyncRun / SyncItem（同步状态）

SyncRun：

| 字段 | 类型 | 说明 |
|---|---|---|
| run_id | str | UUID |
| platform | str | 平台键 |
| resource | str | orders / products / inventory / bills |
| status | enum | pending / processing / succeeded / retry_pending / failed |
| cursor_before | str/null | 本次开始时的游标 |
| cursor_after | str/null | 本次结束时的游标 |
| fetched | int | 抓取条数 |
| inserted | int | 新增条数 |
| updated | int | 更新条数 |
| failed | int | 失败条数 |
| started_at | datetime | 开始时间 |
| finished_at | datetime/null | 结束时间 |

SyncItem：

| 字段 | 类型 | 说明 |
|---|---|---|
| run_id | str | 所属运行 |
| item_key | str | 订单号 / SKU / 账单号 |
| status | enum | 同上 |
| attempts | int | 已尝试次数 |
| last_error | str/null | 最后一次错误 |
| next_retry_at | datetime/null | 下次重试时间 |

- SyncRun 主键：`run_id`。
- SyncItem 主键：`(run_id, item_key)`。
- 状态机见第 5 节。

---

## 4. 唯一键与约束

```sql
-- 订单
PRIMARY KEY (platform, platform_order_id)
-- 订单行
PRIMARY KEY (platform, platform_order_id, line_id)
-- 账单行
PRIMARY KEY (platform, bill_id, line_id)
-- 商品
PRIMARY KEY (platform, sku)
-- 汇率
PRIMARY KEY (base_currency, quote_currency, effective_date)
-- 同步任务
PRIMARY KEY (run_id)
-- 同步逐项
PRIMARY KEY (run_id, item_key)
```

- 用自然键做主键，不引入自增 id。
- 应用层的“先查再插”只用于统计和可读错误；唯一性由数据库约束保证。
- 不使用 `INSERT OR IGNORE`；用 `ON CONFLICT DO UPDATE` 显式声明更新字段。

---

## 5. 状态机

```text
pending → processing → succeeded
                    ↘ retry_pending → processing → ...
                    ↘ failed
```

- `retry_pending` 只用于可重试错误：429、5xx、超时、连接错误。
- `failed` 用于：401/403 刷新 token 后仍失败、4xx 校验错误、重试预算耗尽。
- 每次状态变化记录 `run_id`、`item_key`、`attempts`、`error_class`。

---

## 6. 错误分类

| 异常 | 典型来源 | 是否重试 | 处理 |
|---|---|---|---|
| AuthError | 401/403 | 刷新 token 一次；仍失败不重试 | 停止任务，告警 |
| RateLimitError | 429 / E901 | 是，尊重 Retry-After | 有上限退避 |
| TransientError | 5xx / 超时 / 连接错误 | 是，指数退避 + 抖动 | 有上限重试 |
| ValidationError | 4xx 参数/业务校验 | 否 | 死信/人工 |
| PermanentError | 其他 4xx | 否 | 死信/人工 |
| ParseError | 2xx 但 JSON/XML 非法 | 有限重试 | 仍失败进死信 |

- POST 默认不重试；只有带幂等键或“先查后写”确认后才重试。
- 统一捕获 `requests.RequestException`，不要只捕获超时子类。

---

## 7. 数据质量规则

| 规则 | 处理 |
|---|---|
| 同一订单的订单级字段（status/currency/paid_at/updated_at）不一致 | 整单跳过，记录错误 |
| 同一文件内同一 `order_id + line_id` 内容完全相同 | 保留一条，记录“重复行已忽略” |
| 同一文件内同一 `order_id + line_id` 内容冲突 | 整单跳过，记录冲突 |
| 账单键 `(platform, bill_id, line_id)` 重复 | 全部进数据质量报告，不参与匹配 |
| 同一订单行出现多条不同 `bill_id` | `multiple_bills_for_line`，人工复核 |
| 账单 `net != gross - fee - refund` | `bill_inconsistent`，人工复核 |
| 汇率缺失 | `fx_missing`，人工复核；不默认 1 |
| 未知币种/负数量/负单价/坏日期 | 校验错误，该单不导入 |

---

## 8. 对账口径（v0.1）

- 行级匹配：`(platform, platform_order_id, line_id)`。
- 订单级兜底：行级未匹配的订单行/账单行按 `(platform, platform_order_id)` 聚合后再比；命中标记 `aggregated_bill`。
- 汇总结算金额：订单行净额 × 汇率，四舍五入到最小单位。
- 差异分类：

| 分类 | 判定 | 去向 |
|---|---|---|
| matched | 差异 = 0 | 正常 |
| fx_or_rounding_diff | `0 < abs(diff) <= 1` | 记录，不告警 |
| amount_mismatch | `abs(diff) > 1` | 人工复核 |
| bill_inconsistent | `net != gross - fee - refund` | 数据质量/人工 |
| refund_mismatch | 账单退款与订单行退款不一致 | 人工复核 |
| missing_order | 有订单行，无账单 | 人工复核 |
| orphan_bill | 有账单，无订单行 | 人工复核 |
| aggregated_bill | 订单级匹配成功 | 记录 |
| fx_missing | 汇率缺失 | 人工复核 |
| duplicate_bill | 重复账单键 | 数据质量 |
| multiple_bills_for_line | 同一订单行多条不同账单 | 人工复核 |

- 手续费率的正确性需要业务规则（费率表），v0.1 不做；v0.1 只校验账单内部一致性和退款金额是否与订单一致。
- 订单级兜底只在同一 `platform_order_id` 下聚合，不做模糊匹配。

---

## 9. CSV 文件

### 9.1 orders.csv

```text
platform,order_id,line_id,sku,qty,unit_price,discount,refund,currency,status,paid_at,updated_at
```

- 一行一条订单行；订单级字段在多行重复。
- 同一订单的订单级字段必须一致。
- `discount` / `refund` 可省略，默认 0。

### 9.2 bills.csv

```text
platform,bill_id,order_id,line_id,settlement_currency,gross,fee,refund,net,settled_at
```

- `line_id` 为空表示订单级汇总账单。
- `net` 必须等于 `gross - fee - refund`。

### 9.3 products.csv

```text
sku,title,price,currency,stock,category,image_url
```

- 平台由导入命令的 `--platform` 指定。
- 同一文件内 SKU 不能重复。

### 9.4 fx_rates.csv

```text
base,quote,rate,effective_date,source
```

- 同一 `(base, quote, effective_date)` 只能有一行。

---

## 10. 变更记录

| 日期 | 版本 | 变更 |
|---|---|---|
| 2026-10-20 | v0.1 | 初版：订单、订单行、账单、商品、汇率、同步状态 |
