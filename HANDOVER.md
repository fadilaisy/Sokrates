# SkillForge — Project Handover Document

> **Project:** SkillForge — Agentic AI Supervisor for Indonesian Manufacturers  
> **Hackathon:** Sokrates Hackathon  
> **Repo:** https://github.com/fadilaisy/Sokrates  
> **Handover Date:** 10 October 2026 (Phases 1–6 verified complete)  
> **Stack:** Python 3.9 · FastAPI · OR-Tools CP-SAT · Gemini 3.5 Flash (Google) · SAP S/4HANA Mock  

---

## 1. What SkillForge Does

SkillForge is an **Agentic AI Supervisor** for CNC manufacturing shop floors in Indonesia.  
When a machine breaks down, it automatically:

1. Detects the disruption via telemetry or manual input
2. Runs an **OR-Tools CP-SAT solver** to generate 3 recovery scenarios in seconds
3. Asks **Gemini 3.5 Flash** to write a Bahasa Indonesia executive summary with IDR cost figures
4. Presents scenarios to a human supervisor for one-click approval
5. Applies the approved schedule change to the mock SAP system with **drift detection** (optimistic locking)
6. Writes every decision to a **tamper-evident SHA-256 hash-chained audit ledger**

---

## 2. Repository Structure

```
sokrates hackathon/
│
├── .env.example                  ← Copy to .env and fill in your API key
├── .github/workflows/ci.yml      ← Backend pytest + frontend tsc/build
├── .gitignore                    ← .env, __pycache__, audit_ledger.jsonl excluded
├── requirements.txt              ← All Python dependencies
├── README.md                     ← Project overview
│
├── backend/
│   └── main.py                   ← FastAPI app — all REST endpoints + WebSocket
│
├── erp_adapter/
│   ├── sap_client.py             ← SAP S/4HANA mock with optimistic locking
│   ├── audit_ledger.py           ← Append-only JSONL ledger with SHA-256 chaining
│   └── mock_sap_state.json       ← PT Karawang shop floor data (5 machines, 8 orders)
│
├── solver/
│   └── schedule_solver.py        ← OR-Tools CP-SAT — generates Scenarios A / B / C (skill-driven costs)
│
├── skills/
│   ├── loader.py                 ← SkillRegistry + safe condition parser (no eval)
│   └── cnc_milling/
│       ├── SKILL.md              ← Bilingual EN/ID AI Supervisor playbook
│       ├── hooks.json            ← Telemetry trigger hooks (5 hooks)
│       └── rules/
│           ├── safety_interlocks.json  ← Tier 1 safety rules (5 rules, never overridable)
│           └── sla_penalties.json      ← SLA penalty matrix in IDR (Classes A / B / C)
│
├── tests/
│   ├── test_solver.py            ← Solver cost/breakdown/constraint tests
│   ├── test_skills_loader.py     ← Registry + safe condition parser tests
│   ├── test_skill_driven.py      ← Skill-file-drives-system regression tests (Phase 2)
│   └── test_api.py               ← REST contract tests via TestClient (Phase 6)
│
└── scripts/
    └── manual_api_check.py       ← Manual live-server check (not collected by pytest)
```

---

## 3. Environment Setup

### Prerequisites
- macOS with Python 3.9+ installed
- A Google Gemini API key (Gemini 3.5 Flash)

### First-time setup

```bash
# 1. Clone the repo
git clone https://github.com/fadilaisy/Sokrates.git
cd Sokrates

# 2. Install dependencies
python3 -m pip install -r requirements.txt

# 3. Create your .env file
cp .env.example .env
```

Edit `.env` and fill in your key:

```env
GEMINI_API_KEY=your-gemini-api-key-here
GEMINI_MODEL=gemini-3.5-flash
PORT=8000
```

Get your API key at: https://aistudio.google.com/app/apikey

### Start the server

```bash
python3 -m uvicorn backend.main:app --reload --port 8000
```

Open **http://localhost:8000/docs** for the interactive Swagger UI.

---

## 4. API Endpoints

| Method | Endpoint | Description |
|--------|----------|-------------|
| `GET` | `/api/state` | Full SAP mock state (machines, orders, maintenance) |
| `POST` | `/api/state/reset` | Reset SAP mock to baseline (ledger is kept) |
| `POST` | `/api/disrupt` | Trigger disruption → CP-SAT solver → Gemini summary. Logs `DISRUPTION_DETECTED` + `SCENARIOS_GENERATED` |
| `POST` | `/api/disrupt/inject` | Inject a real second disruption (sets machine status, bumps version, logs `EXTERNAL_CHANGE`) — failure-drill trigger for real 409s |
| `POST` | `/api/approve` | Approve scenario, apply to SAP, write audit entry (409 on stale `expected_sap_version`) |
| `GET` | `/api/ledger` | Full tamper-evident audit ledger |
| `GET` | `/api/ledger/verify` | SHA-256 hash-chain integrity check |
| `POST` | `/api/ledger/{entry_id}/revert` | Revert an approved scenario via inverse delta, logs `SCENARIO_REVERTED` |
| `POST` | `/api/skills/generate` | Generate a SKILL.md playbook from natural language |
| `POST` | `/api/chat` | Hybrid-chat fallback: free-form Q&A grounded on live SAP state, text-only (never approves/writes) |
| `POST` | `/api/skills/draft` | Skill Studio: build skill files from guided interview (rejects Tier 1 relaxation) |
| `POST` | `/api/skills/lint` | Skill Studio: lint skill files — Tier 1 rules can only get stricter, never `override_allowed` |
| `POST` | `/api/skills/approve` | Skill Studio: write skill to `skills/<id>/`, reload registry, log `SKILL_APPROVED` |
| `WS` | `/ws/telemetry` | Real-time telemetry stream + AI analysis (severity from skill files) |
| `GET` | `/health` | Health check (Gemini configured? SAP version?) |

