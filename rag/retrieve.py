"""
rag/retrieve.py
───────────────
Incident-context-aware retrieval:

  1. Incident context object (machine, fault code, severity, shift, timestamp)
  2. Hard filters first: status, plant, ACL, language/doc type, and the incident
     machine (documents scoped to *other* machines are excluded)
  3. Candidates: FTS5 BM25 top-N, plus every chunk carrying the incident fault
     code, plus Safety-tier chunks for the machine
  4. Score and rerank with the scoring layer (scoring.py)
  5. Always include Safety-tier chunks matching the machine (reserved slots)
  6. Abstain below threshold: "no grounded source"

`mode` supports the evaluation ablation:
  "bm25"          status filter only, rank by BM25
  "bm25_filters"  + hard metadata filters, rank by BM25
  "full"          + scoring layer, safety slots, abstention
"""

from __future__ import annotations

import json
import math
import time
from dataclasses import asdict, dataclass, field
from typing import Any, Dict, List, Optional, Set, Tuple

from . import config
from .config import ScoringConfig, load_scoring_config
from .metadata import Machine, build_machine_catalog, normalize_fault_code, resolve_machine
from .scoring import Features, score
from .store import Store, get_store
from .text import expand_term, fts_quote, tokens

# Disruption types used by the cockpit / solver → bilingual concept terms.
DISRUPTION_TERMS: Dict[str, List[str]] = {
    "BREAKDOWN": ["breakdown", "rusak", "kerusakan", "failure", "fault", "gangguan", "alarm"],
    "OVERHEAT": ["overheat", "panas", "suhu", "temperature", "thermal", "pendingin", "cooling"],
    "VIBRATION": ["getaran", "vibration", "bearing", "balancing"],
    "COOLANT": ["coolant", "pendingin", "tekanan", "pressure"],
    "MAINTENANCE": ["perawatan", "maintenance", "servis", "service", "pelumasan", "lubrication"],
    "QUALITY": ["kualitas", "quality", "toleransi", "tolerance", "reject", "inspeksi"],
    "ESTOP": ["darurat", "emergency", "e-stop", "stop"],
    "POWER": ["listrik", "power", "breaker", "tegangan", "voltage"],
    "TOOL": ["tool", "pahat", "insert", "aus", "wear", "patah", "breakage"],
}
# Common aliases sent by telemetry / UI
DISRUPTION_ALIASES = {"OVERHEATING": "OVERHEAT", "MOTOR_OVERHEAT": "OVERHEAT", "SPINDLE_OVERHEAT": "OVERHEAT",
                      "E_STOP": "ESTOP", "EMERGENCY_STOP": "ESTOP", "TOOL_BREAKAGE": "TOOL",
                      "TOOL_WEAR": "TOOL", "POWER_DIP": "POWER",
                      "QUALITY_ISSUE": "QUALITY", "POWER_FAILURE": "POWER", "LOW_COOLANT": "COOLANT"}


@dataclass
class IncidentContext:
    machine_id: Optional[str] = None
    fault_code: Optional[str] = None
    severity: Optional[str] = None          # INFO | WARNING | HIGH | CRITICAL
    shift: Optional[str] = None
    timestamp: Optional[str] = None
    disruption_type: Optional[str] = None
    plant: Optional[str] = None
    line: Optional[str] = None
    role: str = "agent"
    language: Optional[str] = None          # restrict to id/en when set
    doc_types: Optional[List[str]] = None

    @classmethod
    def from_dict(cls, d: Optional[Dict[str, Any]]) -> "IncidentContext":
        d = d or {}
        known = {k: d.get(k) for k in cls.__dataclass_fields__ if k in d}
        ctx = cls(**known)
        ctx.role = (ctx.role or "agent").lower()
        return ctx

    def to_dict(self) -> Dict[str, Any]:
        return {k: v for k, v in asdict(self).items() if v not in (None, [], "")}


def _fault_variants(code: str) -> List[str]:
    """Phrases that find a fault code in raw text: ALM108 → 'alarm 108', 'alm 108', '108'."""
    n = normalize_fault_code(code)
    out = [n]
    import re
    m = re.match(r"^([A-Z]+)(\d+)$", n)
    if m:
        pre, num = m.group(1), m.group(2)
        out.append(f"{pre} {num}")
        if pre == "ALM":
            out += [f"alarm {num}", num]
        if pre == "SAFE":
            out.append(f"safe {num}")
    return out


