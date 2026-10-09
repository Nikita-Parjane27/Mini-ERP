"""Small read-only JSON API. Run:  uvicorn mini_erp.api:app --reload
Database file comes from the MINI_ERP_DB environment variable (default: erp.db)."""
import os

from fastapi import Depends, FastAPI, HTTPException

from . import __version__, db, reports

app = FastAPI(title="Mini ERP API", version=__version__)


def get_db():
    conn = db.get_conn(os.environ.get("MINI_ERP_DB", db.DEFAULT_DB), check_same_thread=False)
    db.init_db(conn)
    try:
        yield conn
    finally:
        conn.close()


@app.get("/health")
def health():
    return {"status": "ok"}


@app.get("/stock")
def stock(conn=Depends(get_db)):
    return [dict(r) for r in reports.stock_rows(conn)]


@app.get("/stock/{sku}")
def stock_item(sku: str, conn=Depends(get_db)):
    row = reports.get_stock(conn, sku)
    if not row:
        raise HTTPException(status_code=404, detail=f"Item '{sku}' not found")
    return dict(row)


@app.get("/low-stock")
def low_stock(conn=Depends(get_db)):
    return [dict(r) for r in reports.low_stock_rows(conn)]


@app.get("/valuation")
def valuation(conn=Depends(get_db)):
    rows = [dict(r) for r in reports.valuation_rows(conn)]
    return {"total_value": round(sum(r["value"] for r in rows), 2), "items": rows}


@app.get("/purchase-summary")
def purchase_summary(conn=Depends(get_db)):
    return [dict(r) for r in reports.purchase_summary_rows(conn)]


@app.get("/ledger")
def ledger(sku: str | None = None, limit: int = 100, conn=Depends(get_db)):
    return [dict(r) for r in reports.ledger_rows(conn, sku=sku, limit=limit)]
