"""
backend/agent_run.py
────────────────────
Agent run: one PLC alarm in, a planned chain of tool calls out.

    alarm ─▶ plan (LLM on SAP AI Core) ─▶ tools, in dependency order ─▶ one approval

Tools are thin wrappers over code that already exists (the proactive maintenance
flow, the SAP mock, the CP-SAT solver), so the LLM decides *which* steps to run
and why, while every number comes from deterministic code. Each step's output
feeds the next one: the parts check decides between a stock reservation and a
purchase requisition, and the supplier lead time becomes the downtime window the
solver plans around.

The agent does everything by itself except the irreversible SAP writes (parts
order + production-order reroute). Those wait for one supervisor approval.
If the LLM is unreachable or returns an unusable plan, the fixed plan runs and
the run says so (planner.source = "fallback").
"""

from __future__ import annotations

import asyncio
import math
import time
import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any, Awaitable, Callable, Dict, List, Optional

from rag.llm import parse_json_loose
from rag.metadata import part_key

_WIB = timezone(timedelta(hours=7))
SHIFT_START_HOUR = 7          # shift hour 0 = 07:00 WIB (same as the solver and the cockpit)
SHIFT_HOURS = 8
MAX_RUNS = 20

# Canonical order = dependency order. The plan picks tools; execution always follows this order.
TOOLS: List[Dict[str, Any]] = [
    {"name": "raise_alarm", "label": "Naikkan alarm",
     "desc": "Buka insiden dari alarm PLC dan catat di audit ledger.", "needs": []},
    {"name": "find_similar_cases", "label": "Cari kasus serupa",
     "desc": "Cari perbaikan serupa di riwayat maintenance (RAG: kode alarm, mesin, gejala).",
     "needs": ["raise_alarm"]},
    {"name": "route_to_expert", "label": "Rutekan ke pakar",
     "desc": "Pilih mekanik dari keahlian, model mesin, shift, dan siapa yang memperbaiki kasus serupa.",
     "needs": ["find_similar_cases"]},
    {"name": "check_spare_stock", "label": "Cek stok suku cadang",
     "desc": "Cek stok SAP untuk suku cadang yang dipakai di kasus serupa.",
     "needs": ["find_similar_cases"]},
    {"name": "order_spare_parts", "label": "Pesan suku cadang",
     "desc": "Reservasi stok yang ada; buat purchase requisition bila stok habis (menunggu persetujuan).",
     "needs": ["check_spare_stock"]},
    {"name": "reschedule_orders", "label": "Alihkan work order",
     "desc": "Pindahkan work order dari mesin yang berhenti ke lini lain dengan solver CP-SAT "
             "(menunggu persetujuan).", "needs": ["raise_alarm"]},
    {"name": "notify_team", "label": "Kabari tim",
     "desc": "Kirim playbook perbaikan ke mekanik terpilih dan ringkasan ke supervisor.",
     "needs": ["route_to_expert"]},
]
TOOL_BY_NAME = {t["name"]: t for t in TOOLS}
TOOL_ORDER = [t["name"] for t in TOOLS]
ACTIVE = ("planning", "running")
FINISHED = ("done", "approved", "failed", "rejected")


@dataclass
class AgentDeps:
    get_state: Callable[[], Dict[str, Any]]
    apply_delta: Callable[[Dict[str, Any], int], Dict[str, Any]]
    ledger_append: Callable[[Dict[str, Any]], Dict[str, Any]]
    handle_case: Callable[[Dict[str, Any]], Awaitable[Dict[str, Any]]]   # proactive flow
    solve: Callable[[Dict[str, Any], Dict[str, Any]], List[Dict[str, Any]]]
    issue_scenarios: Callable[[List[Dict[str, Any]]], None]
    # (scenario_id, scenario, sap_version, approved_by, source=…) → receipt
    approve_scenario: Callable[..., Awaitable[Dict[str, Any]]]
    llm: Callable[[str, int, Optional[str]], Awaitable[str]]               # prompt, max_tokens, model
    active_model: Callable[[], str]
    planner_model: Callable[[], str]
    # (mechanic, message, context) → delivery result {"status": sent|failed|not_configured, …}
    notify: Optional[Callable[[Dict[str, Any], List[str], Dict[str, Any]], Awaitable[Dict[str, Any]]]] = None
    # (machine_id, metric, alarm_code, evidence case ids) → the routed skill's guidance, or None
    skill_guidance: Optional[Callable[[str, Optional[str], Optional[str], List[str]], Optional[Dict[str, Any]]]] = None
    now: Callable[[], datetime] = lambda: datetime.now(_WIB)
    step_delay: float = 0.35      # seconds between steps, so a watcher can follow the run live


