"""
backend/chat_context.py
───────────────────────
Text blocks for the AI Supervisor chat prompt (/api/chat), built from what the
backend already holds: the audit ledger, the live PLC feed, agent runs, the
solver's latest plans and the shift metrics. Pure functions, so each block can
be tested without a running app or an LLM.

Every block is DATA for the prompt. Where something is unknown, the block says
so instead of leaving it out, so the model answers "not recorded" rather than
guessing.
"""

from __future__ import annotations

from typing import Any, Dict, Iterable, List, Optional

SCENARIO_NAMES = {
    "scenario_a": "A · Status Quo (terima keterlambatan)",
    "scenario_b": "B · Lembur shift malam",
    "scenario_c": "C · Rerute optimal",
}
MAX_RUNS = 3
MAX_STEP_CHARS = 220


def _hhmm(ts: Any) -> str:
    """'2025-01-15T07:00:00' -> '07:00'; anything else is passed through."""
    t = str(ts or "")
    return t[11:16] if len(t) >= 16 and t[10] == "T" else (t or "?")


def _rp(v: Any) -> str:
    try:
        return f"Rp {int(round(float(v))):,}".replace(",", ".")
    except (TypeError, ValueError):
        return "Rp ?"


def late_note(order: Dict[str, Any]) -> str:
    """', TERLAMBAT …' when the planned end is past the deadline (both ISO timestamps), else ''."""
    end, due = str(order.get("planned_end") or ""), str(order.get("deadline") or "")
    iso = lambda t: len(t) >= 16 and t[10] == "T"  # noqa: E731
    if iso(end) and iso(due) and end[:16] > due[:16]:
        return f", TERLAMBAT: rencana selesai {_hhmm(end)} melewati deadline {_hhmm(due)}"
    return ""


def scenario_name(scenario_id: Any, name: Optional[str] = None) -> str:
    """Readable plan name; agent runs prefix ids as 'RUN-XXXX:scenario_c'."""
    if name:
        return str(name)
    sid = str(scenario_id or "")
    return SCENARIO_NAMES.get(sid.rsplit(":", 1)[-1], sid or "?")


def describe_source(source: Optional[Dict[str, Any]]) -> str:
    """Who set a schedule change in motion, as one phrase."""
    src = source or {}
    if src.get("kind") == "agent_run":
        alarm = src.get("alarm_code") or "alarm manual"
        text = f" ({src['alarm_text']})" if src.get("alarm_text") else ""
        return f"agen SkillForge {src.get('run_id') or ''} setelah {alarm}{text} di {src.get('machine_id') or '?'}".replace("  ", " ")
    if src.get("kind") == "dashboard":
        return "analisis gangguan di dasbor"
    return "persetujuan skenario (sumber tidak tercatat)"


