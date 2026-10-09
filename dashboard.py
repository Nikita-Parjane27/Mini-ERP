"""Streamlit dashboard for the Mini ERP.   Run:  streamlit run dashboard.py"""
import json
import os
import tempfile

import pandas as pd
import streamlit as st

from mini_erp import db, demo, inventory as inv, reports
from mini_erp.errors import ERPError

st.set_page_config(page_title="Mini ERP", page_icon="🏭", layout="wide")

PAGES = ["Dashboard", "Purchase Orders", "Production", "Stock & Ledger", "Master Data", "Reports & Import"]
XLSX_MIME = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"


# ---------- small helpers ----------

def table(rows):
    return pd.DataFrame([dict(r) for r in rows])


def flash(kind, message):
    """Show a message on the next run (after st.rerun refreshes the tables)."""
    st.session_state["flash"] = (kind, message)


def show_flash():
    kind_msg = st.session_state.pop("flash", None)
    if kind_msg:
        getattr(st, kind_msg[0])(kind_msg[1])


def act(fn, success_message, *args):
    """Run a business action. Business-rule errors are shown to the user, never as a crash."""
    try:
        result = fn(*args)
    except ERPError as e:
        st.error(str(e))
        return
    flash("success", success_message(result) if callable(success_message) else success_message)
    st.rerun()


def mark_status(df):
    if "status" in df.columns:
        df["status"] = df["status"].map({"LOW": "🔴 LOW", "OK": "🟢 OK", "SHORT": "🔴 SHORT"}).fillna(df["status"])
    return df


# ---------- pages ----------

def page_dashboard(conn):
    st.header("Dashboard")
    stock = reports.stock_rows(conn)
    if not stock:
        st.info("The database is empty. Load demo data to explore, or add items under Master Data.")
        if st.button("Load demo data", key="load_demo"):
            act(demo.load_demo, "Demo data loaded", conn)
        return

    low = reports.low_stock_rows(conn)
    value = sum(r["value"] for r in reports.valuation_rows(conn))
    open_pos = conn.execute("SELECT COUNT(*) FROM purchase_orders WHERE status = 'OPEN'").fetchone()[0]
    c1, c2, c3, c4 = st.columns(4)
    c1.metric("Items", len(stock))
    c2.metric("Low-stock items", len(low))
    c3.metric("Open purchase orders", open_pos)
    c4.metric("Stock value", f"₹{value:,.0f}")

    if reports.verify_ledger(conn):
        st.error("Ledger check FAILED: stock does not match the ledger for some items.")
    else:
        st.success("Ledger check passed: stock of every item matches its ledger.")

    st.subheader("Low-stock alerts")
    if low:
        st.dataframe(table(low), hide_index=True)
    else:
        st.success("All items are above their reorder level.")

    st.subheader("Stock vs reorder level")
    st.bar_chart(table(stock).set_index("sku")[["stock_qty", "reorder_level"]])


def page_purchase_orders(conn):
    st.header("Purchase Orders")
    pos = reports.po_rows(conn)
    if pos:
        st.dataframe(table(pos), hide_index=True)
    else:
        st.info("No purchase orders yet.")

    left, right = st.columns(2)
    with left:
        st.subheader("Create purchase order")
        suppliers = [r["name"] for r in conn.execute("SELECT name FROM suppliers ORDER BY name")]
        skus = [r["sku"] for r in conn.execute("SELECT sku FROM items ORDER BY sku")]
        if not suppliers or not skus:
            st.warning("Add at least one supplier and one item under Master Data first.")
        else:
            supplier = st.selectbox("Supplier", suppliers, key="po_supplier")
            lines = st.session_state.setdefault("po_lines", [])
            a, b, c = st.columns([2, 1, 1])
            sku = a.selectbox("Item", skus, key="po_sku")
            qty = b.number_input("Qty", min_value=0.0, value=1.0, step=1.0, key="po_qty")
            rate = c.number_input("Rate", min_value=0.0, value=0.0, step=1.0, key="po_rate")
            if st.button("Add line", key="po_add"):
                lines.append((sku, qty, rate))
                st.rerun()
            if lines:
                st.table(pd.DataFrame(lines, columns=["sku", "qty", "rate"]))
                b1, b2 = st.columns(2)
                if b1.button("Create PO", key="po_create"):
                    try:
                        po_no = inv.create_po(conn, supplier, lines)
                    except ERPError as e:
                        st.error(str(e))
                    else:
                        st.session_state["po_lines"] = []
                        flash("success", f"Created {po_no} (OPEN)")
                        st.rerun()
                if b2.button("Clear lines", key="po_clear"):
                    st.session_state["po_lines"] = []
                    st.rerun()

    with right:
        st.subheader("Receive or cancel")
        open_pos = [r["po_no"] for r in pos if r["status"] == "OPEN"]
        if not open_pos:
            st.info("No open purchase orders.")
        else:
            po_no = st.selectbox("Open PO", open_pos, key="po_select")
            b1, b2 = st.columns(2)
            if b1.button("Receive goods", key="po_receive"):
                act(inv.receive_po, f"{po_no} received. Stock updated.", conn, po_no)
            if b2.button("Cancel PO", key="po_cancel"):
                act(inv.cancel_po, f"{po_no} cancelled.", conn, po_no)


