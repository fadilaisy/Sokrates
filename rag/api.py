"""
rag/api.py
──────────
FastAPI router for the RAG layer. Mounted by backend/main.py.

POST /api/documents                       upload (supervisor/admin) → doc_id, status pending
GET  /api/documents                       list documents + status
GET  /api/documents/{id}                  status + extracted metadata
GET  /api/documents/{id}/chunks           chunks (optionally one page)
GET  /api/documents/{id}/file             original (or converted) file
GET  /api/documents/{id}/pages/{n}.png    rendered page, optional ?chunk_id= highlight
POST /api/documents/{id}/reindex          re-run parse/OCR/metadata (supervisor/admin)
POST /api/retrieve                        incident + query → scored chunks with breakdown
POST /api/answer                          incident + query → cited answer or abstention
GET  /api/rag/status                      index stats, OCR languages, LLM providers, weights
POST /api/rag/seed-demo                   ingest the demo corpus (admin)

There is no auth in the backend yet, so the role comes from the `X-User-Role`
header (operator | supervisor | admin | agent). Swap this for real auth before
production; every check already goes through `_role()`.
"""

from __future__ import annotations

import json
from typing import Any, Dict, List, Optional

from fastapi import (APIRouter, BackgroundTasks, File, Form, Header, HTTPException, Query,
                     UploadFile, status)
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import FileResponse, Response
from pydantic import BaseModel, Field

from . import config
from .generate import answer_from_retrieval
from .ingest import UploadError, create_document, process_document
from .llm import available_providers
from .parse import ocr_available
from .retrieve import Retriever
from .store import get_store

router = APIRouter(tags=["RAG — dokumen & retrieval"])


def _role(x_user_role: Optional[str]) -> str:
    return (x_user_role or "operator").strip().lower()


def _require_writer(role: str) -> None:
    if role not in config.UPLOAD_ROLES:
        raise HTTPException(status.HTTP_403_FORBIDDEN,
                            "Hanya supervisor atau admin yang boleh mengubah dokumen (header X-User-Role).")


def _visible(doc: Dict[str, Any], role: str) -> bool:
    return role in (doc.get("acl") or []) or role == "admin"


def _public_doc(d: Dict[str, Any]) -> Dict[str, Any]:
    d = dict(d)
    for k in ("file_path", "view_path", "overrides"):
        d.pop(k, None)
    return d


# ── Models ──────────────────────────────────────────────────────────────────

class Incident(BaseModel):
    machine_id: Optional[str] = Field(None, examples=["CNC-02"])
    fault_code: Optional[str] = Field(None, examples=["Alarm 108"])
    severity: Optional[str] = Field(None, examples=["HIGH"])
    shift: Optional[str] = Field(None, examples=["pagi"])
    timestamp: Optional[str] = None
    disruption_type: Optional[str] = Field(None, examples=["OVERHEAT"])
    plant: Optional[str] = None
    line: Optional[str] = None
    language: Optional[str] = None
    doc_types: Optional[List[str]] = None


class RetrieveRequest(BaseModel):
    incident: Incident = Field(default_factory=Incident)
    query: str = Field("", examples=["langkah pemulihan setelah alarm overheat spindle"])
    top_k: Optional[int] = Field(None, ge=1, le=20)
    mode: str = Field("full", pattern="^(full|bm25|bm25_filters)$")
    include_superseded: bool = False


class AnswerRequest(BaseModel):
    incident: Incident = Field(default_factory=Incident)
    query: str = Field("", examples=["Apa yang harus dilakukan operator sekarang?"])
    provider: Optional[str] = Field(None, pattern="^(auto|gemini|claude)$")


# ── Documents ───────────────────────────────────────────────────────────────

@router.post("/api/documents", status_code=status.HTTP_202_ACCEPTED,
             summary="Upload dokumen (PDF, gambar scan, TXT/MD) — supervisor/admin")
