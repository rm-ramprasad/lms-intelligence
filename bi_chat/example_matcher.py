"""Rank approved example questions by word overlap ("Did you mean…"). Suggestions only:
the caller must never execute a suggestion without the user choosing it."""
from .normalizer import tokenize


class ExampleMatcher:
    def __init__(self, examples: dict, stop_words: set[str]):
        self.stop = stop_words
        self.items = []   # (example_id, display question, token set)
        for ex in examples.values():
            phrasings = [ex.question] + list(getattr(ex, "paraphrases", ()) or ())
            for q in phrasings:
                self.items.append((ex.id, ex.question, self._content(q)))

    def _content(self, text: str) -> set[str]:
        return {t for t in tokenize(text) if t not in self.stop}

    def suggest(self, question: str, k: int = 3) -> list[tuple[str, str]]:
        q = self._content(question)
        best: dict[str, tuple[float, str]] = {}
        for ex_id, display, toks in self.items:
            if not q or not toks:
                continue
            score = len(q & toks) / len(q | toks)
            if score > 0 and score > best.get(ex_id, (0, ""))[0]:
                best[ex_id] = (score, display)
        ranked = sorted(best.items(), key=lambda kv: (-kv[1][0], kv[0]))
        return [(ex_id, display) for ex_id, (_, display) in ranked[:k]]
