"""
backend/metrics.py
──────────────────
Shift downtime and OEE, computed from the live SAP mock state (no sample numbers).

Time base: one shift (shop_floor_config.shift_start → shift_end) on the shift date
(the earliest planned_start among production orders, same rule as the frontend).

Downtime per machine, clipped to the shift, overlaps counted once:
  maintenance  PREVENTIVE maintenance slots / windows             (planned stop)
  unplanned    CORRECTIVE maintenance, breakdowns confirmed by a
               supervisor approval (_meta.downtime_events), and a
               machine currently in FAULT (whole shift: the mock has no clock)
  changeover   orders moved to another machine by an approved
               scenario, changeover_minutes each (skill file, default 30)
Precedence when intervals overlap: unplanned > maintenance.

OEE (standard definition):
  planned time  = shift hours × machines − planned maintenance
  availability  = (planned time − unplanned − changeover) / planned time
  performance   = mean feed_rate_percent of RUNNING machines (telemetry)
  quality       = assumption: the mock has no scrap / rework data
  OEE           = availability × performance × quality
"""

from __future__ import annotations

import json
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Iterable

_ROOT = Path(__file__).resolve().parent.parent
_SKILL_COSTS = _ROOT / "skills" / "cnc_milling" / "rules" / "sla_penalties.json"

DEFAULT_CHANGEOVER_MINUTES = 30
QUALITY_ASSUMPTION_PERCENT = 98.0   # no scrap data in the mock; flagged as an assumption
OEE_TARGET_PERCENT = 85.0           # common world-class benchmark

CATEGORY_LABELS = {
    "changeover": "Changeover",
    "maintenance": "Perawatan",
    "unplanned": "Tak terencana",
}

Interval = tuple[datetime, datetime]


# ─────────────────────────────────────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────────────────────────────────────


def changeover_minutes() -> float:
    """Read operational_costs.changeover_minutes from the skill file (fresh, like the solver)."""
    try:
        data = json.loads(_SKILL_COSTS.read_text(encoding="utf-8"))
        return float(data.get("operational_costs", {}).get("changeover_minutes", DEFAULT_CHANGEOVER_MINUTES))
    except Exception:
        return float(DEFAULT_CHANGEOVER_MINUTES)


def _parse_dt(value: Any) -> datetime | None:
    """ISO timestamp → naive datetime (local plant time). Display strings return None."""
    s = str(value or "").strip()
    if len(s) < 16 or s[10] != "T":
        return None
    try:
        dt = datetime.fromisoformat(s.replace("Z", "+00:00"))
    except ValueError:
        return None
    return dt.replace(tzinfo=None)


def _hhmm(value: str, default: str) -> tuple[int, int]:
    try:
        h, m = str(value or default).split(":")[:2]
        return int(h), int(m)
    except ValueError:
        h, m = default.split(":")
        return int(h), int(m)


def shift_window(state: dict[str, Any]) -> Interval | None:
    """Shift start/end datetimes on the shift date, or None if the plan has no dated orders."""
    dates = sorted(
        str(o.get("planned_start", ""))[:10]
        for o in state.get("production_orders", [])
        if _parse_dt(o.get("planned_start"))
    )
    if not dates:
        return None
    day = datetime.fromisoformat(dates[0])
    cfg = state.get("shop_floor_config", {})
    sh, sm = _hhmm(cfg.get("shift_start"), "07:00")
    eh, em = _hhmm(cfg.get("shift_end"), "15:00")
    return day.replace(hour=sh, minute=sm), day.replace(hour=eh, minute=em)


def _clip(iv: Interval, window: Interval) -> Interval | None:
    a, b = max(iv[0], window[0]), min(iv[1], window[1])
    return (a, b) if b > a else None


def _merge(ivs: Iterable[Interval]) -> list[Interval]:
    out: list[Interval] = []
    for a, b in sorted(ivs):
        if out and a <= out[-1][1]:
            out[-1] = (out[-1][0], max(out[-1][1], b))
        else:
            out.append((a, b))
    return out


