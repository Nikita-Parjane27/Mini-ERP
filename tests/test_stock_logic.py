import sqlite3

import pytest

from mini_erp import inventory as inv, reports
from mini_erp.errors import ERPError

from conftest import count, fill_stock, stock


# ---------- purchase orders ----------

def test_new_po_is_open_and_does_not_change_stock(conn):
    po = inv.create_po(conn, "Shree Metals", [("CU", 100, 700)])
    assert po == "PO-0001"
    assert stock(conn, "CU") == 0


def test_receive_po_increases_stock_and_writes_ledger(conn):
    po = fill_stock(conn, cu=100, core=50)
    assert stock(conn, "CU") == 100 and stock(conn, "CORE") == 50
    entries = reports.ledger_rows(conn, "CU")
    assert [e["movement_type"] for e in entries] == ["PO_RECEIPT"]
    status = conn.execute("SELECT status FROM purchase_orders WHERE po_no=?", (po,)).fetchone()[0]
    assert status == "RECEIVED"


def test_receive_po_updates_unit_cost_to_last_rate(conn):
    fill_stock(conn)
    assert conn.execute("SELECT unit_cost FROM items WHERE sku='CU'").fetchone()[0] == 700


def test_po_cannot_be_received_twice(conn):
    po = fill_stock(conn, cu=100)
    with pytest.raises(ERPError, match="already RECEIVED"):
        inv.receive_po(conn, po)
    assert stock(conn, "CU") == 100


def test_cancelled_po_cannot_be_received(conn):
    po = inv.create_po(conn, "Shree Metals", [("CU", 10, 700)])
    inv.cancel_po(conn, po)
    with pytest.raises(ERPError, match="CANCELLED"):
        inv.receive_po(conn, po)
    assert stock(conn, "CU") == 0


def test_po_with_bad_line_is_fully_rolled_back(conn):
    with pytest.raises(ERPError):
        inv.create_po(conn, "Shree Metals", [("CU", 10, 700), ("NOPE", 5, 10)])
    assert count(conn, "purchase_orders") == 0 and count(conn, "po_lines") == 0


def test_po_rejects_unknown_supplier_and_empty_lines(conn):
    with pytest.raises(ERPError):
        inv.create_po(conn, "Ghost Ltd", [("CU", 1, 1)])
    with pytest.raises(ERPError):
        inv.create_po(conn, "Shree Metals", [])


# ---------- BOM ----------

def test_bom_update_changes_quantity_not_row_count(conn):
    inv.set_bom_line(conn, "CT", "CU", 3)
    bom = {r["component"]: r["qty_per_unit"] for r in inv.get_bom(conn, "CT")}
    assert bom == {"CORE": 4, "CU": 3} and count(conn, "bom") == 2


def test_bom_rejects_raw_product_and_self_reference(conn):
    with pytest.raises(ERPError, match="not a FINISHED"):
        inv.set_bom_line(conn, "CU", "CORE", 1)
    with pytest.raises(ERPError, match="itself"):
        inv.set_bom_line(conn, "CT", "CT", 1)


# ---------- work orders ----------

def test_work_order_consumes_components_and_adds_finished_goods(conn):
    fill_stock(conn, cu=100, core=100)
    wo = inv.create_work_order(conn, "CT", 10)
    assert wo == "WO-0001"
    assert stock(conn, "CU") == 80 and stock(conn, "CORE") == 60 and stock(conn, "CT") == 10


def test_work_order_blocked_when_stock_short_and_nothing_changes(conn):
    fill_stock(conn, cu=100, core=10)  # core only enough for 2 units
    ledger_before = count(conn, "stock_ledger")
    with pytest.raises(ERPError, match="CORE"):
        inv.create_work_order(conn, "CT", 10)
    assert stock(conn, "CU") == 100 and stock(conn, "CORE") == 10 and stock(conn, "CT") == 0
    assert count(conn, "work_orders") == 0 and count(conn, "stock_ledger") == ledger_before


def test_shortage_message_lists_every_missing_component(conn):
    fill_stock(conn, cu=1, core=1)
    with pytest.raises(ERPError) as err:
        inv.create_work_order(conn, "CT", 10)
    assert "CU" in str(err.value) and "CORE" in str(err.value)


def test_work_order_rejects_raw_item_and_missing_bom(conn):
    with pytest.raises(ERPError, match="not a FINISHED"):
        inv.create_work_order(conn, "CU", 1)
    inv.add_item(conn, "PT", "Potential Transformer", "pcs", "FINISHED")
    with pytest.raises(ERPError, match="no BOM"):
        inv.create_work_order(conn, "PT", 1)


def test_work_order_exact_stock_is_allowed(conn):
    fill_stock(conn, cu=20, core=40)  # exactly enough for 10 units
    inv.create_work_order(conn, "CT", 10)
    assert stock(conn, "CU") == 0 and stock(conn, "CORE") == 0


# ---------- adjustments, ledger, constraints ----------

def test_adjustment_cannot_make_stock_negative(conn):
    inv.adjust_stock(conn, "CU", 10, "Opening stock")
    with pytest.raises(ERPError, match="below zero"):
        inv.adjust_stock(conn, "CU", -11)
    assert stock(conn, "CU") == 10


def test_ledger_matches_stock_after_many_movements(conn):
    fill_stock(conn, cu=100, core=100)
    inv.create_work_order(conn, "CT", 5)
    inv.adjust_stock(conn, "CU", -3, "Damaged")
    inv.create_work_order(conn, "CT", 2)
    assert reports.verify_ledger(conn) == []


def test_verify_detects_tampered_stock(conn):
    fill_stock(conn)
    conn.execute("UPDATE items SET stock_qty = stock_qty + 5 WHERE sku = 'CU'")
    bad = reports.verify_ledger(conn)
    assert [r["sku"] for r in bad] == ["CU"]


def test_database_itself_rejects_negative_stock(conn):
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute("UPDATE items SET stock_qty = -1 WHERE sku = 'CU'")


def test_foreign_keys_are_enforced(conn):
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute("INSERT INTO po_lines (po_id, item_id, qty, rate) VALUES (999, 1, 1, 1)")
