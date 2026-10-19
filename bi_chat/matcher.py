"""Longest-phrase matching of a question against the catalog. Produces a Parse: what was
recognised (KPIs, dimensions, filter values, period, top-N, cues) and what was not."""
import re
from dataclasses import dataclass, field
from datetime import date

from .catalog import Catalog
from .normalizer import tokenize, tokenize_raw
from .period_parser import PeriodMatch, PeriodParser

ID_PATTERNS = [
    re.compile(r"^\d{6,}$"),                 # long numbers (account / phone)
    re.compile(r"^[a-z]{2}\d{6,}$"),         # AA99999999 style account / customer ids
    re.compile(r"^[a-z]{5}\d{4}[a-z]$"),     # PAN
]
EMAIL = re.compile(r"\S+@\S+")


@dataclass
class Parse:
    tokens: list[str]
    metrics: list[str] = field(default_factory=list)
    weak_metrics: list[str] = field(default_factory=list)
    ambiguous: list[tuple[str, ...]] = field(default_factory=list)
    pending: list[str] = field(default_factory=list)
    dimensions: list[str] = field(default_factory=list)
    filters: dict[str, dict] = field(default_factory=dict)   # dim -> {"values": [...], "negate": bool}
    period: PeriodMatch | None = None
    top: tuple[str, int | None] | None = None                # (direction, n)
    leftovers: list[str] = field(default_factory=list)
    understood: list[tuple[str, str]] = field(default_factory=list)   # (kind, phrase)
    refuse: str | None = None
    followup_cue: bool = False
    also: bool = False
    clear_filters: bool = False


def _phrase_list(items) -> list[tuple[str, ...]]:
    return sorted((tuple(tokenize(s)) for s in items), key=len, reverse=True)


class Matcher:
    def __init__(self, catalog: Catalog):
        self.cat = catalog
        lang = catalog.language
        self.periods = PeriodParser(lang)
        self.stop = catalog.stop_words()
        self.negation = _phrase_list(lang["negation"])
        self.also_words = _phrase_list(lang["also"])
        self.cues = _phrase_list(lang["followup_cues"])
        self.clear = _phrase_list(lang["clear_filters"])
        self.id_words = _phrase_list(lang["refuse"]["identifier_words"])
        self.oos_words = _phrase_list(lang["refuse"]["out_of_scope_words"])
        self.top_desc = set(lang["top_n"]["desc"])
        self.top_asc = set(lang["top_n"]["asc"])
        self.top_default = lang["top_n"]["default_n"]

    @staticmethod
    def _find(tokens, phrases, used=None) -> list[tuple[int, int]]:
        hits = []
        for i in range(len(tokens)):
            for ph in phrases:
                if tuple(tokens[i:i + len(ph)]) == ph and not (used and used & set(range(i, i + len(ph)))):
                    hits.append((i, len(ph)))
                    break
        return hits

    def parse(self, question: str, anchor: date) -> Parse:
        raw = tokenize_raw(question)
        t = [x.lower() for x in raw]
        p = Parse(t)
        used: set[int] = set()

        # 1. refusals: identifiers and out-of-scope intent
        if EMAIL.search(question) or any(pat.search(tok) for tok in t for pat in ID_PATTERNS):
            p.refuse = "identifier"
            return p
        if self._find(t, self.id_words):
            p.refuse = "identifier"
            return p
        if self._find(t, self.oos_words):
            p.refuse = "out_of_scope"
            return p

        # 2. conversation cues (only at the start) and clear-filter phrases
        if self._find(t[:2], self.cues) and self._find(t[:2], self.cues)[0][0] == 0:
            p.followup_cue = True
        for i, n in self._find(t, self.also_words):
            p.also = True
            used |= set(range(i, i + n))
        for i, n in self._find(t, self.clear, used):
            p.clear_filters = True
            used |= set(range(i, i + n))

        # 3. period (one per question)
        pm = self.periods.parse(t, anchor)
        if pm:
            p.period = pm
            used |= pm.consumed
            p.understood.append(("period", pm.label))

        # 4. top-N: "top 5", "bottom 3", "highest"
        for i, tok in enumerate(t):
            if i in used or (tok not in self.top_desc and tok not in self.top_asc):
                continue
            n = None
            if i + 1 < len(t) and t[i + 1].isdigit() and int(t[i + 1]) <= 100:
                n = int(t[i + 1])
                used.add(i + 1)
            elif tok in ("top", "bottom"):
                n = self.top_default
            p.top = ("desc" if tok in self.top_desc else "asc", n)
            used.add(i)
            p.understood.append(("ranking", f"{tok} {n or ''}".strip()))
            break

        # 5. longest-phrase catalog matching, with negation scope
        negate = False
        last_value = None   # (dimension id, end position) of the previous value match
        i = 0
        while i < len(t):
            if i in used:
                i += 1
                continue
            hit = None
            for n in range(min(self.cat.max_phrase_len, len(t) - i), 0, -1):
                span = set(range(i, i + n))
                if span & used:
                    continue
                e = self.cat.index.get(tuple(t[i:i + n]))
                if e:
                    hit = (n, e)
                    break
            if hit is None and raw[i] in self.cat.code_index:
                hit = (1, self.cat.code_index[raw[i]])
            if hit is None:
                neg = next((n for (j, n) in self._find(t[i:], self.negation) if j == 0), 0)
                if neg:
                    negate = True
                    used |= set(range(i, i + neg))
                    i += neg
                    continue
                i += 1
                continue
            n, e = hit
            phrase = " ".join(t[i:i + n])
            used |= set(range(i, i + n))
            if e.kind == "metric":
                if e.target not in p.metrics:
                    p.metrics.append(e.target)
                p.understood.append(("kpi", phrase))
                negate = False
            elif e.kind == "metric_weak":
                p.weak_metrics.append(e.target)
                p.understood.append(("kpi", phrase))
            elif e.kind == "metric_ambiguous":
                p.ambiguous.append(e.target)
                p.understood.append(("kpi?", phrase))
            elif e.kind == "metric_pending":
                p.pending.append(e.target)
            elif e.kind == "dimension" and last_value == (e.target, i):
                p.understood.append(("filter", phrase))   # "90+ bucket", "east branch": qualifies the value
            elif e.kind == "dimension":
                if e.target not in p.dimensions:
                    p.dimensions.append(e.target)
                p.understood.append(("breakdown", phrase))
                negate = False
            elif e.kind == "value":
                dim, val = e.target
                f = p.filters.setdefault(dim, {"values": [], "negate": negate})
                if val not in f["values"]:
                    f["values"].append(val)
                f["negate"] = f["negate"] or negate
                p.understood.append(("filter", phrase))
            last_value = (e.target[0], i + n) if e.kind == "value" else None
            i += n

        # 6. anything left that is not a stop word is unrecognised
        p.leftovers = [t[k] for k in range(len(t)) if k not in used and t[k] not in self.stop]
        return p
