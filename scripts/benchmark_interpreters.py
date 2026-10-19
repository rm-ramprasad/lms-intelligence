"""Benchmark interpreters on the eval sets (LIVE Bedrock for ai/hybrid; approval required).

    python scripts/benchmark_interpreters.py --modes deterministic,hybrid,ai [--sets freeform,eval,golden]

No Athena queries are run: only interpretation is measured. Results append to docs/benchmarks_interpreters.jsonl.
"""
import argparse
import json
import statistics
import sys
from datetime import date, datetime, timezone
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from bi_chat.catalog import load_catalog  # noqa: E402
from bi_chat.hybrid import HybridInterpreter  # noqa: E402
from bi_chat.interpreter import Clarify, ConversationState, DeterministicInterpreter, Interpreted, Refuse  # noqa: E402
from bi_chat.llm_client import BedrockClient, BedrockSettings  # noqa: E402
from bi_chat.llm_interpreter import LlmInterpreter  # noqa: E402
from bi_service.config import ServiceConfig  # noqa: E402
from bi_service.query_service import QueryService  # noqa: E402
from bi_service.semantic_loader import load_semantic_model  # noqa: E402

KINDS = {Interpreted: "interpreted", Clarify: "clarify", Refuse: "refuse"}


def norm_filters(fs):
    return {(f["dimension"], f["op"], tuple(f["value"]) if isinstance(f["value"], list) else f["value"]) for f in fs}


def correct(case: dict, r) -> bool:
    """Matches the primary expectation, or any documented alternative in `accept`."""
    return _matches(case, r) or any(_matches(alt, r) for alt in case.get("accept", []))


def _matches(case: dict, r) -> bool:
    if KINDS[type(r)] != case["expect"]:
        return False
    for k, exp in (case.get("spec") or {}).items():
        got = r.spec.get(k, [] if k in ("dimensions", "filters") else None)
        if k == "filters":
            if norm_filters(got) != norm_filters(exp):
                return False
        elif k == "period":
            if {kk: str(v) for kk, v in (got or {}).items()} != {kk: str(v) for kk, v in exp.items()}:
                return False
        elif got != exp:
            return False
    return True


def load_cases(sets: list[str], model) -> list[tuple[str, dict]]:
    out = []
    if "freeform" in sets:
        out += [("freeform", c) for c in yaml.safe_load((ROOT / "tests/eval/freeform.yaml").read_text())["cases"]]
    if "eval" in sets:
        out += [("eval", c) for c in yaml.safe_load((ROOT / "tests/eval/questions.yaml").read_text())["cases"]]
    if "golden" in sets:
        for ex in model.examples.values():
            spec = {k: ex.spec[k] for k in ("metric", "dimensions") if k in ex.spec}
            out += [("golden", {"q": p, "expect": "interpreted", "spec": spec}) for p in ex.paraphrases]
    return out


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--modes", default="deterministic,hybrid")
    ap.add_argument("--sets", default="freeform,eval,golden")
    args = ap.parse_args()
    modes, sets = args.modes.split(","), args.sets.split(",")

    model = load_semantic_model()
    cfg = ServiceConfig.load()
    det = DeterministicInterpreter(model, load_catalog(model), QueryService(model, None, cfg).compile)
    settings = BedrockSettings.load()
    client = BedrockClient.from_aws(cfg.profile, cfg.region, settings) if set(modes) & {"ai", "hybrid"} else None
    router = HybridInterpreter(det, LlmInterpreter(det, client) if client else None)
    anchor = date(2026, 3, 31)
    cases = load_cases(sets, model)

    ts = datetime.now(timezone.utc).isoformat(timespec="seconds")
    out_path = ROOT / "docs" / "benchmarks_interpreters.jsonl"
    with open(out_path, "a", encoding="utf-8") as out:
        for mode in modes:
            stats = {s: [0, 0] for s in sets}
            lat, cost, calls, misses = [], 0.0, 0, []
            for set_name, case in cases:
                state = ConversationState(case["context"]) if case.get("context") else None
                r = router.interpret(case["q"], state, anchor=anchor, mode=mode)
                ok = correct(case, r)
                stats[set_name][0] += ok
                stats[set_name][1] += 1
                if r.source == "ai":
                    calls += r.meta.get("calls", 0)
                    cost += r.meta.get("cost_usd", 0.0)
                    lat.append(r.meta.get("latency_ms", 0))
                if not ok:
                    misses.append({"set": set_name, "q": case["q"], "got": KINDS[type(r)],
                                   "spec": getattr(r, "spec", None), "source": r.source})
                out.write(json.dumps({"ts": ts, "mode": mode, "set": set_name, "q": case["q"], "ok": ok,
                                      "outcome": KINDS[type(r)], "source": r.source,
                                      "model": settings.model_id if r.source == "ai" else None,
                                      "ai": r.meta if r.source == "ai" else None}, default=str) + "\n")
            print(f"\n== mode={mode}")
            for s, (k, n) in stats.items():
                print(f"  {s:<9} {k}/{n}  ({100 * k / n:.1f}%)" if n else f"  {s:<9} -")
            if lat:
                p95 = sorted(lat)[int(0.95 * (len(lat) - 1))]
                print(f"  AI calls={calls} cost=${cost:.4f} latency p50={statistics.median(lat):.0f}ms p95={p95}ms")
            for m in misses[:15]:
                print(f"  MISS [{m['set']}] {m['q']!r} -> {m['got']} {m['spec']} ({m['source']})")
    print(f"\nappended to {out_path.relative_to(ROOT)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
