"""
rag/highlight.py
────────────────
Find the sentences inside a cited passage that support an answer, so the
cockpit can highlight them and a supervisor can check the claim by hand.

Deterministic and dependency-free: the passage is split into sentences / list
lines, each segment is scored by how many of a claim's concepts (with the
ID↔EN lexicon) and numbers it contains, and the best segment for each claim is
returned as character offsets into the passage text.
"""

from __future__ import annotations

import re
from typing import Iterable, List, Tuple

from .generate import _concepts
from .text import expand_term, numbers_in

# Segment boundaries: line breaks, or sentence punctuation followed by whitespace
# and something that starts a new sentence (so "2.5 bar" and "3.2" stay intact).
_BOUNDARY = re.compile(r"\n+|(?<=[.!?;])\s+(?=[A-ZÀ-Ý0-9•\-(\"'])")
_CITE = re.compile(r"\[S\d+(?:\s*,\s*S\d+)*\]")


def segments(text: str) -> List[Tuple[int, int]]:
    """(start, end) of each non-empty sentence or line, whitespace-trimmed."""
    spans, pos = [], 0
    for m in list(_BOUNDARY.finditer(text)) + [None]:
        end = m.start() if m else len(text)
        s, e = pos, end
        while s < e and text[s].isspace():
            s += 1
        while e > s and text[e - 1].isspace():
            e -= 1
        if e > s:
            spans.append((s, e))
        pos = m.end() if m else len(text)
    return spans


def _score(claim: str, segment: str) -> float:
    claim_c = {c for c in _concepts(_CITE.sub(" ", claim)) if not c.isdigit() and len(c) > 2}
    seg_c = set(_concepts(segment))

    def hit(c: str) -> bool:
        for x in expand_term(c):
            if x in seg_c:
                return True
            if len(x) >= 5:
                stem = x[:max(5, len(x) - 2)]
                if any(s.startswith(stem) for s in seg_c):
                    return True
        return False

    concepts = sum(1 for c in claim_c if hit(c))
    seg_norm = segment.replace(",", ".")
    nums = sum(1 for n in numbers_in(claim) if n.replace(",", ".") in seg_norm)
    return concepts + 1.5 * nums


def highlight_spans(text: str, claims: Iterable[str], *, max_spans: int = 3) -> List[List[int]]:
    """
    Offsets [[start, end], …] of the passage sentence that best supports each claim,
    in reading order. Empty when nothing in the passage overlaps the claims.
    """
    claims = [c for c in claims if c and c.strip()]
    segs = segments(text) if text else []
    picked = {}
    for c in claims:
        best = max(((_score(c, text[s:e]), s, e) for s, e in segs), default=(0.0, 0, 0))
        # Two matching concepts (or a number plus a concept) is the bar for support.
        if best[0] >= 2:
            picked[(best[1], best[2])] = max(best[0], picked.get((best[1], best[2]), 0))
    keep = sorted(picked, key=lambda k: -picked[k])[:max_spans]
    return [[s, e] for s, e in sorted(keep)]


def quote(text: str, spans: List[List[int]]) -> str:
    """The highlighted sentences joined, for a one-line preview."""
    return " … ".join(text[s:e] for s, e in spans)