def _concepts(ctx: IncidentContext, query: str) -> Tuple[List[Tuple[str, List[str]]], List[str]]:
    """
    Topical concept groups for R (each group = synonyms, satisfied by any member)
    and extra recall-only terms (machine aliases, fault variants) for the FTS query.
    """
    groups: List[Tuple[str, List[str]]] = []
    seen: Set[str] = set()
    for t in tokens(query or ""):
        if t not in seen:
            seen.add(t)
            # Bilingual expansion: the concept is covered by the word or its translation.
            groups.append((t, expand_term(t)))
    dt = (ctx.disruption_type or "").upper().strip()
    dt = DISRUPTION_ALIASES.get(dt, dt)
    if dt in DISRUPTION_TERMS:
        groups.append((f"[{dt}]", DISRUPTION_TERMS[dt]))
    return groups, []


def _term_hits(term: str, chunk_tokens: Set[str]) -> bool:
    if term in chunk_tokens:
        return True
    if len(term) >= 5:
        stem = term[:max(5, len(term) - 2)]
        return any(ct.startswith(stem) for ct in chunk_tokens)
    return False


def _fts_term(t: str) -> str:
    # Prefix match for longer words: "overheat*" also finds "overheating".
    if len(t) >= 5 and t.isalnum():
        return fts_quote(t[:max(5, len(t) - 2)]) + "*"
    return fts_quote(t)


