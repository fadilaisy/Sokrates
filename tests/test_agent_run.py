"""tests/test_agent_run.py — agent run: plan, tools, branching, one approval (LLM mocked)."""

import asyncio
import json
import time
from datetime import datetime

import pytest
from fastapi.testclient import TestClient

import backend.main as m
from backend import agent_run as ar
from knowledge.maintenance_ledger import MaintenanceLedger
from knowledge.proactive_agent import ProactiveAgent
from rag.store import Store

client: TestClient


@pytest.fixture(scope="module", autouse=True)
def _client(tmp_path_factory):
    # Entered as a context manager so the app's event loop outlives each request:
    # a run is a background task that keeps going after POST /api/agent/run returns.
    # Startup indexes maintenance cases; a private agent keeps that off the shared RAG index.
    global client
    tmp = tmp_path_factory.mktemp("agent_run")
    agent = ProactiveAgent(MaintenanceLedger(tmp / "m.jsonl"), store=Store(tmp / "rag.sqlite3"))
    with pytest.MonkeyPatch.context() as mp:
        mp.setattr(m, "_agent", agent)
        with TestClient(m.app) as c:
            client = c
            yield

PLAN_REPLY = json.dumps({
    "reasoning": "Bearing aus; pesan suku cadang dan pindahkan order.",
    "steps": [{"tool": "order_spare_parts", "why": "bearing"}, {"tool": "reschedule_orders", "why": "SLA"},
              {"tool": "notify_team", "why": "mekanik"}, {"tool": "drop_database", "why": "?"}],
})


@pytest.fixture
def runner(monkeypatch):
    calls = []

    async def llm(prompt, max_tokens, model):
        calls.append((prompt, model))
        if "Susun rencana" in prompt:
            return PLAN_REPLY
        return "Hendra ditugaskan, bearing dipesan, dua order pindah ke CNC-02. Mohon setujui."

    monkeypatch.setattr(m._agent_runner.deps, "llm", llm)
    monkeypatch.setattr(m._agent_runner.deps, "step_delay", 0)
    m._sap.reset()
    yield calls
    m._sap.reset()


def _wait(run_id):
    for _ in range(200):
        run = client.get(f"/api/agent/runs/{run_id}").json()
        if run["status"] not in ar.ACTIVE:
            return run
        time.sleep(0.05)
    raise AssertionError("agent run did not finish")


# ── pure helpers ─────────────────────────────────────────────────────────────

def test_normalize_plan_drops_unknown_tools_and_adds_dependencies():
    plan = ar.normalize_plan(json.loads(PLAN_REPLY))
    assert [p["tool"] for p in plan] == ar.TOOL_ORDER          # all deps pulled in, in order
    added = {p["tool"] for p in plan if p.get("added") == "dependency"}
    assert added == {"raise_alarm", "find_similar_cases", "route_to_expert", "check_spare_stock"}


@pytest.mark.parametrize("raw", [None, "text", {"steps": []}, {"steps": [{"tool": "nope"}]}])
def test_normalize_plan_rejects_unusable_replies(raw):
    assert ar.normalize_plan(raw) is None


def test_stock_check_matches_ocr_spelling_and_flags_out_of_stock():
    parts = [{"part_no": "SEAL-SP-0031", "stock_qty": 6, "lead_time_days": 1},
             {"part_no": "NSK-7014CTYNSULP4", "stock_qty": 0, "lead_time_days": 2}]
    rows = ar.stock_check(["SEAL-SP-O031", "NSK-7014CTYNSULP4", "XX-1"], parts)
    assert [r["status"] for r in rows] == ["IN_STOCK", "OUT_OF_STOCK", "UNKNOWN"]
    order = ar.parts_order(rows, "RUN-1", "CNC-01")
    assert len(order["reservations"]) == 1 and len(order["purchase_requisitions"]) == 1
    assert order["max_lead_time_days"] == 2


def test_shift_window_stays_on_the_shift_grid():
    wib = ar._WIB
    assert ar.shift_window(datetime(2026, 10, 26, 9, 30, tzinfo=wib), 3) == {"start_hour": 2, "end_hour": 5}
    assert ar.shift_window(datetime(2026, 10, 26, 13, 0, tzinfo=wib), 8) == {"start_hour": 6, "end_hour": 8}
    # Outside the shift: next shift's 07:00, never the past.
    assert ar.shift_window(datetime(2026, 10, 26, 21, 0, tzinfo=wib), 2) == {"start_hour": 0, "end_hour": 2}


# ── full run through the API ─────────────────────────────────────────────────

