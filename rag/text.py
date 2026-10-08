"""
rag/text.py
───────────
Small, dependency-free text helpers shared by ingestion and retrieval.
Bilingual (Bahasa Indonesia + English) stopwords keep BM25 queries clean.
"""

from __future__ import annotations

import hashlib
import re
import unicodedata
from typing import Iterable, List

_WORD = re.compile(r"[0-9A-Za-zÀ-ɏ]+(?:[-./][0-9A-Za-zÀ-ɏ]+)*", re.UNICODE)

STOPWORDS_ID = {
    "yang", "dan", "di", "ke", "dari", "untuk", "pada", "dengan", "ini", "itu", "atau",
    "adalah", "akan", "tidak", "dalam", "oleh", "sebagai", "juga", "jika", "bila", "agar",
    "harus", "dapat", "bisa", "saat", "setelah", "sebelum", "karena", "maka", "ada",
    "para", "tersebut", "serta", "lalu", "kemudian", "secara", "hingga", "sampai",
    "apa", "bagaimana", "mengapa", "kenapa", "apakah", "mana", "berapa", "kapan",
    "saya", "kami", "kita", "anda", "nya", "per", "sudah", "belum", "masih", "lebih",
    "sangat", "telah", "yaitu", "yakni", "ia", "dia", "mereka", "seperti", "tentang",
}
STOPWORDS_EN = {
    "the", "a", "an", "and", "or", "of", "to", "in", "on", "for", "with", "is", "are",
    "be", "been", "was", "were", "it", "this", "that", "these", "those", "as", "at",
    "by", "from", "if", "then", "when", "must", "should", "can", "may", "will", "not",
    "do", "does", "did", "what", "how", "why", "which", "who", "where", "there", "their",
    "into", "after", "before", "any", "all", "has", "have", "had", "than", "so", "such",
    "you", "your", "we", "our", "i", "me", "my", "its", "about", "also", "per", "up",
}
STOPWORDS = STOPWORDS_ID | STOPWORDS_EN


# Bilingual shop-floor lexicon (ID ↔ EN). Without embeddings this is what lets an
# Indonesian question find an English OEM manual and vice versa. Keys are the
# lowercase, accent-folded tokens produced by tokens(); extend freely.
_LEXICON_PAIRS = [
    ("langkah", ["step", "steps", "procedure"]), ("prosedur", ["procedure"]),
    ("pemulihan", ["recovery", "recover", "restart"]), ("penanganan", ["handling", "procedure", "response"]),
    ("suhu", ["temperature", "thermal"]), ("panas", ["heat", "hot", "overheat"]),
    ("dingin", ["cool", "cooling"]), ("pendingin", ["cooling", "coolant"]), ("kipas", ["fan"]),
    ("saringan", ["filter"]), ("pelumasan", ["lubrication", "grease"]), ("pelumas", ["lubricant", "grease"]),
    ("gemuk", ["grease"]), ("bantalan", ["bearing"]), ("getaran", ["vibration"]),
    ("tekanan", ["pressure"]), ("kerusakan", ["failure", "fault", "damage"]), ("rusak", ["failure", "broken"]),
    ("gangguan", ["fault", "disruption", "failure"]), ("perawatan", ["maintenance", "service"]),
    ("perbaikan", ["repair", "service"]), ("ganti", ["replace", "change"]), ("penggantian", ["replacement"]),
    ("periksa", ["check", "inspect", "verify"]), ("pemeriksaan", ["inspection", "check"]),
    ("hentikan", ["stop"]), ("berhenti", ["stop"]), ("matikan", ["stop", "off"]), ("darurat", ["emergency"]),
    ("keselamatan", ["safety"]), ("bahaya", ["danger", "hazard"]), ("peringatan", ["warning"]),
    ("mesin", ["machine"]), ("menit", ["minutes", "minute"]), ("jam", ["hours", "hour"]),
    ("kecepatan", ["speed"]), ("putaran", ["speed", "rpm"]), ("beban", ["load"]), ("batas", ["limit", "threshold"]),
    ("minimum", ["minimum"]), ("maksimum", ["maximum"]), ("penyebab", ["cause", "causes"]),
    ("sebab", ["cause"]), ("riwayat", ["history", "log"]), ("catatan", ["log", "notes", "record"]),
    ("kode", ["code", "number"]), ("suku", ["part"]), ("cadang", ["spare", "part"]), ("alat", ["tool"]),
    ("pahat", ["tool", "cutter"]), ("aus", ["wear", "worn"]), ("patah", ["broken", "breakage"]),
    ("listrik", ["electrical", "power"]), ("tegangan", ["voltage"]), ("kualitas", ["quality"]),
    ("toleransi", ["tolerance"]), ("pemanasan", ["warm-up", "warmup"]), ("motor", ["motor"]),
    ("sinyal", ["signal"]), ("pintu", ["door"]), ("kalibrasi", ["calibration"]), ("jadwal", ["schedule"]),
]
LEXICON = {}
for _id, _ens in _LEXICON_PAIRS:
    LEXICON.setdefault(_id, set()).update(_ens)
    for _en in _ens:
        LEXICON.setdefault(_en, set()).add(_id)


def expand_term(t: str):
    """A token plus its translations, e.g. 'pemulihan' → ['pemulihan', 'recovery', 'recover', 'restart']."""
    return [t] + sorted(LEXICON.get(t, set()) - {t})


def normalize(s: str) -> str:
    s = unicodedata.normalize("NFKC", s or "")
    return s.replace("­", "").replace("​", "")


def strip_accents(s: str) -> str:
    return "".join(c for c in unicodedata.normalize("NFKD", s) if not unicodedata.combining(c))


def words(s: str) -> List[str]:
    return _WORD.findall(normalize(s))


def tokens(s: str, drop_stopwords: bool = True) -> List[str]:
    """Lowercased, accent-folded content tokens."""
    out = []
    for w in words(s):
        t = strip_accents(w.lower())
        if drop_stopwords and (t in STOPWORDS or len(t) < 2):
            continue
        out.append(t)
    return out


def est_tokens(s: str) -> int:
    """Rough LLM-token estimate (≈1.3 tokens per word for ID/EN technical text)."""
    return int(len(words(s)) * 1.3) + 1


def sha256_bytes(b: bytes) -> str:
    return hashlib.sha256(b).hexdigest()


def sha256_text(s: str) -> str:
    return hashlib.sha256(s.encode("utf-8")).hexdigest()


def fts_quote(term: str) -> str:
    """Quote a term for an FTS5 MATCH expression (escapes embedded quotes)."""
    return '"' + term.replace('"', '""') + '"'


def fts_or(terms: Iterable[str]) -> str:
    seen, parts = set(), []
    for t in terms:
        t = t.strip()
        if not t or t in seen:
            continue
        seen.add(t)
        parts.append(fts_quote(t))
    return " OR ".join(parts)


def numbers_in(s: str) -> List[str]:
    """Numeric facts (thresholds, torques, temperatures) — used by the citation post-check."""
    return re.findall(r"\d+(?:[.,]\d+)?", s or "")
