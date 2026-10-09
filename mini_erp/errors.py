class ERPError(Exception):
    """A business-rule error (bad SKU, not enough stock, wrong PO status, etc.)."""
