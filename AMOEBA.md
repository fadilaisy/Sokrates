# AMOEBA.md — Repository-Wide Rules

Project: **SkillForge** — Supervised Autonomy Agentic AI Supervisor for Indonesian manufacturers.

## Stack
- Backend: FastAPI (`backend/main.py`), OR-Tools CP-SAT (`solver/`), SAP S/4HANA mock adapter (`erp_adapter/`)
- AI: Gemini 3.5 Flash via Google Generative AI SDK (env `GEMINI_MODEL`, default `gemini-3.5-flash`)
- Skills: declarative playbooks under `skills/<skill>/SKILL.md` + `rules/` + `hooks.json`

## Rules
1. **Tier hierarchy is inviolable:** Tier 1 Safety > Tier 2 Quality/SLA > Tier 3 Cost. Never let cost optimization override safety interlocks (`skills/cnc_milling/rules/safety_interlocks.json`).
2. **Human authority in execution:** any scenario with SLA penalty ≥ Rp 1.000.000 requires supervisor approval before applying. Auto-apply only below threshold.
3. **All ERP writes go through** `SAPClient.apply_delta()` with optimistic-locking version check; every approval is appended to the SHA-256 hash-chained audit ledger (`erp_adapter/audit_ledger.py`). Never mutate `mock_sap_state.json` directly.
4. **Determinism:** solver outputs must be reproducible; no randomness in `solver/`.
5. **Language:** user-facing output (alerts, summaries, SKILL.md operator sections) is Bahasa Indonesia; code comments/docs bilingual where practical.
6. **Costs in IDR (Rupiah)** — never USD in operator-facing figures.

## Commands
- Install: `pip install -r requirements.txt` (after updating with `pip install google-generativeai`)
- Run backend: `uvicorn backend.main:app --reload --port 8000` (or `python backend/main.py`)
- Docs: http://localhost:8000/docs
- Copy `.env.example` → `.env` and set `GEMINI_API_KEY` before enabling AI summaries.

## Notes
- No `.env`/secrets committed (`.gitignore` covers it).
- Optional pre-build hook may be configured in `.amoeba/hooks.json` (`beforeBuild` command).
