# Metadata-Augmented RAG — Implementation Notes

Implements `Metadata-Augmented RAG for Sokrates: Spec` (Oct 8, 2026). The spec's
open questions were answered as follows, and the design changed accordingly:

| Open question | Answer | Effect on the design |
| --- | --- | --- |
| Scanned or digital, which language? | Mixed scans + digital, Bahasa + English | Text layer first, Tesseract OCR (`ind+eng`) only for pages without text; a bilingual shop-floor lexicon so an Indonesian question finds an English OEM manual (and vice versa) |
| On-prem or cloud? | **No embedding model.** A scoring layer (decision model) over indexed metadata ranks the passages, then a generative LLM (Gemini, Claude Haiku 4.5) writes the cited answer | Dense retrieval and the cross-encoder are removed. Candidates come from FTS5/BM25 under hard metadata filters, and the scoring layer is deterministic and explainable |
| "Scores based on what happened"? | Incident-context-aware reranking | Incident context (machine, fault code, disruption type, severity) drives hard filters, the metadata term M, reserved safety slots, and the query concepts |
| Current data store? | None (SQLite) | One SQLite file: typed metadata columns + JSON arrays + an FTS5 index. No new infrastructure |

## Running it

```bash
python3 -m pip install -r requirements.txt     # adds pymupdf, pytesseract, python-multipart
# OCR binary + languages:  apt install tesseract-ocr tesseract-ocr-ind   |   brew install tesseract tesseract-lang
python3 -m rag.samples seed                    # optional: ingest the fictional demo corpus
python3 -m uvicorn backend.main:app --reload --port 8000
python3 -m rag.eval                            # golden set + ablation table
python3 -m pytest tests/test_rag.py -q         # offline test suite
```

In the cockpit, open **📚 Dokumen & RAG**. **Muat korpus demo** loads the demo
documents (the button calls `POST /api/rag/seed-demo` as admin).

Environment (`.env`): `GEMINI_API_KEY`, `ANTHROPIC_API_KEY`,
`RAG_GEN_PROVIDER=auto|gemini|claude` (auto = Gemini first, Claude Haiku 4.5 as
fallback), `CLAUDE_MODEL` (default `claude-haiku-4-5`), `RAG_DATA_DIR`
(default `./rag_data`), `RAG_OCR_LANGS` (default `ind+eng`). Without any LLM
key, `/api/answer` still returns the top passages, with status
`llm_unavailable`.

## Pipeline

```
upload ─► parse / OCR ─► chunk ─► metadata ─► index (SQLite + FTS5)
                                                     │
incident context ─► hard filters ─► BM25 candidates ─► scoring layer ─► top-k + safety slots ─► LLM (cited JSON) ─► post-check
                                                          │                                     
                                                          └─► below threshold: abstain ("no grounded source")
```

| Stage | Module | Notes |
| --- | --- | --- |
| Upload | `rag/ingest.py` | Supervisor/admin only (`X-User-Role` header until real auth exists). SHA-256 dedupe per plant. A file in the same family (`SOP_x_rev2.pdf` after `SOP_x_rev1.pdf`, or an explicit `family`) becomes v2, and v1 is marked `superseded` only after v2 is indexed. Status: `pending → processing → indexed / failed / superseded` |
| Parse / OCR | `rag/parse.py` | PyMuPDF text layer, heading detection (font size, bold, `3.2`, `BAB II`), `find_tables()` for tables. Pages with no text are OCR'd at 300 dpi; images are wrapped into a 1-page PDF so they share the OCR path and the viewer. Per-page OCR confidence; pages below 0.70 are flagged for review |
| Chunk | `rag/chunking.py` | Never crosses a heading or a page (so `chunk_id = doc_id:page:ordinal` and its bbox are exact). Max 800 tokens, sentence-split when longer. Tables are always their own chunk. Each chunk points at its parent section |
| Metadata | `rag/metadata.py` | Deterministic regexes for fault codes (`Alarm 108 → ALM108`, `SV0401`, `SAFE-003`), part numbers, and effective dates (ID and EN month names). Machines come from the SAP mock catalog (explicit `CNC-02` wins over a model match). Doc type is a keyword vote; the LLM is used only when that is not confident (< 0.6). Constraint tier (Safety / Quality / Cost) matches the solver |
| Index | `rag/store.py` | `documents`, `sections`, `chunks`, `chunks_fts` (header = title + section path, weighted 2×), `retrieval_log` |

## Scoring layer (the decision model)

