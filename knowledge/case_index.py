"""
knowledge/case_index.py
───────────────────────
Bridge from the maintenance ledger to the RAG index (rag/).

Every RESOLVED case is rendered as a small Markdown document and ingested through
the normal rag pipeline as doc_type `maintenance_log`, so similar-case search uses
the same BM25 + metadata filters + scoring layer + abstention as the rest of the
plant documents. The document *family* is the case id, which is how a retrieved
chunk is mapped back to its ledger entry (and its SHA-256) for citations.

Scoping: a case applies to every machine of the same model (a VF-2 repair is
evidence for both VF-2s). The chunk itself names the original machine, so the
scoring layer still ranks same-machine history first.
"""

from __future__ import annotations

import re
from typing import Any, Dict, List, Optional

from rag.ingest import create_document, process_document
from rag.metadata import build_machine_catalog
from rag.store import Store, get_store

CASE_FAMILY = re.compile(r"^case-(\d{4})$")


def family_for_case(case_id: str) -> str:
    return case_id.lower()


def case_id_for_doc(doc: Optional[Dict[str, Any]]) -> Optional[str]:
    m = CASE_FAMILY.match((doc or {}).get("family_id") or "")
    return f"CASE-{m.group(1)}" if m else None


def render_case(case: Dict[str, Any]) -> str:
    src = case.get("source", "typed")
    if case.get("ocr_confidence"):
        src += f" (OCR {round(case['ocr_confidence'] * 100)}%)"
    lines = [
        f"# {case['id']} — {case.get('symptom', '')}",
        "",
        f"Log perawatan · Mesin: {case.get('machine_id')} ({case.get('machine_model')}) · "
        f"Kategori: {case.get('category')} · Dicatat: {str(case.get('logged_at', ''))[:10]} · "
        f"Sumber: {src} · Teknisi: {case.get('mechanic_id')} · Hasil: {case.get('outcome')}",
        "",
    ]
    if case.get("error_codes"):
        lines += [f"Kode alarm: {', '.join(case['error_codes'])}", ""]
    if case.get("part_codes"):
        lines += [f"Part No.: {', '.join(case['part_codes'])}", ""]
    lines += [f"Gejala: {case.get('symptom', '')}", ""]
    if case.get("root_cause"):
        lines += [f"Akar masalah: {case['root_cause']}", ""]
    if case.get("fix_steps"):
        lines += ["Langkah perbaikan: " + " ".join(f"({i}) {s}" for i, s in enumerate(case["fix_steps"], 1)), ""]
    if case.get("safety_notes"):
        lines += [f"Keselamatan: {case['safety_notes']}", ""]
    if case.get("text"):
        lines += [f"Catatan asli: {case['text']}", ""]
    return "\n".join(lines)


def _same_model_machines(model: Optional[str]) -> List[str]:
    return [m.id for m in build_machine_catalog() if model and m.model == model]


def index_case(case: Dict[str, Any], store: Optional[Store] = None) -> Dict[str, Any]:
    """Ingest one resolved case (idempotent: identical content is a SHA-256 duplicate)."""
    store = store or get_store()
    created = create_document(
        render_case(case).encode("utf-8"), f"{case['id']}.md", role="admin", uploaded_by="maintenance-ledger",
        overrides={
            "family": family_for_case(case["id"]),
            "title": f"{case['id']} — {case.get('symptom', '')}"[:120],
            "doc_type": "maintenance_log",
            "machine_ids": _same_model_machines(case.get("machine_model")) or [case.get("machine_id")],
            "effective_date": str(case.get("logged_at", ""))[:10] or None,
        },
        store=store,
    )
    if created.get("duplicate"):
        return created
    out = process_document(created["doc_id"], store=store, use_llm=False)
    # The case text came from a scan: carry its OCR confidence into the index so the
    # scoring layer applies the same OCR penalty it uses for scanned PDFs.
    if case.get("ocr_confidence") and out.get("status") == "indexed":
        store.execute("UPDATE chunks SET ocr_confidence = ?, source = 'ocr' WHERE doc_id = ?",
                      (float(case["ocr_confidence"]), created["doc_id"]))
        store.commit()
    return out


def sync_cases(cases: List[Dict[str, Any]], store: Optional[Store] = None) -> Dict[str, int]:
    """Index every resolved case that isn't indexed yet. Returns counts."""
    counts = {"indexed": 0, "unchanged": 0, "failed": 0}
    for case in cases:
        if case.get("status") != "RESOLVED" or not case.get("fix_steps"):
            continue
        r = index_case(case, store)
        if r.get("duplicate"):
            counts["unchanged"] += 1
        elif r.get("status") == "indexed":
            counts["indexed"] += 1
        else:
            counts["failed"] += 1
    return counts
