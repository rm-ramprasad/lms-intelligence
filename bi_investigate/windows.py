"""Comparison windows. Snapshot KPIs compare month-end positions (window end); flow KPIs compare sums over
the window's months. Fiscal year Apr-Mar, named by end year; quarters are fiscal."""
import calendar
from datetime import date

from bi_service.formatting import fiscal_quarter

from .models import Comparison, Window

MONTHS = ("Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec")


def month_end(y: int, m: int) -> date:
    return date(y, m, calendar.monthrange(y, m)[1])


def shift_months(d: date, n: int) -> date:
    """Month-end n months from d's month (n may be negative)."""
    idx = d.year * 12 + (d.month - 1) + n
    return month_end(idx // 12, idx % 12 + 1)


def month_start(d: date) -> date:
    return date(d.year, d.month, 1)


def _mlabel(d: date) -> str:
    return f"{MONTHS[d.month - 1]} {d.year}"


def _qlabel(d: date) -> str:
    q, fy = fiscal_quarter(d)
    return f"Q{q} FY{fy % 100:02d}"


def fy_start(d: date) -> date:
    return date(d.year if d.month >= 4 else d.year - 1, 4, 1)


def comparison(kind: str, as_of: date) -> Comparison:
    if kind == "mom":
        a = shift_months(as_of, -1)
        return Comparison(kind=kind, a=Window(label=_mlabel(a), start=month_start(a), end=a),
                          b=Window(label=_mlabel(as_of), start=month_start(as_of), end=as_of))
    if kind == "qoq":   # trailing 3 months vs the 3 before (quarter-end positions when as_of is a quarter-end)
        b_start = month_start(shift_months(as_of, -2))
        a_end = shift_months(as_of, -3)
        a_start = month_start(shift_months(as_of, -5))
        return Comparison(kind=kind, a=Window(label=_qlabel(a_end), start=a_start, end=a_end),
                          b=Window(label=_qlabel(as_of), start=b_start, end=as_of))
    if kind == "yoy":
        a = shift_months(as_of, -12)
        return Comparison(kind=kind, a=Window(label=_mlabel(a), start=month_start(a), end=a),
                          b=Window(label=_mlabel(as_of), start=month_start(as_of), end=as_of))
    if kind == "fytd":
        fs = fy_start(as_of)
        a_end = shift_months(as_of, -12)
        fy = fs.year + 1
        return Comparison(kind=kind, a=Window(label=f"FY{(fy - 1) % 100:02d} YTD", start=date(fs.year - 1, 4, 1), end=a_end),
                          b=Window(label=f"FY{fy % 100:02d} YTD", start=fs, end=as_of))
    raise ValueError(kind)


def available(c: Comparison, first_date: date) -> bool:
    return c.a.start >= month_start(first_date)