`S = w_R·R + w_M·M + w_F·F + w_A·A − w_P·P`, defaults `0.45 / 0.25 / 0.10 / 0.15 / 0.15`
(override in `rag/scoring_config.json` or `RAG_SCORING_JSON`).

- **R relevance** = `0.7 · coverage + 0.3 · bm25_norm`. Coverage is the IDF-weighted share of query concepts
  (each query word plus its translation; the disruption type maps to bilingual synonyms) found in the
  chunk. Coverage is absolute, which is what makes abstention work. BM25 here uses topical terms only, so
  machine aliases and fault-code variants, which are used for recall, can't make an off-topic chunk look relevant.
- **M metadata**: +0.6 for an exact fault-code hit on the chunk; +0.4 when the chunk names the machine,
  +0.25 when the document is for that machine model, +0.1 for a plant-wide document.
- **F freshness**: 0 if superseded; 1.0 within a year of the effective date, falling to 0.5 at 5 years; 0.8 if the date is unknown.
- **A authority**: OEM manual 1.0, safety 0.95, SOP 0.85, quality 0.8, maintenance log 0.6, other 0.5.
- **P OCR penalty**: `1 − confidence` for scanned passages.

The system abstains when no passage has `S ≥ 0.40` and `R ≥ 0.20`. Safety-tier chunks for the incident machine
(or from plant-wide documents) are always added in reserved slots, 2 normally and 3 when severity is
`CRITICAL`, so they can't crowd out the token budget. Every result carries its breakdown and the reasons
behind it, and the cockpit shows these as a stacked bar.

## Generation

`rag/generate.py` packs the top chunks, expanded to their parent section within a 4k-token budget,
as `<source id="S1">` blocks. Lines that look like instructions are neutralised first (prompt-injection guard).
The LLM must return `{"claims": [{"text", "sources": ["S1"]}]}`. The post-check rejects a claim if:

- it cites an id that doesn't exist,
- a number in the claim (temperature, minutes, part number) is missing from the cited text, or
- lexical support is under 34%. Support is measured through the ID↔EN lexicon, so an Indonesian claim can be checked against an English manual.

Rejected claims are returned separately and are never part of the answer.
`/api/disrupt` now retrieves passages for the incident and passes them to the Gemini summary prompt. It also returns
`sources` and `grounding_abstained`. The cockpit shows these as citation chips that open the page with the passage
highlighted.

## API

| Endpoint | Purpose |
| --- | --- |
| `POST /api/documents` (multipart) | Upload: `file`, optional `title`, `doc_type`, `machine_ids`, `effective_date`, `family`, `acl`, `force_ocr` |
| `GET /api/documents`, `GET /api/documents/{id}` | Status, metadata, fault codes, part numbers, chunks per tier, OCR warnings |
| `GET /api/documents/{id}/chunks`, `/file`, `/pages/{n}.png?chunk_id=` | Chunks, original file, rendered page with highlighted bbox |
| `POST /api/documents/{id}/reindex` | Re-run the pipeline (for example after installing `tesseract-ocr-ind`) |
| `POST /api/retrieve` | `{incident, query, top_k?, mode?}` → scored chunks + breakdown, filters applied, latency |
| `POST /api/answer` | `{incident, query, provider?}` → cited claims, rejected claims, sources, or abstention |
| `GET /api/rag/status`, `POST /api/rag/seed-demo` | Index stats / demo corpus |

## Evaluation

`python -m rag.eval` runs the golden set in three ablation modes: `bm25` → `bm25_filters` → `full`.
On the demo corpus, recall@5 is 1.0 in every mode, MRR goes 0.83 → 0.93 → 1.0, the off-topic question
abstains, and retrieval p95 is about 5 ms. The demo corpus is tiny and fictional, so treat these numbers as a
sanity check. Before tuning weights, build a real golden set of 50–100 questions from the plant's own
documents (format in `rag/eval.py`). `--answer` adds citation precision and faithfulness once an LLM key is set.

## Known limits / next steps

- Roles come from a header; replace with real auth before anything leaves the demo.
- Tesseract `ind` data improves Bahasa scans. Without it OCR falls back to `eng`, and the cockpit shows a warning.
- The lexical post-check is conservative. If it rejects good cross-language claims, add terms to `LEXICON` in `rag/text.py`.
- Single-process SQLite matches today's single-worker uvicorn. Move to Postgres together with the SAP state.
- P4 (expose retrieval as an agent tool / skill-generation input) is partly there: `/api/disrupt` already grounds its summary.
