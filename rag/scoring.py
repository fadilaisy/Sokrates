"""
rag/scoring.py
──────────────
The scoring layer ("decision model"). Pure functions, no I/O, so it is easy
to test and every number in the cockpit's score breakdown comes from here.

    S = w_R·R + w_M·M + w_F·F + w_A·A − w_P·P

R  relevance   — topical match to the incident and query, no embeddings:
                 R = c·coverage + (1−c)·bm25_norm
                 coverage  = IDF-weighted share of query concepts the chunk contains
                             (absolute, so it can drive abstention)
                 bm25_norm = FTS5 BM25 rank, min-max normalised over the candidates
M  metadata    — exact fault-code hit on the chunk, machine hit on chunk / document,
                 small credit for plant-wide documents
F  freshness   — current version and recent effective date
A  authority   — OEM manual > safety > SOP > quality > maintenance log > other
P  OCR penalty — 1 − OCR confidence for scanned passages (0 for digital text)
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime
from typing import Dict, List, Optional

from .config import ScoringConfig


@dataclass
class Features:
    coverage: Optional[float]          # None when the request carries no topical terms
    bm25_norm: float
    fault_hit: bool
    machine_hit_chunk: bool
    machine_hit_doc: bool
    doc_is_generic: bool               # document not scoped to particular machines
    incident_has_machine: bool
    incident_has_fault: bool
    superseded: bool
    effective_date: Optional[str]
    doc_type: str
    ocr_confidence: Optional[float]
    matched_terms: List[str] = field(default_factory=list)


@dataclass
class Score:
    total: float
    R: float
    M: float
    F: float
    A: float
    P: float
    weighted: Dict[str, float]
    reasons: List[str]

    def as_dict(self) -> dict:
        return {
            "total": round(self.total, 4),
            "R": round(self.R, 4), "M": round(self.M, 4), "F": round(self.F, 4),
            "A": round(self.A, 4), "P": round(self.P, 4),
            "weighted": {k: round(v, 4) for k, v in self.weighted.items()},
            "reasons": self.reasons,
        }


def relevance(f: Features, cfg: ScoringConfig) -> float:
    if f.coverage is None:
        # Pure metadata lookup (e.g. only a machine + fault code): neutral relevance.
        return 0.5 if (f.fault_hit or f.machine_hit_chunk) else 0.3 * f.bm25_norm
    c = cfg.r_coverage_weight
    return c * f.coverage + (1 - c) * f.bm25_norm


def metadata_match(f: Features) -> float:
    m = 0.0
    if f.incident_has_fault and f.fault_hit:
        m += 0.6
    if f.incident_has_machine:
        if f.machine_hit_chunk:
            m += 0.4
        elif f.machine_hit_doc:
            m += 0.25
        elif f.doc_is_generic:
            m += 0.1
    return min(m, 1.0)


def _age_years(iso: Optional[str], today: Optional[date] = None) -> Optional[float]:
    if not iso:
        return None
    try:
        d = datetime.fromisoformat(iso[:10]).date()
    except ValueError:
        return None
    today = today or date.today()
    return max((today - d).days / 365.25, 0.0)


def freshness(f: Features, today: Optional[date] = None) -> float:
    if f.superseded:
        return 0.0
    age = _age_years(f.effective_date, today)
    if age is None:
        return 0.8
    if age <= 1:
        return 1.0
    return max(0.5, 1.0 - 0.125 * (age - 1))       # 0.5 floor at 5 years


def authority(f: Features, cfg: ScoringConfig) -> float:
    return float(cfg.authority_by_doc_type.get(f.doc_type or "other",
                                               cfg.authority_by_doc_type.get("other", 0.5)))


def ocr_penalty(f: Features) -> float:
    if f.ocr_confidence is None:
        return 0.0
    return max(0.0, min(1.0, 1.0 - f.ocr_confidence))


def score(f: Features, cfg: ScoringConfig, today: Optional[date] = None) -> Score:
    R, M = relevance(f, cfg), metadata_match(f)
    F, A, P = freshness(f, today), authority(f, cfg), ocr_penalty(f)
    weighted = {
        "R": cfg.w_relevance * R,
        "M": cfg.w_metadata * M,
        "F": cfg.w_freshness * F,
        "A": cfg.w_authority * A,
        "P": -cfg.w_ocr_penalty * P,
    }
    total = sum(weighted.values())

    reasons: List[str] = []
    if f.matched_terms:
        reasons.append("cocok kata kunci: " + ", ".join(f.matched_terms[:6]))
    if f.incident_has_fault and f.fault_hit:
        reasons.append("fault code insiden ada di passage ini")
    if f.incident_has_machine:
        if f.machine_hit_chunk:
            reasons.append("menyebut mesin insiden secara langsung")
        elif f.machine_hit_doc:
            reasons.append("dokumen untuk model mesin ini")
        elif f.doc_is_generic:
            reasons.append("dokumen berlaku untuk semua mesin")
    if f.superseded:
        reasons.append("versi lama (superseded)")
    reasons.append(f"otoritas {f.doc_type or 'other'}")
    if P > 0:
        reasons.append(f"hasil OCR, confidence {f.ocr_confidence:.0%}")
    return Score(total=total, R=R, M=M, F=F, A=A, P=P, weighted=weighted, reasons=reasons)