class Retriever:
    def __init__(self, store: Optional[Store] = None, cfg: Optional[ScoringConfig] = None,
                 catalog: Optional[List[Machine]] = None):
        self.store = store or get_store()
        self.cfg = cfg or load_scoring_config()
        self.catalog = catalog if catalog is not None else build_machine_catalog()

    # ── SQL helpers ──────────────────────────────────────────────────────────
    def _filters(self, ctx: IncidentContext, machine: Optional[Machine], mode: str,
                 include_superseded: bool) -> Tuple[str, List[Any], List[str]]:
        where = ["d.status IN ('indexed'" + (",'superseded'" if include_superseded else "") + ")"]
        params: List[Any] = []
        applied = ["status=" + ("indexed|superseded" if include_superseded else "indexed")]
        if mode == "bm25":
            return " AND ".join(where), params, applied
        plant = ctx.plant or config.DEFAULT_PLANT
        where.append("d.plant = ?")
        params.append(plant)
        applied.append(f"plant={plant}")
        where.append("EXISTS (SELECT 1 FROM json_each(d.acl) WHERE value = ?)")
        params.append(ctx.role)
        applied.append(f"acl∋{ctx.role}")
        if ctx.language in ("id", "en"):
            where.append("(d.language IN (?, 'mixed', 'unknown'))")
            params.append(ctx.language)
            applied.append(f"language={ctx.language}")
        if ctx.doc_types:
            where.append("d.doc_type IN (" + ",".join("?" for _ in ctx.doc_types) + ")")
            params += list(ctx.doc_types)
            applied.append("doc_type∈" + "|".join(ctx.doc_types))
        if machine:
            where.append("(d.machine_ids = '[]' OR EXISTS (SELECT 1 FROM json_each(d.machine_ids) WHERE value = ?))")
            params.append(machine.id)
            applied.append(f"machine∈{{{machine.id}, plant-wide}}")
        return " AND ".join(where), params, applied

    def _fts_candidates(self, match: str, where: str, params: List[Any], limit: int) -> List[Tuple[Any, float]]:
        if not match:
            return []
        sql = (
            "SELECT c.*, d.title AS doc_title, d.doc_type, d.version, d.status AS doc_status, "
            "d.effective_date, d.machine_ids AS doc_machine_ids, d.language AS doc_language, "
            "bm25(chunks_fts, 0.0, 2.0, 1.0) AS bm25 "
            "FROM chunks_fts JOIN chunks c ON c.chunk_id = chunks_fts.chunk_id "
            "JOIN documents d ON d.doc_id = c.doc_id "
            f"WHERE chunks_fts MATCH ? AND {where} ORDER BY bm25 LIMIT ?"
        )
        try:
            rows = self.store.query(sql, [match] + params + [limit])
        except Exception:
            return []
        return [(r, -float(r["bm25"])) for r in rows]

    def _meta_candidates(self, extra_where: str, extra_params: List[Any], where: str,
                         params: List[Any], limit: int = 50) -> List[Any]:
        sql = (
            "SELECT c.*, d.title AS doc_title, d.doc_type, d.version, d.status AS doc_status, "
            "d.effective_date, d.machine_ids AS doc_machine_ids, d.language AS doc_language, 0.0 AS bm25 "
            "FROM chunks c JOIN documents d ON d.doc_id = c.doc_id "
            f"WHERE {where} AND {extra_where} LIMIT ?"
        )
        return self.store.query(sql, params + extra_params + [limit])

    def _topical_bm25(self, terms: List[str], chunk_ids: List[str]) -> Dict[str, float]:
        if not terms or not chunk_ids:
            return {}
        match = " OR ".join(dict.fromkeys(_fts_term(t) for t in terms))
        ph = ",".join("?" for _ in chunk_ids)
        try:
            rows = self.store.query(
                "SELECT chunk_id, bm25(chunks_fts, 0.0, 2.0, 1.0) AS s FROM chunks_fts "
                f"WHERE chunks_fts MATCH ? AND chunk_id IN ({ph})", [match] + chunk_ids)
        except Exception:
            return {}
        return {r["chunk_id"]: -float(r["s"]) for r in rows}

    def _idf(self, terms: List[str]) -> Dict[str, float]:
        n = self.store.query("SELECT count(*) AS n FROM chunks")[0]["n"] or 1
        out = {}
        for t in terms:
            try:
                df = self.store.query("SELECT count(*) AS n FROM chunks_fts WHERE chunks_fts MATCH ?",
                                      [_fts_term(t)])[0]["n"]
            except Exception:
                df = 0
            out[t] = math.log(1 + (n - df + 0.5) / (df + 0.5))
        return out

    # ── main entry ───────────────────────────────────────────────────────────
    def retrieve(self, incident: Dict[str, Any] | IncidentContext, query: str = "", *,
                 top_k: Optional[int] = None, mode: str = "full", include_superseded: bool = False,
                 log: bool = True) -> Dict[str, Any]:
        t0 = time.perf_counter()
        ctx = incident if isinstance(incident, IncidentContext) else IncidentContext.from_dict(incident)
        cfg = self.cfg
        top_k = top_k or cfg.top_k
        machine = resolve_machine(ctx.machine_id, self.catalog)
        fault = normalize_fault_code(ctx.fault_code) if ctx.fault_code else None

        where, params, applied = self._filters(ctx, machine, mode, include_superseded)
        groups, _ = _concepts(ctx, query)
        topical_terms = [t for _, syn in groups for t in syn]

        recall_terms: List[str] = [_fts_term(t) for t in topical_terms]
        if fault:
            recall_terms += [fts_quote(v) for v in _fault_variants(fault)]
        if machine and mode != "bm25":
            recall_terms += [fts_quote(a) for a in machine.aliases]
        match = " OR ".join(dict.fromkeys(recall_terms))

        cands: Dict[str, Dict[str, Any]] = {}
        for row, s in self._fts_candidates(match, where, params, cfg.candidate_pool):
            cands[row["chunk_id"]] = {"row": row, "bm25": s}

        if mode == "full":
            if fault:
                for row in self._meta_candidates(
                        "EXISTS (SELECT 1 FROM json_each(c.fault_codes) WHERE value = ?)", [fault], where, params):
                    cands.setdefault(row["chunk_id"], {"row": row, "bm25": 0.0})
            if machine:
                for row in self._meta_candidates("c.constraint_tier = 'Safety'", [], where, params):
                    cands.setdefault(row["chunk_id"], {"row": row, "bm25": 0.0})

        # ── Baseline modes: rank by BM25 only ───────────────────────────────
        if mode != "full":
            ranked = sorted(cands.values(), key=lambda c: -c["bm25"])[:top_k]
            results = [self._result(c["row"], None, False) | {"bm25": round(c["bm25"], 4)} for c in ranked]
            return {"mode": mode, "abstained": not results, "results": results, "filters": applied,
                    "candidates": len(cands), "latency_ms": round((time.perf_counter() - t0) * 1000, 1),
                    "incident": ctx.to_dict(), "query": query}

        # ── Scoring layer ───────────────────────────────────────────────────
        # BM25 for R must be *topical*: recompute it with only the query/disruption
        # terms, so machine aliases or fault variants (used for recall) can't make an
        # off-topic chunk look relevant.
        topical = self._topical_bm25(topical_terms, list(cands.keys()))
        bm = [v for v in topical.values() if v > 0]
        lo, hi = (min(bm), max(bm)) if bm else (0.0, 0.0)
        idf = self._idf(topical_terms) if topical_terms else {}
        scored = []
        for cid, c in cands.items():
            row = c["row"]
            ctoks = set(tokens((row["header"] or "") + " " + row["text"], drop_stopwords=False))
            covered_w, total_w, matched = 0.0, 0.0, []
            for label, syn in groups:
                is_disruption_group = label.startswith("[")
                w = 1.5 if is_disruption_group else max(idf.get(t, 1.0) for t in syn)
                total_w += w
                hit = next((t for t in syn if _term_hits(t, ctoks)), None)
                if hit:
                    covered_w += w
                    matched.append(hit)
            coverage = (covered_w / total_w) if total_w else None
            tb = topical.get(cid, 0.0)
            if tb <= 0:
                bm25_norm = 0.0
            elif hi > lo:
                bm25_norm = (tb - lo) / (hi - lo)
            else:
                bm25_norm = 1.0
            chunk_machines = json.loads(row["machine_ids"] or "[]")
            doc_machines = json.loads(row["doc_machine_ids"] or "[]")
            fault_codes = json.loads(row["fault_codes"] or "[]")
            f = Features(
                coverage=coverage, bm25_norm=bm25_norm,
                fault_hit=bool(fault and fault in fault_codes),
                machine_hit_chunk=bool(machine and machine.id in chunk_machines),
                machine_hit_doc=bool(machine and machine.id in doc_machines),
                doc_is_generic=not doc_machines,
                incident_has_machine=machine is not None, incident_has_fault=bool(fault),
                superseded=row["doc_status"] == "superseded",
                effective_date=row["effective_date"], doc_type=row["doc_type"] or "other",
                ocr_confidence=row["ocr_confidence"], matched_terms=matched,
            )
            sc = score(f, cfg)
            scored.append((sc, row))
        scored.sort(key=lambda x: -x[0].total)

        # Abstain if nothing (excluding forced safety) clears the bar.
        passing = [(s, r) for s, r in scored
                   if s.total >= cfg.abstain_threshold and s.R >= cfg.min_relevance]
        abstained = not passing
        chosen = passing[:top_k]
        chosen_ids = {r["chunk_id"] for _, r in chosen}

        # Reserved Safety slots: Safety-tier chunks for this machine (or plant-wide docs).
        slots = cfg.safety_slots_critical if (ctx.severity or "").upper() == "CRITICAL" else cfg.safety_slots
        forced: List[Tuple[Any, Any]] = []
        if machine:
            have = sum(1 for _, r in chosen if r["constraint_tier"] == "Safety")
            # Most incident-relevant safety passages first; skip header/metadata stubs.
            for s, r in sorted(scored, key=lambda x: (-x[0].R, -x[0].total)):
                if have + len(forced) >= slots:
                    break
                if r["chunk_id"] in chosen_ids or r["constraint_tier"] != "Safety":
                    continue
                if (r["token_count"] or 0) < 25:
                    continue
                dm = json.loads(r["doc_machine_ids"] or "[]")
                cm = json.loads(r["machine_ids"] or "[]")
                if not dm or machine.id in dm or machine.id in cm:
                    forced.append((s, r))

        results = [self._result(r, s, False) for s, r in chosen]
        results += [self._result(r, s, True) for s, r in forced]
        near_miss = [self._result(r, s, False, brief=True)
                     for s, r in scored if r["chunk_id"] not in chosen_ids][:3]

        latency = round((time.perf_counter() - t0) * 1000, 1)
        out = {
            "mode": mode, "abstained": abstained,
            "message": ("Tidak ada sumber yang cukup relevan (no grounded source)." if abstained else None),
            "results": results, "near_miss": near_miss, "filters": applied,
            "candidates": len(cands), "threshold": cfg.abstain_threshold,
            "weights": {"R": cfg.w_relevance, "M": cfg.w_metadata, "F": cfg.w_freshness,
                        "A": cfg.w_authority, "P": cfg.w_ocr_penalty},
            "machine": ({"id": machine.id, "model": machine.model} if machine else None),
            "fault_code": fault, "latency_ms": latency, "incident": ctx.to_dict(), "query": query,
        }
        if log:
            try:
                self.store.log_retrieval(ctx.to_dict(), query, abstained,
                                         [{"chunk_id": r["chunk_id"], "score": r["score"]} for r in results],
                                         latency)
            except Exception:
                pass
        return out

    @staticmethod
    def _result(row, sc, forced: bool, brief: bool = False) -> Dict[str, Any]:
        d = {
            "chunk_id": row["chunk_id"], "doc_id": row["doc_id"], "doc_title": row["doc_title"],
            "doc_type": row["doc_type"], "version": row["version"], "page": row["page"],
            "section_path": row["section_path"], "constraint_tier": row["constraint_tier"],
            "citation": f"{row['doc_id']}#p{row['page']}",
            "score": round(sc.total, 4) if sc else None,
            "forced_safety": forced,
        }
        if brief:
            d["breakdown"] = sc.as_dict() if sc else None
            return d
        d.update({
            "kind": row["kind"], "text": row["text"],
            "bbox": json.loads(row["bbox"]) if row["bbox"] else None,
            "section_id": row["section_id"], "fault_codes": json.loads(row["fault_codes"] or "[]"),
            "part_numbers": json.loads(row["part_numbers"] or "[]"),
            "ocr_confidence": row["ocr_confidence"], "superseded": row["doc_status"] == "superseded",
            "breakdown": sc.as_dict() if sc else None,
        })
        return d


def retrieve(incident: Dict[str, Any], query: str = "", **kw) -> Dict[str, Any]:
    return Retriever().retrieve(incident, query, **kw)