def test_bearing_alarm_runs_every_tool_and_waits_for_one_approval(runner):
    run = client.post("/api/agent/run", json={"machine_id": "CNC-01", "alarm_code": "ALM-162",
                                              "alarm_text": "SPINDLE OVERLOAD"}).json()
    run = _wait(run["id"])
    assert run["status"] == "awaiting_approval", run.get("error")
    assert run["planner"]["source"] == "llm"
    assert runner[0][1] == "anthropic--claude-4.5-sonnet"      # planner model
    steps = {s["tool"]: s for s in run["steps"]}
    assert list(steps) == ar.TOOL_ORDER and all(s["status"] == "done" for s in steps.values())
    assert steps["route_to_expert"]["output"]["name"] == "Hendra Wijaya"
    order = steps["order_spare_parts"]["output"]
    assert [p["part_no"] for p in order["purchase_requisitions"]] == ["NSK-7014CTYNSULP4"]
    # Orchestration: waiting for the part blocks the rest of the shift, and the solver plans for it.
    assert steps["reschedule_orders"]["output"]["disruption"]["end_hour"] == ar.SHIFT_HOURS
    assert run["pending_approval"]["scenario"]["gantt_changes"]
    assert run["summary"].startswith("Hendra")

    version = m._sap.get_version()
    r = client.post(f"/api/agent/runs/{run['id']}/approve", json={"approved_by": "Supervisor Budi"})
    assert r.status_code == 200
    done = r.json()
    assert done["status"] == "approved"
    assert done["approval"]["scenario"]["sap_version_before"] == version
    state = m._sap.get_state()
    assert [p["id"] for p in state["purchase_requisitions"]] == done["approval"]["parts"]["purchase_requisitions"]
    seal = next(p for p in state["spare_parts"] if p["part_no"] == "SEAL-SP-0031")
    assert seal["stock_qty"] == 5                                # reserved one from the shelf
    kinds = [e["action_type"] for e in m._ledger.get_all()]
    for k in ("AGENT_PLAN_CREATED", "AGENT_ALARM_RAISED", "SCENARIO_APPROVED", "SPARE_PARTS_ORDERED"):
        assert k in kinds

    again = client.post(f"/api/agent/runs/{run['id']}/approve", json={"approved_by": "Supervisor Budi"})
    assert again.status_code == 409                              # one approval, never twice


def test_unusable_llm_plan_falls_back_to_fixed_plan(runner, monkeypatch):
    async def down(prompt, max_tokens, model):
        return "[AI error: AI Core login failed] Silakan tinjau skenario solver secara manual."

    monkeypatch.setattr(m._agent_runner.deps, "llm", down)
    run = _wait(client.post("/api/agent/run", json={"machine_id": "CNC-04", "alarm_code": "ALM-410",
                                                    "alarm_text": "SPINDLE MOTOR OVERHEAT"}).json()["id"])
    assert run["planner"]["source"] == "fallback"
    assert [s["tool"] for s in run["steps"]] == ar.TOOL_ORDER
    assert run["summary"] and not run["summary"].startswith("[AI")
    assert run["status"] in ("awaiting_approval", "done")


def test_unknown_machine_is_404():
    assert client.post("/api/agent/run", json={"machine_id": "CNC-99"}).status_code == 404
    assert client.get("/api/agent/runs/RUN-NOPE").status_code == 404


def test_plc_alarm_starts_a_run_by_itself(runner, monkeypatch):
    monkeypatch.setenv("AGENT_AUTORUN", "1")
    frames = []

    async def capture(frame):
        frames.append(frame)

    monkeypatch.setattr(m, "_plc_broadcast", capture)
    row = {"machine_id": "CNC-01", "tags": {"spindle_vibration_mm_per_s": 7.4}}
    before = m._agent_runner.latest()

    async def go():
        await m._autorun_agent(row, "ALM-162", "SPINDLE OVERLOAD")
        await m._autorun_agent(row, "ALM-162", "SPINDLE OVERLOAD")   # same alarm again: no second run
        run = m._agent_runner.latest()
        for _ in range(200):
            if run["status"] not in ar.ACTIVE:
                break
            await asyncio.sleep(0.05)
        return run

    run = asyncio.run(go())
    assert run is not before and run["trigger"]["tag"] == "spindle_vibration_mm_per_s"
    assert run["trigger"]["value"] == 7.4
    assert [f["event"] for f in frames] == ["agent_run"]


# ── review fixes (PR #32 follow-up) ──────────────────────────────────────────