async def upload_document(
    background: BackgroundTasks,
    file: UploadFile = File(...),
    title: Optional[str] = Form(None),
    doc_type: Optional[str] = Form(None),
    machine_ids: Optional[str] = Form(None, description="comma-separated, e.g. CNC-01,CNC-02"),
    effective_date: Optional[str] = Form(None, description="YYYY-MM-DD"),
    family: Optional[str] = Form(None, description="same family → new version supersedes the old one"),
    plant: Optional[str] = Form(None),
    line: Optional[str] = Form(None),
    acl: Optional[str] = Form(None, description="comma-separated roles allowed to read"),
    force_ocr: bool = Form(False),
    x_user_role: Optional[str] = Header(None),
    x_user_name: Optional[str] = Header(None),
) -> Dict[str, Any]:
    role = _role(x_user_role)
    data = await file.read()
    overrides = {
        "title": title, "doc_type": doc_type, "effective_date": effective_date, "family": family,
        "line": line, "force_ocr": force_ocr or None,
        "machine_ids": [m.strip() for m in machine_ids.split(",") if m.strip()] if machine_ids else None,
        "acl": [a.strip().lower() for a in acl.split(",") if a.strip()] if acl else None,
    }
    try:
        created = await run_in_threadpool(create_document, data, file.filename or "upload.pdf",
                                          role=role, uploaded_by=x_user_name or role, plant=plant,
                                          overrides=overrides)
    except UploadError as exc:
        raise HTTPException(exc.status_code, str(exc))
    if not created.get("duplicate"):
        background.add_task(process_document, created["doc_id"])
    return created


@router.get("/api/documents", summary="Daftar dokumen dan statusnya")
async def list_documents(plant: Optional[str] = None, status_: Optional[str] = Query(None, alias="status"),
                         x_user_role: Optional[str] = Header(None)) -> List[Dict[str, Any]]:
    role = _role(x_user_role)
    docs = get_store().list_documents(plant=plant, status=status_)
    return [_public_doc(d) for d in docs if _visible(d, role)]


def _get_doc_or_404(doc_id: str, role: str) -> Dict[str, Any]:
    d = get_store().get_document(doc_id)
    if not d or not _visible(d, role):
        raise HTTPException(status.HTTP_404_NOT_FOUND, f"Dokumen {doc_id} tidak ditemukan.")
    return d


@router.get("/api/documents/{doc_id}", summary="Status dan metadata dokumen")
async def get_document(doc_id: str, x_user_role: Optional[str] = Header(None)) -> Dict[str, Any]:
    d = _get_doc_or_404(doc_id, _role(x_user_role))
    out = _public_doc(d)
    rows = get_store().query(
        "SELECT constraint_tier, count(*) AS n FROM chunks WHERE doc_id = ? GROUP BY constraint_tier", (doc_id,))
    out["chunks_by_tier"] = {r["constraint_tier"]: r["n"] for r in rows}
    fc = get_store().query("SELECT fault_codes, part_numbers FROM chunks WHERE doc_id = ?", (doc_id,))
    out["fault_codes"] = sorted({c for r in fc for c in json.loads(r["fault_codes"] or "[]")})
    out["part_numbers"] = sorted({c for r in fc for c in json.loads(r["part_numbers"] or "[]")})
    out["has_viewer"] = bool(d.get("view_path"))
    return out


@router.get("/api/documents/{doc_id}/chunks", summary="Chunk dokumen (opsional per halaman)")
async def get_document_chunks(doc_id: str, page: Optional[int] = None,
                              x_user_role: Optional[str] = Header(None)) -> List[Dict[str, Any]]:
    _get_doc_or_404(doc_id, _role(x_user_role))
    return get_store().chunks_for_doc(doc_id, page)


@router.get("/api/documents/{doc_id}/file", summary="File asli dokumen")
async def get_document_file(doc_id: str, x_user_role: Optional[str] = Header(None)):
    d = _get_doc_or_404(doc_id, _role(x_user_role))
    path = d.get("view_path") or d["file_path"]
    return FileResponse(path, filename=d["filename"] if path == d["file_path"] else f"{doc_id}.pdf")


