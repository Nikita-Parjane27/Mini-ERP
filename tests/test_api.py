import pytest
from fastapi.testclient import TestClient

from mini_erp import db, inventory as inv
from mini_erp.api import app


@pytest.fixture
def client(tmp_path, monkeypatch):
    path = str(tmp_path / "api.db")
    monkeypatch.setenv("MINI_ERP_DB", path)
    c = db.get_conn(path)
    db.init_db(c)
    inv.add_supplier(c, "Shree Metals")
    inv.add_item(c, "CU", "Copper Wire", "kg", "RAW", reorder_level=50)
    inv.receive_po(c, inv.create_po(c, "Shree Metals", [("CU", 40, 700)]))
    c.close()
    return TestClient(app)


def test_health(client):
    assert client.get("/health").json() == {"status": "ok"}


def test_stock_endpoint_returns_json_list(client):
    data = client.get("/stock").json()
    assert data[0]["sku"] == "CU" and data[0]["stock_qty"] == 40 and data[0]["status"] == "LOW"


def test_single_item_and_404(client):
    assert client.get("/stock/cu").json()["name"] == "Copper Wire"
    assert client.get("/stock/NOPE").status_code == 404


def test_low_stock_valuation_and_ledger(client):
    assert client.get("/low-stock").json()[0]["shortfall"] == 10
    assert client.get("/valuation").json()["total_value"] == 28000
    assert client.get("/ledger?sku=CU").json()[0]["movement_type"] == "PO_RECEIPT"
