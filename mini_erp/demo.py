"""Loads realistic demo data for an electrical equipment maker."""
from . import inventory as inv
from .errors import ERPError


def load_demo(conn):
    if conn.execute("SELECT COUNT(*) FROM items").fetchone()[0]:
        raise ERPError("Demo needs an empty database. Use a new file, e.g. --db demo.db")

    for name, phone, city in [
        ("Shree Metals", "9800000001", "Waluj"),
        ("Sai Resins", "9800000002", "Chhatrapati Sambhajinagar"),
        ("Pune Electricals", "9800000003", "Pune"),
    ]:
        inv.add_supplier(conn, name, phone, city)

    # sku, name, unit, type, reorder level
    for sku, name, unit, typ, reorder in [
        ("CU-WIRE", "Copper Wire 2.5 sq mm", "kg", "RAW", 450),
        ("CORE-CRGO", "CRGO Silicon Steel Core", "kg", "RAW", 100),
        ("EPOXY-RESIN", "Epoxy Resin", "kg", "RAW", 80),
        ("HARDENER", "Epoxy Hardener", "kg", "RAW", 20),
        ("TERMINAL", "Terminal Bolt Set", "pcs", "RAW", 200),
        ("HOUSING", "Cast Housing", "pcs", "RAW", 20),
        ("CT-11KV", "Current Transformer 11kV", "pcs", "FINISHED", 10),
        ("PT-11KV", "Potential Transformer 11kV", "pcs", "FINISHED", 5),
    ]:
        inv.add_item(conn, sku, name, unit, typ, reorder)

    for product, lines in {
        "CT-11KV": [("CU-WIRE", 2.5), ("CORE-CRGO", 4), ("EPOXY-RESIN", 1.5), ("HARDENER", 0.4),
                    ("TERMINAL", 4), ("HOUSING", 1)],
        "PT-11KV": [("CU-WIRE", 3.2), ("CORE-CRGO", 6), ("EPOXY-RESIN", 2), ("HARDENER", 0.5),
                    ("TERMINAL", 4), ("HOUSING", 1)],
    }.items():
        for component, qty in lines:
            inv.set_bom_line(conn, product, component, qty)

    for supplier, lines in [
        ("Shree Metals", [("CU-WIRE", 500, 720), ("CORE-CRGO", 300, 95)]),
        ("Sai Resins", [("EPOXY-RESIN", 120, 310), ("HARDENER", 40, 450)]),
        ("Pune Electricals", [("TERMINAL", 400, 18), ("HOUSING", 50, 260)]),
    ]:
        inv.receive_po(conn, inv.create_po(conn, supplier, lines))

    inv.create_work_order(conn, "CT-11KV", 40)
    inv.create_po(conn, "Shree Metals", [("CU-WIRE", 200, 730)])  # left OPEN on purpose