def _subtract(ivs: list[Interval], minus: list[Interval]) -> list[Interval]:
    out: list[Interval] = []
    for a, b in ivs:
        pieces = [(a, b)]
        for c, d in minus:
            nxt = []
            for x, y in pieces:
                if d <= x or c >= y:
                    nxt.append((x, y))
                    continue
                if c > x:
                    nxt.append((x, c))
                if d < y:
                    nxt.append((d, y))
            pieces = nxt
        out.extend(pieces)
    return out


def _hours(ivs: Iterable[Interval]) -> float:
    return sum((b - a).total_seconds() for a, b in ivs) / 3600.0


def _maintenance_records(state: dict[str, Any]) -> list[dict[str, Any]]:
    """maintenance_slots (richer) plus any work-center window not already listed there."""
    recs = [dict(m) for m in state.get("maintenance_slots") or []]
    seen = {m.get("id") for m in recs}
    for w in state.get("work_centers", []):
        for m in w.get("planned_maintenance_windows") or []:
            if m.get("id") not in seen:
                recs.append({**m, "work_center_id": w.get("id")})
                seen.add(m.get("id"))
    return recs


# ─────────────────────────────────────────────────────────────────────────────
# Downtime events written on approval (stored in _meta so they reset with state)
# ─────────────────────────────────────────────────────────────────────────────


def events_for_approval(
    state: dict[str, Any],
    scenario: dict[str, Any],
    approved_at: str,
) -> list[dict[str, Any]]:
    """
    Downtime events implied by approving `scenario`:
      - the disruption itself (unplanned), from scenario["disruption"]
        (attached by /api/disrupt: machine_id, disruption_type, start_hour, end_hour,
        hours relative to shift start)
      - one changeover per order moved to another machine with a changeover cost
    """
    events: list[dict[str, Any]] = []
    win = shift_window(state)
    d = scenario.get("disruption") or {}
    if win and d.get("machine_id") is not None:
        try:
            start = win[0] + timedelta(hours=float(d.get("start_hour", 0)))
            end = win[0] + timedelta(hours=float(d.get("end_hour", 0)))
        except (TypeError, ValueError):
            start = end = win[0]
        if end > start:
            events.append({
                "machine_id": d["machine_id"],
                "category": "unplanned",
                "start": start.isoformat(),
                "end": end.isoformat(),
                "reason": d.get("disruption_type", "BREAKDOWN"),
                "scenario_id": scenario.get("id"),
                "approved_at": approved_at,
            })
    co_min = changeover_minutes()
    for ch in scenario.get("gantt_changes") or []:
        moved = ch.get("to_machine") and ch.get("to_machine") != ch.get("from_machine")
        if moved and (ch.get("changeover_cost_idr") or 0) > 0:
            events.append({
                "machine_id": ch["to_machine"],
                "category": "changeover",
                "minutes": co_min,
                "reason": f"{ch.get('order_id')} dari {ch.get('from_machine')}",
                "scenario_id": scenario.get("id"),
                "approved_at": approved_at,
            })
    return events


# ─────────────────────────────────────────────────────────────────────────────
# Main computation
# ─────────────────────────────────────────────────────────────────────────────


