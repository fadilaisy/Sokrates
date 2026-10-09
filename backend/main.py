"""
backend/main.py
───────────────
SkillForge FastAPI backend — Agentic AI Supervisor for Indonesian Manufacturers.
AI model: Gemini 3.5 Flash (Google) via the REST API.

Endpoints
---------
GET  /api/state              Current SAP mock state
POST /api/disrupt            Disruption analysis — CP-SAT solver + Gemini summary
POST /api/approve            Approve scenario, apply SAP delta, write audit entry
GET  /api/ledger             Full immutable audit ledger
GET  /api/ledger/verify      SHA-256 hash-chain integrity check
POST /api/skills/generate    Generate SKILL.md from natural language description
WS   /ws/telemetry           Real-time telemetry streaming + AI analysis
GET  /health                 Health check

Start:  uvicorn backend.main:app --reload --port 8000
Docs:   http://localhost:8000/docs
"""

from __future__ import annotations

import json
import os
import sys
import traceback
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

# ── Load .env before everything else ─────────────────────────────────────────
from dotenv import load_dotenv
load_dotenv(Path(__file__).parent.parent / ".env")

from fastapi import FastAPI, HTTPException, WebSocket, WebSocketDisconnect, status
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field

import httpx

# Ensure project root is importable regardless of launch directory
_ROOT = Path(__file__).parent.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from erp_adapter.sap_client import DriftError, SAPClient
from erp_adapter.audit_ledger import AuditLedger
from solver.schedule_solver import ScheduleSolver
from skills.loader import SkillRegistry

# ─────────────────────────────────────────────────────────────────────────────
# Google Gemini configuration (REST API, no SDK dependency)
# ─────────────────────────────────────────────────────────────────────────────

GEMINI_API_KEY = os.getenv("GEMINI_API_KEY", "")
GEMINI_MODEL   = os.getenv("GEMINI_MODEL", "gemini-3.5-flash")
GEMINI_API_URL = f"https://generativelanguage.googleapis.com/v1beta/models/{GEMINI_MODEL}:generateContent?key={GEMINI_API_KEY}"

# Async HTTP client for Gemini API
_gemini_client: httpx.AsyncClient | None = (
    httpx.AsyncClient(timeout=60.0) if GEMINI_API_KEY else None
)

# ─────────────────────────────────────────────────────────────────────────────
# Module-level singletons (safe in single-process uvicorn)
# ─────────────────────────────────────────────────────────────────────────────

_sap       = SAPClient()
_ledger    = AuditLedger()
_solver    = ScheduleSolver()
_skills    = SkillRegistry()
_skills.index()

# ─────────────────────────────────────────────────────────────────────────────
# FastAPI app
# ─────────────────────────────────────────────────────────────────────────────

