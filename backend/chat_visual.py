"""
backend/chat_visual.py
──────────────────────
Optional visual in a chat answer: when a picture explains the answer better
(trend, comparison between machines, schedule, cost or stock breakdown), the
model may add one fenced ```html-visual block with a small self-contained
HTML snippet (inline CSS + inline SVG). This module takes it out of the reply
text and makes it safe to show.

Defence in depth: the frontend renders the snippet in a sandboxed iframe with
scripts off and a CSP that blocks every network request. Here we still strip
scripts, event handlers, javascript: URLs and external resources, cap the
size, and drop a visual whose numbers do not come from the prompt's data.
"""

from __future__ import annotations

import re
from typing import Optional, Tuple

MAX_VISUAL_BYTES = 20_000

VISUAL_INSTRUCTION = (
    "Jika sebuah gambar membuat jawaban jauh lebih jelas (tren, perbandingan antarmesin, jadwal, "
    "rincian biaya atau stok), tambahkan SATU blok setelah jawaban teks:\n"
    "```html-visual\n<div>…</div>\n```\n"
    "Isinya HTML sederhana yang berdiri sendiri: CSS inline dan SVG inline saja (batang, garis, tabel), "
    "tanpa <script>, tanpa gambar atau tautan eksternal, lebar maksimal 100%, paling banyak 60 baris. "
    "Setiap angka di visual harus berasal dari data di atas. Jika teks saja sudah cukup, jangan buat visual."
)

_BLOCK = re.compile(r"```html-visual[ \t]*\r?\n(.*?)```", re.DOTALL | re.IGNORECASE)
_DANGEROUS_BLOCKS = re.compile(
    r"<(script|iframe|object|embed|link|meta|base|form|style\s+[^>]*src)[^>]*>.*?</\1\s*>|"
    r"<(script|iframe|object|embed|link|meta|base|form|input|button)\b[^>]*/?>",
    re.DOTALL | re.IGNORECASE,
)
_EVENT_ATTR = re.compile(r"\s+on[a-z]+\s*=\s*(\"[^\"]*\"|'[^']*'|[^\s>]+)", re.IGNORECASE)
_URL_ATTR = re.compile(r"\s+(href|src|xlink:href|action|formaction)\s*=\s*(\"[^\"]*\"|'[^']*'|[^\s>]+)",
                       re.IGNORECASE)
_CSS_URL = re.compile(r"url\s*\([^)]*\)|@import[^;]*;?|expression\s*\(", re.IGNORECASE)
_TAGS = re.compile(r"<[^>]+>")
_NUM = re.compile(r"\d[\d.,]*\d|\d")


def split_visual(reply: str) -> Tuple[str, Optional[str]]:
    """(reply without the html-visual block, the raw snippet or None)."""
    m = _BLOCK.search(reply)
    if not m:
        return reply, None
    text = (reply[:m.start()] + reply[m.end():]).strip()
    return text, m.group(1).strip()


def sanitize_visual(html: str) -> Optional[str]:
    """Strip anything that could run or fetch; None when nothing usable is left or it is too big."""
    if not html or len(html.encode("utf-8")) > MAX_VISUAL_BYTES:
        return None
    out = _DANGEROUS_BLOCKS.sub("", html)
    out = _EVENT_ATTR.sub("", out)
    out = _URL_ATTR.sub(lambda mm: "" if not _local_ref(mm.group(2)) else mm.group(0), out)
    out = _CSS_URL.sub("", out)
    out = out.strip()
    return out if _TAGS.search(out) else None


def _local_ref(value: str) -> bool:
    """Only in-document references (#gradient ids in SVG) survive."""
    return value.strip("\"' ").startswith("#")


def _numbers(text: str) -> set[str]:
    return {re.sub(r"[.,]", "", n) for n in _NUM.findall(text)}


def grounded(html: str, context: str) -> bool:
    """At least one number of 2+ digits in the visual also appears in the prompt's data."""
    shown = {n for n in _numbers(_TAGS.sub(" ", html)) if len(n) >= 2}
    return bool(shown & _numbers(context))


def extract(reply: str, context: str) -> Tuple[str, Optional[str]]:
    """Reply text plus a safe, grounded visual (or None)."""
    text, raw = split_visual(reply)
    if raw is None:
        return reply, None
    safe = sanitize_visual(raw)
    if safe is None or not grounded(safe, context):
        return text, None
    return text, safe