def _pending_run():
    run = client.post("/api/agent/run", json={"machine_id": "CNC-01", "alarm_code": "ALM-162",
                                              "alarm_text": "SPINDLE OVERLOAD"}).json()
    run = _wait(run["id"])
    assert run["status"] == "awaiting_approval", run.get("error")
    return run


def test_run_stays_approvable_after_a_later_solve(runner):
    run = _pending_run()
    assert run["pending_approval"]["scenario"]["id"].startswith(run["id"] + ":")
    r = client.post("/api/disrupt", json={"machine_id": "CNC-02", "disruption_type": "BREAKDOWN",
                                          "start_hour": 2, "end_hour": 5})
    assert r.status_code == 200
    ok = client.post(f"/api/agent/runs/{run['id']}/approve", json={"approved_by": "Supervisor Budi"})
    assert ok.status_code == 200, ok.text


def test_placeholder_signer_cannot_approve(runner):
    run = _pending_run()
    r = client.post(f"/api/agent/runs/{run['id']}/approve", json={"approved_by": "Production Supervisor"})
    assert r.status_code == 422
    assert client.get(f"/api/agent/runs/{run['id']}").json()["status"] == "awaiting_approval"


def test_part_gone_since_planning_becomes_a_requisition(runner):
    run = _pending_run()
    assert "SEAL-SP-0031" in [r["part_no"] for r in run["pending_approval"]["parts"]["reservations"]]
    state = m._sap.get_state()
    m._sap.apply_delta({"spare_part_updates": [{"part_no": "SEAL-SP-0031", "stock_qty": 0}]},
                       expected_version=state["_meta"]["version"])
    done = client.post(f"/api/agent/runs/{run['id']}/approve", json={"approved_by": "Supervisor Budi"}).json()
    parts = done["approval"]["parts"]
    state = m._sap.get_state()
    seal_rows = [p for p in state["purchase_requisitions"] if p["part_no"] == "SEAL-SP-0031"]
    assert len(seal_rows) == 1 and seal_rows[0]["id"] in parts["purchase_requisitions"]
    assert not any(r["part_no"] == "SEAL-SP-0031" for r in state["reservations"])
    seal = next(p for p in m._sap.get_state()["spare_parts"] if p["part_no"] == "SEAL-SP-0031")
    assert seal["stock_qty"] == 0


def test_refused_approval_is_recorded_on_the_run(runner, monkeypatch):
    run = _pending_run()

    async def refuse(*_a, **_k):
        raise ar.ApprovalRefused("Mesin CNC-02 berstatus FAULT")

    monkeypatch.setattr(m._agent_runner.deps, "approve_scenario", refuse)
    r = client.post(f"/api/agent/runs/{run['id']}/approve", json={"approved_by": "Supervisor Budi"})
    assert r.status_code == 409
    after = client.get(f"/api/agent/runs/{run['id']}").json()
    assert after["status"] == "rejected" and "FAULT" in after["error"]


def test_retry_after_failed_parts_step_does_not_reroute_twice(runner, monkeypatch):
    run = _pending_run()
    real = m._agent_runner._apply_parts
    calls = {"n": 0}

    def flaky(*a):
        calls["n"] += 1
        if calls["n"] == 1:
            raise RuntimeError("SAP busy")
        return real(*a)

    monkeypatch.setattr(m._agent_runner, "_apply_parts", flaky)
    first = client.post(f"/api/agent/runs/{run['id']}/approve", json={"approved_by": "Supervisor Budi"})
    assert first.status_code == 503
    mid = client.get(f"/api/agent/runs/{run['id']}").json()
    assert mid["status"] == "awaiting_approval" and "SAP busy" in mid["error"]
    downtime = len(m._sap.get_state()["_meta"].get("downtime_events") or [])
    ok = client.post(f"/api/agent/runs/{run['id']}/approve", json={"approved_by": "Supervisor Budi"})
    assert ok.status_code == 200, ok.text
    assert len(m._sap.get_state()["_meta"].get("downtime_events") or []) == downtime


def test_pending_runs_are_never_evicted_and_stay_latest():
    r = ar.AgentRunner(m._agent_runner.deps)
    pending = r.create({"machine_id": "CNC-01"})
    pending["status"] = "awaiting_approval"
    for _ in range(ar.MAX_RUNS + 5):
        r.create({"machine_id": "CNC-03"})["status"] = "done"
    assert pending["id"] in r.runs and len(r.runs) <= ar.MAX_RUNS + 1
    assert r.latest()["id"] == pending["id"]


