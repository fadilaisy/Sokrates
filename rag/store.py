"""
rag/store.py
────────────
Stage 5 of ingestion: the index. One SQLite file holds everything:

  documents   document-level metadata, status machine, versioning
  sections    parent sections (for chunk → section expansion)
  chunks      chunk-level metadata as typed columns (+ JSON arrays)
  chunks_fts  FTS5 index (BM25) over a context header and the chunk text

SQLite fits the backend today (no database yet, single-process uvicorn) and
FTS5's bm25() gives lexical relevance without any embedding model. Filterable
fields are real columns so hard filters are plain SQL.
"""

from __future__ import annotations

import json
import sqlite3
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional

from . import config

SCHEMA = """
PRAGMA journal_mode=WAL;
PRAGMA foreign_keys=ON;

CREATE TABLE IF NOT EXISTS documents (
    doc_id              TEXT PRIMARY KEY,
    family_id           TEXT NOT NULL,
    version             INTEGER NOT NULL DEFAULT 1,
    title               TEXT NOT NULL,
    filename            TEXT NOT NULL,
    file_path           TEXT NOT NULL,
    view_path           TEXT,
    mime_ext            TEXT,
    sha256              TEXT NOT NULL,
    size_bytes          INTEGER,
    doc_type            TEXT,
    doc_type_confidence REAL,
    doc_type_source     TEXT,
    summary             TEXT,
    language            TEXT,
    plant               TEXT NOT NULL,
    line                TEXT,
    machine_ids         TEXT NOT NULL DEFAULT '[]',
    machine_models      TEXT NOT NULL DEFAULT '[]',
    effective_date      TEXT,
    supersedes          TEXT,
    superseded_by       TEXT,
    authority_tier      INTEGER,
    acl                 TEXT NOT NULL DEFAULT '[]',
    status              TEXT NOT NULL,
    error               TEXT,
    warnings            TEXT NOT NULL DEFAULT '[]',
    page_count          INTEGER,
    ocr_pages           TEXT NOT NULL DEFAULT '[]',
    low_conf_pages      TEXT NOT NULL DEFAULT '[]',
    chunk_count         INTEGER DEFAULT 0,
    uploaded_by         TEXT,
    uploaded_role       TEXT,
    uploaded_at         TEXT NOT NULL,
    updated_at          TEXT NOT NULL,
    overrides           TEXT NOT NULL DEFAULT '{}'
);
CREATE INDEX IF NOT EXISTS ix_docs_sha ON documents(sha256);
CREATE INDEX IF NOT EXISTS ix_docs_family ON documents(family_id, version);
CREATE INDEX IF NOT EXISTS ix_docs_status ON documents(status, plant);

CREATE TABLE IF NOT EXISTS sections (
    section_id  TEXT PRIMARY KEY,
    doc_id      TEXT NOT NULL REFERENCES documents(doc_id) ON DELETE CASCADE,
    path        TEXT NOT NULL,
    level       INTEGER,
    text        TEXT NOT NULL,
    page_start  INTEGER,
    page_end    INTEGER
);

CREATE TABLE IF NOT EXISTS chunks (
    chunk_id        TEXT PRIMARY KEY,
    doc_id          TEXT NOT NULL REFERENCES documents(doc_id) ON DELETE CASCADE,
    section_id      TEXT REFERENCES sections(section_id) ON DELETE CASCADE,
    page            INTEGER NOT NULL,
    ordinal         INTEGER NOT NULL,
    kind            TEXT NOT NULL,
    section_path    TEXT,
    header          TEXT,
    text            TEXT NOT NULL,
    bbox            TEXT,
    fault_codes     TEXT NOT NULL DEFAULT '[]',
    part_numbers    TEXT NOT NULL DEFAULT '[]',
    machine_ids     TEXT NOT NULL DEFAULT '[]',
    constraint_tier TEXT NOT NULL,
    source          TEXT NOT NULL,
    ocr_confidence  REAL,
    content_hash    TEXT NOT NULL,
    token_count     INTEGER
);
CREATE INDEX IF NOT EXISTS ix_chunks_doc ON chunks(doc_id);
CREATE INDEX IF NOT EXISTS ix_chunks_tier ON chunks(constraint_tier);

CREATE VIRTUAL TABLE IF NOT EXISTS chunks_fts USING fts5(
    chunk_id UNINDEXED,
    header,
    text,
    tokenize = "unicode61 remove_diacritics 2"
);

CREATE TABLE IF NOT EXISTS retrieval_log (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    at          TEXT NOT NULL,
    incident    TEXT NOT NULL,
    query       TEXT,
    abstained   INTEGER NOT NULL,
    results     TEXT NOT NULL,
    latency_ms  REAL
);
"""

