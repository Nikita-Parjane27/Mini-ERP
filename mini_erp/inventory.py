"""Business logic: master data, purchase orders, BOM, work orders, stock movements."""
import csv
import logging
import sqlite3
from dataclasses import dataclass, field

from .db import transaction
from .errors import ERPError

log = logging.getLogger("mini_erp")

ITEM_TYPES = ("RAW", "FINISHED")
EPS = 1e-9


# ---------- helpers ----------

def _positive(value, label):
    try:
        value = float(value)
    except (TypeError, ValueError):
        raise ERPError(f"{label} must be a number")
    if value <= 0:
        raise ERPError(f"{label} must be greater than 0")
    return value


def _non_negative(value, label):
    try:
        value = float(value)
    except (TypeError, ValueError):
        raise ERPError(f"{label} must be a number")
    if value < 0:
        raise ERPError(f"{label} cannot be negative")
    return value


def get_item(conn, sku):
    row = conn.execute("SELECT * FROM items WHERE sku = ?", (sku.strip().upper(),)).fetchone()
    if not row:
        raise ERPError(f"Item '{sku}' not found")
    return row


def _get_supplier(conn, name):
    row = conn.execute("SELECT * FROM suppliers WHERE name = ?", (name.strip(),)).fetchone()
    if not row:
        raise ERPError(f"Supplier '{name}' not found")
    return row


def _next_number(conn, table, prefix):
    n = conn.execute(f"SELECT COALESCE(MAX(id), 0) + 1 FROM {table}").fetchone()[0]
    return f"{prefix}-{n:04d}"


def _move_stock(conn, item_id, delta, movement_type, ref_type=None, ref_id=None, note=None):
    """The ONLY place stock changes. Updates the item and writes a ledger row.
    Must be called inside a transaction()."""
    row = conn.execute("SELECT sku, stock_qty FROM items WHERE id = ?", (item_id,)).fetchone()
    new_qty = round(row["stock_qty"] + delta, 6)
    if new_qty < 0:
        raise ERPError(
            f"Stock of {row['sku']} cannot go below zero (have {row['stock_qty']:g}, change {delta:g})"
        )
    conn.execute("UPDATE items SET stock_qty = ? WHERE id = ?", (new_qty, item_id))
    conn.execute(
        "INSERT INTO stock_ledger (item_id, movement_type, qty_change, ref_type, ref_id, note) "
        "VALUES (?, ?, ?, ?, ?, ?)",
        (item_id, movement_type, delta, ref_type, ref_id, note),
    )


# ---------- master data ----------

def add_supplier(conn, name, phone=None, city=None):
    name = (name or "").strip()
    if not name:
        raise ERPError("Supplier name is required")
    try:
        with transaction(conn):
            cur = conn.execute(
                "INSERT INTO suppliers (name, phone, city) VALUES (?, ?, ?)", (name, phone, city)
            )
        log.info("Supplier added: %s", name)
        return cur.lastrowid
    except sqlite3.IntegrityError:
        raise ERPError(f"Supplier '{name}' already exists")


def add_item(conn, sku, name, unit="pcs", item_type="RAW", reorder_level=0, unit_cost=0):
    sku, name = (sku or "").strip().upper(), (name or "").strip()
    item_type = (item_type or "RAW").strip().upper()
    if not sku:
        raise ERPError("SKU is required")
    if not name:
        raise ERPError("Item name is required")
    if item_type not in ITEM_TYPES:
        raise ERPError(f"item_type must be one of {', '.join(ITEM_TYPES)}")
    reorder_level = _non_negative(reorder_level, "reorder_level")
    unit_cost = _non_negative(unit_cost, "unit_cost")
    try:
        with transaction(conn):
            cur = conn.execute(
                "INSERT INTO items (sku, name, unit, item_type, reorder_level, unit_cost) "
                "VALUES (?, ?, ?, ?, ?, ?)",
                (sku, name, (unit or "pcs").strip(), item_type, reorder_level, unit_cost),
            )
        log.info("Item added: %s (%s)", sku, item_type)
        return cur.lastrowid
    except sqlite3.IntegrityError:
        raise ERPError(f"Item with SKU '{sku}' already exists")


def adjust_stock(conn, sku, qty_change, note="Manual adjustment"):
    """Opening stock, stock-take corrections. Logged in the ledger like everything else."""
    qty_change = float(qty_change)
    if qty_change == 0:
        raise ERPError("Adjustment cannot be zero")
    with transaction(conn):
        item = get_item(conn, sku)
        _move_stock(conn, item["id"], qty_change, "ADJUSTMENT", note=note)
    log.info("Stock adjusted: %s %+g (%s)", item["sku"], qty_change, note)


