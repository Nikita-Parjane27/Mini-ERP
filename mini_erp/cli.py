import argparse
import logging
import sys

from . import db, demo, inventory as inv, reports
from .errors import ERPError


def fmt(v):
    if isinstance(v, float):
        return f"{v:,.2f}".rstrip("0").rstrip(".")
    return "" if v is None else str(v)


def print_table(rows):
    if not rows:
        print("(no rows)")
        return
    headers = list(rows[0].keys())
    data = [[fmt(r[h]) for h in headers] for r in rows]
    widths = [max(len(h), *(len(row[i]) for row in data)) for i, h in enumerate(headers)]
    line = "  ".join(h.upper().ljust(widths[i]) for i, h in enumerate(headers))
    print(line)
    print("-" * len(line))
    for row in data:
        print("  ".join(c.ljust(widths[i]) for i, c in enumerate(row)))


def parse_line(text):
    """'SKU:QTY:RATE' -> (sku, qty, rate)"""
    parts = text.split(":")
    if len(parts) != 3:
        raise ERPError(f"Bad line '{text}'. Use SKU:QTY:RATE, e.g. CU-WIRE:100:720")
    return parts[0], parts[1], parts[2]


def build_parser():
    p = argparse.ArgumentParser(prog="mini-erp", description="Mini ERP: purchase, BOM, production, stock ledger")
    p.add_argument("--db", default=db.DEFAULT_DB, help="SQLite file (default: erp.db)")
    p.add_argument("--log", default="mini_erp.log", help="log file (default: mini_erp.log)")
    sub = p.add_subparsers(dest="cmd", required=True)

    sub.add_parser("init", help="create database tables")
    sub.add_parser("demo", help="load demo data (needs an empty database)")

    s = sub.add_parser("add-supplier", help="add a supplier")
    s.add_argument("name"); s.add_argument("--phone"); s.add_argument("--city")

    s = sub.add_parser("add-item", help="add an item (RAW or FINISHED)")
    s.add_argument("sku"); s.add_argument("name")
    s.add_argument("--unit", default="pcs")
    s.add_argument("--type", dest="item_type", default="RAW", choices=["RAW", "FINISHED", "raw", "finished"])
    s.add_argument("--reorder", type=float, default=0)
    s.add_argument("--cost", type=float, default=0)

    s = sub.add_parser("adjust", help="manual stock adjustment, e.g. opening stock (+) or damage (-)")
    s.add_argument("sku"); s.add_argument("qty", type=float); s.add_argument("--note", default="Manual adjustment")

    s = sub.add_parser("add-bom", help="add or update a BOM line for a finished product")
    s.add_argument("product"); s.add_argument("component"); s.add_argument("qty", type=float)
    s = sub.add_parser("show-bom", help="show the BOM of a product")
    s.add_argument("product")

    s = sub.add_parser("create-po", help="create a purchase order")
    s.add_argument("supplier")
    s.add_argument("--line", action="append", required=True, metavar="SKU:QTY:RATE")
    s = sub.add_parser("receive-po", help="goods received: stock goes up")
    s.add_argument("po_no")
    s = sub.add_parser("cancel-po", help="cancel an OPEN purchase order")
    s.add_argument("po_no")
    sub.add_parser("list-pos", help="list purchase orders")

    s = sub.add_parser("work-order", help="produce finished goods from the BOM")
    s.add_argument("product"); s.add_argument("qty", type=float)

    sub.add_parser("stock", help="current stock")
    sub.add_parser("low-stock", help="items at or below reorder level")
    sub.add_parser("valuation", help="stock value (qty x last purchase rate)")
    sub.add_parser("purchase-summary", help="received purchases per supplier")
    s = sub.add_parser("ledger", help="stock movement history")
    s.add_argument("--sku"); s.add_argument("--limit", type=int)
    sub.add_parser("verify", help="audit: recompute stock from the ledger and compare")

    s = sub.add_parser("import-items", help="bulk import items from CSV with validation")
    s.add_argument("csv_path"); s.add_argument("--errors", default="import_errors.csv")
    s = sub.add_parser("export-excel", help="Excel report (Stock, Low Stock, Valuation, Purchase Summary)")
    s.add_argument("--out", default="erp_report.xlsx")
    s = sub.add_parser("export-json", help="export stock data as JSON")
    s.add_argument("--out", default="stock.json")
    s = sub.add_parser("daily-job", help="save Excel report and log low-stock items (schedule this daily)")
    s.add_argument("--out-dir", default="reports")
    return p


