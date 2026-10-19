"""Briefing engine: runs every playbook question for one month-end and assembles a Briefing snapshot.

All numbers come from governed templates (sql_templates) and exact analyses (analyses). Findings are ranked
by materiality x unusualness; the top ones become "What leadership should look at". Failures or
reconciliation errors make an analysis unavailable (recorded), never silently wrong.
"""
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import date, datetime, timezone

from bi_service.formatting import format_value
from bi_service.semantic_loader import SemanticModel

from . import analyses as A
from .models import Briefing, ChangeSummary, Comparison, Finding, KpiTile, QuestionResult, Unusual
from .playbook import Playbook, Question
from .sql_templates import Templates
from .windows import available, comparison, shift_months

BLANK = "(blank)"
MIN_ROLL_ACCOUNTS = 5   # roll-rate findings need at least this many accounts in the transition
KIND_LABEL = {"mom": "vs last month", "qoq": "vs previous quarter", "yoy": "vs last year", "fytd": "vs last year to date"}


class RunError(RuntimeError):
    pass


class QueryRunner:
    """Executes governed SQL via bi_service's AthenaExecutor; caches by SQL text for one briefing run."""

    def __init__(self, executor, max_rows: int = 5000):
        self.executor = executor
        self.max_rows = max_rows
        self._cache: dict[str, list[dict]] = {}
        self._lock = threading.Lock()
        self.count = 0
        self.bytes = 0
        self.query_ids: list[str] = []

    def run(self, sql: str) -> list[dict]:
        with self._lock:
            if sql in self._cache:
                return self._cache[sql]
        out = self.executor.execute(sql, max_rows=self.max_rows)
        if out.state != "SUCCEEDED":
            raise RunError(f"{out.state}: {out.error_code}")
        names = [n for n, _ in out.columns]
        rows = [dict(zip(names, r)) for r in out.rows]
        with self._lock:
            self._cache[sql] = rows
            self.count += 1
            self.bytes += out.bytes_scanned or 0
            if out.query_id:
                self.query_ids.append(out.query_id)
        return rows