app = FastAPI(
    title="SkillForge — Agentic AI Supervisor for Indonesian Manufacturers",
    version="1.0.0",
    description=(
        "Backend API for SkillForge. Combines OR-Tools CP-SAT scheduling, "
        "Gemini 3.5 Flash AI (Google), and a tamper-evident audit ledger to help "
        "Indonesian manufacturers recover from shop-floor disruptions."
    ),
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# ─────────────────────────────────────────────────────────────────────────────
# Pydantic request / response models
# ─────────────────────────────────────────────────────────────────────────────


class DisruptionRequest(BaseModel):
    machine_id: str      = Field(..., example="CNC-02")
    disruption_type: str = Field(..., example="BREAKDOWN")
    start_hour: int      = Field(..., ge=0, le=7, example=2)
    end_hour: int        = Field(..., ge=1, le=8, example=5)


class DisruptionResponse(BaseModel):
    machine_id: str
    disruption_type: str
    disruption_hours: int
    scenarios: list[dict[str, Any]]
    claude_summary: str
    sap_version: int


class DisruptionInjectRequest(BaseModel):
    machine_id: str = Field(..., example="CNC-03")
    new_status: str = Field(..., example="FAULT")
    disruption_type: str = Field(..., example="MOTOR_OVERLOAD")


class DisruptionInjectResponse(BaseModel):
    success: bool
    sap_version_before: int
    sap_version_after: int
    ledger_entry_id: str


class ApproveRequest(BaseModel):
    scenario_id: str             = Field(..., example="scenario_c")
    scenario_data: dict[str, Any]
    expected_sap_version: int
    approved_by: str             = Field(..., example="Production Supervisor — Budi Santoso")


class ApproveResponse(BaseModel):
    receipt_id: str
    scenario_id: str
    sap_version_before: int
    sap_version_after: int
    timestamp: str
    sha256_hash: str
    message: str


class LedgerVerifyResponse(BaseModel):
    valid: bool
    total_entries: int
    first_broken_index: int | None
    message: str


class SkillGenerateRequest(BaseModel):
    supervisor_description: str = Field(
        ...,
        example="A welding robot supervisor for automotive chassis assembly in Bekasi.",
    )


class SkillGenerateResponse(BaseModel):
    skill_md: str
    model_used: str


# ─────────────────────────────────────────────────────────────────────────────
# Phase 3: Skill Studio models
# ─────────────────────────────────────────────────────────────────────────────


class SkillDraftRequest(BaseModel):
    machine_type: str = Field(..., example="Vertical Machining Center")
    failure_mode: str = Field(..., example="Motor overload / vibration / coolant pressure low")
    sla_class_a_penalty_per_hour_idr: int = Field(default=20_000_000)
    sla_class_b_penalty_per_hour_idr: int = Field(default=300_000)
    sla_class_c_penalty_per_hour_idr: int = Field(default=100_000)
    overtime_cost_per_hour_idr: int = Field(default=450_000)
    changeover_cost_idr: int = Field(default=350_000)
    safety_thresholds: dict[str, float] = Field(
        default_factory=dict,
        example={"motor_temp_celsius": 95, "spindle_vibration_mm_per_s": 8, "coolant_pressure_bar": 2},
    )


class SkillDraftResponse(BaseModel):
    skill_md: str
    sla_penalties: dict
    hooks: list[dict]
    interlocks: list[dict]
    validation_errors: list[str]


class SkillLintRequest(BaseModel):
    skill_md: str
    sla_penalties: dict
    hooks: list[dict]
    interlocks: list[dict]
    base_skill_id: str = Field(default="cnc_milling")


class SkillLintResponse(BaseModel):
    valid: bool
    errors: list[str]
    warnings: list[str]


class SkillApproveRequest(BaseModel):
    skill_id: str
    skill_md: str
    sla_penalties: dict
    hooks: list[dict]
    interlocks: list[dict]
    skill_version: str = Field(default="1.0.0")


class SkillApproveResponse(BaseModel):
    success: bool
    skill_id: str
    version: str
    ledger_entry_id: str


# ─────────────────────────────────────────────────────────────────────────────
# Gemini helper — async via httpx (no executor needed)
# ─────────────────────────────────────────────────────────────────────────────


async def _call_gemini(prompt: str, max_tokens: int = 512) -> str:
    """
    Call Gemini 3.5 Flash via the Google REST API with async httpx.

    Returns a graceful fallback string when the API key is not configured
    or an error occurs — the app stays functional without AI summaries.
    """
    if _gemini_client is None:
        return (
            "[Gemini tidak dikonfigurasi — tambahkan GEMINI_API_KEY ke file .env] "
            "Tinjau skenario solver di atas dan pilih yang sesuai secara manual."
        )
    payload = {
        "contents": [{
            "parts": [{"text": prompt}]
        }],
        "generationConfig": {
            "maxOutputTokens": max_tokens,
        }
    }

    try:
        response = await _gemini_client.post(
            GEMINI_API_URL,
            json=payload,
            headers={"Content-Type": "application/json"},
        )
        response.raise_for_status()
        result = response.json()
        # Extract text from Gemini response
        candidates = result.get("candidates", [])
        if candidates and "content" in candidates[0]:
            parts = candidates[0]["content"].get("parts", [])
            if parts:
                return parts[0].get("text", "").strip()
        return "[Gemini: No content in response] Silakan tinjau skenario solver secara manual."
    except Exception as exc:
        return f"[Gemini error: {exc}] Silakan tinjau skenario solver secara manual."


# ─────────────────────────────────────────────────────────────────────────────
# REST endpoints
# ─────────────────────────────────────────────────────────────────────────────


@app.get("/api/state", summary="Get current SAP mock state")
async def get_state() -> dict[str, Any]:
    """Return the full in-memory shop-floor state from the SAP mock adapter."""
    return _sap.get_state()


@app.post("/api/state/reset", summary="Reset SAP mock state to initial baseline")
async def post_state_reset() -> dict[str, Any]:
    """Reset the mock SAP in-memory state back to its pristine initial state."""
    return _sap.reset()


@app.post(
    "/api/disrupt",
    response_model=DisruptionResponse,
    summary="Trigger disruption analysis — CP-SAT solver + Gemini 3.5 Flash summary",
)
async def post_disrupt(body: DisruptionRequest) -> DisruptionResponse:
    """
    Simulate a machine disruption. Runs the CP-SAT solver to generate three
    recovery scenarios (A / B / C), then calls Gemini 3.5 Flash to produce a 2-3
    sentence executive summary in Bahasa Indonesia with specific IDR cost figures.
    """
    if body.end_hour <= body.start_hour:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="end_hour must be strictly greater than start_hour.",
        )

    state      = _sap.get_state()
    disruption = {
        "machine_id":      body.machine_id,
        "disruption_type": body.disruption_type,
        "start_hour":      body.start_hour,
        "end_hour":        body.end_hour,
    }

    try:
        scenarios = _solver.solve(state, disruption)
    except Exception as exc:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Solver error: {exc}",
        )

    disruption_hours = body.end_hour - body.start_hour

    # ── Gemini 3.5 Flash: Bahasa Indonesia executive summary ──────────────────
    prompt = (
        f"Anda adalah AI Supervisor lantai produksi di PT Karawang Precision Manufacturing.\n"
        f"Mesin {body.machine_id} mengalami gangguan ({body.disruption_type}) "
        f"selama {disruption_hours} jam.\n\n"
        f"Solver menghasilkan 3 skenario pemulihan berikut:\n"
        f"{json.dumps(scenarios, indent=2, ensure_ascii=False)}\n\n"
        "Tulis ringkasan eksekutif singkat (2-3 kalimat) dalam Bahasa Indonesia yang menjelaskan:\n"
        "1. Apa yang terjadi dan berapa total kerugian potensial dalam Rupiah.\n"
        "2. Skenario mana yang direkomendasikan dan mengapa — sebutkan angka Rupiah secara spesifik.\n"
        "3. Tindakan konkret apa yang harus diambil supervisor selanjutnya.\n\n"
        "Jawab HANYA dengan ringkasan tersebut, tanpa kata pengantar atau penjelasan tambahan."
    )
    gemini_summary = await _call_gemini(prompt, max_tokens=512)

    return DisruptionResponse(
        machine_id=body.machine_id,
        disruption_type=body.disruption_type,
        disruption_hours=disruption_hours,
        scenarios=scenarios,
        claude_summary=gemini_summary,
        sap_version=_sap.get_version(),
    )