def page_production(conn):
    st.header("Production (Work Orders)")
    products = [r["sku"] for r in conn.execute("SELECT sku FROM items WHERE item_type = 'FINISHED' ORDER BY sku")]
    if not products:
        st.info("Add a FINISHED item and its BOM under Master Data first.")
        return
    product = st.selectbox("Product", products, key="wo_product")
    qty = st.number_input("Quantity to produce", min_value=1.0, value=1.0, step=1.0, key="wo_qty")

    needs = inv.bom_requirements(conn, product, qty)
    if not needs:
        st.warning("This product has no BOM yet. Add one under Master Data.")
    else:
        st.dataframe(mark_status(table(needs)), hide_index=True)
        short = [n for n in needs if n["status"] == "SHORT"]
        if short:
            st.warning(f"Short on {len(short)} component(s). The work order will be blocked.")
        else:
            st.success("All components are available.")

    if st.button("Run work order", key="wo_run"):
        act(inv.create_work_order, lambda wo: f"Completed {wo}: produced {qty:g} x {product}", conn, product, qty)


def page_stock(conn):
    st.header("Stock & Ledger")
    st.subheader("Current stock")
    st.dataframe(mark_status(table(reports.stock_rows(conn))), hide_index=True)

    st.subheader("Stock ledger (newest first)")
    skus = ["All items"] + [r["sku"] for r in conn.execute("SELECT sku FROM items ORDER BY sku")]
    choice = st.selectbox("Filter by item", skus, key="ledger_sku")
    rows = reports.ledger_rows(conn, None if choice == "All items" else choice, limit=200)
    if rows:
        st.dataframe(table(rows).sort_values("id", ascending=False), hide_index=True)
    else:
        st.info("No stock movements yet.")