class BriefingEngine:
    def __init__(self, model: SemanticModel, playbook: Playbook, runner: QueryRunner, workers: int = 4):
        self.model = model
        self.pb = playbook
        self.runner = runner
        self.t = Templates(model)
        self.workers = workers
        self.first_date: date | None = None

    # ---- helpers -----------------------------------------------------------------------------------
    def name(self, metric_id: str) -> str:
        return self.model.metrics[metric_id].name

    def is_ratio(self, metric_id: str) -> bool:
        return self.model.metrics[metric_id].measure.aggregation == "ratio"

    def fmt(self, metric_id: str, v) -> str:
        return format_value(v, self.model.metrics[metric_id].format)

    def fmt_delta(self, metric_id: str, d: float | None) -> str:
        if d is None:
            return "—"
        if self.model.metrics[metric_id].format.get("type") == "percent":
            return f"{d * 100:+.2f} pp"
        sign = "+" if d >= 0 else "−"
        return sign + self.fmt(metric_id, abs(d))

    def seg_rows(self, metric_id: str, c: Comparison, dims: list[str]):
        rows = self.runner.run(self.t.segments(metric_id, c, dims))
        n = len(dims)
        totals: dict[str, tuple] = {}
        seg: dict[str, dict[str, list]] = {d: {} for d in dims}
        for r in rows:
            g = r.get("g", 0) if dims else 0
            g = int(g or 0)
            if not dims or g == (1 << n) - 1:
                totals[r["w"]] = (r["num"], r["den"])
                continue
            for i, d in enumerate(dims):
                if not (g >> (n - 1 - i)) & 1:
                    key = str(r[d]) if r[d] is not None else BLANK
                    cell = seg[d].setdefault(key, [None, None, None, None])
                    off = 0 if r["w"] == "A" else 2
                    cell[off], cell[off + 1] = r["num"], r["den"]
        na, da = totals.get("A", (None, None))
        nb, db = totals.get("B", (None, None))
        summary = A.change(na, nb, da, db) if self.is_ratio(metric_id) else A.change(na, nb)
        return summary, {d: {k: tuple(v) for k, v in s.items()} for d, s in seg.items()}

    def rag(self, spec: dict | None, value: float | None) -> str:
        if not spec or value is None:
            return "none"
        worse_high = spec["worse"] == "higher"
        if (value >= spec["red"]) if worse_high else (value <= spec["red"]):
            return "red"
        if (value >= spec["amber"]) if worse_high else (value <= spec["amber"]):
            return "amber"
        return "green"

    @staticmethod
    def importance(u: Unusual | None) -> float:
        if u is None or u.z is None:
            return 0.5 if (u is None or u.rank_of_13 is None) else max(0.2, 1 - (u.rank_of_13 - 1) / 12)
        return min(1.0, 0.3 + abs(u.z) / 3)

    # ---- question types ------------------------------------------------------------------------------
    def kpi_question(self, q: Question, as_of: date) -> QuestionResult:
        kpi = q.kpi
        res = QuestionResult(id=q.id, theme=q.theme, question=q.question, answer="", kpi=kpi,
                             caveats=list(q.caveats), drilldowns=list(q.drilldowns))
        comps = []
        for k in q.comparisons:
            c = comparison(k, as_of)
            if available(c, self.first_date):
                comps.append(c)
            else:
                res.unavailable.append(f"{k}: not enough history")
        if not comps:
            res.answer = "Not enough history for this comparison."
            return res
        primary = comps[0]
        dims = list(self.pb.segment_dimensions) if {"segments", "mix_rate"} & set(q.analyses) else []
        for c in comps:
            summary, seg = self.seg_rows(kpi, c, dims if c is primary else [])
            res.comparisons[c.kind] = summary
            res.comparison_windows[c.kind] = c
            if c is primary and dims and summary.delta is not None:
                for d in dims:
                    try:
                        cs = A.contributions(d, seg[d], self.is_ratio(kpi), summary.delta)
                        res.contributions[d] = cs[:8]
                        if "mix_rate" in q.analyses and self.is_ratio(kpi):
                            res.mix_rate = {**(res.mix_rate or {}), d: A.mix_rate_total(cs)}
                    except A.ReconciliationError as e:
                        res.unavailable.append(f"segments {d}: {e}")
        ps = res.comparisons[primary.kind]
        snapshot = self.model.metrics[kpi].behavior == "snapshot"

        if "num_den" in q.analyses and self.is_ratio(kpi) and None not in (ps.num_a, ps.den_a, ps.num_b, ps.den_b) \
                and ps.den_a and ps.den_b:
            try:
                res.num_den = A.num_den(ps.num_a, ps.den_a, ps.num_b, ps.den_b)
            except A.ReconciliationError as e:
                res.unavailable.append(str(e))
        for kind in ("par90", "npa"):
            if f"bridge_{kind}" in q.analyses and snapshot:
                try:
                    res.bridge = A.bridge(self.runner.run(self.t.flows(primary.a.end, primary.b.end)), kind)
                except A.ReconciliationError as e:
                    res.unavailable.append(str(e))
        if "concentration" in q.analyses and snapshot:
            cm = q.concentration_metric or kpi
            rows = self.runner.run(self.t.concentration(cm, primary.a.end, primary.b.end))
            if rows:
                res.concentration = A.concentration(rows[0])
        if "unusual" in q.analyses:
            res.unusual = self.series(kpi, as_of)

        res.rag = self.rag(q.rag, ps.value_b)
        self.kpi_findings(q, res, primary)
        res.answer = self.kpi_answer(q, res, primary)
        return res

    def series(self, kpi: str, as_of: date) -> Unusual:
        start = max(shift_months(as_of, -24), self.first_date)
        rows = self.runner.run(self.t.trend(kpi, start, as_of))
        pts = []
        for r in rows:
            v = A.ratio(r["num"], r["den"]) if self.is_ratio(kpi) else A.f(r["num"])
            pts.append((str(r["d"])[:10], v))
        return A.unusual(pts)

    def kpi_findings(self, q: Question, res: QuestionResult, primary: Comparison) -> None:
        kpi, ps = res.kpi, res.comparisons[primary.kind]
        imp = self.importance(res.unusual)
        if ps.delta is None:
            return
        direction = "rose" if ps.delta > 0 else "fell" if ps.delta < 0 else "was unchanged"
        unusual_txt = ""
        if res.unusual and res.unusual.rank_of_13 == 1:
            unusual_txt = " (largest monthly move in 13 months)"
        res.findings.append(Finding(
            question_id=q.id, kind="change", kpi=kpi, score=imp * 0.6,
            text=f"{self.name(kpi)} {direction} {self.fmt_delta(kpi, abs(ps.delta)).lstrip('+')} "
                 f"{KIND_LABEL[primary.kind]} to {self.fmt(kpi, ps.value_b)}{unusual_txt}."))
        best = None
        for d, cs in res.contributions.items():
            for c in A.material(cs, self.pb.min_share, self.pb.max_per_dimension):
                if c.segment != BLANK and (best is None or abs(c.share) > abs(best.share)):
                    best = c
        if best is not None and abs(ps.delta) > 0:
            label = self.model.dimensions[best.dimension].label.lower()
            res.findings.append(Finding(
                question_id=q.id, kind="contributor", kpi=kpi, dimension=best.dimension, segment=best.segment,
                score=imp * min(1.0, abs(best.share)),
                text=(f"{best.segment} ({label}) moved {self.name(kpi)} {KIND_LABEL[primary.kind]} by more than the "
                      f"whole net change ({best.share * 100:.0f}%); other segments partly offset it." if abs(best.share) > 1 else
                      f"{best.segment} ({label}) accounted for {best.share * 100:.0f}% of the change in {self.name(kpi)} "
                      f"{KIND_LABEL[primary.kind]}."),
                drilldown={"metric": kpi, "dimensions": [best.dimension]}))
        if res.bridge and res.bridge.steps and res.bridge.closing != res.bridge.opening:
            tot = res.bridge.closing - res.bridge.opening
            step = max(res.bridge.steps, key=lambda s: abs(s.amount))
            share = step.amount / tot if tot else 0
            if abs(share) >= self.pb.min_share:
                what = "PAR 90+" if res.bridge.kind == "par90" else "NPA outstanding"
                res.findings.append(Finding(
                    question_id=q.id, kind="bridge", kpi=kpi, segment=step.label, score=imp * min(1.0, abs(share)),
                    text=(f"{step.label}: {self.fmt_delta('total_outstanding', step.amount)} ({step.accounts} accounts), "
                          + (f"more than the whole net {what} movement ({abs(share) * 100:.0f}%); other flows partly offset it."
                             if abs(share) > 1 else f"{abs(share) * 100:.0f}% of the {what} movement."))))
        if res.mix_rate:
            d, mr = max(res.mix_rate.items(), key=lambda kv: abs(kv[1]["mix"]))
            driver = "mix (riskier segments grew)" if abs(mr["mix"]) > abs(mr["rate"]) else "rate (segments got worse or better)"
            res.findings.append(Finding(
                question_id=q.id, kind="mix_rate", kpi=kpi, dimension=d, score=imp * 0.4,
                text=f"By {self.model.dimensions[d].label.lower()}, the change is driven by {driver}: "
                     f"rate {mr['rate'] * 100:+.2f} pp, mix {mr['mix'] * 100:+.2f} pp."))
        if res.num_den and abs(res.num_den["denominator"]) > abs(res.num_den["numerator"]):
            book = "shrank" if (ps.den_b or 0) < (ps.den_a or 0) else "grew"
            res.findings.append(Finding(
                question_id=q.id, kind="num_den", kpi=kpi, score=imp * 0.6,
                text=f"{self.name(kpi)} moved mainly because the base {book} "
                     f"({res.num_den['denominator'] * 100:+.2f} pp), not because the problem itself changed "
                     f"({res.num_den['numerator'] * 100:+.2f} pp)."))

    def kpi_answer(self, q: Question, res: QuestionResult, primary: Comparison) -> str:
        kpi = res.kpi
        ps = res.comparisons[primary.kind]
        parts = [f"{self.name(kpi)} was {self.fmt(kpi, ps.value_b)} at {primary.b.label}"
                 f" ({self.fmt_delta(kpi, ps.delta)} {KIND_LABEL[primary.kind]})."]
        for k, s in res.comparisons.items():
            if k != primary.kind and s.delta is not None:
                parts.append(f"{self.fmt_delta(kpi, s.delta)} {KIND_LABEL[k]}.")
        drivers = [f for f in res.findings if f.kind in ("bridge", "contributor", "num_den")]
        if drivers:
            parts.append("Main driver: " + max(drivers, key=lambda f: f.score).text)
        elif ps.delta is not None and res.unusual and res.unusual.rank_of_13 and res.unusual.rank_of_13 > 6:
            parts.append("No material change this month compared with recent months.")
        return " ".join(parts)

    def roll_question(self, q: Question, as_of: date) -> QuestionResult:
        res = QuestionResult(id=q.id, theme=q.theme, question=q.question, answer="", kpi=q.kpi,
                             caveats=list(q.caveats), drilldowns=list(q.drilldowns))
        c = comparison("mom", as_of)
        hist_start = max(shift_months(as_of, -13), self.first_date)
        flows = self.runner.run(self.t.flows(c.a.end, c.b.end))
        hist = self.runner.run(self.t.monthly_transitions(hist_start, c.a.end))
        res.roll_rates = A.roll_rates(flows, hist)
        worse = []
        for r in res.roll_rates:
            if A.is_forward(r) and r.avg_rate_12m is not None and r.accounts >= MIN_ROLL_ACCOUNTS                     and r.rate - r.avg_rate_12m >= 0.05:
                worse.append(r)
                res.findings.append(Finding(
                    question_id=q.id, kind="roll_rate",
                    score=min(1.0, (r.rate - r.avg_rate_12m) * 2) * min(1.0, r.accounts / 20),
                    text=f"{r.from_bucket} → {r.to_bucket}: {r.rate * 100:.0f}% of accounts rolled forward "
                         f"vs {r.avg_rate_12m * 100:.0f}% 12-month average ({r.accounts} accounts).",
                    drilldown={"metric": "account_count", "dimensions": ["par_bucket"]}))
        key = [r for r in res.roll_rates if A.is_forward(r) and
               (r.from_bucket, r.to_bucket) in {("Current", "1-30 DPD"), ("61-90 DPD", "90+ DPD")}]
        rates = "; ".join(f"{r.from_bucket} → {r.to_bucket} {r.rate * 100:.0f}% (avg {r.avg_rate_12m * 100:.0f}%)"
                          for r in key if r.avg_rate_12m is not None)
        res.answer = (f"{len(worse)} forward roll rate(s) are materially above their 12-month average in {c.b.label}."
                      if worse else f"No forward roll rate is materially above its 12-month average in {c.b.label}.")
        if rates:
            res.answer += f" Key rates: {rates}."
        res.rag = "red" if len(worse) >= 2 else "amber" if worse else "green"
        return res

    def deterioration_question(self, q: Question, as_of: date) -> QuestionResult:
        res = QuestionResult(id=q.id, theme=q.theme, question=q.question, answer="", kpi=q.kpi,
                             caveats=list(q.caveats), drilldowns=list(q.drilldowns))
        table = []
        for k in q.comparisons:
            c = comparison(k, as_of)
            if not available(c, self.first_date):
                res.unavailable.append(f"{k}: not enough history")
                continue
            res.comparison_windows[k] = c
            for kpi in q.deterioration_kpis:
                _, seg = self.seg_rows(kpi, c, list(q.deterioration_dimensions))
                for d, rows in seg.items():
                    tot_b = sum(A.f(v[3]) or 0 for v in rows.values())
                    for s, (na, da, nb, db) in rows.items():
                        ra, rb = A.ratio(na, da), A.ratio(nb, db)
                        if s != BLANK and ra is not None and rb is not None:
                            table.append({"comparison": k, "kpi": kpi, "dimension": d, "segment": s,
                                          "value_a": ra, "value_b": rb, "delta": rb - ra,
                                          "book_share": (A.f(db) or 0) / tot_b if tot_b else 0.0})
        res.extra["deterioration"] = sorted(table, key=lambda r: -r["delta"])
        top = []
        for kpi in q.deterioration_kpis:
            for d in q.deterioration_dimensions:
                rows = [r for r in table if r["comparison"] == q.comparisons[0] and r["kpi"] == kpi and r["dimension"] == d]
                if rows:
                    w = max(rows, key=lambda r: r["delta"])
                    if w["delta"] > 0:
                        top.append(w)
                        res.findings.append(Finding(
                            question_id=q.id, kind="deterioration", kpi=kpi, dimension=d, segment=w["segment"],
                            score=min(1.0, w["delta"] * 20) * min(1.0, w["book_share"] * 5),
                            text=f"{w['segment']} ({self.model.dimensions[d].label.lower()}, "
                                 f"{w['book_share'] * 100:.0f}% of the book): {self.name(kpi)} "
                                 f"{w['value_a'] * 100:.2f}% → {w['value_b'] * 100:.2f}% ({w['delta'] * 100:+.2f} pp).",
                            drilldown={"metric": kpi, "dimensions": [d]}))
        res.answer = ("Fastest deterioration " + KIND_LABEL[q.comparisons[0]] + ": " +
                      "; ".join(f.text for f in res.findings) if res.findings
                      else "No branch or state deteriorated " + KIND_LABEL[q.comparisons[0]] + ".")
        return res

    def concentration_question(self, q: Question, as_of: date) -> QuestionResult:
        res = QuestionResult(id=q.id, theme=q.theme, question=q.question, answer="", kpi=q.kpi,
                             caveats=list(q.caveats), drilldowns=list(q.drilldowns))
        c = comparison("mom", as_of)
        res.comparison_windows["mom"] = c
        dims = ["state_name", "branch_code", "ticket_size"]
        shares = {}
        for kpi in ("total_outstanding", "npa_outstanding"):
            _, seg = self.seg_rows(kpi, c, dims)
            for d in dims:
                tot = sum(A.f(v[2]) or 0 for v in seg[d].values())
                shares.setdefault(d, {})[kpi] = {s: (A.f(v[2]) or 0) / tot for s, v in seg[d].items() if tot}
        res.extra["shares"] = shares
        texts = []
        st_npa = shares["state_name"]["npa_outstanding"]
        st_book = shares["state_name"]["total_outstanding"]
        if st_npa:
            top3 = sum(sorted(st_npa.values(), reverse=True)[:3])
            texts.append(f"Top 3 states hold {top3 * 100:.0f}% of NPA outstanding")
            s, sh = max(st_npa.items(), key=lambda kv: kv[1] - st_book.get(kv[0], 0))
            gap = sh - st_book.get(s, 0)
            if gap > 0.05:
                res.findings.append(Finding(
                    question_id=q.id, kind="concentration", kpi="npa_outstanding", dimension="state_name", segment=s,
                    score=min(1.0, gap * 3),
                    text=f"{s} holds {sh * 100:.0f}% of NPA but only {st_book.get(s, 0) * 100:.0f}% of the book.",
                    drilldown={"metric": "gross_npa_ratio", "dimensions": ["branch_code"],
                               "filters": [{"dimension": "state_name", "op": "eq", "value": s}]}))
        large = shares["ticket_size"]["npa_outstanding"].get("Above 2.5 Cr")
        if large is not None:
            texts.append(f"large tickets (above ₹2.5 Cr) are {large * 100:.0f}% of NPA")
        if "concentration" in q.analyses:
            rows = self.runner.run(self.t.concentration(q.concentration_metric or "npa_outstanding", c.a.end, c.b.end))
            if rows:
                res.concentration = A.concentration(rows[0])
                cc = res.concentration
                if cc.accounts_for_50pct:
                    texts.append(f"{cc.accounts_for_50pct} accounts explain half of this month's NPA movement")
        answer = ("; ".join(texts) + ".") if texts else "No concentration data."
        res.answer = answer[0].upper() + answer[1:]
        return res

    # ---- orchestration ---------------------------------------------------------------------------------
    def run_question(self, q: Question, as_of: date) -> QuestionResult:
        try:
            if "roll_rates" in q.analyses:
                return self.roll_question(q, as_of)
            if "deterioration" in q.analyses:
                return self.deterioration_question(q, as_of)
            if "concentration_shares" in q.analyses:
                return self.concentration_question(q, as_of)
            return self.kpi_question(q, as_of)
        except RunError as e:
            return QuestionResult(id=q.id, theme=q.theme, question=q.question, kpi=q.kpi,
                                  answer="This answer could not be computed this month.", unavailable=[str(e)])

    def tiles(self, as_of: date) -> list[KpiTile]:
        out = []
        c = comparison("mom", as_of)
        for t in self.pb.tiles:
            kpi = t["kpi"]
            s, _ = self.seg_rows(kpi, c, [])
            out.append(KpiTile(kpi=kpi, label=t["label"], value=s.value_b, delta=s.delta,
                               delta_label=f"{self.fmt_delta(kpi, s.delta)} {KIND_LABEL['mom']}",
                               rag=self.rag(t.get("rag"), s.value_b)))
        return out

    def build(self, as_of: date) -> Briefing:
        started = time.monotonic()
        q0, b0 = self.runner.count, self.runner.bytes
        b = self.runner.run(self.t.bounds())[0]
        self.first_date = date.fromisoformat(str(b["first_date"])[:10])
        last = date.fromisoformat(str(b["last_date"])[:10])
        if as_of > last or as_of < self.first_date:
            raise ValueError(f"as_of {as_of} outside data range {self.first_date}..{last}")
        tiles = self.tiles(as_of)
        work = [q for q in self.pb.questions if "summary" not in q.analyses]
        with ThreadPoolExecutor(max_workers=self.workers) as ex:
            results = list(ex.map(lambda q: self.run_question(q, as_of), work))
        findings = sorted((f for r in results for f in r.findings), key=lambda f: -f.score)
        # De-duplicate findings that tell the same story for different KPIs (e.g. GNPA and PAR 90+ coincide in the demo data)
        names = sorted({m.name for m in self.model.metrics.values()} | {"NPA outstanding", "PAR 90+"}, key=len, reverse=True)

        def story(f: Finding) -> tuple:
            t = f.text
            for n in names:
                t = t.replace(n, "KPI")
            return f.kind, t

        top, seen = [], set()
        for f in findings:
            key = story(f)
            if key in seen:
                continue
            seen.add(key)
            top.append(f)
            if len(top) == 5:
                break
        for q in self.pb.questions:
            if "summary" in q.analyses:
                results.append(QuestionResult(
                    id=q.id, theme=q.theme, question=q.question, findings=top,
                    answer=" ".join(f"{i}. {f.text}" for i, f in enumerate(top, 1)) or "Nothing material this month."))
        order = {q.id: i for i, q in enumerate(self.pb.questions)}
        results.sort(key=lambda r: order[r.id])
        return Briefing(as_of=as_of, generated_at=datetime.now(timezone.utc).isoformat(timespec="seconds"),
                        playbook_version=self.pb.version, tiles=tiles, top_findings=top, questions=results,
                        query_count=self.runner.count - q0, bytes_scanned=self.runner.bytes - b0,
                        elapsed_ms=int((time.monotonic() - started) * 1000))
