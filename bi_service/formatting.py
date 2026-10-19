"""Display formatting for result values and period labels (presentation only; raw values are unchanged).

Approved conventions (2026-10-19): amounts in ₹ Cr with 2 decimals, ₹ L below 1 Cr (D3);
fiscal quarters labelled "Q1 FY26" (D1); fiscal years "FY 2026".
"""
from datetime import date
from decimal import Decimal

CRORE = Decimal(10_000_000)
LAKH = Decimal(100_000)
MONTHS = ("Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec")


def format_value(value, fmt: dict) -> str:
    if value is None:
        return "—"
    kind = fmt.get("type")
    if kind == "currency":
        v = Decimal(str(value))
        if abs(v) >= CRORE:
            return f"₹{v / CRORE:,.2f} Cr"
        return f"₹{v / LAKH:,.2f} L"
    if kind == "percent":
        return f"{float(value) * 100:,.{fmt.get('decimals', 2)}f}%"
    if kind == "integer":
        return f"{int(value):,}"
    return str(value)


def fiscal_quarter(d: date) -> tuple[int, int]:
    """(quarter 1-4, fiscal year named by end year) for a date; fiscal year starts 1 April."""
    return (d.month - 4) % 12 // 3 + 1, d.year + 1 if d.month >= 4 else d.year


def period_label(value, grain: str | None) -> str:
    if value is None:
        return "—"
    if grain == "month":
        d = date.fromisoformat(str(value)[:10])
        return f"{MONTHS[d.month - 1]} {d.year}"
    if grain == "quarter":
        q, fy = fiscal_quarter(date.fromisoformat(str(value)[:10]))
        return f"Q{q} FY{fy % 100:02d}"
    if grain == "fiscal_year":
        return f"FY {value}"
    return str(value)


def date_label(value) -> str:
    if value is None:
        return "—"
    d = date.fromisoformat(str(value)[:10])
    return f"{d.day} {MONTHS[d.month - 1]} {d.year}"