def page_master(conn):
    st.header("Master Data")
    tab_s, tab_i, tab_b, tab_a = st.tabs(["Supplier", "Item", "BOM", "Opening stock / adjustment"])

    with tab_s, st.form("supplier_form", clear_on_submit=True):
        name = st.text_input("Supplier name", key="sup_name")
        phone = st.text_input("Phone", key="sup_phone")
        city = st.text_input("City", key="sup_city")
        if st.form_submit_button("Add supplier"):
            act(inv.add_supplier, f"Supplier '{name}' added", conn, name, phone or None, city or None)

    with tab_i, st.form("item_form", clear_on_submit=True):
        c1, c2 = st.columns(2)
        sku = c1.text_input("SKU", key="item_sku")
        name = c2.text_input("Name", key="item_name")
        unit = c1.text_input("Unit", value="pcs", key="item_unit")
        item_type = c2.selectbox("Type", ["RAW", "FINISHED"], key="item_type")
        reorder = c1.number_input("Reorder level", min_value=0.0, step=1.0, key="item_reorder")
        cost = c2.number_input("Unit cost", min_value=0.0, step=1.0, key="item_cost")
        if st.form_submit_button("Add item"):
            act(inv.add_item, f"Item '{sku.upper()}' added", conn, sku, name, unit, item_type, reorder, cost)

    with tab_b:
        products = [r["sku"] for r in conn.execute("SELECT sku FROM items WHERE item_type = 'FINISHED' ORDER BY sku")]
        all_skus = [r["sku"] for r in conn.execute("SELECT sku FROM items ORDER BY sku")]
        if not products:
            st.info("Add a FINISHED item first.")
        else:
            product = st.selectbox("Product", products, key="bom_product")
            bom = inv.get_bom(conn, product)
            st.dataframe(table(bom), hide_index=True) if bom else st.caption("No components yet.")
            with st.form("bom_form", clear_on_submit=True):
                component = st.selectbox("Component", [s for s in all_skus if s != product], key="bom_component")
                qty = st.number_input("Quantity per unit", min_value=0.0, value=1.0, step=0.5, key="bom_qty")
                if st.form_submit_button("Add / update component"):
                    act(inv.set_bom_line, f"{component} added to BOM of {product}", conn, product, component, qty)

    with tab_a, st.form("adjust_form", clear_on_submit=True):
        skus = [r["sku"] for r in conn.execute("SELECT sku FROM items ORDER BY sku")]
        sku = st.selectbox("Item", skus, key="adj_sku") if skus else None
        qty = st.number_input("Quantity (+ add, - remove)", value=0.0, step=1.0, key="adj_qty")
        note = st.text_input("Reason", value="Opening stock", key="adj_note")
        if st.form_submit_button("Apply adjustment") and sku:
            act(inv.adjust_stock, f"Adjusted {sku} by {qty:+g}", conn, sku, qty, note)


def page_reports(conn):
    st.header("Reports & Import")

    st.subheader("Download reports")
    with tempfile.TemporaryDirectory() as tmp:
        xlsx = open(reports.export_excel(conn, os.path.join(tmp, "erp_report.xlsx")), "rb").read()
        js = open(reports.export_json(conn, os.path.join(tmp, "stock.json")), "rb").read()
    c1, c2 = st.columns(2)
    c1.download_button("Excel report", xlsx, "erp_report.xlsx", XLSX_MIME, key="dl_excel")
    c2.download_button("JSON export", js, "stock.json", "application/json", key="dl_json")

    st.subheader("Import items from CSV")
    st.caption("Required columns: sku, name. Optional: unit, item_type, reorder_level, unit_cost. "
               "Good rows are loaded; bad rows are rejected with a reason.")
    uploaded = st.file_uploader("CSV file", type="csv", key="csv_upload")
    if uploaded and st.button("Import file", key="do_import"):
        with tempfile.TemporaryDirectory() as tmp:
            src, err = os.path.join(tmp, "in.csv"), os.path.join(tmp, "errors.csv")
            with open(src, "wb") as f:
                f.write(uploaded.getvalue())
            try:
                res = inv.import_items_csv(conn, src, err)
            except ERPError as e:
                st.error(str(e))
                return
            err_bytes = open(err, "rb").read() if res.errors else None
        st.session_state["import_result"] = (res.added, res.errors, err_bytes)

    if "import_result" in st.session_state:
        added, errors, err_bytes = st.session_state["import_result"]
        st.success(f"Imported {added} item(s). Rejected {len(errors)} row(s).")
        if errors:
            st.dataframe(pd.DataFrame(errors, columns=["line", "sku", "error"]), hide_index=True)
            st.download_button("Download error report", err_bytes, "import_errors.csv", "text/csv", key="dl_errors")


PAGE_FUNCS = {
    "Dashboard": page_dashboard,
    "Purchase Orders": page_purchase_orders,
    "Production": page_production,
    "Stock & Ledger": page_stock,
    "Master Data": page_master,
    "Reports & Import": page_reports,
}

st.sidebar.title("🏭 Mini ERP")
db_path = st.sidebar.text_input("Database file", os.environ.get("MINI_ERP_DB", "erp.db"), key="db_path")
page = st.sidebar.radio("Go to", PAGES, key="page")

conn = db.get_conn(db_path, check_same_thread=False)
db.init_db(conn)
try:
    show_flash()
    PAGE_FUNCS[page](conn)
finally:
    conn.close()
