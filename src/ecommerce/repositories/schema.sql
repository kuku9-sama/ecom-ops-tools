-- 商品：v0.1 只创建 draft，不发布。
CREATE TABLE IF NOT EXISTS products (
    platform      TEXT NOT NULL,
    sku           TEXT NOT NULL,
    title         TEXT NOT NULL,
    price_minor   INTEGER NOT NULL CHECK (price_minor >= 0),
    currency      TEXT NOT NULL,
    stock         INTEGER NOT NULL CHECK (stock >= 0),
    status        TEXT NOT NULL CHECK (status IN ('draft', 'active', 'inactive')),
    category      TEXT,
    image_url     TEXT,
    platform_product_id TEXT,
    updated_at    TEXT NOT NULL,
    PRIMARY KEY (platform, sku)
);

-- 订单头：复合主键就是幂等约束。
CREATE TABLE IF NOT EXISTS orders (
    platform           TEXT NOT NULL,
    platform_order_id  TEXT NOT NULL,
    status             TEXT NOT NULL,
    currency           TEXT NOT NULL,
    paid_at            TEXT NOT NULL,
    updated_at         TEXT NOT NULL,
    raw_json           TEXT,
    PRIMARY KEY (platform, platform_order_id)
);

-- 订单行：对账的最小单位。
CREATE TABLE IF NOT EXISTS order_lines (
    platform           TEXT NOT NULL,
    platform_order_id  TEXT NOT NULL,
    line_id            TEXT NOT NULL,
    sku                TEXT NOT NULL,
    qty                INTEGER NOT NULL CHECK (qty > 0),
    unit_price_minor   INTEGER NOT NULL CHECK (unit_price_minor >= 0),
    discount_minor     INTEGER NOT NULL DEFAULT 0 CHECK (discount_minor >= 0),
    refund_minor       INTEGER NOT NULL DEFAULT 0 CHECK (refund_minor >= 0),
    currency           TEXT NOT NULL,
    PRIMARY KEY (platform, platform_order_id, line_id),
    FOREIGN KEY (platform, platform_order_id)
        REFERENCES orders (platform, platform_order_id)
);

-- 账单行：line_id = '' 表示订单级汇总账单。
CREATE TABLE IF NOT EXISTS bill_lines (
    platform             TEXT NOT NULL,
    bill_id              TEXT NOT NULL,
    platform_order_id    TEXT NOT NULL,
    line_id              TEXT NOT NULL DEFAULT '',
    settlement_currency  TEXT NOT NULL,
    gross_minor          INTEGER NOT NULL,
    fee_minor            INTEGER NOT NULL DEFAULT 0 CHECK (fee_minor >= 0),
    refund_minor         INTEGER NOT NULL DEFAULT 0 CHECK (refund_minor >= 0),
    net_minor            INTEGER NOT NULL,
    settled_at           TEXT NOT NULL,
    PRIMARY KEY (platform, bill_id, line_id)
);

-- 汇率：rate 存 Decimal 字符串，避免浮点。
CREATE TABLE IF NOT EXISTS fx_rates (
    base_currency   TEXT NOT NULL,
    quote_currency  TEXT NOT NULL,
    rate            TEXT NOT NULL,
    effective_date  TEXT NOT NULL,
    source          TEXT NOT NULL,
    PRIMARY KEY (base_currency, quote_currency, effective_date)
);

-- 一次同步任务。
CREATE TABLE IF NOT EXISTS sync_runs (
    run_id         TEXT PRIMARY KEY,
    platform       TEXT NOT NULL,
    resource       TEXT NOT NULL,
    status         TEXT NOT NULL CHECK (
        status IN ('pending', 'processing', 'succeeded', 'retry_pending', 'failed')
    ),
    cursor_before  TEXT,
    cursor_after   TEXT,
    fetched        INTEGER NOT NULL DEFAULT 0,
    inserted       INTEGER NOT NULL DEFAULT 0,
    updated        INTEGER NOT NULL DEFAULT 0,
    failed         INTEGER NOT NULL DEFAULT 0,
    started_at     TEXT NOT NULL,
    finished_at    TEXT
);

-- 同步逐项状态：支持重试和恢复。
CREATE TABLE IF NOT EXISTS sync_items (
    run_id         TEXT NOT NULL,
    item_key       TEXT NOT NULL,
    status         TEXT NOT NULL CHECK (
        status IN ('pending', 'processing', 'succeeded', 'retry_pending', 'failed')
    ),
    attempts       INTEGER NOT NULL DEFAULT 0,
    last_error     TEXT,
    next_retry_at  TEXT,
    PRIMARY KEY (run_id, item_key),
    FOREIGN KEY (run_id) REFERENCES sync_runs (run_id)
);
