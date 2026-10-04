# SkillForge — Project Handover Document

> **Project:** SkillForge — Agentic AI Supervisor for Indonesian Manufacturers  
> **Hackathon:** Sokrates Hackathon  
> **Repo:** https://github.com/fadilaisy/Sokrates  
> **Handover Date:** 04 October 2026  
> **Stack:** Python 3.9 · FastAPI · OR-Tools CP-SAT · Claude Haiku (Anthropic) · SAP S/4HANA Mock  

---

## 1. What SkillForge Does

SkillForge is an **Agentic AI Supervisor** for CNC manufacturing shop floors in Indonesia.  
When a machine breaks down, it automatically:

1. Detects the disruption via telemetry or manual input
2. Runs an **OR-Tools CP-SAT solver** to generate 3 recovery scenarios in seconds
3. Asks **Claude Haiku** to write a Bahasa Indonesia executive summary with IDR cost figures
4. Presents scenarios to a human supervisor for one-click approval
5. Applies the approved schedule change to the mock SAP system with **drift detection** (optimistic locking)
6. Writes every decision to a **tamper-evident SHA-256 hash-chained audit ledger**

---

## 2. Repository Structure

```
sokrates hackathon/
│
├── .env.example                  ← Copy to .env and fill in your API key
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
│   └── schedule_solver.py        ← OR-Tools CP-SAT — generates Scenarios A / B / C
│
└── skills/cnc_milling/
    ├── SKILL.md                  ← Bilingual EN/ID AI Supervisor playbook
    ├── hooks.json                ← Telemetry trigger hooks (5 hooks)
    └── rules/
        ├── safety_interlocks.json  ← Tier 1 safety rules (5 rules, never overridable)
        └── sla_penalties.json      ← SLA penalty matrix in IDR (Classes A / B / C)
```

---

## 3. Environment Setup

### Prerequisites
- macOS with Python 3.9+ installed
- An Anthropic API key (Claude Haiku)

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
ANTHROPIC_API_KEY=sk-ant-xxxxxxxxxxxxxxxxxxxxxxxx
ANTHROPIC_MODEL=claude-haiku-4-5
PORT=8000
```

Get your API key at: https://console.anthropic.com/settings/keys

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
| `POST` | `/api/disrupt` | Trigger disruption → CP-SAT solver → Claude summary |
| `POST` | `/api/approve` | Approve scenario, apply to SAP, write audit entry |
| `GET` | `/api/ledger` | Full tamper-evident audit ledger |
| `GET` | `/api/ledger/verify` | SHA-256 hash-chain integrity check |
| `POST` | `/api/skills/generate` | Generate a new SKILL.md from natural language |
| `WS` | `/ws/telemetry` | Real-time telemetry stream + AI analysis |
| `GET` | `/health` | Health check (Claude configured? SAP version?) |

### Example: Trigger a disruption

```bash
curl -X POST http://localhost:8000/api/disrupt \
  -H "Content-Type: application/json" \
  -d '{"machine_id": "CNC-02", "disruption_type": "BREAKDOWN", "start_hour": 2, "end_hour": 5}'
```

Response includes:
- `scenarios` — 3 options (Status Quo / Lembur / Rerute Optimal) with IDR costs
- `claude_summary` — 2-3 sentence Bahasa Indonesia recommendation from Claude Haiku
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

The solver recommends whichever scenario has the **highest `net_savings_idr`**.

---

## 6. SLA Penalty Matrix

| Order Class | Customer Type | Penalty/Hour | Max Penalty | Auto-resolve below |
|-------------|--------------|-------------|------------|-------------------|
| **A** | OEM Automotive (Toyota, Honda) | Rp 750.000 | Rp 15.000.000 | Rp 500.000 |
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

**Supported metrics:**
- `motor_temp_celsius` — warning >85°C, critical >95°C
- `spindle_vibration_mm_per_s` — warning >5, critical >8
- `coolant_pressure_bar` — warning <2.5, critical <2.0
- `concurrent_axis_faults` — critical ≥2

---

## 11. Key Design Decisions

| Decision | Reason |
|----------|--------|
| **Optimistic locking** on SAP state via `version` field | Prevents two simultaneous approvals from corrupting state without needing a database |
| **SHA-256 hash chaining** in audit ledger | Tamper-evident record keeping, verifiable without blockchain infrastructure |
| **CP-SAT 5-second timeout** | Keeps `/api/disrupt` latency fast enough for demo use |
| **`run_in_executor` for Claude** | Claude SDK is synchronous; executor prevents blocking the async FastAPI event loop |
| **Three-tier constraint hierarchy** | Mirrors real industrial ISA-95 / safety engineering practice |
| **SKILL.md as declarative playbook** | Domain knowledge is separated from solver code — skills are swappable without changing Python |

---

## 12. Known Limitations & Future Work

| Item | Notes |
|------|-------|
| Mock SAP state resets on server restart | No persistent DB — wire to a real SAP OData API or PostgreSQL for production |
| Claude model name | `claude-haiku-4-5` — verify latest model ID at console.anthropic.com if errors occur |
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
70df636  refactor: swap Gemini for Claude Haiku (Anthropic)
5ab97bb  feat: SkillForge complete Python backend
c558251  Add files via upload (initial repo creation)
```

---

## 15. Contact

**Repo owner:** [@fadilaisy](https://github.com/fadilaisy)  
**GitHub:** https://github.com/fadilaisy/Sokrates  

---

*Generated automatically on 04 October 2026, 22:04 WIB*
