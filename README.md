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
- `backend/` — FastAPI orchestration service powering incident streams, scenarios, and skill generation.
- `rag/` — Metadata-augmented RAG: plant documents (manuals, SOPs, logs, K3) are OCR'd, chunked and indexed with machine / fault-code / tier metadata; an incident-aware scoring layer picks passages and every AI claim is cited. See `docs/RAG.md`.
- `frontend/` — React Supervisor Cockpit with live Gantt chart, Bahasa Indonesia alerts, and 1-click approvals.
# Sokrates