def _since_last_reset(entries: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Ledger entries after the last SAP reset; earlier ones describe a schedule that no longer exists."""
    for i in range(len(entries) - 1, -1, -1):
        if entries[i].get("action_type") == "STATE_RESET":
            return entries[i + 1:]
    return entries


def order_history(entries: List[Dict[str, Any]]) -> Dict[str, List[str]]:
    """Per order id, every approved move and revert since the last reset, oldest first."""
    out: Dict[str, List[str]] = {}
    for e in _since_last_reset(entries):
        kind = e.get("action_type")
        delta = e.get("delta_applied") or {}
        at = f"ledger {_hhmm(e.get('timestamp'))} UTC"
        if kind == "SCENARIO_APPROVED":
            snaps = delta.get("order_snapshots") or {}
            plan = scenario_name(e.get("scenario_chosen"), e.get("scenario_name"))
            origin = describe_source(e.get("source"))
            for u in delta.get("order_updates") or []:
                before = snaps.get(u.get("id")) or {}
                frm = (f"{before.get('work_center_id') or '?'} "
                       f"{_hhmm(before.get('planned_start'))}–{_hhmm(before.get('planned_end'))}")
                to = f"{u.get('work_center_id') or '?'} {_hhmm(u.get('planned_start'))}–{_hhmm(u.get('planned_end'))}"
                out.setdefault(u.get("id"), []).append(
                    f"dipindah dari {frm} ke {to}; asal: {origin}; skenario {plan}; "
                    f"disetujui {e.get('approved_by') or '?'} ({at}, receipt {str(e.get('id') or '')[:8]})")
        elif kind == "SCENARIO_REVERTED":
            for u in delta.get("order_updates") or []:
                out.setdefault(u.get("id"), []).append(
                    f"dikembalikan ke {u.get('work_center_id') or '?'} "
                    f"{_hhmm(u.get('planned_start'))}–{_hhmm(u.get('planned_end'))} "
                    f"(revert receipt {str(e.get('original_entry_id') or '')[:8]}, oleh {e.get('approved_by') or '?'}, {at})")
    return out


def order_history_block(entries: List[Dict[str, Any]], order_ids: Iterable[str]) -> str:
    history = order_history(entries)
    lines = [f"- {oid}: " + " → ".join(history[oid]) for oid in order_ids if oid in history]
    note = ("Order yang tidak tercantum di sini masih rencana awal dari SAP: belum pernah dipindah, "
            "baik oleh agen maupun dari dasbor.")
    return "\n".join(lines + [note]) if lines else "- Belum ada order yang dipindah. " + note


def plc_block(scan: Optional[Dict[str, Any]], offline: Optional[Dict[str, Any]],
              alarms: Iterable[Dict[str, Any]], predictions: Iterable[Dict[str, Any]]) -> str:
    if offline:
        return f"- PLC OFFLINE sejak {offline.get('timestamp') or '?'} ({offline.get('error') or '-'}); tidak ada bacaan live."
    if not scan:
        return "- Tidak ada bacaan PLC live saat ini."
    lines = []
    for m in scan.get("machines") or []:
        tags = ", ".join(f"{k}={v}" for k, v in (m.get("tags") or {}).items())
        flags = [f for f, on in (("E-STOP", m.get("estop")), ("LOCKOUT", m.get("lockout"))) if on]
        alarm = m.get("alarm")
        alarm_txt = f", alarm {alarm.get('code')} {alarm.get('text') or ''}".rstrip() if alarm else ""
        lines.append(f"- {m.get('machine_id')}: {m.get('status')}{' ' + '/'.join(flags) if flags else ''}"
                     f"{alarm_txt}; {tags or 'tanpa tag'}")
    for a in alarms:
        fixes = a.get("past_fixes") or []
        lines.append(f"- Alarm aktif {a.get('machine_id')} {a.get('code')}: {a.get('text') or '-'}"
                     f" ({len(fixes)} perbaikan serupa di riwayat)")
    for p in predictions:
        eta = p.get("eta_warning_s") if p.get("eta_warning_s") is not None else p.get("eta_critical_s")
        lim = "warning" if p.get("eta_warning_s") is not None else "critical"
        mins = f"~{max(1, round(float(eta) / 60))} menit" if eta is not None else "?"
        lines.append(f"- Prediksi {p.get('machine_id')} {p.get('label') or p.get('metric')}: "
                     f"{p.get('current')} {p.get('unit') or ''}, naik {p.get('slope_per_min')}/menit, "
                     f"mencapai batas {lim} dalam {mins}")
    lines.append(f"- Skenario PLC: {scan.get('scenario') or 'normal'}; waktu scan {scan.get('timestamp') or '?'}.")
    return "\n".join(lines)


def agent_runs_block(runs: List[Dict[str, Any]]) -> str:
    """The newest runs, newest first, with what each step did and what is waiting for approval."""
    if not runs:
        return "- Belum ada agent run."
    out = []
    for r in list(reversed(runs))[:MAX_RUNS]:
        t = r.get("trigger") or {}
        out.append(f"- {r.get('id')} status {r.get('status')}: {t.get('alarm_code') or 'alarm manual'} "
                   f"{t.get('alarm_text') or ''} di {t.get('machine_id')}, mulai {_hhmm(r.get('started_at'))} UTC"
                   f"{'; error: ' + str(r['error'])[:160] if r.get('error') else ''}")
        for s in r.get("steps") or []:
            if s.get("summary"):
                out.append(f"  - {s.get('label') or s.get('tool')} ({s.get('status')}): "
                           f"{str(s['summary'])[:MAX_STEP_CHARS]}")
        pending = r.get("pending_approval") or {}
        if r.get("status") == "awaiting_approval":
            sc = pending.get("scenario") or {}
            moves = [f"{c.get('order_id')} {c.get('from_machine')}→{c.get('to_machine')}"
                     for c in sc.get("gantt_changes") or [] if c.get("from_machine") != c.get("to_machine")]
            parts = pending.get("parts") or {}
            out.append(f"  - Menunggu persetujuan: reroute {', '.join(moves) or '-'}"
                       f"{' (' + scenario_name(sc.get('id'), sc.get('name_id')) + ', biaya ' + _rp(sc.get('total_cost_idr')) + ')' if sc else ''}; "
                       f"reservasi {len(parts.get('reservations') or [])} item, "
                       f"purchase requisition {len(parts.get('purchase_requisitions') or [])} item.")
        if r.get("approval"):
            out.append(f"  - Disetujui {r['approval'].get('approved_by')} {_hhmm(r['approval'].get('at'))} UTC.")
    return "\n".join(out)


def scenarios_block(issued: Dict[str, Dict[str, Any]]) -> str:
    """The plans from the latest dashboard disruption analysis (agent-run plans are in the agent block)."""
    plans = [s for sid, s in issued.items() if ":" not in sid]
    if not plans:
        return "- Belum ada analisis gangguan sejak SAP terakhir dimuat."
    d = plans[0].get("disruption") or {}
    head = (f"- Gangguan {d.get('machine_id') or '?'} {d.get('disruption_type') or ''} "
            f"jam ke-{d.get('start_hour', '?')}–{d.get('end_hour', '?')} shift:")
    lines = [head]
    for s in sorted(plans, key=lambda x: str(x.get("id"))):
        moved = sum(1 for c in s.get("gantt_changes") or [] if c.get("from_machine") != c.get("to_machine"))
        lines.append(f"  - {scenario_name(s.get('id'), s.get('name_id'))}{' [DIREKOMENDASIKAN]' if s.get('recommended') else ''}: "
                     f"biaya {_rp(s.get('total_cost_idr'))}, hemat {_rp(s.get('net_savings_idr'))}, {moved} order dipindah")
    return "\n".join(lines)


def metrics_block(metrics: Optional[Dict[str, Any]]) -> str:
    if not metrics:
        return "- Metrik shift tidak tersedia."
    sh, dt, oee = metrics.get("shift") or {}, metrics.get("downtime") or {}, metrics.get("oee") or {}
    lines = [f"- Shift {sh.get('date', '?')} {sh.get('start', '?')}–{sh.get('end', '?')}, "
             f"{sh.get('machines', '?')} mesin × {sh.get('hours_per_machine', '?')} jam "
             f"= {sh.get('total_hours', '?')} jam-mesin. Semua angka downtime di bawah dalam jam-mesin.",
             f"- OEE {oee.get('oee')}% (target {oee.get('target')}%): availability {oee.get('availability')}%, "
             f"performance {oee.get('performance')}%, quality {oee.get('quality')}% (quality adalah asumsi, "
             f"mock belum punya data scrap).",
             f"- Downtime total {dt.get('total_hours')} jam-mesin ({dt.get('percent_of_shift')}% dari total jam-mesin shift): "
             + ", ".join(f"{c.get('label')} {c.get('hours')} jam" for c in dt.get("by_category") or [])]
    for m in dt.get("by_machine") or []:
        if m.get("total_hours"):
            lines.append(f"  - {m.get('machine_id')}: {m.get('total_hours')} jam ({', '.join(m.get('reasons') or []) or '-'})")
    return "\n".join(lines)


def history_block(history: List[Dict[str, str]], max_turns: int = 6, max_chars: int = 600) -> str:
    """Earlier turns of this conversation, oldest first, clipped so they can't crowd out the data."""
    rows = []
    for h in history[-max_turns:]:
        who = "Supervisor" if h.get("role") == "user" else "AI Supervisor"
        text = " ".join(str(h.get("text") or "").split())[:max_chars]
        if text:
            rows.append(f"{who}: {text}")
    return "\n".join(rows)


def skills_block(skills: List[Dict[str, Any]]) -> str:
    """Installed skills: what each catches, its status and the experts' top causes and first tasks."""
    if not skills:
        return "- Belum ada skill dengan know-how."
    out = []
    for sk in skills:
        trig = sk.get("trigger") or {}
        status = {"trial": "uji lapangan (saran saja, tanpa aksi PLC)"}.get(sk.get("status"), sk.get("status"))
        out.append(f"- {sk.get('name')} v{sk.get('version') or '-'} ({sk.get('skill_id')}), {status}, "
                   f"pemilik {sk.get('owner') or '-'}: kode {', '.join(trig.get('alarm_codes') or []) or '-'}, "
                   f"sinyal {', '.join(trig.get('metrics') or []) or '-'}, "
                   f"mesin {', '.join(trig.get('machines') or []) or 'semua'}")
        for n, c in enumerate((sk.get("root_causes") or [])[:3], 1):
            out.append(f"  - Penyebab {n}: {c.get('cause')} [{', '.join(c.get('sources') or [])}]")
        if sk.get("tasks"):
            out.append(f"  - Tugas pertama: {'; '.join(sk['tasks'][:2])}")
    return "\n".join(out)
