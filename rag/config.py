"""
rag/config.py
─────────────
Central configuration for the RAG layer. Everything is env-overridable so the
same code runs in the demo, in tests (tmp dirs) and later on-prem.
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass, field, asdict
from pathlib import Path
from typing import Dict, Optional

ROOT = Path(__file__).resolve().parent.parent


def data_dir() -> Path:
    """Where the SQLite DB and original uploads live (gitignored)."""
    d = Path(os.getenv("RAG_DATA_DIR", str(ROOT / "rag_data")))
    d.mkdir(parents=True, exist_ok=True)
    (d / "files").mkdir(exist_ok=True)
    return d


def db_path() -> Path:
    return data_dir() / "rag.sqlite3"


# ── Chunking ────────────────────────────────────────────────────────────────
CHUNK_MIN_TOKENS = int(os.getenv("RAG_CHUNK_MIN_TOKENS", "300"))
CHUNK_MAX_TOKENS = int(os.getenv("RAG_CHUNK_MAX_TOKENS", "800"))

# ── OCR ─────────────────────────────────────────────────────────────────────
OCR_LANGS = os.getenv("RAG_OCR_LANGS", "ind+eng")       # filtered to what's installed
OCR_DPI = int(os.getenv("RAG_OCR_DPI", "300"))
OCR_LOW_CONFIDENCE = float(os.getenv("RAG_OCR_LOW_CONF", "0.70"))
TEXT_LAYER_MIN_CHARS = 25                                # below → page needs OCR

# ── Upload authority ────────────────────────────────────────────────────────
UPLOAD_ROLES = {"supervisor", "admin"}
DEFAULT_ACL = ["operator", "supervisor", "admin", "agent"]
DEFAULT_PLANT = os.getenv("RAG_DEFAULT_PLANT", "KPMI-KRW")
MAX_UPLOAD_MB = int(os.getenv("RAG_MAX_UPLOAD_MB", "50"))


# ── Scoring layer ───────────────────────────────────────────────────────────
@dataclass
class ScoringConfig:
    """
    S = w_relevance·R + w_metadata·M + w_freshness·F + w_authority·A − w_ocr_penalty·P

    Weights are deliberately simple numbers so a supervisor can read the
    breakdown and see why a passage ranked where it did.
    """
    w_relevance: float = 0.45
    w_metadata: float = 0.25
    w_freshness: float = 0.10
    w_authority: float = 0.15
    w_ocr_penalty: float = 0.15

    # Abstain when no non-safety passage reaches this score …
    abstain_threshold: float = 0.40
    # … or when its topical relevance is below this floor.
    min_relevance: float = 0.20

    candidate_pool: int = 50          # BM25 candidates before scoring
    top_k: int = 6                    # passages returned / packed for the LLM
    safety_slots: int = 2             # reserved Safety-tier passages (3 when CRITICAL)
    safety_slots_critical: int = 3

    # Blend inside R: absolute concept coverage vs. relative BM25 rank.
    r_coverage_weight: float = 0.7

    authority_by_doc_type: Dict[str, float] = field(default_factory=lambda: {
        "oem_manual": 1.00,
        "safety": 0.95,
        "sop": 0.85,
        "quality": 0.80,
        "maintenance_log": 0.60,
        "other": 0.50,
    })

    def to_dict(self) -> dict:
        return asdict(self)


def load_scoring_config() -> ScoringConfig:
    """Defaults ← rag/scoring_config.json (if present) ← RAG_SCORING_JSON env."""
    cfg = ScoringConfig()
    overrides: dict = {}
    f = Path(os.getenv("RAG_SCORING_FILE", str(Path(__file__).parent / "scoring_config.json")))
    if f.exists():
        try:
            overrides.update(json.loads(f.read_text(encoding="utf-8")))
        except Exception:
            pass
    env_json = os.getenv("RAG_SCORING_JSON")
    if env_json:
        try:
            overrides.update(json.loads(env_json))
        except Exception:
            pass
    for k, v in overrides.items():
        if hasattr(cfg, k):
            setattr(cfg, k, v)
    return cfg


# ── Generation ──────────────────────────────────────────────────────────────
GEN_PROVIDER = os.getenv("RAG_GEN_PROVIDER", "auto")    # auto | gemini | claude
CONTEXT_TOKEN_BUDGET = int(os.getenv("RAG_CONTEXT_TOKENS", "4000"))


def gemini_model() -> str:
    return os.getenv("GEMINI_MODEL", "gemini-3.5-flash")


def claude_model() -> str:
    return os.getenv("CLAUDE_MODEL", "claude-haiku-4-5")


def gemini_key() -> Optional[str]:
    return os.getenv("GEMINI_API_KEY") or None


def anthropic_key() -> Optional[str]:
    return os.getenv("ANTHROPIC_API_KEY") or None
