# SkillForge: Supervised Autonomy Agentic AI for Industrial Operations

**Team:** Choco Munchacho  
**Track:** Intelligent Manufacturing & Supply Chain  

## Overview
SkillForge is an Agentic AI Supervisor operating under supervised autonomy for Indonesian manufacturers (automotive, textile, FMCG in Cikarang/Karawang).
It bridges shop-floor disruptions (e.g. CNC motor overloads) with SAP S/4HANA:
- **Autonomy in Analysis:** Evaluates disruptions, loads declarative skills, and solves constraint-valid reschedules via OR-Tools CP-SAT in <60 seconds.
- **Human Authority in Execution:** Generates 3 Rupiah-priced scenarios, shows field-level diffs, requires 1-click supervisor approval, and checks for ERP drift before writing back.

## Project Structure
- `skills/` — Declarative skill playbooks (`SKILL.md`), tier-based constraint rules (`rules/`), and telemetry triggers (`hooks.json`).
- `solver/` — Deterministic OR-Tools CP-SAT scheduling engine (Safety > Quality > Cost in IDR).
- `erp_adapter/` — SAP S/4HANA OData mock/sandbox client with drift detection and append-only audit ledger.
- `plc_adapter/` — PLC layer: a simulator and an OPC UA driver behind one interface. Machines stream tags and alarm codes over `/ws/plc`; trends predict faults before the alarm, alarms pull up past fixes, and setpoint changes from a per-machine limits file are written only after pre-action checks and a supervisor approval (or limited autonomy), each logged in the audit ledger. See `docs/PLC.md`.
- `backend/` — FastAPI orchestration service powering incident streams, scenarios, and skill generation.
- `rag/` — Metadata-augmented RAG: plant documents (manuals, SOPs, logs, K3) are OCR'd, chunked and indexed with machine / fault-code / tier metadata; an incident-aware scoring layer picks passages and every AI claim is cited. See `docs/RAG.md`.
- `frontend/` — React Supervisor Cockpit with live Gantt chart, Bahasa Indonesia alerts, and 1-click approvals.
- `knowledge/` — Maintenance ledger (resolved cases, playbooks, signed expert notes) and the proactive agent that routes a new alarm to a mechanic with a cited repair playbook.

## Agent run
On a live PLC alarm the agent plans and runs one chained loop by itself (`backend/agent_run.py`): raise the alarm, find similar cases, use the skill that matches the alarm, route to the right mechanic, check and order spare parts, reroute work orders with the solver, and send the playbook to the mechanic on Telegram. Only the irreversible SAP writes (parts and reroute) wait for one supervisor sign-off. The chatbot reads the same runs and skills, so it can explain what the agent did and which skill it used.

## Skill Studio
Skills hold the plant's expert know-how (`skills/<id>/skill.json`, schema `skillforge_skill_v2`): trigger, checks, ranked root causes with sources, mechanic tasks and allowed actions. The agent writes skills; experts correct and sign them.
- **Agent proposals:** resolved cases that no skill covers yet, grouped by alarm code, become a proposed skill. A repair that a skill got wrong becomes a proposed change (v1.1). A field-trial skill that repairs keep confirming is proposed for promotion.
- **Expert notes:** know-how that was never logged as a case is signed as `EXPERT-xxxx` and can be cited like a case.
- **Field trial:** a skill built from expert notes alone starts as `trial`. It advises but never triggers a PLC action, until 2 repairs confirm it.
- **Test run:** every skill is tested on an alarm generated from its own trigger (or a PLC simulator scenario), with a backtest against past cases. Nothing is written to the PLC or SAP. Approval needs a passed test of the same skill.json.
- **Learning loop:** closing a repair records whether the skill that handled the alarm was right (`SKILL_FEEDBACK` in the audit ledger).

Main endpoints: `GET /api/skills`, `GET /api/skills/proposals`, `POST /api/skills/proposals/{id}/draft`, `POST /api/skills/expert-notes`, `POST /api/skills/compose`, `POST /api/skills/test-run`, `POST /api/skills/approve`. Full API at `/docs`.

## Run locally
- Backend: `pip install -r requirements.txt` then `uvicorn backend.main:app --port 8000`
- Frontend: `cd frontend && npm install && VITE_API_BASE=http://localhost:8000 npm run dev`
- Tests: `python -m pytest -q`

# Sokrates