# ---------- BOM ----------

def set_bom_line(conn, product_sku, component_sku, qty_per_unit):
    """Add a component to a product's BOM, or update its quantity if already there."""
    qty_per_unit = _positive(qty_per_unit, "Quantity per unit")
    with transaction(conn):
        product, component = get_item(conn, product_sku), get_item(conn, component_sku)
        if product["item_type"] != "FINISHED":
            raise ERPError(f"{product['sku']} is not a FINISHED item, so it cannot have a BOM")
        if product["id"] == component["id"]:
            raise ERPError("A product cannot be a component of itself")
        conn.execute(
            "INSERT INTO bom (product_id, component_id, qty_per_unit) VALUES (?, ?, ?) "
            "ON CONFLICT(product_id, component_id) DO UPDATE SET qty_per_unit = excluded.qty_per_unit",
            (product["id"], component["id"], qty_per_unit),
        )


def get_bom(conn, product_sku):
    product = get_item(conn, product_sku)
    return conn.execute(
        "SELECT i.sku AS component, i.name, i.unit, b.qty_per_unit "
        "FROM bom b JOIN items i ON i.id = b.component_id "
        "WHERE b.product_id = ? ORDER BY i.sku",
        (product["id"],),
    ).fetchall()


# ---------- purchase orders ----------

def create_po(conn, supplier_name, lines):
    """lines = [(sku, qty, rate), ...]. Returns the PO number. Saved all-or-nothing."""
    if not lines:
        raise ERPError("A purchase order needs at least one line")
    with transaction(conn):
        supplier = _get_supplier(conn, supplier_name)
        po_no = _next_number(conn, "purchase_orders", "PO")
        cur = conn.execute(
            "INSERT INTO purchase_orders (po_no, supplier_id) VALUES (?, ?)", (po_no, supplier["id"])
        )
        seen = set()
        for sku, qty, rate in lines:
            item = get_item(conn, sku)
            if item["id"] in seen:
                raise ERPError(f"{item['sku']} appears twice in the same PO")
            seen.add(item["id"])
            conn.execute(
                "INSERT INTO po_lines (po_id, item_id, qty, rate) VALUES (?, ?, ?, ?)",
                (cur.lastrowid, item["id"], _positive(qty, "Quantity"), _non_negative(rate, "Rate")),
            )
    log.info("PO created: %s for %s", po_no, supplier_name)
    return po_no


def receive_po(conn, po_no, received_on=None):
    """Goods received: stock goes up, one ledger row per line, PO becomes RECEIVED."""
    po_no = po_no.strip().upper()
    with transaction(conn):
        po = conn.execute("SELECT id, status FROM purchase_orders WHERE po_no = ?", (po_no,)).fetchone()
        if not po:
            raise ERPError(f"PO '{po_no}' not found")
        if po["status"] != "OPEN":
            raise ERPError(f"{po_no} is already {po['status']}")
        lines = conn.execute(
            "SELECT item_id, qty, rate FROM po_lines WHERE po_id = ?", (po["id"],)
        ).fetchall()
        for line in lines:
            _move_stock(conn, line["item_id"], line["qty"], "PO_RECEIPT", "PO", po["id"], po_no)
            conn.execute("UPDATE items SET unit_cost = ? WHERE id = ?", (line["rate"], line["item_id"]))
        conn.execute(
            "UPDATE purchase_orders SET status = 'RECEIVED', received_on = COALESCE(?, date('now')) "
            "WHERE id = ?",
            (received_on, po["id"]),
        )
    log.info("PO received: %s (%d lines)", po_no, len(lines))


def cancel_po(conn, po_no):
    po_no = po_no.strip().upper()
    with transaction(conn):
        po = conn.execute("SELECT id, status FROM purchase_orders WHERE po_no = ?", (po_no,)).fetchone()
        if not po:
            raise ERPError(f"PO '{po_no}' not found")
        if po["status"] != "OPEN":
            raise ERPError(f"Only OPEN POs can be cancelled ({po_no} is {po['status']})")
        conn.execute("UPDATE purchase_orders SET status = 'CANCELLED' WHERE id = ?", (po["id"],))
    log.info("PO cancelled: %s", po_no)


# ---------- production ----------