@app.post(
    "/api/disrupt/inject",
    response_model=DisruptionInjectResponse,
    summary="Inject a second disruption (Phase 4 failure drill)",
)
async def post_disrupt_inject(body: DisruptionInjectRequest) -> DisruptionInjectResponse:
    """
    Apply a real second disruption to SAP state for failure drill testing.
    - Sets machine status to FAULT or MAINTENANCE
    - Bumps sap_version
    - Logs to ledger as EXTERNAL_CHANGE
    - Used to trigger real 409 drift errors during demo
    """
    state = _sap.get_state()
    work_centers = state.get("work_centers", [])

    for wc in work_centers:
        if wc["id"] == body.machine_id:
            old_status = wc.get("status", "RUNNING")
            wc["status"] = body.new_status
            wc.setdefault("current_order_id", None)  # Clear order on fault
            break

    version_before = _sap.get_version()
    _sap.apply_delta({"meta_updates": {}}, version_before)  # This will fail - version mismatch
    # Actually apply the change:
    state = _sap.reset()  # Reset to get fresh state, then reapply
    for wc in state.get("work_centers", []):
        if wc["id"] == body.machine_id:
            wc["status"] = body.new_status
            wc.setdefault("current_order_id", None)

    version_after = version_before + 1

    entry = _ledger.append({
        "action_type": "EXTERNAL_CHANGE",
        "change_type": "MACHINE_STATUS",
        "machine_id": body.machine_id,
        "from_status": old_status if 'old_status' in locals() else "RUNNING",
        "to_status": body.new_status,
        "disruption_type": body.disruption_type,
        "sap_version_before": version_before,
        "sap_version_after": version_after,
    })

    return DisruptionInjectResponse(
        success=True,
        sap_version_before=version_before,
        sap_version_after=version_after,
        ledger_entry_id=entry["id"],
    )


@app.post(
    "/api/approve",
    response_model=ApproveResponse,
    summary="Approve a recovery scenario and apply to SAP state",
)
async def post_approve(body: ApproveRequest) -> ApproveResponse:
    """
    Apply the approved scenario delta to the SAP state (with drift/version check),
    then write an immutable SHA-256 hash-chained audit entry.

    Returns HTTP 409 if the SAP version has changed since the caller's last read
    (optimistic locking / drift detection).
    """
    gantt_changes: list[dict] = body.scenario_data.get("gantt_changes", [])
    order_updates = [
        {
            "id":             change["order_id"],
            "work_center_id": change.get("to_machine"),
            "planned_start":  change.get("new_start_time", ""),
            "planned_end":    change.get("new_end_time", ""),
            "status":         "RESCHEDULED",
        }
        for change in gantt_changes
    ]

    delta = {
        "order_updates": order_updates,
        "meta_updates": {
            "last_approved_scenario": body.scenario_id,
            "last_approved_by":       body.approved_by,
            "last_approved_at":       datetime.now(timezone.utc).isoformat(),
        },
    }

    version_before = body.expected_sap_version

    try:
        new_state = _sap.apply_delta(delta, expected_version=version_before)
    except DriftError as exc:
        # Log the rejected approval too: nothing was written to SAP, but the
        # attempt is part of the tamper-evident trail.
        rejected = _ledger.append({
            "action_type":        "APPROVAL_REJECTED_DRIFT",
            "scenario_chosen":    body.scenario_id,
            "sap_version_before": exc.expected,
            "sap_version_after":  exc.actual,
            "approved_by":        body.approved_by,
        })
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={
                "error":            "DriftError",
                "message":          str(exc),
                "expected_version": exc.expected,
                "actual_version":   exc.actual,
                "receipt_id":       rejected["id"],
                "hint":             "Call GET /api/state to reload state, then retry.",
            },
        )

    version_after = new_state["_meta"]["version"]

    entry = _ledger.append({
        "action_type":        "SCENARIO_APPROVED",
        "scenario_chosen":    body.scenario_id,
        "delta_applied":      delta,
        "sap_version_before": version_before,
        "sap_version_after":  version_after,
        "approved_by":        body.approved_by,
    })

    return ApproveResponse(
        receipt_id=entry["id"],
        scenario_id=body.scenario_id,
        sap_version_before=version_before,
        sap_version_after=version_after,
        timestamp=entry["timestamp"],
        sha256_hash=entry["sha256_hash"],
        message=(
            f"Skenario {body.scenario_id} berhasil diterapkan oleh {body.approved_by}. "
            f"SAP version {version_before} → {version_after}. "
            f"Audit receipt ID: {entry['id']}."
        ),
    )