def test_reset_clears_issued_scenarios_and_expires_pending_runs(runner):
    run = _pending_run()
    assert m._issued_scenarios
    assert client.post("/api/state/reset").status_code == 200
    assert m._issued_scenarios == {}
    after = client.get(f"/api/agent/runs/{run['id']}").json()
    assert after["status"] == "rejected" and "direset" in after["error"]
    r = client.post(f"/api/agent/runs/{run['id']}/approve", json={"approved_by": "Supervisor Budi"})
    assert r.status_code == 409


def test_issued_scenarios_are_capped_but_keep_pending_ones(runner, monkeypatch):
    run = _pending_run()
    sid = run["pending_approval"]["scenario"]["id"]
    monkeypatch.setattr(m, "_ISSUED_MAX", 5)
    for i in range(20):
        m._remember_scenario({"id": f"x{i}"})
    assert len(m._issued_scenarios) <= 6 and sid in m._issued_scenarios and "x19" in m._issued_scenarios


def test_shutdown_cancels_runs_still_working():
    r = ar.AgentRunner(m._agent_runner.deps)

    async def go():
        started = asyncio.Event()

        async def slow():
            started.set()
            await asyncio.sleep(60)
        t = asyncio.create_task(slow())
        r._tasks.add(t)
        await started.wait()
        await r.shutdown()
        return t
    assert asyncio.run(go()).cancelled()


def test_agent_reroute_is_tagged_and_the_chat_can_say_so(runner):
    run = _pending_run()
    ok = client.post(f"/api/agent/runs/{run['id']}/approve", json={"approved_by": "Supervisor Budi"})
    assert ok.status_code == 200, ok.text
    entry = next(e for e in reversed(m._ledger.get_all()) if e["action_type"] == "SCENARIO_APPROVED")
    assert entry["source"] == {"kind": "agent_run", "run_id": run["id"], "machine_id": "CNC-01",
                               "alarm_code": "ALM-162", "alarm_text": "SPINDLE OVERLOAD"}
    moved = [u["id"] for u in entry["delta_applied"]["order_updates"]]
    block = m.chat_context.order_history_block(m._ledger.get_all(), moved)
    assert f"agen SkillForge {run['id']} setelah ALM-162 (SPINDLE OVERLOAD) di CNC-01" in block


@pytest.mark.parametrize("delivery, expect", [
    ({"channel": "telegram", "status": "sent", "to": "••••321"}, "dikirim ke Hendra Wijaya via Telegram (••••321)"),
    ({"channel": "telegram", "status": "failed", "error": "token bot Telegram tidak valid"}, "gagal: token bot Telegram tidak valid"),
    ({"channel": "telegram", "status": "not_configured"}, "belum dikonfigurasi"),
])
def test_notify_team_summary_follows_delivery(runner, monkeypatch, delivery, expect):
    seen = []

    async def notify(mech, message, context):
        seen.append(context)
        return delivery

    monkeypatch.setattr(m._agent_runner.deps, "notify", notify)
    run = _wait(client.post("/api/agent/run", json={"machine_id": "CNC-01", "alarm_code": "ALM-162",
                                                    "alarm_text": "SPINDLE OVERLOAD"}).json()["id"])
    step = next(s for s in run["steps"] if s["tool"] == "notify_team")
    assert expect in step["summary"] and step["output"]["delivery"] == delivery
    case_id = m._agent.ledger.playbooks()[-1]["case_id"]           # the Telegram button opens this case
    assert seen == [{"agent_run_id": run["id"], "machine_id": "CNC-01", "maintenance_case_id": case_id}]


def test_agent_run_approval_tells_the_routed_mechanic(runner, monkeypatch):
    sent = []

    async def notify(mech, messages, context):
        sent.append((mech, messages, context))
        return {"channel": "telegram", "status": "sent"}

    run = _wait(client.post("/api/agent/run", json={"machine_id": "CNC-01", "alarm_code": "ALM-162",
                                                    "alarm_text": "SPINDLE OVERLOAD"}).json()["id"])
    monkeypatch.setattr(m, "_notify_mechanic", notify)
    assert client.post(f"/api/agent/runs/{run['id']}/approve", json={"approved_by": "Supervisor Budi"}).status_code == 200
    mech, (text,), context = sent[-1]
    assert mech["name"] == "Hendra Wijaya" and context["update"] == "approved" and context["agent_run_id"] == run["id"]
    assert text.startswith("✅ Rencana CNC-01 disetujui oleh Supervisor Budi")
    assert "NSK-7014CTYNSULP4" in text and "🔀 Jadwal produksi diperbarui:" in text