@router.get("/api/documents/{doc_id}/pages/{page}.png", summary="Render halaman dengan highlight bbox")
async def get_document_page(doc_id: str, page: int, chunk_id: Optional[str] = None,
                            dpi: int = Query(110, ge=50, le=200), role: Optional[str] = None,
                            x_user_role: Optional[str] = Header(None)):
    # `role` query param exists because <img src> cannot send headers.
    d = _get_doc_or_404(doc_id, _role(x_user_role or role))
    if not d.get("view_path"):
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Dokumen ini tidak punya tampilan halaman (teks biasa).")
    bbox = None
    if chunk_id:
        ch = get_store().get_chunks([chunk_id]).get(chunk_id)
        if ch and ch["doc_id"] == doc_id and ch["page"] == page:
            bbox = ch.get("bbox")

    def render() -> bytes:
        from .parse import fitz
        doc = fitz.open(d["view_path"])
        try:
            if page < 1 or page > doc.page_count:
                raise HTTPException(status.HTTP_404_NOT_FOUND, "Halaman tidak ada.")
            pg = doc[page - 1]
            if bbox:
                r = fitz.Rect(*bbox) + (-4, -4, 4, 4)
                pg.draw_rect(r, color=(0.95, 0.66, 0.0), fill=(0.95, 0.66, 0.0), fill_opacity=0.18, width=1.6)
            return pg.get_pixmap(dpi=dpi).tobytes("png")
        finally:
            doc.close()

    png = await run_in_threadpool(render)
    return Response(content=png, media_type="image/png", headers={"Cache-Control": "no-store"})


@router.post("/api/documents/{doc_id}/reindex", summary="Proses ulang dokumen (supervisor/admin)")
async def reindex_document(doc_id: str, background: BackgroundTasks,
                           x_user_role: Optional[str] = Header(None)) -> Dict[str, Any]:
    role = _role(x_user_role)
    _require_writer(role)
    d = _get_doc_or_404(doc_id, role)
    if d["status"] == "superseded":
        raise HTTPException(status.HTTP_409_CONFLICT, "Versi lama (superseded) tidak diproses ulang.")
    get_store().update_document(doc_id, status="pending")
    background.add_task(process_document, doc_id)
    return {"doc_id": doc_id, "status": "pending"}


# ── Retrieval & answers ─────────────────────────────────────────────────────

def _incident_dict(inc: Incident, role: str) -> Dict[str, Any]:
    d = inc.model_dump(exclude_none=True)
    d["role"] = role
    return d


@router.post("/api/retrieve", summary="Retrieval berbasis konteks insiden, dengan breakdown skor")
async def post_retrieve(body: RetrieveRequest, x_user_role: Optional[str] = Header(None)) -> Dict[str, Any]:
    role = _role(x_user_role or "agent")
    return await run_in_threadpool(
        lambda: Retriever().retrieve(_incident_dict(body.incident, role), body.query, top_k=body.top_k,
                                     mode=body.mode, include_superseded=body.include_superseded))


@router.post("/api/answer", summary="Jawaban bersitasi dari dokumen, atau abstain")
async def post_answer(body: AnswerRequest, x_user_role: Optional[str] = Header(None)) -> Dict[str, Any]:
    role = _role(x_user_role or "agent")

    def run():
        retrieval = Retriever().retrieve(_incident_dict(body.incident, role), body.query)
        return answer_from_retrieval(body.query, retrieval, provider=body.provider)

    return await run_in_threadpool(run)


@router.get("/api/rag/status", summary="Status index RAG")
async def rag_status() -> Dict[str, Any]:
    s = get_store()
    by_status = {r["status"]: r["n"] for r in s.query("SELECT status, count(*) AS n FROM documents GROUP BY status")}
    n_chunks = s.query("SELECT count(*) AS n FROM chunks")[0]["n"]
    ok, langs = ocr_available()
    from .config import load_scoring_config
    return {"documents": by_status, "chunks": n_chunks, "ocr": {"available": ok, "languages": langs},
            "llm_providers": available_providers(), "provider_order": config.GEN_PROVIDER,
            "models": {"gemini": config.gemini_model(), "claude": config.claude_model()},
            "scoring": load_scoring_config().to_dict()}


@router.post("/api/rag/seed-demo", summary="Ingest korpus demo (admin)")
async def seed_demo(x_user_role: Optional[str] = Header(None)) -> List[Dict[str, Any]]:
    if _role(x_user_role) != "admin":
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Hanya admin.")
    from .samples import seed
    res = await run_in_threadpool(seed)
    return [{k: r.get(k) for k in ("file", "doc_id", "status", "chunks", "doc_type", "duplicate", "error")}
            for r in res]