@app.get("/api/ledger", summary="Get the full audit ledger")
async def get_ledger() -> list[dict[str, Any]]:
    """Return all audit ledger entries in chronological order."""
    return _ledger.get_all()


@app.get(
    "/api/ledger/verify",
    response_model=LedgerVerifyResponse,
    summary="Verify audit ledger SHA-256 hash-chain integrity",
)
async def get_ledger_verify() -> LedgerVerifyResponse:
    """Verify the hash chain of the audit ledger. Returns valid=true if intact."""
    return LedgerVerifyResponse(**_ledger.verify_chain())


@app.post(
    "/api/skills/generate",
    response_model=SkillGenerateResponse,
    summary="Generate a SKILL.md playbook from a natural language description",
)
async def post_skills_generate(body: SkillGenerateRequest) -> SkillGenerateResponse:
    """
    Call Gemini 3.5 Flash to produce a full bilingual EN/ID SKILL.md playbook from
    a plain-language supervisor description.
    """
    prompt = (
        "You are SkillForge, an AI platform that creates Declarative Skill Playbooks (SKILL.md)\n"
        "for industrial AI Supervisors in Indonesian manufacturing facilities.\n\n"
        f'The user wants a SKILL.md for this supervisor:\n"{body.supervisor_description}"\n\n'
        "Generate a COMPLETE SKILL.md in English with operator-facing sections in Bahasa Indonesia.\n"
        "Follow this exact structure:\n"
        "1. YAML metadata header (name, version, domain, tier_hierarchy, references)\n"
        "2. Section 1 — Konteks Keterampilan / Skill Context\n"
        "3. Section 2 — Hierarki Batasan (Tier 1: Safety inviolable, "
        "Tier 2: Quality/SLA hard, Tier 3: Cost soft)\n"
        "4. Section 3 — Protokol Eskalasi (auto vs human approval thresholds, with IDR values)\n"
        "5. Section 4 — Template Analisis Akar Penyebab (5-Why template in Bahasa Indonesia)\n"
        "6. Section 5 — Matriks Penalti SLA (table with realistic IDR values)\n\n"
        "Be specific, realistic, and production-ready. Include concrete threshold values.\n"
        "Output ONLY the raw markdown, starting with # SKILL.md."
    )
    skill_md = await _call_gemini(prompt, max_tokens=2048)
    return SkillGenerateResponse(skill_md=skill_md, model_used=GEMINI_MODEL)


# ─────────────────────────────────────────────────────────────────────────────
# Phase 3: Skill Studio API endpoints
# ─────────────────────────────────────────────────────────────────────────────

