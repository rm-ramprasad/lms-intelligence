"""Deterministic period parsing. All relative phrases resolve against L, the latest snapshot date.

Returns a DatePeriod-compatible dict plus the token positions it consumed. Fiscal year starts
1 April and is named by its end year; quarters are fiscal (D1); YTD and "last year" are fiscal (D2).
"""
import calendar
import re
from dataclasses import dataclass, field
from datetime import date

FY_TOKEN = re.compile(r"^fy(\d{2}|\d{4})(?:-(\d{2}|\d{4}))?$")
YEAR_RANGE = re.compile(r"^(\d{4})-(\d{2})$")           # 2025-26 (FY) or 2026-03 (month)
MON_YY = re.compile(r"^([a-z]+)-(\d{2}|\d{4})$")       # mar-26, mar-2026
Q_TOKEN = re.compile(r"^q([1-4])(?:fy(\d{2}|\d{4}))?$")
YEAR = re.compile(r"^(19|20)\d{2}$")
YY = re.compile(r"^\d{2}$")
INT = re.compile(r"^\d{1,2}$")


@dataclass
class PeriodMatch:
    period: dict
    consumed: set[int] = field(default_factory=set)
    label: str = ""


def _month_end(y: int, m: int) -> date:
    return date(y, m, calendar.monthrange(y, m)[1])


def _year4(tok: str) -> int:
    return int(tok) if len(tok) == 4 else 2000 + int(tok)


def fiscal_year_of(d: date) -> int:
    return d.year + 1 if d.month >= 4 else d.year


def _fq_range(q: int, fy: int) -> tuple[date, date]:
    start_month = 4 + 3 * (q - 1)
    y = fy - 1 if start_month <= 12 else fy
    m = start_month if start_month <= 12 else start_month - 12
    end_m = m + 2
    return date(y, m, 1), _month_end(y, end_m)


def _fq_of(d: date) -> tuple[int, int]:
    return (d.month - 4) % 12 // 3 + 1, fiscal_year_of(d)


def _shift_quarter(q: int, fy: int, delta: int) -> tuple[int, int]:
    idx = fy * 4 + (q - 1) + delta
    return idx % 4 + 1, idx // 4


