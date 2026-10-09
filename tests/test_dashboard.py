from pathlib import Path

import pytest
from streamlit.testing.v1 import AppTest

from mini_erp import db, demo

APP = str(Path(__file__).resolve().parent.parent / "dashboard.py")
PAGES = ["Dashboard", "Purchase Orders", "Production", "Stock & Ledger", "Master Data", "Reports & Import"]


def open_app(path):
    return AppTest.from_file(APP, default_timeout=30).run()


@pytest.fixture
def db_path(tmp_path, monkeypatch):
    path = str(tmp_path / "ui.db")
    monkeypatch.setenv("MINI_ERP_DB", path)
    return path


@pytest.fixture
def app(db_path):
    c = db.get_conn(db_path)
    db.init_db(c)
    demo.load_demo(c)
    c.close()
    return open_app(db_path)


def go(at, page):
    at.sidebar.radio(key="page").set_value(page).run()
    return at


def stock(db_path, sku):
    c = db.get_conn(db_path)
    try:
        return c.execute("SELECT stock_qty FROM items WHERE sku = ?", (sku,)).fetchone()[0]
    finally:
        c.close()


def test_dashboard_shows_metrics_and_passes_ledger_check(app):
    assert not app.exception
    assert len(app.metric) == 4
    assert any("Ledger check passed" in s.value for s in app.success)


@pytest.mark.parametrize("page", PAGES)
def test_every_page_loads_without_errors(app, page):
    assert not go(app, page).exception


def test_empty_database_offers_demo_data(db_path):
    at = open_app(db_path)
    assert at.button(key="load_demo") is not None
    at.button(key="load_demo").click().run()
    assert not at.exception and len(at.metric) == 4


def test_short_work_order_is_blocked_with_clear_message(app, db_path):
    go(app, "Production")
    app.selectbox(key="wo_product").set_value("PT-11KV")
    app.number_input(key="wo_qty").set_value(100.0)
    app.button(key="wo_run").click().run()
    assert any("Cannot produce" in e.value for e in app.error)
    assert stock(db_path, "PT-11KV") == 0 and stock(db_path, "CU-WIRE") == 400


def test_work_order_runs_when_stock_is_enough(app, db_path):
    go(app, "Production")
    app.selectbox(key="wo_product").set_value("CT-11KV")
    app.number_input(key="wo_qty").set_value(1.0)
    app.button(key="wo_run").click().run()
    assert any("Completed WO-0002" in s.value for s in app.success)
    assert stock(db_path, "CT-11KV") == 41


def test_create_and_receive_po_from_the_ui(app, db_path):
    go(app, "Purchase Orders")
    app.number_input(key="po_qty").set_value(100.0)
    app.button(key="po_add").click().run()
    app.button(key="po_create").click().run()
    assert any("Created PO-0005" in s.value for s in app.success)
    go(app, "Purchase Orders")
    app.selectbox(key="po_select").set_value("PO-0005")
    app.button(key="po_receive").click().run()
    assert any("PO-0005 received" in s.value for s in app.success)


def test_master_data_forms_add_supplier_and_item(db_path):
    at = open_app(db_path)
    go(at, "Master Data")
    at.text_input(key="sup_name").set_value("Test Supplier")
    at.button[0].click().run()          # "Add supplier" submit button
    assert any("Test Supplier" in s.value for s in at.success)
    go(at, "Master Data")
    at.text_input(key="item_sku").set_value("x-1")
    at.text_input(key="item_name").set_value("Test Item")
    at.button[1].click().run()          # "Add item" submit button
    assert any("X-1" in s.value for s in at.success)
    assert stock(db_path, "X-1") == 0