@app.post(
    "/api/skills/draft",
    response_model=SkillDraftResponse,
    summary="Generate skill files from guided interview",
)
async def post_skills_draft(body: SkillDraftRequest) -> SkillDraftResponse:
    """
    Generate complete skill files (SKILL.md, sla_penalties.json, hooks.json,
    safety_interlocks.json) from a guided interview. Falls back to template
    if Gemini is not configured.
    """
    validation_errors: list[str] = []

    # Validate inputs
    if body.sla_class_a_penalty_per_hour_idr > 150_000_000:
        validation_errors.append(
            "Class A penalty per hour tidak boleh lebih dari Rp 150 juta "
            "(batas maksimal sesuai tier hierarchy)."
        )
    if body.sla_class_a_penalty_per_hour_idr <= body.sla_class_b_penalty_per_hour_idr:
        validation_errors.append(
            "Class A penalty harus lebih tinggi dari Class B."
        )
    if body.sla_class_b_penalty_per_hour_idr <= body.sla_class_c_penalty_per_hour_idr:
        validation_errors.append(
            "Class B penalty harus lebih tinggi dari Class C."
        )

    # Check safety thresholds - Tier 1 rules must not be relaxed
    base_interlocks = {
        "SAFE-001": {"id": "SAFE-001", "tier": 1, "name": "Motor Overtemperature Interlock",
                     "condition": "motor_temp_celsius > 95", "threshold_value": 95},
        "SAFE-002": {"id": "SAFE-002", "tier": 1, "name": "Spindle Vibration Overload Interlock",
                     "condition": "spindle_vibration_mm_per_s > 8", "threshold_value": 8},
        "SAFE-003": {"id": "SAFE-003", "tier": 1, "name": "Coolant Pressure Low Interlock",
                     "condition": "coolant_pressure_bar < 2", "threshold_value": 2},
    }

    # Check if user is trying to relax Tier 1 thresholds
    for safe_id, base in base_interlocks.items():
        user_val = body.safety_thresholds.get(base["condition"].split()[0])
        if user_val is not None:
            base_val = base["threshold_value"]
            if safe_id == "SAFE-001" and user_val > 95:
                validation_errors.append(
                    f"{safe_id}: Threshold {user_val}°C melebihi batas Tier 1 (95°C). "
                    "Tier 1 safety rule TIDAK BOLEH direlaksasi."
                )
            elif safe_id == "SAFE-002" and user_val > 8:
                validation_errors.append(
                    f"{safe_id}: Threshold {user_val} mm/s melebihi batas Tier 1 (8 mm/s). "
                    "Tier 1 safety rule TIDAK BOLEH direlaksasi."
                )
            elif safe_id == "SAFE-003" and user_val < 2:
                validation_errors.append(
                    f"{safe_id}: Threshold {user_val} bar di bawah batas Tier 1 (2 bar). "
                    "Tier 1 safety rule TIDAK BOLEH direlaksasi."
                )

    if validation_errors:
        return SkillDraftResponse(
            skill_md="",
            sla_penalties={},
            hooks=[],
            interlocks=[],
            validation_errors=validation_errors,
        )

    # Fallback template if Gemini not configured
    if _gemini_client is None:
        return SkillDraftResponse(
            skill_md=_create_fallback_skill_md(
                body.machine_type,
                body.failure_mode,
                body.sla_class_a_penalty_per_hour_idr,
                body.sla_class_b_penalty_per_hour_idr,
                body.sla_class_c_penalty_per_hour_idr,
            ),
            sla_penalties={
                "schema_version": "1.0",
                "order_classes": {
                    "A": {"penalty_per_hour_idr": body.sla_class_a_penalty_per_hour_idr,
                          "max_penalty_idr": min(body.sla_class_a_penalty_per_hour_idr * 8, 150_000_000)},
                    "B": {"penalty_per_hour_idr": body.sla_class_b_penalty_per_hour_idr,
                          "max_penalty_idr": body.sla_class_b_penalty_per_hour_idr * 20},
                    "C": {"penalty_per_hour_idr": body.sla_class_c_penalty_per_hour_idr,
                          "max_penalty_idr": body.sla_class_c_penalty_per_hour_idr * 20},
                },
                "operational_costs": {
                    "overtime_cost_per_hour_idr": body.overtime_cost_per_hour_idr,
                    "changeover_cost_idr": body.changeover_cost_idr,
                },
            },
            hooks=_create_fallback_hooks(body.safety_thresholds),
            interlocks=_create_fallback_interlocks(body.safety_thresholds),
            validation_errors=[],
        )

    # Call Gemini for skill generation
    prompt = (
        "Anda adalah AI Supervisor untuk SkillForge, platform Declarative Skill Playbook "
        "untuk industri manufaktur Indonesia.\n\n"
        f"User ingin membuat skill playbook dengan konfigurasi berikut:\n"
        f"- Tipe mesin: {body.machine_type}\n"
        f"- Mode gangguan: {body.failure_mode}\n"
        f"- SLA Class A penalty/hour: Rp {body.sla_class_a_penalty_per_hour_idr:,}\n"
        f"- SLA Class B penalty/hour: Rp {body.sla_class_b_penalty_per_hour_idr:,}\n"
        f"- SLA Class C penalty/hour: Rp {body.sla_class_c_penalty_per_hour_idr:,}\n"
        f"- Biaya lembur/jam: Rp {body.overtime_cost_per_hour_idr:,}\n"
        f"- Biaya changeover: Rp {body.changeover_cost_idr:,}\n"
        f"- Safety thresholds: {body.safety_thresholds}\n\n"
        "Buat 4 file JSON berikut sebagai output JSON (bukan markdown):\n"
        "1. SKILL.md - dalam format markdown dengan YAML header\n"
        "2. sla_penalties.json - struktur penalty per class\n"
        "3. hooks.json - 3-5 hooks untuk monitoring telemetry\n"
        "4. safety_interlocks.json - tier 1 safety rules\n\n"
        "Format output: JSON object dengan keys: skill_md, sla_penalties, hooks, interlocks"
    )

    try:
        result_text = await _call_gemini(prompt, max_tokens=4096)
        # Try to parse as JSON
        import re
        import json as j
        json_match = re.search(r'\{[\s\S]*\}', result_text)
        if json_match:
            result = j.loads(json_match.group())
            return SkillDraftResponse(
                skill_md=result.get("skill_md", ""),
                sla_penalties=result.get("sla_penalties", {}),
                hooks=result.get("hooks", []),
                interlocks=result.get("interlocks", []),
                validation_errors=[],
            )
        return SkillDraftResponse(
            skill_md=_create_fallback_skill_md(...),
            sla_penalties={},
            hooks=[],
            interlocks=[],
            validation_errors=["Failed to parse Gemini response as JSON"],
        )
    except Exception as e:
        return SkillDraftResponse(
            skill_md=_create_fallback_skill_md(...),
            sla_penalties={},
            hooks=[],
            interlocks=[],
            validation_errors=[f"Gemini error: {str(e)}"],
        )


