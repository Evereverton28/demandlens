-- DemandLens schema.
-- Stock on hand is always derived from stock_movements (the ledger), never stored.

CREATE TABLE IF NOT EXISTS users (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    username      TEXT UNIQUE NOT NULL,
    email         TEXT UNIQUE NOT NULL,
    password      TEXT NOT NULL,
    created_at    TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS user_settings (
    user_id                 INTEGER PRIMARY KEY REFERENCES users(id) ON DELETE CASCADE,
    default_lead_time_days  INTEGER NOT NULL DEFAULT 7,
    review_period_days      INTEGER NOT NULL DEFAULT 7,
    overstock_weeks         INTEGER NOT NULL DEFAULT 12,
    holiday_country         TEXT    NOT NULL DEFAULT 'KE',
    currency                TEXT    NOT NULL DEFAULT 'KES'
);

CREATE TABLE IF NOT EXISTS categories (
    category_id   INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id       INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    name          TEXT NOT NULL,
    UNIQUE (user_id, name)
);

CREATE TABLE IF NOT EXISTS products (
    product_id     INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id        INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    sku            TEXT NOT NULL,
    name           TEXT NOT NULL,
    category_id    INTEGER REFERENCES categories(category_id) ON DELETE SET NULL,
    cost_price     REAL,
    selling_price  REAL,
    lead_time_days INTEGER,                      -- NULL = use the user's default
    is_active      INTEGER NOT NULL DEFAULT 1,   -- archived instead of deleted, so history survives
    created_at     TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    UNIQUE (user_id, sku)
);

CREATE TABLE IF NOT EXISTS import_batches (
    batch_id      INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id       INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    file_name     TEXT NOT NULL,
    format        TEXT NOT NULL,                 -- online_retail | generic | sims | scenario
    rows_read     INTEGER,
    rows_imported INTEGER,
    rows_rejected INTEGER,
    report_json   TEXT,                          -- per-rule counts from preprocessing
    is_synthetic  INTEGER NOT NULL DEFAULT 0,
    is_scenario   INTEGER NOT NULL DEFAULT 0,    -- simulated stock levels, not real counts
    imported_at   TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

-- The ledger. SALE, RETURN, RESTOCK and ADJUSTMENT change stock by an amount
-- (ADJUSTMENT is signed). STOCKTAKE records a counted level that resets stock.
CREATE TABLE IF NOT EXISTS stock_movements (
    movement_id   INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id       INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    product_id    INTEGER NOT NULL REFERENCES products(product_id) ON DELETE CASCADE,
    type          TEXT NOT NULL CHECK (type IN ('SALE','RETURN','RESTOCK','ADJUSTMENT','STOCKTAKE')),
    quantity      REAL NOT NULL,
    unit_price    REAL,                          -- price at the time of the movement
    occurred_at   TIMESTAMP NOT NULL,
    recorded_at   TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    source        TEXT NOT NULL DEFAULT 'manual',  -- manual | import | scenario | migration
    batch_id      INTEGER REFERENCES import_batches(batch_id) ON DELETE CASCADE,
    note          TEXT
);
CREATE INDEX IF NOT EXISTS ix_mov_user_product_time ON stock_movements(user_id, product_id, occurred_at);
CREATE INDEX IF NOT EXISTS ix_mov_user_type_time ON stock_movements(user_id, type, occurred_at);

CREATE TABLE IF NOT EXISTS jobs (
    job_id        INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id       INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    kind          TEXT NOT NULL,                 -- analysis | import
    status        TEXT NOT NULL,                 -- running | done | failed
    message       TEXT,
    progress      TEXT,
    started_at    TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    finished_at   TIMESTAMP
);

CREATE TABLE IF NOT EXISTS model_runs (
    run_id         INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id        INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    trained_at     TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    status         TEXT NOT NULL,                -- running | completed | failed
    algorithm      TEXT,
    params_json    TEXT,
    data_start     DATE,
    data_end       DATE,                         -- start of the last complete week used
    n_products     INTEGER,
    n_modelled     INTEGER,
    metrics_json   TEXT,                         -- backtest, method selection, calibration, anomaly evaluation
    duration_s     REAL,
    uses_synthetic INTEGER NOT NULL DEFAULT 0,
    uses_scenario  INTEGER NOT NULL DEFAULT 0,
    error          TEXT
);

CREATE TABLE IF NOT EXISTS forecasts (
    run_id      INTEGER NOT NULL REFERENCES model_runs(run_id) ON DELETE CASCADE,
    product_id  INTEGER NOT NULL REFERENCES products(product_id) ON DELETE CASCADE,
    week_start  DATE NOT NULL,
    horizon     INTEGER NOT NULL,
    p50         REAL NOT NULL,             -- median weekly demand
    p90         REAL NOT NULL,             -- 90th percentile (a busy week)
    mean        REAL NOT NULL,             -- expected demand, used for cumulative quantities
    method      TEXT NOT NULL,
    PRIMARY KEY (run_id, product_id, horizon)
);

CREATE TABLE IF NOT EXISTS product_metrics (
    run_id           INTEGER NOT NULL REFERENCES model_runs(run_id) ON DELETE CASCADE,
    product_id       INTEGER NOT NULL REFERENCES products(product_id) ON DELETE CASCADE,
    revenue_52w      REAL, units_52w REAL, revenue_share REAL,
    abc_class        TEXT, xyz_class TEXT, cv REAL,
    demand_pattern   TEXT, adi REAL, cv2 REAL,
    trend            TEXT, trend_slope REAL, trend_p REAL,
    velocity_12w     REAL, weeks_since_sale INTEGER, movement_class TEXT,
    stock_on_hand    REAL,
    days_of_cover    REAL,
    runout_expected_days  REAL, runout_worst_days REAL,
    overstock_units  REAL, overstock_value REAL, overstock_basis TEXT,
    reorder_qty      REAL,
    lead_time_days   INTEGER,
    forecast_method  TEXT,
    PRIMARY KEY (run_id, product_id)
);

-- Backtest actuals against forecasts: feeds the product charts and the anomaly review.
CREATE TABLE IF NOT EXISTS backtest_points (
    run_id      INTEGER NOT NULL REFERENCES model_runs(run_id) ON DELETE CASCADE,
    product_id  INTEGER NOT NULL REFERENCES products(product_id) ON DELETE CASCADE,
    week_start  DATE NOT NULL,
    horizon     INTEGER NOT NULL,
    actual      REAL, p50 REAL, p90 REAL,
    PRIMARY KEY (run_id, product_id, week_start)
);

CREATE TABLE IF NOT EXISTS anomalies (
    anomaly_id  INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id     INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    run_id      INTEGER REFERENCES model_runs(run_id) ON DELETE SET NULL,
    product_id  INTEGER NOT NULL REFERENCES products(product_id) ON DELETE CASCADE,
    kind        TEXT NOT NULL,                   -- weekly_sales | transaction
    week_start  DATE NOT NULL,
    movement_id INTEGER NOT NULL DEFAULT 0,      -- 0 for weekly anomalies
    actual      REAL, expected REAL, score REAL,
    direction   TEXT,                            -- spike | drop
    severity    TEXT,                            -- high | moderate
    status      TEXT NOT NULL DEFAULT 'open',    -- open | confirmed | dismissed
    note        TEXT,
    created_at  TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    UNIQUE (user_id, product_id, kind, week_start, movement_id)
);

CREATE TABLE IF NOT EXISTS recommendations (
    run_id      INTEGER NOT NULL REFERENCES model_runs(run_id) ON DELETE CASCADE,
    product_id  INTEGER NOT NULL REFERENCES products(product_id) ON DELETE CASCADE,
    action      TEXT NOT NULL,
    quantity    REAL,
    priority    INTEGER NOT NULL,                -- 1 = most urgent
    reason      TEXT NOT NULL,
    PRIMARY KEY (run_id, product_id, action)
);