JSON_DOC_FIELDS = ("machine_ids", "machine_models", "acl", "warnings", "ocr_pages",
                   "low_conf_pages", "overrides")
JSON_CHUNK_FIELDS = ("bbox", "fault_codes", "part_numbers", "machine_ids")

DOC_STATUSES = ("pending", "processing", "indexed", "failed", "superseded")


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


class Store:
    """Thin, thread-safe wrapper. Reads use fresh cursors; writes take a lock."""

    def __init__(self, path: Optional[Path] = None):
        self.path = Path(path or config.db_path())
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()
        self._conn = sqlite3.connect(str(self.path), check_same_thread=False, timeout=30)
        self._conn.row_factory = sqlite3.Row
        with self._lock:
            self._conn.executescript(SCHEMA)
            self._conn.commit()

    # ── helpers ──────────────────────────────────────────────────────────────
    def execute(self, sql: str, params: Iterable[Any] = ()) -> sqlite3.Cursor:
        with self._lock:
            return self._conn.execute(sql, tuple(params))

    def query(self, sql: str, params: Iterable[Any] = ()) -> List[sqlite3.Row]:
        with self._lock:
            return self._conn.execute(sql, tuple(params)).fetchall()

    def commit(self) -> None:
        with self._lock:
            self._conn.commit()

    def transaction(self):
        return _Tx(self)

    @staticmethod
    def doc_row(r: sqlite3.Row) -> Dict[str, Any]:
        d = dict(r)
        for k in JSON_DOC_FIELDS:
            if k in d and isinstance(d[k], str):
                try:
                    d[k] = json.loads(d[k])
                except Exception:
                    pass
        return d

    @staticmethod
    def chunk_row(r: sqlite3.Row) -> Dict[str, Any]:
        d = dict(r)
        for k in JSON_CHUNK_FIELDS:
            if k in d and isinstance(d[k], str):
                try:
                    d[k] = json.loads(d[k])
                except Exception:
                    pass
        return d

    # ── documents ────────────────────────────────────────────────────────────
    def get_document(self, doc_id: str) -> Optional[Dict[str, Any]]:
        rows = self.query("SELECT * FROM documents WHERE doc_id = ?", (doc_id,))
        return self.doc_row(rows[0]) if rows else None

    def find_by_sha(self, sha: str, plant: str) -> Optional[Dict[str, Any]]:
        rows = self.query(
            "SELECT * FROM documents WHERE sha256 = ? AND plant = ? AND status != 'failed' "
            "ORDER BY version DESC LIMIT 1", (sha, plant))
        return self.doc_row(rows[0]) if rows else None

    def latest_in_family(self, family_id: str, plant: str) -> Optional[Dict[str, Any]]:
        rows = self.query(
            "SELECT * FROM documents WHERE family_id = ? AND plant = ? AND status != 'failed' "
            "ORDER BY version DESC LIMIT 1", (family_id, plant))
        return self.doc_row(rows[0]) if rows else None

    def list_documents(self, plant: Optional[str] = None, status: Optional[str] = None,
                       limit: int = 200) -> List[Dict[str, Any]]:
        sql, params = "SELECT * FROM documents WHERE 1=1", []
        if plant:
            sql += " AND plant = ?"
            params.append(plant)
        if status:
            sql += " AND status = ?"
            params.append(status)
        sql += " ORDER BY uploaded_at DESC, version DESC LIMIT ?"
        params.append(limit)
        return [self.doc_row(r) for r in self.query(sql, params)]

    def insert_document(self, doc: Dict[str, Any]) -> None:
        d = dict(doc)
        for k in JSON_DOC_FIELDS:
            if k in d and not isinstance(d[k], str):
                d[k] = json.dumps(d[k], ensure_ascii=False)
        cols = ", ".join(d.keys())
        ph = ", ".join("?" for _ in d)
        with self._lock:
            self._conn.execute(f"INSERT INTO documents ({cols}) VALUES ({ph})", tuple(d.values()))
            self._conn.commit()

    def update_document(self, doc_id: str, **fields: Any) -> None:
        if not fields:
            return
        fields["updated_at"] = now_iso()
        for k in JSON_DOC_FIELDS:
            if k in fields and not isinstance(fields[k], str):
                fields[k] = json.dumps(fields[k], ensure_ascii=False)
        sets = ", ".join(f"{k} = ?" for k in fields)
        with self._lock:
            self._conn.execute(f"UPDATE documents SET {sets} WHERE doc_id = ?",
                               tuple(fields.values()) + (doc_id,))
            self._conn.commit()

    def delete_document_content(self, doc_id: str) -> None:
        """Remove chunks/sections/FTS rows (used on re-index)."""
        with self._lock:
            self._conn.execute(
                "DELETE FROM chunks_fts WHERE chunk_id IN (SELECT chunk_id FROM chunks WHERE doc_id = ?)",
                (doc_id,))
            self._conn.execute("DELETE FROM chunks WHERE doc_id = ?", (doc_id,))
            self._conn.execute("DELETE FROM sections WHERE doc_id = ?", (doc_id,))
            self._conn.commit()

    # ── chunks ───────────────────────────────────────────────────────────────
    def get_chunks(self, chunk_ids: List[str]) -> Dict[str, Dict[str, Any]]:
        if not chunk_ids:
            return {}
        ph = ",".join("?" for _ in chunk_ids)
        rows = self.query(f"SELECT * FROM chunks WHERE chunk_id IN ({ph})", chunk_ids)
        return {r["chunk_id"]: self.chunk_row(r) for r in rows}

    def chunks_for_doc(self, doc_id: str, page: Optional[int] = None) -> List[Dict[str, Any]]:
        if page is None:
            rows = self.query("SELECT * FROM chunks WHERE doc_id = ? ORDER BY page, ordinal", (doc_id,))
        else:
            rows = self.query("SELECT * FROM chunks WHERE doc_id = ? AND page = ? ORDER BY ordinal",
                              (doc_id, page))
        return [self.chunk_row(r) for r in rows]

    def get_sections(self, section_ids: List[str]) -> Dict[str, Dict[str, Any]]:
        if not section_ids:
            return {}
        ph = ",".join("?" for _ in section_ids)
        rows = self.query(f"SELECT * FROM sections WHERE section_id IN ({ph})", section_ids)
        return {r["section_id"]: dict(r) for r in rows}

    def log_retrieval(self, incident: dict, query: str, abstained: bool,
                      results: list, latency_ms: float) -> None:
        with self._lock:
            self._conn.execute(
                "INSERT INTO retrieval_log (at, incident, query, abstained, results, latency_ms) "
                "VALUES (?, ?, ?, ?, ?, ?)",
                (now_iso(), json.dumps(incident, ensure_ascii=False), query, int(abstained),
                 json.dumps(results, ensure_ascii=False), latency_ms))
            self._conn.commit()


class _Tx:
    def __init__(self, store: Store):
        self.s = store

    def __enter__(self) -> sqlite3.Connection:
        self.s._lock.acquire()
        return self.s._conn

    def __exit__(self, exc_type, exc, tb):
        try:
            if exc_type is None:
                self.s._conn.commit()
            else:
                self.s._conn.rollback()
        finally:
            self.s._lock.release()
        return False


_STORE: Optional[Store] = None
_STORE_LOCK = threading.Lock()


def get_store() -> Store:
    """Process-wide store. Re-created if RAG_DATA_DIR changed (tests)."""
    global _STORE
    with _STORE_LOCK:
        if _STORE is None or _STORE.path != config.db_path():
            _STORE = Store(config.db_path())
        return _STORE