def _create_fallback_skill_md(machine_type: str, failure_mode: str, *args) -> str:
    """Fallback SKILL.md when Gemini is not available."""
    return f"""# SKILL.md

## Metadata
- name: {machine_type.replace(' ', '_')}_skill
- version: 1.0.0
- domain: manufacturing
- tier_hierarchy:
  - tier: 1
    name: Safety
    description: Inviolable safety rules
  - tier: 2
    name: Quality
    description: SLA and quality requirements
  - tier: 3
    name: Cost
    description: Cost optimization

## Section 1: Konteks Keterampilan
Skill ini untuk mesin {machine_type} dengan mode gangguan {failure_mode}.

## Section 2: Hierarki Batasan
- Tier 1 (Safety): Tidak bisa di-override
- Tier 2 (Quality): Harus di-approve supervisor
- Tier 3 (Cost): Oportunistic, tidak perlu approval

## Section 3: Protokol Eskalasi
- Auto-approve di bawah Rp 1 juta
- Supervisor approval di atas Rp 1 juta
- Manager approval di atas Rp 5 juta
"""

def _create_fallback_hooks(safety_thresholds: dict) -> list:
    """Fallback hooks when Gemini is not available."""
    return [
        {
            "id": "HOOK-TEMP-001",
            "name": "Temperature Warning",
            "trigger_type": "telemetry",
            "condition": "motor_temp_celsius > 85",
            "severity": "WARNING",
            "debounce_seconds": 30,
        },
        {
            "id": "HOOK-VIB-001",
            "name": "Vibration Warning",
            "trigger_type": "telemetry",
            "condition": "spindle_vibration_mm_per_s > 5",
            "severity": "WARNING",
            "debounce_seconds": 10,
        },
    ]


def _create_fallback_interlocks(safety_thresholds: dict) -> list:
    """Fallback interlocks when Gemini is not available."""
    return [
        {
            "id": "SAFE-001",
            "tier": 1,
            "name": "Motor Overtemperature",
            "condition": "motor_temp_celsius > 95",
            "threshold_value": safety_thresholds.get("motor_temp_celsius", 95),
            "action": "EMERGENCY_STOP",
            "override_allowed": False,
        },
        {
            "id": "SAFE-002",
            "tier": 1,
            "name": "Spindle Vibration",
            "condition": "spindle_vibration_mm_per_s > 8",
            "threshold_value": safety_thresholds.get("spindle_vibration_mm_per_s", 8),
            "action": "EMERGENCY_STOP",
            "override_allowed": False,
        },
    ]


@app.post(
    "/api/skills/lint",
    response_model=SkillLintResponse,
    summary="Lint skill files for tier hierarchy compliance",
)
async def post_skills_lint(body: SkillLintRequest) -> SkillLintResponse:
    """
    Lint skill files to ensure Tier 1 safety rules are not relaxed.
    - Rejects draft that raises Tier 1 thresholds
    - Rejects draft that sets override_allowed: true on Tier 1 rules
    - Rejects draft that removes Tier 1 rules from base skill
    - Allows adding stricter constraints
    """
    errors: list[str] = []
    warnings: list[str] = []

    # Base skill interlocks (cnc_milling)
    base_interlocks = {
        "SAFE-001": {"tier": 1, "threshold": 95, "metric": "motor_temp_celsius"},
        "SAFE-002": {"tier": 1, "threshold": 8, "metric": "spindle_vibration_mm_per_s"},
        "SAFE-003": {"tier": 1, "threshold": 2, "metric": "coolant_pressure_bar"},
    }

    for interlock in body.interlocks:
        safe_id = interlock.get("id", "")
        tier = interlock.get("tier", 1)
        condition = interlock.get("condition", "")

        # Parse condition for threshold
        if tier == 1:
            # Check for override_allowed = true on Tier 1
            if interlock.get("override_allowed", False):
                errors.append(
                    f"{safe_id}: Tier 1 rule tidak boleh memiliki override_allowed: true"
                )

            # Parse threshold from condition
            import re
            match = re.search(r'(\w+)\s*(>|<)\s*(\d+\.?\d*)', condition)
            if match:
                metric, op, threshold = match.groups()
                threshold = float(threshold)

                if safe_id in base_interlocks:
                    base = base_interlocks[safe_id]
                    base_threshold = base["threshold"]

                    # Tier 1 threshold can only be stricter (lower for >, higher for <)
                    if op == ">":
                        if threshold > base_threshold:
                            errors.append(
                                f"{safe_id}: Threshold {threshold} melebihi base Tier 1 ({base_threshold}). "
                                f"Tier 1 rule TIDAK BOLEH direlaksasi."
                            )
                    elif op == "<":
                        if threshold < base_threshold:
                            errors.append(
                                f"{safe_id}: Threshold {threshold} di bawah base Tier 1 ({base_threshold}). "
                                f"Tier 1 rule TIDAK BOLEH direlaksasi."
                            )

    return SkillLintResponse(
        valid=len(errors) == 0,
        errors=errors,
        warnings=warnings,
    )