class PeriodParser:
    def __init__(self, language: dict):
        p = language["periods"]
        self.months = language["months"]
        self.bare_ignore = set(language.get("bare_month_ignore", []))
        self.phrases = {k: [tuple(s.split()) for s in p[k]]
                        for k in ("latest", "this_quarter", "last_quarter", "this_fiscal_year", "last_fiscal_year")}
        self.last_n = set(p["last_n_months"])
        self.range_start = set(p["range_start"])
        self.range_join = set(p["range_join"])
        self.trend_default_months = p["trend_default_months"]

    # ---- single month expression at i: returns (year, month, n_tokens) ----------------------
    def _month_at(self, t: list[str], i: int, anchor: date, allow_bare: bool = True):
        tok = t[i]
        m = MON_YY.match(tok)
        if m and m.group(1) in self.months:
            return _year4(m.group(2)), self.months[m.group(1)], 1
        m = YEAR_RANGE.match(tok)
        if m and 1 <= int(m.group(2)) <= 12 and (i == 0 or t[i - 1] != "fy"):
            return int(m.group(1)), int(m.group(2)), 1
        if tok in self.months:
            mon = self.months[tok]
            if i + 1 < len(t) and (YEAR.match(t[i + 1]) or YY.match(t[i + 1])):
                return _year4(t[i + 1]), mon, 2
            if allow_bare and tok not in self.bare_ignore:
                y = anchor.year if mon <= anchor.month else anchor.year - 1   # most recent such month <= L
                return y, mon, 1
        return None

    def _phrase_at(self, t: list[str], i: int, kind: str) -> int:
        for ph in sorted(self.phrases[kind], key=len, reverse=True):
            if tuple(t[i:i + len(ph)]) == ph:
                return len(ph)
        return 0

    def parse(self, t: list[str], anchor: date) -> PeriodMatch | None:
        for i in range(len(t)):
            r = self._parse_at(t, i, anchor)
            if r:
                return r
        return None

    def _parse_at(self, t: list[str], i: int, L: date) -> PeriodMatch | None:
        tok = t[i]
        rng = lambda s, e: {"kind": "range", "start": s.isoformat(), "end": e.isoformat()}  # noqa: E731

        # between <month> and <month>
        if tok in self.range_start:
            a = self._month_at(t, i + 1, L) if i + 1 < len(t) else None
            if a:
                j = i + 1 + a[2]
                if j < len(t) and t[j] in self.range_join and j + 1 < len(t):
                    b = self._month_at(t, j + 1, L)
                    if b:
                        s, e = date(a[0], a[1], 1), _month_end(b[0], b[1])
                        if s <= e:
                            return PeriodMatch(rng(s, e), set(range(i, j + 1 + b[2])), f"{s:%b %Y} to {e:%b %Y}")

        # relative phrases
        for kind in ("this_quarter", "last_quarter", "this_fiscal_year", "last_fiscal_year", "latest"):
            n = self._phrase_at(t, i, kind)
            if not n:
                continue
            used = set(range(i, i + n))
            if kind == "latest":
                return PeriodMatch({"kind": "latest"}, used, "latest")
            if kind in ("this_quarter", "last_quarter"):
                q, fy = _fq_of(L)
                if kind == "last_quarter":
                    q, fy = _shift_quarter(q, fy, -1)
                s, e = _fq_range(q, fy)
                return PeriodMatch(rng(s, min(e, L)), used, f"Q{q} FY{fy % 100:02d}")
            fy = fiscal_year_of(L)
            if kind == "this_fiscal_year":
                return PeriodMatch(rng(date(fy - 1, 4, 1), L), used, f"FY {fy} to date")
            return PeriodMatch({"kind": "fiscal_year", "year": fy - 1}, used, f"FY {fy - 1}")

        # last/past N months
        if tok in self.last_n and i + 2 < len(t) and INT.match(t[i + 1]) and t[i + 2] in ("months", "month"):
            n = int(t[i + 1])
            if 1 <= n <= 120:
                y, m = L.year, L.month - (n - 1)
                while m < 1:
                    y, m = y - 1, m + 12
                return PeriodMatch(rng(date(y, m, 1), L), {i, i + 1, i + 2}, f"last {n} months")

        # fiscal year: fy26, fy2026, fy25-26, "fy 2026", "fy 2025-26", "fy 26"
        m = FY_TOKEN.match(tok)
        if m:
            fy = _year4(m.group(2)) if m.group(2) else _year4(m.group(1))
            if m.group(2) and len(m.group(2)) == 2 and len(m.group(1)) == 4:
                fy = (int(m.group(1)) // 100) * 100 + int(m.group(2))
            return PeriodMatch({"kind": "fiscal_year", "year": fy}, {i}, f"FY {fy}")
        # "fy 2026", "fy 26", "fy 2025-26", "fiscal year 2026", "financial year 2025-26"
        j = None
        if tok == "fy":
            j = i + 1
        elif tok in ("fiscal", "financial") and i + 1 < len(t) and t[i + 1] == "year":
            j = i + 2
        if j is not None and j < len(t):
            nxt = t[j]
            yr = YEAR_RANGE.match(nxt)
            fy = None
            if yr:
                fy = (int(yr.group(1)) // 100) * 100 + int(yr.group(2))
            elif YEAR.match(nxt) or YY.match(nxt):
                fy = _year4(nxt)
            if fy is not None:
                return PeriodMatch({"kind": "fiscal_year", "year": fy}, set(range(i, j + 1)), f"FY {fy}")

        # fiscal quarter: q4, q4fy26, "q4 fy26", "q4 fy 2026"
        m = Q_TOKEN.match(tok)
        if m:
            q = int(m.group(1))
            used = {i}
            fy = _year4(m.group(2)) if m.group(2) else None
            if fy is None and i + 1 < len(t):
                f = FY_TOKEN.match(t[i + 1])
                if f and not f.group(2):
                    fy, used = _year4(f.group(1)), {i, i + 1}
                elif t[i + 1] == "fy" and i + 2 < len(t) and (YEAR.match(t[i + 2]) or YY.match(t[i + 2])):
                    fy, used = _year4(t[i + 2]), {i, i + 1, i + 2}
            if fy is None:   # most recent fiscal quarter q that has started by L
                lq, lfy = _fq_of(L)
                fy = lfy if q <= lq else lfy - 1
            s, e = _fq_range(q, fy)
            return PeriodMatch(rng(s, e), used, f"Q{q} FY{fy % 100:02d}")

        # month
        mm = self._month_at(t, i, L)
        if mm:
            y, mon, n = mm
            return PeriodMatch({"kind": "month", "month": f"{y:04d}-{mon:02d}"}, set(range(i, i + n)),
                               f"{date(y, mon, 1):%b %Y}")

        # bare calendar year: "2025"
        if YEAR.match(tok) and (i == 0 or t[i - 1] not in ("fy",)):
            y = int(tok)
            return PeriodMatch({"kind": "calendar_year", "year": y}, {i}, str(y))
        return None

    def trend_default(self, L: date) -> dict:
        n = self.trend_default_months
        y, m = L.year, L.month - (n - 1)
        while m < 1:
            y, m = y - 1, m + 12
        return {"kind": "range", "start": date(y, m, 1).isoformat(), "end": L.isoformat()}