def compute_metrics(state: dict[str, Any]) -> dict[str, Any]:
    cfg = state.get("shop_floor_config", {})
    machines = state.get("work_centers", [])
    win = shift_window(state)
    shift_h = (win[1] - win[0]).total_seconds() / 3600.0 if win else float(cfg.get("shift_hours", 8))
    meta_events = list((state.get("_meta") or {}).get("downtime_events") or [])
    maint = _maintenance_records(state)

    per_machine: list[dict[str, Any]] = []
    totals = {"changeover": 0.0, "maintenance": 0.0, "unplanned": 0.0}

    for w in machines:
        mid = w.get("id")
        planned_ivs: list[Interval] = []
        unplanned_ivs: list[Interval] = []
        reasons: list[str] = []

        if win:
            for m in maint:
                if m.get("work_center_id") != mid:
                    continue
                a, b = _parse_dt(m.get("start")), _parse_dt(m.get("end"))
                iv = _clip((a, b), win) if a and b else None
                if not iv:
                    continue
                kind = str(m.get("type", "")).upper()
                (planned_ivs if kind == "PREVENTIVE" else unplanned_ivs).append(iv)
                reasons.append(f"{m.get('id')} {kind.lower() or 'maintenance'}")

            for ev in meta_events:
                if ev.get("machine_id") != mid or ev.get("category") == "changeover":
                    continue
                a, b = _parse_dt(ev.get("start")), _parse_dt(ev.get("end"))
                iv = _clip((a, b), win) if a and b else None
                if iv:
                    (planned_ivs if ev.get("category") == "maintenance" else unplanned_ivs).append(iv)
                    reasons.append(f"{ev.get('reason', 'gangguan')} (disetujui)")

            status = str(w.get("status", "")).upper()
            if status == "FAULT":
                # No clock in the mock: a machine currently in FAULT is treated as down
                # for the whole shift (overlaps with other windows are merged below).
                unplanned_ivs.append(win)
                reasons.append("status FAULT (sepanjang shift)")
            if status == "MAINTENANCE" and not planned_ivs and not unplanned_ivs:
                planned_ivs.append(win)
                reasons.append("status MAINTENANCE (sepanjang shift)")

        unplanned = _merge(unplanned_ivs)
        planned = _subtract(_merge(planned_ivs), unplanned)
        u_h, p_h = _hours(unplanned), _hours(planned)
        co_h = sum(
            float(ev.get("minutes", 0)) / 60.0
            for ev in meta_events
            if ev.get("machine_id") == mid and ev.get("category") == "changeover"
        )
        co_h = min(co_h, max(shift_h - u_h - p_h, 0.0))  # cannot exceed the remaining shift time

        totals["maintenance"] += p_h
        totals["unplanned"] += u_h
        totals["changeover"] += co_h
        per_machine.append({
            "machine_id": mid,
            "maintenance_hours": round(p_h, 2),
            "unplanned_hours": round(u_h, 2),
            "changeover_hours": round(co_h, 2),
            "total_hours": round(p_h + u_h + co_h, 2),
            "reasons": reasons,
        })

    n = len(machines)
    shift_total = shift_h * n
    downtime_total = sum(totals.values())
    planned_time = shift_total - totals["maintenance"]
    run_time = planned_time - totals["unplanned"] - totals["changeover"]
    availability = (run_time / planned_time * 100.0) if planned_time > 0 else 0.0

    feeds = [
        float((w.get("telemetry") or {}).get("feed_rate_percent", 0) or 0)
        for w in machines
        if str(w.get("status", "")).upper() == "RUNNING"
    ]
    performance = (sum(feeds) / len(feeds)) if feeds else 0.0
    performance = max(0.0, min(performance, 100.0))
    quality = QUALITY_ASSUMPTION_PERCENT
    oee = availability * performance * quality / 10_000.0

    return {
        "shift": {
            "date": win[0].date().isoformat() if win else None,
            "start": win[0].strftime("%H:%M") if win else cfg.get("shift_start"),
            "end": win[1].strftime("%H:%M") if win else cfg.get("shift_end"),
            "hours_per_machine": round(shift_h, 2),
            "machines": n,
            "total_hours": round(shift_total, 2),
        },
        "downtime": {
            "total_hours": round(downtime_total, 2),
            "percent_of_shift": round(downtime_total / shift_total * 100.0, 1) if shift_total else 0.0,
            "by_category": [
                {"key": k, "label": CATEGORY_LABELS[k], "hours": round(totals[k], 2)}
                for k in ("changeover", "maintenance", "unplanned")
            ],
            "by_machine": per_machine,
        },
        "oee": {
            "target": OEE_TARGET_PERCENT,
            "availability": round(availability, 1),
            "performance": round(performance, 1),
            "quality": round(quality, 1),
            "oee": round(oee, 1),
            "sources": {
                "availability": "state",
                "performance": "telemetry",
                "quality": "assumption",
            },
        },
        "sap_version": (state.get("_meta") or {}).get("version"),
    }