def _rp(v: Any) -> str:
    """Rupiah with Indonesian thousands dots: Rp 18.500.000."""
    return "Rp " + f"{float(v or 0):,.0f}".replace(",", ".")


AWAITING = " Menunggu persetujuan supervisor."


def _iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def fallback_plan() -> List[Dict[str, str]]:
    why = {
        "raise_alarm": "Alarm PLC harus tercatat sebagai insiden sebelum apa pun.",
        "find_similar_cases": "Perbaikan yang pernah berhasil adalah petunjuk terbaik.",
        "route_to_expert": "Kasus serupa menunjukkan siapa yang paling paham.",
        "check_spare_stock": "Suku cadang dari kasus serupa kemungkinan dibutuhkan lagi.",
        "order_spare_parts": "Memesan lebih awal memangkas downtime.",
        "reschedule_orders": "Mesin berhenti; order harus pindah agar SLA aman.",
        "notify_team": "Mekanik perlu playbook sebelum tiba di mesin.",
    }
    return [{"tool": n, "why": why[n]} for n in TOOL_ORDER]


def normalize_plan(raw: Any) -> Optional[List[Dict[str, str]]]:
    """Keep known tools only, add missing dependencies, return them in dependency order.

    None when the LLM gave nothing usable (then the fixed plan runs instead)."""
    if not isinstance(raw, dict) or not isinstance(raw.get("steps"), list):
        return None
    chosen: Dict[str, Dict[str, str]] = {}
    for s in raw["steps"]:
        if isinstance(s, dict) and s.get("tool") in TOOL_BY_NAME and s["tool"] not in chosen:
            chosen[s["tool"]] = {"tool": s["tool"], "why": str(s.get("why") or "")[:240]}
    if not chosen:
        return None
    changed = True
    while changed:                        # pull in dependencies of dependencies
        changed = False
        for name in list(chosen):
            for dep in TOOL_BY_NAME[name]["needs"]:
                if dep not in chosen:
                    chosen[dep] = {"tool": dep, "why": "Ditambahkan otomatis: dibutuhkan oleh langkah lain.",
                                   "added": "dependency"}
                    changed = True
    return [chosen[n] for n in TOOL_ORDER if n in chosen]


def plan_prompt(trigger: Dict[str, Any]) -> str:
    tools = "\n".join(f"- {t['name']}: {t['desc']}" for t in TOOLS)
    return (
        "Anda adalah agen maintenance SkillForge di PT Karawang Precision Manufacturing. "
        "Sebuah alarm PLC baru saja aktif. Susun rencana tindakan memakai HANYA tool berikut:\n"
        f"{tools}\n\n"
        f"Alarm: mesin {trigger['machine_id']}, kode {trigger.get('alarm_code') or '-'}, "
        f"teks '{trigger.get('alarm_text') or '-'}', tag {trigger.get('tag') or '-'} = {trigger.get('value')}.\n\n"
        "Tujuan: amankan mesin, perbaiki secepatnya, minimalkan downtime dan denda SLA. "
        "Penulisan ke SAP (pesan suku cadang, alihkan order) baru dijalankan setelah supervisor menyetujui.\n"
        'Balas JSON saja: {"reasoning": "<1-2 kalimat>", "steps": [{"tool": "<nama>", "why": "<alasan singkat>"}]}'
    )


