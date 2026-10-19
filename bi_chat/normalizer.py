"""Tokenisation shared by the catalog (phrases) and the interpreter (questions), so both sides
always tokenise identically."""
import re

# words, numbers incl. decimals, hyphenated/slashed compounds ("1-30", "fy25-26", "a/c"), a trailing "+",
# or a standalone "%".
TOKEN = re.compile(r"[A-Za-z0-9]+(?:[.\-/][A-Za-z0-9]+)*\+?|%")


def tokenize_raw(text: str) -> list[str]:
    """Tokens with original case (used for UPPERCASE code aliases)."""
    text = text.replace("–", "-").replace("—", "-").replace("’", "'").replace("'s", "")
    return TOKEN.findall(text)


def tokenize(text: str) -> list[str]:
    return [t.lower() for t in tokenize_raw(text)]


def phrase_key(text: str) -> tuple[str, ...]:
    return tuple(tokenize(text))