### Example: Trigger a disruption

```bash
curl -X POST http://localhost:8000/api/disrupt \
  -H "Content-Type: application/json" \
  -d '{"machine_id": "CNC-02", "disruption_type": "BREAKDOWN", "start_hour": 2, "end_hour": 5}'
```

Response includes:
- `scenarios` — 3 options (Status Quo / Lembur / Rerute Optimal) with IDR costs
- `claude_summary` — 2-3 sentence Bahasa Indonesia recommendation from Gemini 3.5 Flash (kept as-is for backward compatibility)
- `sap_version` — current version number for optimistic locking

### Example: Approve a scenario

```bash
curl -X POST http://localhost:8000/api/approve \
  -H "Content-Type: application/json" \
  -d '{
    "scenario_id": "scenario_c",
    "scenario_data": { "gantt_changes": [...] },
    "expected_sap_version": 1,
    "approved_by": "Production Supervisor — Budi"
  }'
```

Returns an audit receipt with `receipt_id` and `sha256_hash`.

---

## 5. The Three Solver Scenarios

Every disruption produces exactly 3 scenarios from the CP-SAT solver:

| ID | Name | Logic | Cost |
|----|------|-------|------|
| `scenario_a` | **Status Quo** | Do nothing, accept delays | SLA penalties only |
| `scenario_b` | **Lembur (Overtime)** | Move orders to night shift | Rp 450.000/hr/machine |
| `scenario_c` | **Rerute Optimal** | CP-SAT reroutes to free machines | Changeover (Rp 350.000) + optional overtime |

The solver recommends whichever scenario has the **lowest `total_cost_idr`**.

Costs are **not hardcoded**: every `solve()` reads `skills/cnc_milling/rules/sla_penalties.json`
fresh from disk (module constants are fallback defaults only), so editing the skill file
changes solver output with no code change or restart.

Tier 1 safety rule (inviolable): the solver never routes work to a machine whose status is
`MAINTENANCE` or `FAULT` (`TIER1_BLOCKED_STATUSES` in `solver/schedule_solver.py`).

---

## 6. SLA Penalty Matrix (live from `skills/cnc_milling/rules/sla_penalties.json`)

| Order Class | Customer Type | Penalty/Hour | Max Penalty | Auto-resolve below |
|-------------|--------------|-------------|------------|-------------------|
| **A** | OEM Automotive (Toyota, Honda) | Rp 20.000.000 | Rp 150.000.000 | Rp 500.000 |
| **B** | General Industry (Pertamina, Astra) | Rp 300.000 | Rp 6.000.000 | Rp 500.000 |
| **C** | MRO / Internal Spare Parts | Rp 100.000 | Rp 2.000.000 | Rp 500.000 |

Supervisor approval required above **Rp 1.000.000** estimated penalty.  
Plant Manager approval required above **Rp 5.000.000**.

---

## 7. Safety Constraint Hierarchy

```
TIER 1 — SAFETY (inviolable, never overridable)
  SAFE-001  Motor temp > 95°C           → Emergency stop
  SAFE-002  Spindle vibration > 8 mm/s  → Emergency stop
  SAFE-003  Coolant pressure < 2 bar    → Stop machining cycle
  SAFE-004  E-Stop activated            → Full machine halt
  SAFE-005  ≥2 concurrent axis faults   → Emergency stop + OEM support

TIER 2 — QUALITY & SLA (hard, Supervisor approval to override)
  Driven by sla_penalties.json

TIER 3 — COST & EFFICIENCY (soft, minimised by CP-SAT solver)
  Driven by schedule_solver.py
```

---

## 8. Audit Ledger

Every approved scenario is written to `audit_ledger.jsonl` at the project root.  
Each entry is SHA-256 chained to the previous one — modifying any historical record breaks all subsequent hashes.

**Verify integrity:**
```bash
curl http://localhost:8000/api/ledger/verify
```

Expected response when clean:
```json
{ "valid": true, "total_entries": 5, "message": "Chain intact — all 5 entries verified." }
```

---

## 9. Mock SAP Data — PT Karawang Precision Manufacturing

The mock represents a real CNC shop floor in Karawang, West Java.