def run(args, conn):
    c = args.cmd
    if c == "init":
        print(f"Database ready: {args.db}")
    elif c == "demo":
        demo.load_demo(conn)
        print("Demo data loaded. Try: stock, low-stock, ledger, work-order PT-11KV 100")
    elif c == "add-supplier":
        inv.add_supplier(conn, args.name, args.phone, args.city)
        print(f"Supplier added: {args.name}")
    elif c == "add-item":
        inv.add_item(conn, args.sku, args.name, args.unit, args.item_type, args.reorder, args.cost)
        print(f"Item added: {args.sku.upper()}")
    elif c == "adjust":
        inv.adjust_stock(conn, args.sku, args.qty, args.note)
        print(f"Stock adjusted: {args.sku.upper()} {args.qty:+g}")
    elif c == "add-bom":
        inv.set_bom_line(conn, args.product, args.component, args.qty)
        print(f"BOM updated: {args.product.upper()} needs {args.qty:g} of {args.component.upper()} per unit")
    elif c == "show-bom":
        print_table(inv.get_bom(conn, args.product))
    elif c == "create-po":
        po_no = inv.create_po(conn, args.supplier, [parse_line(x) for x in args.line])
        print(f"Created {po_no} (status OPEN)")
    elif c == "receive-po":
        inv.receive_po(conn, args.po_no)
        print(f"Received {args.po_no.upper()}: stock updated")
    elif c == "cancel-po":
        inv.cancel_po(conn, args.po_no)
        print(f"Cancelled {args.po_no.upper()}")
    elif c == "list-pos":
        print_table(reports.po_rows(conn))
    elif c == "work-order":
        wo_no = inv.create_work_order(conn, args.product, args.qty)
        print(f"Completed {wo_no}: produced {args.qty:g} x {args.product.upper()}")
    elif c == "stock":
        print_table(reports.stock_rows(conn))
    elif c == "low-stock":
        print_table(reports.low_stock_rows(conn))
    elif c == "valuation":
        rows = reports.valuation_rows(conn)
        print_table(rows)
        print(f"\nTotal stock value: {sum(r['value'] for r in rows):,.2f}")
    elif c == "purchase-summary":
        print_table(reports.purchase_summary_rows(conn))
    elif c == "ledger":
        print_table(reports.ledger_rows(conn, args.sku, args.limit))
    elif c == "verify":
        bad = reports.verify_ledger(conn)
        if bad:
            print("MISMATCH between stock and ledger:")
            print_table(bad)
            sys.exit(2)
        print("OK: stock of every item matches its ledger.")
    elif c == "import-items":
        res = inv.import_items_csv(conn, args.csv_path, args.errors)
        print(f"Imported {res.added} item(s), rejected {len(res.errors)}")
        for line_no, sku, msg in res.errors:
            print(f"  line {line_no} [{sku or '-'}]: {msg}")
        if res.errors:
            print(f"Error report saved: {args.errors}")
    elif c == "export-excel":
        print(f"Excel report saved: {reports.export_excel(conn, args.out)}")
    elif c == "export-json":
        print(f"JSON saved: {reports.export_json(conn, args.out)}")
    elif c == "daily-job":
        path, low = reports.daily_job(conn, args.out_dir)
        print(f"Report saved: {path}")
        print(f"Low-stock items: {len(low)}")
        for r in low:
            print(f"  {r['sku']}: {r['stock_qty']:g} (reorder level {r['reorder_level']:g})")


def main(argv=None):
    args = build_parser().parse_args(argv)
    logging.basicConfig(
        filename=args.log, level=logging.INFO, force=True,
        format="%(asctime)s %(levelname)s %(message)s",
    )
    conn = db.get_conn(args.db)
    db.init_db(conn)
    try:
        run(args, conn)
    except ERPError as e:
        print(f"Error: {e}", file=sys.stderr)
        sys.exit(1)
    finally:
        conn.close()


if __name__ == "__main__":
    main()
