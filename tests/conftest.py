import pytest

from mini_erp import db, inventory as inv


@pytest.fixture
def conn(tmp_path):
    c = db.get_conn(str(tmp_path / "test.db"))
    db.init_db(c)
    inv.add_supplier(c, "Shree Metals", city="Waluj")
    inv.add_item(c, "CU", "Copper Wire", "kg", "RAW", reorder_level=50)
    inv.add_item(c, "CORE", "Steel Core", "kg", "RAW", reorder_level=20)
    inv.add_item(c, "CT", "Current Transformer", "pcs", "FINISHED", reorder_level=5)
    inv.set_bom_line(c, "CT", "CU", 2)    # 2 kg copper per CT
    inv.set_bom_line(c, "CT", "CORE", 4)  # 4 kg core per CT
    yield c
    c.close()


def stock(conn, sku):
    return conn.execute("SELECT stock_qty FROM items WHERE sku = ?", (sku,)).fetchone()[0]


def count(conn, table):
    return conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]


def fill_stock(conn, cu=100, core=100):
    """Receive a PO so raw materials are in stock."""
    from mini_erp import inventory as inv
    po = inv.create_po(conn, "Shree Metals", [("CU", cu, 700), ("CORE", core, 90)])
    inv.receive_po(conn, po)
    return po