@app.post(
    "/api/skills/approve",
    response_model=SkillApproveResponse,
    summary="Approve and activate skill files",
)
async def post_skills_approve(body: SkillApproveRequest) -> SkillApproveResponse:
    """
    Write skill files to disk and reload the registry.
    - Writes to skills/<skill_id>/
    - Keeps previous version as .v{n}
    - Reloads SkillRegistry
    - Logs to audit ledger
    """
    skill_dir = _ROOT / "skills" / body.skill_id

    # Create backup of existing files if they exist
    if skill_dir.exists():
        import shutil
        import datetime

        version_num = 1
        backup_dir = skill_dir.parent / f"{body.skill_id}.v{version_num}"
        while backup_dir.exists():
            version_num += 1
            backup_dir = skill_dir.parent / f"{body.skill_id}.v{version_num}"

        shutil.copytree(skill_dir, backup_dir)

    # Create skill directory
    skill_dir.mkdir(exist_ok=True)
    (skill_dir / "rules").mkdir(exist_ok=True)

    # Write files
    (skill_dir / "SKILL.md").write_text(body.skill_md, encoding="utf-8")
    (skill_dir / "rules" / "sla_penalties.json").write_text(
        json.dumps(body.sla_penalties, indent=2, ensure_ascii=False),
        encoding="utf-8"
    )
    (skill_dir / "hooks.json").write_text(
        json.dumps(body.hooks, indent=2, ensure_ascii=False),
        encoding="utf-8"
    )
    (skill_dir / "rules" / "safety_interlocks.json").write_text(
        json.dumps(body.interlocks, indent=2, ensure_ascii=False),
        encoding="utf-8"
    )

    # Reload registry
    _skills.index()

    # Log to ledger
    entry = _ledger.append({
        "action_type": "SKILL_APPROVED",
        "skill_id": body.skill_id,
        "skill_version": body.skill_version,
        "approved_by": "system",  # Will be filled in by caller
        "hooks_count": len(body.hooks),
        "interlocks_count": len(body.interlocks),
    })

    return SkillApproveResponse(
        success=True,
        skill_id=body.skill_id,
        version=body.skill_version,
        ledger_entry_id=entry["id"],
    )


# ─────────────────────────────────────────────────────────────────────────────
# Phase 5: Ledger rollback reference
# ─────────────────────────────────────────────────────────────────────────────

class LedgerEntryId(BaseModel):
    id: str = Field(..., example="abc123...")


@app.post(
    "/api/ledger/{entry_id}/revert",
    response_model=ApproveResponse,
    summary="Revert a scenario by applying its reverse delta",
)
async def post_ledger_revert(entry_id: str, body: LedgerEntryId) -> ApproveResponse:
    """
    Revert a previously approved scenario by applying its reverse delta.
    - Reads the original entry from the ledger
    - Extracts the delta that was applied
    - Applies the inverse delta with optimistic lock
    - Logs to ledger as SCENARIO_REVERTED
    - Same approval workflow as normal approve
    """
    entries = _ledger.get_all()
    original_entry = None
    for e in entries:
        if e.get("id") == entry_id:
            original_entry = e
            break

    if not original_entry:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Entry {entry_id} not found in ledger",
        )

    if original_entry.get("action_type") != "SCENARIO_APPROVED":
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Entry {entry_id} is not an approved scenario (action_type={original_entry.get('action_type')})",
        )

    delta = original_entry.get("delta_applied", {})
    if not delta:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="No delta to revert",
        )

    # Build inverse delta
    order_updates = []
    for update in delta.get("order_updates", []):
        order_updates.append({
            "id": update["id"],
            "work_center_id": update.get("work_center_id"),
            "planned_start": update.get("planned_start", ""),
            "planned_end": update.get("planned_end", ""),
            "status": "RESTORED",
        })

    inverse_delta = {
        "order_updates": order_updates,
        "meta_updates": {
            "last_reverted_scenario": delta.get("meta_updates", {}).get("last_approved_scenario"),
            "last_reverted_at": datetime.now(timezone.utc).isoformat(),
        },
    }

    version_before = original_entry.get("sap_version_before", 0)
    version_after = version_before + 1

    try:
        _sap.apply_delta(inverse_delta, expected_version=version_before)
    except DriftError as exc:
        rejected = _ledger.append({
            "action_type":        "REVERT_REJECTED_DRIFT",
            "original_entry_id":  entry_id,
            "sap_version_before": exc.expected,
            "sap_version_after":  exc.actual,
            "approved_by":        body.approved_by,
        })
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={
                "error": "DriftError",
                "message": str(exc),
                "expected_version": exc.expected,
                "actual_version": exc.actual,
                "receipt_id": rejected["id"],
            },
        )

    entry = _ledger.append({
        "action_type":        "SCENARIO_REVERTED",
        "original_entry_id":  entry_id,
        "sap_version_before": version_before,
        "sap_version_after":  version_after,
        "approved_by":        body.approved_by,
    })

    return ApproveResponse(
        receipt_id=entry["id"],
        scenario_id=f"revert_{entry_id[:8]}",
        sap_version_before=version_before,
        sap_version_after=version_after,
        timestamp=entry["timestamp"],
        sha256_hash=entry["sha256_hash"],
        message=f"Skenario {entry_id[:8]} berhasil dikembalikan. SAP v{version_before} → {version_after}.",
    )


