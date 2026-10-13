"""
rag/
────
Metadata-augmented RAG for Sokrates / SkillForge.

No embeddings. Candidates come from SQLite FTS5 (BM25) under hard metadata
filters, then a deterministic scoring layer (decision model) ranks them:

    S = w1·R + w2·M + w3·F + w4·A − w5·P

The top passages are handed to a generative LLM (Gemini, or Claude Haiku 4.5)
that must cite every claim; a post-check drops anything the sources do not
support. Below the score threshold the system abstains.

Modules
-------
config.py     paths, weights, thresholds (env-overridable)
text.py       tokenizing, stopwords (ID+EN), normalization helpers
metadata.py   deterministic extraction: fault codes, part numbers, dates,
              machine models, doc type, constraint tier, language
parse.py      PDF text layer (PyMuPDF), table detection, Tesseract OCR fallback
chunking.py   structure-aware chunking with parent sections
store.py      SQLite schema (documents, sections, chunks, FTS5 index)
ingest.py     the five-stage pipeline: upload → parse/OCR → chunk → metadata → index
scoring.py    the scoring layer (decision model) with explainable breakdown
retrieve.py   incident context → hard filters → candidates → score → abstain
llm.py        Gemini + Claude Haiku 4.5 REST clients (sync, httpx)
generate.py   cited answer generation, injection guard, citation post-check
api.py        FastAPI router: /api/documents, /api/retrieve, /api/answer
eval.py       golden-set evaluation harness (recall@k, MRR, nDCG, ablation)
samples.py    demo documents + golden set generator for the hackathon demo
"""