**Machines:**
| ID | Machine | Status |
|----|---------|--------|
| CNC-01 | Haas VF-2 No.1 | Running |
| CNC-02 | Haas VF-2 No.2 | Running |
| CNC-03 | Fanuc Robodrill | Idle |
| CNC-04 | Mazak Variaxis i-500 (5-axis) | Running |
| CNC-05 | DMG MORI NLX 2500 | Maintenance |

**Orders:** 8 production orders across classes A, B, C for customers including Toyota, Honda, Pertamina, Astra, and Medco Energi.

---

## 10. WebSocket Telemetry

Connect to `ws://localhost:8000/ws/telemetry` and send JSON frames:

```json
{ "machine_id": "CNC-04", "metric": "motor_temp_celsius", "value": 91.5 }
```

The server streams back real-time AI analysis + safety alerts.

**Supported metrics (severity driven by skill files, not hardcoded):**
- `motor_temp_celsius` — WARNING via `hooks.json` HOOK-001 (>85°C), CRITICAL via `safety_interlocks.json` SAFE-001 (>95°C)
- `spindle_vibration_mm_per_s` — WARNING via HOOK-002 (>5), CRITICAL via SAFE-002 (>8)
- `coolant_pressure_bar` — WARNING via HOOK-003 (<2.5), CRITICAL via SAFE-003 (<2.0)
- `concurrent_axis_faults` — CRITICAL via SAFE-005 (≥2)
- `emergency_stop_activated` — CRITICAL via SAFE-004 (= true)

`_classify_telemetry` in `backend/main.py` evaluates Tier 1 interlocks first, then WARNING
hooks, both re-read from disk per event — editing either JSON changes classification live.
A hardcoded table remains as fallback only when skill files are unreadable.

### Skill Studio (frontend overlay)
Sidebar **BANTUAN → Skill Studio** (also in mobile nav) opens a guided
interview → draft → lint → approve drawer backed by `/api/skills/*`.
`SkillStudio.tsx` is mounted globally in `Shell` and driven by the store's
`skillStudio*` state. Tier 1 relaxation is rejected at draft time and again at lint.

---

## 11. Key Design Decisions

| Decision | Reason |
|----------|--------|
| **Optimistic locking** on SAP state via `version` field | Prevents two simultaneous approvals from corrupting state without needing a database |
| **SHA-256 hash chaining** in audit ledger | Tamper-evident record keeping, verifiable without blockchain infrastructure |
| **CP-SAT 5-second timeout** | Keeps `/api/disrupt` latency fast enough for demo use |
| **`run_in_executor` for Gemini** | Gemini SDK is synchronous; executor prevents blocking the async FastAPI event loop |
| **Three-tier constraint hierarchy** | Mirrors real industrial ISA-95 / safety engineering practice |
| **SKILL.md as declarative playbook** | Domain knowledge is separated from solver code — skills are swappable without changing Python |
| **Skill-driven costs + telemetry** | Solver and WebSocket classifier read `sla_penalties.json` / `safety_interlocks.json` / `hooks.json` fresh from disk; Tier 1 statuses are never routed to |
| **Real failure drill** | `/api/disrupt/inject` truly mutates SAP (+1 version, `EXTERNAL_CHANGE`); approval sends the analysis-time version, so a post-analysis inject yields a real 409 followed by automatic re-solve |
| **Audited analysis** | `/api/disrupt` logs `DISRUPTION_DETECTED` + `SCENARIOS_GENERATED`; startup logs `SKILLS_LOADED`; approver name is an editable field written to every entry; ledger drawer has type filters + per-entry revert |

---

## 12. Known Limitations & Future Work

| Item | Notes |
|------|-------|
| Frontend | `frontend/` is a SkillForge cockpit (Dasbor / Cockpit / Lantai Pabrik + Skill Studio overlay). Downtime & OEE cards are labelled "data contoh" until the backend exposes them. |
| Mock SAP state resets on server restart | No persistent DB — wire to a real SAP OData API or PostgreSQL for production |
| Gemini model name | `gemini-3.5-flash` — verify latest model ID at aistudio.google.com if errors occur |
| Single-machine solver | CP-SAT currently solves one disruption at a time — extend for multi-disruption scenarios |
| No authentication | Add OAuth2 / API key middleware before production deployment |
| Frontend | The `frontend/` directory exists but is separate — connect it to the API using the Swagger docs |

---

## 13. Running in Production

```bash
# Without --reload flag, with multiple workers
python3 -m uvicorn backend.main:app --host 0.0.0.0 --port 8000 --workers 4
```

> ⚠️ The in-memory SAP mock and AuditLedger are **not safe with multiple workers** — they will have separate memory spaces. For multi-worker production use, replace with a shared database (PostgreSQL / Redis).

---

## 14. Git History

```
f8a2b1c  refactor: swap Claude Haiku for Gemini 3.5 Flash (Google)
5ab97bb  feat: SkillForge complete Python backend
c558251  Add files via upload (initial repo creation)
```

---

## 15. Contact

**Repo owner:** [@fadilaisy](https://github.com/fadilaisy)  
**GitHub:** https://github.com/fadilaisy/Sokrates  

---

*Generated automatically on 04 October 2026, 22:04 WIB*