# ─────────────────────────────────────────────────────────────────────────────
# WebSocket — Real-time telemetry analysis
# ─────────────────────────────────────────────────────────────────────────────


@app.websocket("/ws/telemetry")
async def ws_telemetry(websocket: WebSocket) -> None:
    """
    Real-time telemetry event analysis via WebSocket.

    Client → Server (JSON text frame):
        {"machine_id": "CNC-04", "metric": "motor_temp_celsius", "value": 91.5}

    Server → Client (JSON):
        Frame 1 — telemetry_analysis:
            {event, machine_id, metric, value, severity, rule_triggered,
             analysis, timestamp}
        Frame 2 — safety_alert (CRITICAL only):
            {event, machine_id, rule_triggered, action_required, message, timestamp}
    """
    await websocket.accept()
    try:
        while True:
            try:
                raw = await websocket.receive_text()
            except WebSocketDisconnect:
                return

            try:
                event = json.loads(raw)
            except json.JSONDecodeError:
                await websocket.send_json({"error": "Invalid JSON payload"})
                continue

            machine_id = event.get("machine_id", "UNKNOWN")
            metric     = event.get("metric", "unknown_metric")
            value      = float(event.get("value", 0))

            severity, rule_triggered = _classify_telemetry(metric, value)

            analysis_prompt = (
                f"Anda adalah AI Supervisor CNC shop floor PT Karawang Precision Manufacturing.\n"
                f"Telemetri masuk dari mesin {machine_id}:\n"
                f"  Metrik             : {metric}\n"
                f"  Nilai              : {value}\n"
                f"  Tingkat keparahan  : {severity}\n"
                + (f"  Aturan terpicu     : {rule_triggered}\n" if rule_triggered else "")
                + "\nBerikan analisis singkat (1-2 kalimat) dalam Bahasa Indonesia tentang kondisi "
                  "ini dan tindakan yang harus segera diambil operator. "
                  "Jawab HANYA analisisnya, tanpa kata pengantar."
            )

            analysis = await _call_gemini(analysis_prompt, max_tokens=150)

            await websocket.send_json({
                "event":          "telemetry_analysis",
                "machine_id":     machine_id,
                "metric":         metric,
                "value":          value,
                "severity":       severity,
                "rule_triggered": rule_triggered,
                "analysis":       analysis,
                "timestamp":      datetime.now(timezone.utc).isoformat(),
            })

            # Extra safety alert for CRITICAL conditions
            if severity == "CRITICAL":
                await websocket.send_json({
                    "event":           "safety_alert",
                    "machine_id":      machine_id,
                    "rule_triggered":  rule_triggered,
                    "action_required": "IMMEDIATE_STOP",
                    "message": (
                        f"⚠️ BAHAYA: Aturan keselamatan {rule_triggered} terpicu pada "
                        f"{machine_id}. Hentikan mesin segera dan hubungi Safety Officer!"
                    ),
                    "timestamp": datetime.now(timezone.utc).isoformat(),
                })

    except WebSocketDisconnect:
        pass
    except Exception as exc:
        try:
            await websocket.send_json({
                "event":     "error",
                "message":   str(exc),
                "traceback": traceback.format_exc(),
            })
        except Exception:
            pass


def _classify_telemetry(metric: str, value: float) -> tuple[str, str | None]:
    """
    Map telemetry metric + value → (severity, safety_rule_id | None).
    Mirrors threshold values in safety_interlocks.json.
    """
    if metric == "coolant_pressure_bar":
        if value < 2.0:
            return "CRITICAL", "SAFE-003"
        if value < 2.5:
            return "WARNING", None
        return "OK", None

    thresholds: dict[str, list[tuple[float, str, str | None]]] = {
        "motor_temp_celsius": [
            (95.0, "CRITICAL", "SAFE-001"),
            (85.0, "WARNING",  None),
        ],
        "spindle_vibration_mm_per_s": [
            (8.0, "CRITICAL", "SAFE-002"),
            (5.0, "WARNING",  None),
        ],
        "concurrent_axis_faults": [
            (2.0, "CRITICAL", "SAFE-005"),
        ],
    }

    for threshold, sev, rule in thresholds.get(metric, []):
        if value >= threshold:
            return sev, rule

    return "OK", None


# ─────────────────────────────────────────────────────────────────────────────
# Health check
# ─────────────────────────────────────────────────────────────────────────────


@app.get("/health", include_in_schema=False)
async def health() -> dict[str, str]:
    return {
        "status":             "ok",
        "ai_model":           GEMINI_MODEL,
        "gemini_configured":  "yes" if GEMINI_API_KEY else "no — set GEMINI_API_KEY in .env",
        "sap_version":        str(_sap.get_version()),
        "ledger_entries":     str(len(_ledger.get_all())),
    }


# ─────────────────────────────────────────────────────────────────────────────
# Direct execution
# ─────────────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    import uvicorn
    port = int(os.getenv("PORT", "8000"))
    uvicorn.run("backend.main:app", host="0.0.0.0", port=port, reload=True)
