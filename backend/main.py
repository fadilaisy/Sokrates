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

_sap    = SAPClient()
_ledger = AuditLedger()
_solver = ScheduleSolver()

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
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={
                "error":            "DriftError",
                "message":          str(exc),
                "expected_version": exc.expected,
                "actual_version":   exc.actual,
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
