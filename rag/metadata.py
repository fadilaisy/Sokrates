"""
rag/metadata.py
───────────────
Deterministic metadata extraction. Regex and vocabulary first; the LLM is only
consulted (optionally, in ingest.py) for fuzzy fields when these heuristics are
not confident.

Every extractor returns plain Python types so results can be stored as JSON
columns and validated by the schema in store.py.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path
from typing import Dict, Iterable, List, Optional, Tuple

from .text import tokens

# ─────────────────────────────────────────────────────────────────────────────
# Fault codes
# ─────────────────────────────────────────────────────────────────────────────

# Haas "Alarm 108", generic "ALM-1001", Fanuc "SV0401" / "SP 1241" / "OT0500",
# controller "ERR 2041", and the plant's own safety rule IDs "SAFE-003".
_FAULT_PATTERNS = [
    re.compile(r"\b(?:ALARM|ALM|AL)[\s\-#:.]*(\d{2,5})\b", re.I),
    re.compile(r"\b(SV|SP|OT|PS|SR|IO|OH|DS|EX|PW|SW|MC|BG|OI)[\s\-]?(\d{3,4})\b"),
    re.compile(r"\b(?:ERR|ERROR)[\s\-#:]*(\d{3,5})\b", re.I),
    re.compile(r"\b(SAFE)-(\d{3})\b", re.I),
]


def normalize_fault_code(code: str) -> str:
    """'Alarm 108' → 'ALM108', 'SV 0401' → 'SV0401', 'safe-003' → 'SAFE003'."""
    c = re.sub(r"[^A-Za-z0-9]", "", code or "").upper()
    c = re.sub(r"^(ALARM|AL)(?=\d)", "ALM", c)
    c = re.sub(r"^ERROR(?=\d)", "ERR", c)
    return c


def extract_fault_codes(text: str) -> List[str]:
    found: List[str] = []
    for i, pat in enumerate(_FAULT_PATTERNS):
        for m in pat.finditer(text):
            if i == 0:
                code = "ALM" + m.group(1)
            elif i == 2:
                code = "ERR" + m.group(1)
            else:
                code = m.group(1) + m.group(2)
            code = normalize_fault_code(code)
            if code not in found:
                found.append(code)
    return found


# ─────────────────────────────────────────────────────────────────────────────
# Part numbers
# ─────────────────────────────────────────────────────────────────────────────

_PN_LABELLED = re.compile(
    r"(?:P/?N|Part\s*(?:No\.?|Number|#)|No\.?\s*Part|Kode\s*(?:Part|Suku\s*Cadang)|Nomor\s*Part)"
    r"\s*[:#.]?\s*([A-Z0-9][A-Z0-9\-./]{3,24})",
    re.I,
)
_PN_HAAS = re.compile(r"\b(\d{2}-\d{4,5}[A-Z]?)\b")      # e.g. 93-1000, 30-12345A


def extract_part_numbers(text: str) -> List[str]:
    out: List[str] = []
    for m in _PN_LABELLED.finditer(text):
        pn = m.group(1).strip(".,;:").upper()
        if pn not in out:
            out.append(pn)
    for m in _PN_HAAS.finditer(text):
        pn = m.group(1).upper()
        if pn not in out:
            out.append(pn)
    return out


# ─────────────────────────────────────────────────────────────────────────────
# Dates
# ─────────────────────────────────────────────────────────────────────────────

_MONTHS = {
    "jan": 1, "januari": 1, "january": 1, "feb": 2, "februari": 2, "february": 2,
    "mar": 3, "maret": 3, "march": 3, "apr": 4, "april": 4, "mei": 5, "may": 5,
    "jun": 6, "juni": 6, "june": 6, "jul": 7, "juli": 7, "july": 7,
    "agu": 8, "agt": 8, "agustus": 8, "aug": 8, "august": 8,
    "sep": 9, "sept": 9, "september": 9, "okt": 10, "oktober": 10, "oct": 10, "october": 10,
    "nov": 11, "nopember": 11, "november": 11, "des": 12, "desember": 12, "dec": 12, "december": 12,
}
_DATE_ISO = re.compile(r"\b(20\d{2}|19\d{2})-(\d{1,2})-(\d{1,2})\b")
_DATE_DMY = re.compile(r"\b(\d{1,2})[/.-](\d{1,2})[/.-](20\d{2}|19\d{2})\b")
_DATE_TEXT = re.compile(r"\b(\d{1,2})\s+([A-Za-z]{3,9})\.?\s+(20\d{2}|19\d{2})\b")
_EFFECTIVE_LABEL = re.compile(
    r"(?:effective(?:\s+date)?|berlaku(?:\s+(?:mulai|sejak|tanggal))?|tanggal\s+berlaku|"
    r"rev(?:isi|ision)?\.?\s*(?:date|tanggal)?|tgl\.?\s*efektif|issued|diterbitkan)\s*[:\-]?\s*",
    re.I,
)


def _safe_date(y: int, m: int, d: int) -> Optional[str]:
    try:
        return date(y, m, d).isoformat()
    except ValueError:
        return None


def parse_date(s: str) -> Optional[str]:
    """First date found in s, as ISO YYYY-MM-DD."""
    m = _DATE_ISO.search(s)
    if m:
        return _safe_date(int(m.group(1)), int(m.group(2)), int(m.group(3)))
    m = _DATE_DMY.search(s)
    if m:  # Indonesian convention: day first
        return _safe_date(int(m.group(3)), int(m.group(2)), int(m.group(1)))
    m = _DATE_TEXT.search(s)
    if m and m.group(2).lower() in _MONTHS:
        return _safe_date(int(m.group(3)), _MONTHS[m.group(2).lower()], int(m.group(1)))
    return None


def extract_effective_date(text: str) -> Optional[str]:
    """Prefer a labelled date ('Berlaku: 12 Maret 2026'); fall back to none."""
    for m in _EFFECTIVE_LABEL.finditer(text):
        d = parse_date(text[m.end(): m.end() + 40])
        if d:
            return d
    return None


# ─────────────────────────────────────────────────────────────────────────────
# Machine catalog (built from the SAP mock so metadata matches the solver)
# ─────────────────────────────────────────────────────────────────────────────

@dataclass
class Machine:
    id: str
    name: str
    model: str
    aliases: List[str] = field(default_factory=list)


_GENERIC = {"no", "vertical", "machining", "center", "compact", "cnc", "the"}


def _model_from_name(name: str) -> str:
    # "Haas VF-2 No. 1" → "Haas VF-2"
    return re.sub(r"\s+No\.?\s*\d+\s*$", "", name, flags=re.I).strip()


def build_machine_catalog(sap_state: Optional[dict] = None) -> List[Machine]:
    if sap_state is None:
        p = Path(__file__).resolve().parent.parent / "erp_adapter" / "mock_sap_state.json"
        try:
            sap_state = json.loads(p.read_text(encoding="utf-8"))
        except Exception:
            sap_state = {"work_centers": []}
    out: List[Machine] = []
    for wc in sap_state.get("work_centers", []):
        name = wc.get("name", "")
        model = _model_from_name(name)
        aliases = {wc.get("id", ""), model}
        parts = model.split()
        # Distinctive model tokens: "VF-2", "Robodrill", "Variaxis", "NLX 2500"
        for i, p in enumerate(parts[1:], start=1):
            if p.lower() not in _GENERIC and len(p) >= 3:
                aliases.add(p)
                if i + 1 < len(parts) and re.match(r"^[\d\-]+", parts[i + 1]):
                    aliases.add(f"{p} {parts[i + 1]}")
        out.append(Machine(
            id=wc.get("id", ""),
            name=name,
            model=model,
            aliases=sorted({a for a in aliases if a}, key=len, reverse=True),
        ))
    return out


def _alias_pattern(alias: str) -> re.Pattern:
    esc = re.escape(alias).replace(r"\ ", r"[\s\-]*").replace(r"\-", r"[\s\-]?")
    return re.compile(r"(?<![A-Za-z0-9])" + esc + r"(?![A-Za-z0-9])", re.I)


def match_machines(text: str, catalog: List[Machine]) -> List[str]:
    """
    Return machine IDs mentioned in text. Explicit IDs ("CNC-02") win: a
    maintenance log for CNC-02 also says "Haas VF-2", but it is not about CNC-01.
    Without explicit IDs, a model mention matches every machine of that model.
    """
    explicit = [mc.id for mc in catalog if mc.id and _alias_pattern(mc.id).search(text)]
    if explicit:
        return explicit
    hit: List[str] = []
    for mc in catalog:
        for a in mc.aliases:
            if _alias_pattern(a).search(text):
                hit.append(mc.id)
                break
    return hit


def models_for_ids(ids: Iterable[str], catalog: List[Machine]) -> List[str]:
    by_id = {m.id: m.model for m in catalog}
    out = []
    for i in ids:
        mdl = by_id.get(i)
        if mdl and mdl not in out:
            out.append(mdl)
    return out


def resolve_machine(machine_id_or_model: Optional[str], catalog: List[Machine]) -> Optional[Machine]:
    if not machine_id_or_model:
        return None
    q = machine_id_or_model.strip().lower()
    for m in catalog:
        if q == m.id.lower() or q == m.model.lower() or q == m.name.lower():
            return m
    for m in catalog:
        if any(q == a.lower() for a in m.aliases):
            return m
    return None


# ─────────────────────────────────────────────────────────────────────────────
# Doc type, constraint tier, language
# ─────────────────────────────────────────────────────────────────────────────

DOC_TYPES = ["oem_manual", "sop", "maintenance_log", "quality", "safety", "other"]

_DOC_TYPE_KEYWORDS: Dict[str, List[str]] = {
    "oem_manual": ["operator's manual", "operator manual", "service manual", "maintenance manual",
                   "user manual", "manual book", "buku manual", "oem", "manufacturer", "pabrikan",
                   "alarm guide", "alarm reference", "programming guide", "machine model", "manual"],
    "sop": ["sop", "standard operating procedure", "prosedur operasi standar", "instruksi kerja",
            "work instruction", "prosedur"],
    "maintenance_log": ["maintenance log", "log perawatan", "catatan perawatan", "riwayat perawatan",
                        "work order", "laporan perbaikan", "service report", "teknisi:"],
    "quality": ["quality", "kualitas", "mutu", "qc", "inspeksi", "inspection", "iso 9001",
                "toleransi", "tolerance", "spc", "cpk"],
    "safety": ["safety", "keselamatan", "k3", "msds", "sds", "hira", "loto", "lockout",
               "apd", "ppe", "bahaya", "hazard"],
}


def classify_doc_type(title: str, text_sample: str) -> Tuple[str, float]:
    """Keyword vote with title hits weighted 3×. Returns (doc_type, confidence 0..1)."""
    title_l = (title or "").lower()
    body_l = (text_sample or "").lower()[:20000]
    scores: Dict[str, float] = {}
    for dt, kws in _DOC_TYPE_KEYWORDS.items():
        s = 0.0
        for kw in kws:
            if re.search(r"(?<![a-z])" + re.escape(kw) + r"(?![a-z])", title_l):
                s += 3.0
            s += min(len(re.findall(r"(?<![a-z])" + re.escape(kw) + r"(?![a-z])", body_l)), 5) * 0.5
        scores[dt] = s
    best = max(scores, key=lambda k: scores[k])
    total = sum(scores.values())
    if scores[best] <= 0:
        return "other", 0.2
    conf = scores[best] / total if total else 0.0
    # Strong title hits are trustworthy even when the body mentions other types.
    if any(re.search(r"(?<![a-z])" + re.escape(kw) + r"(?![a-z])", title_l) for kw in _DOC_TYPE_KEYWORDS[best]):
        conf = max(conf, 0.75)
    return best, round(min(conf, 0.99), 2)


_SAFETY_KW = re.compile(
    r"\b(bahaya|danger|warning|peringatan|awas|keselamatan|safety|e-?stop|emergency|darurat|"
    r"lockout|tagout|loto|apd|ppe|interlock|cedera|injury|kebakaran|fire|SAFE-\d{3})\b",
    re.I,
)
_QUALITY_KW = re.compile(
    r"\b(quality|kualitas|mutu|toleransi|tolerance|inspeksi|inspection|reject|cacat|defect|"
    r"spesifikasi|specification|sla|cpk|dimensi|dimension|kekasaran|roughness)\b",
    re.I,
)


def classify_constraint_tier(text: str, doc_type: str) -> str:
    """Safety / Quality / Cost — mirrors the solver's three-tier hierarchy."""
    s = len(_SAFETY_KW.findall(text))
    q = len(_QUALITY_KW.findall(text))
    if s >= 2 or (s >= 1 and doc_type == "safety"):
        return "Safety"
    if q >= 2 or (q >= 1 and doc_type == "quality") or s == 1:
        return "Quality" if q >= s else "Safety"
    return "Cost"


_ID_MARKERS = {"yang", "dan", "untuk", "dengan", "tidak", "pada", "adalah", "mesin", "harus",
               "jika", "segera", "periksa", "lakukan", "sebelum", "setelah", "operator"}
_EN_MARKERS = {"the", "and", "for", "with", "not", "must", "machine", "check", "before",
               "after", "when", "should", "this", "that", "is", "are"}


def detect_language(text: str) -> str:
    ws = [w.lower() for w in re.findall(r"[A-Za-z]+", text[:20000])]
    if not ws:
        return "unknown"
    id_n = sum(1 for w in ws if w in _ID_MARKERS - {"operator"})
    en_n = sum(1 for w in ws if w in _EN_MARKERS)
    if id_n == 0 and en_n == 0:
        return "unknown"
    ratio = id_n / (id_n + en_n)
    if ratio >= 0.75:
        return "id"
    if ratio <= 0.25:
        return "en"
    return "mixed"


def keywords_for_header(title: str, section_path: str) -> str:
    """The context header that is prepended to every chunk in the FTS index."""
    return f"{title} > {section_path}".strip(" >")


def content_terms(text: str) -> List[str]:
    return tokens(text)