def shift_window(now: datetime, downtime_h: float) -> Dict[str, int]:
    """(start_hour, end_hour) on the 8-hour shift grid the solver uses.

    Outside the shift the window starts at the next shift's 07:00, never in the past."""
    h = now.astimezone(_WIB).hour - SHIFT_START_HOUR
    start = h if 0 <= h <= SHIFT_HOURS - 1 else 0
    end = min(SHIFT_HOURS, start + max(1, math.ceil(downtime_h)))
    return {"start_hour": start, "end_hour": max(end, start + 1)}


def stock_check(part_codes: List[str], spare_parts: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Each needed part against SAP stock; OCR spellings match by part_key."""
    by_key = {part_key(p["part_no"]): p for p in spare_parts}
    rows, seen = [], set()
    for code in part_codes:
        key = part_key(code)
        if key in seen:
            continue
        seen.add(key)
        p = by_key.get(key)
        if p is None:
            rows.append({"code": code, "in_master": False, "status": "UNKNOWN"})
            continue
        rows.append({"code": code, "part_no": p["part_no"], "in_master": True,
                     "description": p.get("description"), "stock_qty": p.get("stock_qty", 0),
                     "unit": p.get("unit"), "storage_location": p.get("storage_location"),
                     "supplier": p.get("supplier"), "lead_time_days": p.get("lead_time_days"),
                     "unit_price_idr": p.get("unit_price_idr"),
                     "status": "IN_STOCK" if (p.get("stock_qty") or 0) > 0 else "OUT_OF_STOCK"})
    return rows


def parts_order(stock: List[Dict[str, Any]], run_id: str, machine_id: str) -> Dict[str, Any]:
    """Branch: reserve what is on the shelf, raise a purchase requisition for what is not."""
    stamp = uuid.uuid4().hex[:6].upper()
    reservations, requisitions = [], []
    for i, r in enumerate(x for x in stock if x["in_master"]):
        base = {"part_no": r["part_no"], "description": r["description"], "qty": 1, "unit": r["unit"],
                "machine_id": machine_id, "agent_run_id": run_id}
        if r["status"] == "IN_STOCK":
            reservations.append({**base, "id": f"RSV-{stamp}-{i + 1}",
                                 "storage_location": r["storage_location"], "status": "PROPOSED"})
        else:
            requisitions.append({**base, "id": f"PR-{stamp}-{i + 1}", "supplier": r["supplier"],
                                 "lead_time_days": r["lead_time_days"],
                                 "value_idr": r["unit_price_idr"], "status": "PROPOSED"})
    lead = max((p["lead_time_days"] or 0 for p in requisitions), default=0)
    return {"reservations": reservations, "purchase_requisitions": requisitions,
            "max_lead_time_days": lead}


class AgentRunner:
    def __init__(self, deps: AgentDeps):
        self.deps = deps
        self.runs: Dict[str, Dict[str, Any]] = {}
        self._tasks: set[asyncio.Task] = set()

    # ── queries ──────────────────────────────────────────────────────────────

    def get(self, run_id: str) -> Optional[Dict[str, Any]]:
        return self.runs.get(run_id)

    def latest(self) -> Optional[Dict[str, Any]]:
        """The run the cockpit should show: one still working, else one waiting for approval, else the newest."""
        runs = list(reversed(self.runs.values()))
        return (next((r for r in runs if r["status"] in ACTIVE), None)
                or next((r for r in runs if r["status"] == "awaiting_approval"), None)
                or (runs[0] if runs else None))

    def busy_for(self, machine_id: str, alarm_code: Optional[str] = None) -> Optional[Dict[str, Any]]:
        """A run still planning/running on this machine, or one waiting for approval on the same alarm."""
        for r in reversed(list(self.runs.values())):
            if r["trigger"]["machine_id"] != machine_id:
                continue
            if r["status"] in ACTIVE:
                return r
            if r["status"] == "awaiting_approval" and r["trigger"].get("alarm_code") == alarm_code:
                return r
        return None

    # ── start ────────────────────────────────────────────────────────────────

    def start(self, trigger: Dict[str, Any]) -> Dict[str, Any]:
        run = self.create(trigger)
        task = asyncio.create_task(self.execute(run["id"]))
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)
        return run

    def create(self, trigger: Dict[str, Any]) -> Dict[str, Any]:
        run_id = f"RUN-{uuid.uuid4().hex[:8].upper()}"
        run = {"id": run_id, "status": "planning", "trigger": dict(trigger), "started_at": _iso(),
               "finished_at": None, "planner": None, "plan": [], "steps": [], "pending_approval": None,
               "summary": None, "summary_model": None, "approval": None, "error": None}
        self.runs[run_id] = run
        # Evict the oldest finished runs only; a run still working or waiting for approval stays.
        for old in [k for k, r in self.runs.items() if r["status"] in FINISHED][:max(0, len(self.runs) - MAX_RUNS)]:
            del self.runs[old]
        return run

    def expire_pending(self, reason: str) -> List[str]:
        """Close every run waiting for approval (its plan no longer fits SAP); returns their ids."""
        expired = [r for r in self.runs.values() if r["status"] == "awaiting_approval"]
        for r in expired:
            r["status"], r["error"], r["finished_at"] = "rejected", reason, _iso()
        return [r["id"] for r in expired]

    async def shutdown(self) -> None:
        """Cancel runs still working so the app can stop without orphaned tasks."""
        tasks = list(self._tasks)
        for t in tasks:
            t.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)

    # ── execute ──────────────────────────────────────────────────────────────

    async def execute(self, run_id: str) -> Dict[str, Any]:
        run = self.runs[run_id]
        try:
            await self._plan(run)
            run["status"] = "running"
            ctx: Dict[str, Any] = {}
            for p in run["plan"]:
                await self._step(run, p["tool"], ctx)
                await asyncio.sleep(self.deps.step_delay)
            await self._summarize(run, ctx)
            needs_approval = bool(run["pending_approval"] and (
                run["pending_approval"].get("parts") or run["pending_approval"].get("scenario")))
            run["status"] = "awaiting_approval" if needs_approval else "done"
        except Exception as exc:  # a broken tool must end the run visibly, never hang it
            run["status"], run["error"] = "failed", f"{type(exc).__name__}: {str(exc)[:300]}"
        run["finished_at"] = _iso()
        return run

    async def _plan(self, run: Dict[str, Any]) -> None:
        model = self.deps.planner_model()
        t0 = time.perf_counter()
        text = await self.deps.llm(plan_prompt(run["trigger"]), 800, model)
        data = None if text.startswith("[AI") else parse_json_loose(text)
        plan = normalize_plan(data)
        if plan:
            run["planner"] = {"source": "llm", "model": self.deps.active_model(),
                              "reasoning": str(data.get("reasoning") or "")[:400]}
        else:
            plan = fallback_plan()
            run["planner"] = {"source": "fallback", "model": None,
                              "reasoning": "LLM tidak memberi rencana yang bisa dipakai; rencana baku dijalankan.",
                              "llm_error": text[:200] if text.startswith("[AI") else None}
        run["planner"]["ms"] = round((time.perf_counter() - t0) * 1000)
        run["plan"] = [{**p, "label": TOOL_BY_NAME[p["tool"]]["label"]} for p in plan]
        self.deps.ledger_append({"action_type": "AGENT_PLAN_CREATED", "agent_run_id": run["id"],
                                 "machine_id": run["trigger"]["machine_id"],
                                 "planner": run["planner"]["source"], "model": run["planner"]["model"],
                                 "tools": [p["tool"] for p in plan]})

    async def _step(self, run: Dict[str, Any], tool: str, ctx: Dict[str, Any]) -> None:
        step = {"tool": tool, "label": TOOL_BY_NAME[tool]["label"], "status": "running",
                "started_at": _iso(), "ms": None, "summary": "", "output": {}}
        run["steps"].append(step)
        t0 = time.perf_counter()
        try:
            summary, output = await getattr(self, f"_tool_{tool}")(run, ctx)
            step.update(status="done", summary=summary, output=output)
        except _Skip as s:
            step.update(status="skipped", summary=str(s))
        step["ms"] = round((time.perf_counter() - t0) * 1000)

    # ── tools ────────────────────────────────────────────────────────────────

    async def _tool_raise_alarm(self, run, ctx):
        t = run["trigger"]
        entry = self.deps.ledger_append({
            "action_type": "AGENT_ALARM_RAISED", "agent_run_id": run["id"], "machine_id": t["machine_id"],
            "alarm_code": t.get("alarm_code"), "alarm_text": t.get("alarm_text"),
            "tag": t.get("tag"), "value": t.get("value"), "source": t.get("source", "plc"),
        })
        ctx["alarm_ledger_id"] = entry["id"]
        reading = f" ({t['tag']} = {t['value']})" if t.get("tag") and t.get("value") is not None else ""
        return (f"Insiden dibuka: {t.get('alarm_code') or 'alarm'} di {t['machine_id']} — "
                f"{t.get('alarm_text') or ''}{reading}.", {"ledger_entry_id": entry["id"]})

    async def _flow(self, run, ctx) -> Dict[str, Any]:
        if "flow" not in ctx:
            t = run["trigger"]
            res = await self.deps.handle_case({
                "machine_id": t["machine_id"],
                "symptom": f"{t.get('alarm_code') or ''} {t.get('alarm_text') or ''}".strip(),
                "text": (f"Alarm PLC {t.get('alarm_code') or ''}: {t.get('alarm_text') or ''}. "
                         f"{t.get('tag') or ''} {t.get('value') if t.get('value') is not None else ''}").strip(),
                "error_codes": [t["alarm_code"]] if t.get("alarm_code") else [],
                "source": "plc", "severity": "CRITICAL", "reported_by": "SkillForge agent",
            })
            ctx["flow"], ctx["case"] = res["flow"], res["entry"]
        return ctx["flow"]

    async def _tool_find_similar_cases(self, run, ctx):
        flow = await self._flow(run, ctx)
        hits = flow["retrieval"]["hits"]
        pb = flow["playbook"]
        top = [{"case_id": h.get("case_id"), "score": h.get("score"), "exact_match": h.get("exact_match"),
                "citation": h.get("citation")} for h in hits[:3]]
        skill = self._skill(run, [h.get("case_id") for h in hits if h.get("case_id")])
        skill_note = ""
        if skill:
            first = (skill["root_causes"] or [{}])[0].get("cause")
            skill_note = (f" Memakai skill {skill['name']} v{skill.get('version') or '-'}"
                          + (" (uji lapangan, saran saja)" if skill.get("status") == "trial" else "")
                          + (f"; penyebab paling mungkin: {first}." if first else "."))
        if not hits:
            return ("Tidak ada kasus serupa yang cukup relevan; eskalasi ke mekanik senior." + skill_note,
                    {"hits": 0, "skill": skill})
        return (f"{len(hits)} kasus serupa ditemukan (keyakinan {pb['confidence']}); "
                f"playbook {len(pb['steps'])} langkah, estimasi downtime "
                f"{pb['est_downtime_hours'] if pb['est_downtime_hours'] is not None else '?'} jam." + skill_note,
                {"hits": len(hits), "skill": skill, "top": top, "case_id": ctx["case"]["id"],
                 "playbook_steps": [s["text"] for s in pb["steps"][:5]],
                 "est_downtime_hours": pb["est_downtime_hours"], "confidence": pb["confidence"],
                 "parts": [p["code"] for p in pb.get("parts", [])]})

    def _skill(self, run, evidence: List[str]) -> Optional[Dict[str, Any]]:
        """The skill the plant's experts wrote for this alarm, with causes backed by the retrieved cases first."""
        if not self.deps.skill_guidance:
            return None
        t = run["trigger"]
        g = self.deps.skill_guidance(t["machine_id"], t.get("tag"), t.get("alarm_code"), evidence)
        if not g or not g.get("has_know_how"):
            return None
        out = {k: g.get(k) for k in ("skill_id", "name", "version", "owner", "status")}
        out["root_causes"] = [{k: c.get(k) for k in ("cause", "sources", "evidence_found")}
                              for c in g.get("root_causes", [])[:3]]
        out["tasks"] = list(g.get("tasks", []))[:6]
        self.deps.ledger_append({"action_type": "SKILL_FIRED", "skill_id": g["skill_id"],
                                 "skill_version": g.get("version"), "agent_run_id": run["id"],
                                 "machine_id": t["machine_id"], "trigger": "agent_run"})
        return out

    async def _tool_route_to_expert(self, run, ctx):
        mech = (await self._flow(run, ctx))["mechanic"]
        if not mech.get("id"):
            raise _Skip("Tidak ada mekanik yang cocok di roster.")
        ctx["mechanic"] = mech
        why = ", ".join(mech.get("reasons") or []) or "paling sesuai"
        return (f"Tiket dirutekan ke {mech['name']} ({mech.get('role')}): {why}.",
                {"mechanic_id": mech["id"], "name": mech["name"], "role": mech.get("role"),
                 "shift": mech.get("shift"), "reasons": mech.get("reasons") or []})

    async def _tool_check_spare_stock(self, run, ctx):
        flow = await self._flow(run, ctx)
        codes = [p["code"] for p in flow["playbook"].get("parts", [])]
        if not codes:
            raise _Skip("Kasus serupa tidak mencatat suku cadang; tidak ada yang perlu dicek.")
        rows = stock_check(codes, self.deps.get_state().get("spare_parts", []))
        ctx["stock"] = rows
        ok = sum(r["status"] == "IN_STOCK" for r in rows)
        out = [r for r in rows if r["status"] == "OUT_OF_STOCK"]
        text = f"{len(rows)} suku cadang dicek di SAP: {ok} tersedia"
        if out:
            text += ", " + ", ".join(f"{r['part_no']} habis (lead time {r['lead_time_days']} hari)" for r in out)
        return text + ".", {"parts": rows}

    async def _tool_order_spare_parts(self, run, ctx):
        stock = ctx.get("stock") or []
        if not any(r["in_master"] for r in stock):
            raise _Skip("Tidak ada suku cadang dari master SAP yang perlu dipesan.")
        order = parts_order(stock, run["id"], run["trigger"]["machine_id"])
        ctx["parts_order"] = order
        self._pending(run)["parts"] = order
        bits = []
        if order["reservations"]:
            bits.append(f"reservasi {len(order['reservations'])} item dari gudang")
        if order["purchase_requisitions"]:
            value = sum(p["value_idr"] or 0 for p in order["purchase_requisitions"])
            bits.append(f"purchase requisition {len(order['purchase_requisitions'])} item "
                        f"({_rp(value)}, tiba ~{order['max_lead_time_days']} hari)")
        return "Diusulkan: " + " dan ".join(bits) + "." + AWAITING, order

    async def _tool_reschedule_orders(self, run, ctx):
        t = run["trigger"]
        repair_h = (ctx.get("flow") or {}).get("playbook", {}).get("est_downtime_hours") or 3.0
        lead_days = (ctx.get("parts_order") or {}).get("max_lead_time_days") or 0
        # Waiting for a part blocks the machine for the rest of the shift; otherwise the repair time does.
        downtime_h = SHIFT_HOURS if lead_days else float(repair_h)
        window = shift_window(self.deps.now(), downtime_h)
        disruption = {"machine_id": t["machine_id"], "disruption_type": "BREAKDOWN", **window}
        state = self.deps.get_state()
        scenarios = await asyncio.to_thread(self.deps.solve, state, disruption)
        for s in scenarios:
            s["disruption"] = dict(disruption)
            # The solver reuses scenario_a/b/c on every solve; a run-scoped id keeps this run's
            # scenario approvable after a later /api/disrupt or another run issues its own.
            s["id"] = f"{run['id']}:{s['id']}"
        self.deps.issue_scenarios(scenarios)
        best = next((s for s in scenarios if s.get("recommended")), scenarios[0] if scenarios else None)
        reason = (f"menunggu suku cadang ~{lead_days} hari" if lead_days
                  else f"estimasi perbaikan {repair_h} jam")
        if not best or not best.get("gantt_changes"):
            return (f"{t['machine_id']} berhenti jam {window['start_hour']}–{window['end_hour']} shift "
                    f"({reason}); tidak ada work order yang perlu dipindah.",
                    {"disruption": disruption, "scenario_id": best["id"] if best else None})
        self._pending(run)["scenario"] = best
        moves = [{"order_id": c.get("order_id"), "from": c.get("from_machine"), "to": c.get("to_machine")}
                 for c in best["gantt_changes"]]
        moved = [m for m in moves if m["to"] != m["from"]]
        targets = sorted({m["to"] for m in moved})
        return (f"Solver CP-SAT ({reason}): {best.get('name_id') or best['id']} — "
                f"{len(moved)} order dipindah ke {', '.join(targets) or 'mesin yang sama'}, "
                f"biaya {_rp(best.get('total_cost_idr'))}, hemat {_rp(best.get('net_savings_idr'))}." + AWAITING,
                {"disruption": disruption, "scenario_id": best["id"], "scenario_name": best.get("name_id"),
                 "moves": moves, "total_cost_idr": best.get("total_cost_idr"),
                 "net_savings_idr": best.get("net_savings_idr"),
                 "alternatives": [s["id"] for s in scenarios if s is not best]})

    async def _tool_notify_team(self, run, ctx):
        flow = await self._flow(run, ctx)
        mech = flow["mechanic"]
        name = mech.get("name") or "tim mekanik"
        context = {"agent_run_id": run["id"], "machine_id": run["trigger"]["machine_id"],
                   "maintenance_case_id": flow.get("trigger_entry_id")}
        delivery = (await self.deps.notify(mech, flow.get("chat_messages") or [flow["message"]], context)
                    if self.deps.notify else {"status": "not_configured"})
        if delivery.get("status") == "sent":
            head = f"Playbook dikirim ke {name} via Telegram ({delivery.get('to')})"
        elif delivery.get("status") == "failed":
            head = f"Pesan Telegram ke {name} gagal: {delivery.get('error')}; playbook tetap ada di halaman Perawatan"
        else:
            head = f"Pesan untuk {name} disiapkan (bot Telegram belum dikonfigurasi)"
        return (head + "; supervisor menerima ringkasan di cockpit.",
                {"message": flow["message"], "playbook_ledger_id": flow.get("playbook_ledger_id"),
                 "mechanic_id": mech.get("id"), "delivery": delivery})

    def _pending(self, run) -> Dict[str, Any]:
        if run["pending_approval"] is None:
            run["pending_approval"] = {"parts": None, "scenario": None}
        return run["pending_approval"]

    # ── summary ──────────────────────────────────────────────────────────────

    async def _summarize(self, run, ctx) -> None:
        facts = "\n".join(f"- {s['label']}: {s['summary']}" for s in run["steps"] if s["summary"])
        picked = [s["summary"].replace(AWAITING, "") for s in run["steps"]
                  if s["tool"] in ("route_to_expert", "order_spare_parts", "reschedule_orders")
                  and s["status"] == "done"]
        fallback = " ".join(picked) + (AWAITING if run["pending_approval"] else "")
        prompt = ("Ringkas hasil kerja agen maintenance berikut untuk supervisor dalam 2-3 kalimat Bahasa "
                  "Indonesia. Pakai hanya fakta di bawah, sebut angka persis, dan akhiri dengan apa yang "
                  "perlu disetujui.\n" + facts + "\n\nJawab HANYA ringkasannya.")
        text = await self.deps.llm(prompt, 400, None)
        if text.startswith("[AI") or len(text.strip()) < 20:
            run["summary"], run["summary_model"] = fallback or "Agen selesai; lihat langkah di atas.", None
        else:
            run["summary"], run["summary_model"] = text.strip(), self.deps.active_model()

    # ── approval ─────────────────────────────────────────────────────────────

    async def approve(self, run_id: str, approved_by: str) -> Dict[str, Any]:
        run = self.runs.get(run_id)
        if run is None:
            raise KeyError(run_id)
        if run["status"] != "awaiting_approval":
            raise ValueError(f"run is {run['status']}, not awaiting approval")
        pending = run["pending_approval"] or {}
        # Receipts of writes already applied survive a failed attempt, so a retry never repeats them.
        receipts: Dict[str, Any] = run.setdefault("receipts", {})
        try:
            # Reroute first: it is the write that Tier 1 / drift checks can refuse, and a
            # refusal must not leave a parts order behind without its schedule change.
            scenario = pending.get("scenario")
            if scenario and "scenario" not in receipts:
                version = int(self.deps.get_state()["_meta"]["version"])
                t = run["trigger"]
                source = {"kind": "agent_run", "run_id": run["id"], "machine_id": t.get("machine_id"),
                          "alarm_code": t.get("alarm_code"), "alarm_text": t.get("alarm_text")}
                receipts["scenario"] = await self.deps.approve_scenario(scenario["id"], scenario, version,
                                                                        approved_by, source=source)
            parts = pending.get("parts")
            if parts and (parts["reservations"] or parts["purchase_requisitions"]) and "parts" not in receipts:
                receipts["parts"] = self._apply_parts(run, parts, approved_by)
        except ApprovalRefused as exc:
            run["status"], run["error"] = "rejected", f"Persetujuan ditolak: {exc}"
            run["finished_at"] = _iso()
            raise
        except Exception as exc:  # e.g. SAP drift: the run stays approvable, with the reason shown
            run["error"] = f"Persetujuan gagal, coba lagi: {type(exc).__name__}: {str(exc)[:200]}"
            raise
        run["approval"] = {"approved_by": approved_by, "at": _iso(), **receipts}
        run["status"], run["error"] = "approved", None
        return run

    def _apply_parts(self, run, order, approved_by) -> Dict[str, Any]:
        state = self.deps.get_state()
        stock = {p["part_no"]: p for p in state.get("spare_parts", [])}
        now = _iso()
        # Stock may have moved since planning: reserve only what is still on the shelf,
        # and turn the rest into a purchase requisition.
        left = {no: p.get("stock_qty") or 0 for no, p in stock.items()}
        reservations, requisitions = [], []
        for r in order["reservations"]:
            if left.get(r["part_no"], 0) >= r["qty"]:
                left[r["part_no"]] -= r["qty"]
                reservations.append({**r, "status": "RESERVED", "approved_by": approved_by, "created_at": now})
            else:
                p = stock.get(r["part_no"], {})
                requisitions.append({k: v for k, v in r.items() if k != "storage_location"} | {
                    "id": r["id"].replace("RSV-", "PR-", 1), "supplier": p.get("supplier"),
                    "lead_time_days": p.get("lead_time_days"),
                    "value_idr": (p.get("unit_price_idr") or 0) * r["qty"],
                    "status": "RELEASED", "approved_by": approved_by, "created_at": now,
                    "note": "stok habis saat persetujuan"})
        requisitions += [{**p, "status": "RELEASED", "approved_by": approved_by, "created_at": now}
                         for p in order["purchase_requisitions"]]
        updates = [{"part_no": no, "stock_qty": qty} for no, qty in left.items()
                   if qty != (stock[no].get("stock_qty") or 0)]
        version = int(state["_meta"]["version"])
        new_state = self.deps.apply_delta({"spare_part_updates": updates, "reservations_add": reservations,
                                           "purchase_requisitions_add": requisitions}, version)
        entry = self.deps.ledger_append({
            "action_type": "SPARE_PARTS_ORDERED", "agent_run_id": run["id"],
            "machine_id": run["trigger"]["machine_id"],
            "reservations": [r["id"] for r in reservations],
            "purchase_requisitions": [p["id"] for p in requisitions],
            "approved_by": approved_by, "sap_version_before": version,
            "sap_version_after": new_state["_meta"]["version"],
        })
        return {"receipt_id": entry["id"], "sha256_hash": entry["sha256_hash"],
                "reservations": [r["id"] for r in reservations],
                "purchase_requisitions": [p["id"] for p in requisitions]}


class ApprovalRefused(Exception):
    """A business rule refused the run's SAP write (Tier 1, scenario mismatch); retrying will not help."""


class _Skip(Exception):
    """A tool had nothing to do; the step shows as skipped, the run goes on."""