def create_work_order(conn, product_sku, qty):
    """Produce `qty` units: consume BOM components, add finished goods.
    Blocked (nothing changes) if any component is short."""
    qty = _positive(qty, "Quantity")
    with transaction(conn):
        product = get_item(conn, product_sku)
        if product["item_type"] != "FINISHED":
            raise ERPError(f"{product['sku']} is not a FINISHED item, so it cannot be produced")
        bom = conn.execute(
            "SELECT b.component_id, i.sku, i.stock_qty, b.qty_per_unit "
            "FROM bom b JOIN items i ON i.id = b.component_id WHERE b.product_id = ? ORDER BY i.sku",
            (product["id"],),
        ).fetchall()
        if not bom:
            raise ERPError(f"{product['sku']} has no BOM defined")

        needs = [(line, round(qty * line["qty_per_unit"], 6)) for line in bom]
        shortages = [
            f"{line['sku']} (need {need:g}, have {line['stock_qty']:g})"
            for line, need in needs
            if need - line["stock_qty"] > EPS
        ]
        if shortages:
            log.warning("Work order blocked for %s x%g: short %s", product["sku"], qty, ", ".join(shortages))
            raise ERPError(f"Cannot produce {qty:g} x {product['sku']}. Short on: " + "; ".join(shortages))

        wo_no = _next_number(conn, "work_orders", "WO")
        cur = conn.execute(
            "INSERT INTO work_orders (wo_no, product_id, qty) VALUES (?, ?, ?)", (wo_no, product["id"], qty)
        )
        for line, need in needs:
            _move_stock(conn, line["component_id"], -need, "WO_CONSUME", "WO", cur.lastrowid, wo_no)
        _move_stock(conn, product["id"], qty, "WO_OUTPUT", "WO", cur.lastrowid, wo_no)
    log.info("Work order completed: %s (%s x%g)", wo_no, product["sku"], qty)
    return wo_no


# ---------- CSV import with validation ----------

REQUIRED_COLUMNS = ("sku", "name")


@dataclass
class ImportResult:
    added: int = 0
    errors: list = field(default_factory=list)  # (line_no, sku, message)


def import_items_csv(conn, path, error_path=None):
    """Load good rows, reject bad rows with a clear reason. Optionally write an error report CSV."""
    result = ImportResult()
    seen = set()
    with open(path, newline="", encoding="utf-8-sig") as f:
        reader = csv.DictReader(f)
        headers = [h.strip().lower() for h in (reader.fieldnames or [])]
        missing = [c for c in REQUIRED_COLUMNS if c not in headers]
        if missing:
            raise ERPError(f"CSV is missing required column(s): {', '.join(missing)}")
        for line_no, raw in enumerate(reader, start=2):
            row = {(k or "").strip().lower(): (v or "").strip() for k, v in raw.items()}
            sku = row.get("sku", "").upper()
            try:
                if sku in seen:
                    raise ERPError(f"Duplicate SKU '{sku}' inside the file")
                add_item(
                    conn,
                    sku,
                    row.get("name"),
                    row.get("unit") or "pcs",
                    row.get("item_type") or "RAW",
                    row.get("reorder_level") or 0,
                    row.get("unit_cost") or 0,
                )
                seen.add(sku)
                result.added += 1
            except ERPError as e:
                result.errors.append((line_no, sku, str(e)))
    if error_path and result.errors:
        with open(error_path, "w", newline="", encoding="utf-8") as f:
            writer = csv.writer(f)
            writer.writerow(["line", "sku", "error"])
            writer.writerows(result.errors)
    log.info("CSV import: %d added, %d rejected (%s)", result.added, len(result.errors), path)
    return result


def bom_requirements(conn, product_sku, qty):
    """What a work order would need vs what is in stock (used by the dashboard preview)."""
    product = get_item(conn, product_sku)
    qty = _positive(qty, "Quantity")
    rows = conn.execute(
        "SELECT i.sku AS component, i.unit, b.qty_per_unit, i.stock_qty AS available "
        "FROM bom b JOIN items i ON i.id = b.component_id WHERE b.product_id = ? ORDER BY i.sku",
        (product["id"],),
    ).fetchall()
    return [
        {
            "component": r["component"],
            "unit": r["unit"],
            "needed": round(qty * r["qty_per_unit"], 6),
            "available": r["available"],
            "status": "SHORT" if round(qty * r["qty_per_unit"], 6) - r["available"] > EPS else "OK",
        }
        for r in rows
    ]
