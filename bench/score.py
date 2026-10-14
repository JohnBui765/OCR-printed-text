"""Score OCR output against the hand-made ground truth for the three pages."""
from __future__ import annotations
import re, difflib
from collections import Counter

def norm_token(t: str) -> str:
    t = t.replace("’", "'").replace("‘", "'").replace("“", '"').replace("”", '"')
    t = t.replace("...", "…")
    if t in {"-", "–", "—"}:
        t = "–"
    return t

def tokens(text: str) -> list[str]:
    return [norm_token(t) for t in text.split()]

def word_scores(gt: str, hyp: str) -> dict:
    g, h = Counter(tokens(gt)), Counter(tokens(hyp))
    matched = sum((g & h).values())
    n = sum(g.values())
    return {"words": n, "correct": matched, "acc": matched / n,
            "missing": sorted((g - h).elements()), "extra": sorted((h - g).elements())}

def norm_line(s: str) -> str:
    return " ".join(tokens(s))

def line_scores(gt: str, hyp_lines: list[str]) -> dict:
    g = [norm_line(l) for l in gt.splitlines() if l.strip()]
    h = [norm_line(l) for l in hyp_lines if l.strip()]
    exact = sum(1 for l in g if l in set(h))
    sm = difflib.SequenceMatcher(a=g, b=h, autojunk=False)
    in_order = sum(b.size for b in sm.get_matching_blocks())
    return {"lines": len(g), "exact": exact, "in_order": in_order}
