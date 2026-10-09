import sqlite3
from contextlib import contextmanager

DEFAULT_DB = "erp.db"

SCHEMA = """
CREATE TABLE IF NOT EXISTS suppliers (
    id     INTEGER PRIMARY KEY AUTOINCREMENT,
    name   TEXT NOT NULL UNIQUE,
    phone  TEXT,
    city   TEXT
);

CREATE TABLE IF NOT EXISTS items (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    sku           TEXT NOT NULL UNIQUE,
    name          TEXT NOT NULL,
    unit          TEXT NOT NULL DEFAULT 'pcs',
    item_type     TEXT NOT NULL DEFAULT 'RAW' CHECK (item_type IN ('RAW', 'FINISHED')),
    reorder_level REAL NOT NULL DEFAULT 0 CHECK (reorder_level >= 0),
    unit_cost     REAL NOT NULL DEFAULT 0 CHECK (unit_cost >= 0),
    stock_qty     REAL NOT NULL DEFAULT 0 CHECK (stock_qty >= 0)
);

CREATE TABLE IF NOT EXISTS purchase_orders (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    po_no       TEXT NOT NULL UNIQUE,
    supplier_id INTEGER NOT NULL REFERENCES suppliers(id),
    status      TEXT NOT NULL DEFAULT 'OPEN' CHECK (status IN ('OPEN', 'RECEIVED', 'CANCELLED')),
    created_on  TEXT NOT NULL DEFAULT (date('now')),
    received_on TEXT
);

CREATE TABLE IF NOT EXISTS po_lines (
    id      INTEGER PRIMARY KEY AUTOINCREMENT,
    po_id   INTEGER NOT NULL REFERENCES purchase_orders(id),
    item_id INTEGER NOT NULL REFERENCES items(id),
    qty     REAL NOT NULL CHECK (qty > 0),
    rate    REAL NOT NULL CHECK (rate >= 0),
    UNIQUE (po_id, item_id)
);

CREATE TABLE IF NOT EXISTS bom (
    product_id   INTEGER NOT NULL REFERENCES items(id),
    component_id INTEGER NOT NULL REFERENCES items(id),
    qty_per_unit REAL NOT NULL CHECK (qty_per_unit > 0),
    PRIMARY KEY (product_id, component_id),
    CHECK (product_id <> component_id)
);

CREATE TABLE IF NOT EXISTS work_orders (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    wo_no      TEXT NOT NULL UNIQUE,
    product_id INTEGER NOT NULL REFERENCES items(id),
    qty        REAL NOT NULL CHECK (qty > 0),
    status     TEXT NOT NULL DEFAULT 'COMPLETED' CHECK (status IN ('COMPLETED')),
    created_on TEXT NOT NULL DEFAULT (date('now'))
);

CREATE TABLE IF NOT EXISTS stock_ledger (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    item_id       INTEGER NOT NULL REFERENCES items(id),
    movement_type TEXT NOT NULL CHECK (movement_type IN
                  ('PO_RECEIPT', 'WO_CONSUME', 'WO_OUTPUT', 'ADJUSTMENT')),
    qty_change    REAL NOT NULL CHECK (qty_change <> 0),
    ref_type      TEXT,
    ref_id        INTEGER,
    note          TEXT,
    created_at    TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE INDEX IF NOT EXISTS idx_ledger_item ON stock_ledger(item_id);
CREATE INDEX IF NOT EXISTS idx_po_lines_po ON po_lines(po_id);
"""


def get_conn(db_path=DEFAULT_DB, check_same_thread=True):
    """Open a connection. Transactions are managed by transaction() below."""
    conn = sqlite3.connect(db_path, isolation_level=None, check_same_thread=check_same_thread)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def init_db(conn):
    conn.executescript(SCHEMA)


@contextmanager
def transaction(conn):
    """All-or-nothing block. Any error rolls back every change made inside it."""
    conn.execute("BEGIN IMMEDIATE")
    try:
        yield conn
        conn.execute("COMMIT")
    except BaseException:
        conn.execute("ROLLBACK")
        raise
