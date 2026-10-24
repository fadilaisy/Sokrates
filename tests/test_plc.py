"""tests/test_plc.py — simulated PLC: scenarios, alarm codes, and the /ws/plc feed."""

import pytest
from fastapi.testclient import TestClient

import backend.main as backend_main
from backend.main import app
from knowledge.maintenance_ledger import MaintenanceLedger
from knowledge.proactive_agent import ProactiveAgent
from plc_adapter.simulator import SCENARIOS, SimulatedPLC
from rag.store import Store

client = TestClient(app)

LINE = [
    {"id": "CNC-01", "status": "RUNNING", "telemetry": {"motor_temp_celsius": 72.4, "spindle_vibration_mm_per_s": 2.1, "coolant_pressure_bar": 3.8}},
    {"id": "CNC-02", "status": "RUNNING", "telemetry": {"motor_temp_celsius": 68.1, "spindle_vibration_mm_per_s": 1.8, "coolant_pressure_bar": 4.1}},
    {"id": "CNC-04", "status": "RUNNING", "telemetry": {"motor_temp_celsius": 81.2, "spindle_vibration_mm_per_s": 3.5, "coolant_pressure_bar": 3.6}},
    {"id": "CNC-05", "status": "MAINTENANCE", "telemetry": {"motor_temp_celsius": 25.0, "spindle_vibration_mm_per_s": 0.0, "coolant_pressure_bar": 0.0}},
]


class FakeClock:
    def __init__(self) -> None:
        self.t = 1000.0

    def __call__(self) -> float:
        return self.t


def _by_id(scan):
    return {m["machine_id"]: m for m in scan}


def test_normal_scan_has_no_alarms_and_stopped_machines_read_flat():
    plc = SimulatedPLC(seed=1)
    scan = _by_id(plc.scan(LINE))
    assert all(m["alarm"] is None and not m["fault"] for m in scan.values())
    assert scan["CNC-05"]["run"] is False
    assert scan["CNC-05"]["tags"]["motor_temp_celsius"] == 25.0
    assert abs(scan["CNC-04"]["tags"]["motor_temp_celsius"] - 81.2) <= 0.3


@pytest.mark.parametrize("scenario_id", [s for s in SCENARIOS if s != "normal"])
def test_scenario_drifts_to_target_and_raises_its_alarm(scenario_id):
    sc = SCENARIOS[scenario_id]
    clock = FakeClock()
    plc = SimulatedPLC(ramp_s=40, clock=clock, noise=False)
    plc.start(scenario_id)
    assert _by_id(plc.scan(LINE))[sc.machine_id]["alarm"] is None  # t=0: still at baseline
    clock.t += 40
    m = _by_id(plc.scan(LINE))[sc.machine_id]
    assert m["tags"][sc.metric] == pytest.approx(sc.target)
    assert m["alarm"] == {"code": sc.alarm_code, "text": sc.alarm_text} and m["fault"]


def test_unknown_scenario_is_rejected():
    assert client.post("/api/plc/scenario", json={"scenario": "nope"}).status_code == 404
    info = client.get("/api/plc").json()
    assert {s["id"] for s in info["scenarios"]} == set(SCENARIOS)


def test_ws_plc_alarm_finds_past_fixes_and_runs_playbook_with_code(monkeypatch, tmp_path):
    agent = ProactiveAgent(MaintenanceLedger(tmp_path / "m.jsonl"), store=Store(tmp_path / "rag.sqlite3"))
    agent.sync()  # seed cases indexed, so ALM-410 has past fixes to find
    monkeypatch.setattr(backend_main, "_agent", agent)
    clock = FakeClock()
    monkeypatch.setattr(backend_main, "_plc", SimulatedPLC(ramp_s=10, clock=clock, noise=False))
    monkeypatch.setattr(backend_main, "_PLC_SCAN_S", 0.01)
    monkeypatch.setattr(backend_main, "_telemetry_last", {})

    async def fake_llm(prompt, max_tokens=150, interactive=True):
        return "analisis uji"
    monkeypatch.setattr(backend_main, "_call_gemini", fake_llm)

    assert client.post("/api/plc/scenario", json={"scenario": "cnc04_overheat"}).json()["scenario"] == "cnc04_overheat"
    clock.t += 10  # fully drifted: 98 °C, ALM-410 latched
    events: dict[str, list] = {}
    with client.websocket_connect("/ws/plc") as ws:
        for _ in range(40):
            f = ws.receive_json()
            events.setdefault(f["event"], []).append(f)
            if "safety_alert" in events and "maintenance_playbook" in events:
                break
    client.post("/api/plc/scenario", json={"scenario": "normal"})

    scan = _by_id(events["plc_scan"][0]["machines"])
    assert scan["CNC-04"]["alarm"]["code"] == "ALM-410"
    alarm = events["plc_alarm"][0]
    assert alarm["machine_id"] == "CNC-04" and alarm["code"] == "ALM-410"
    assert alarm["past_fixes"] and alarm["past_fixes"][0]["exact_match"]
    assert any(f["rule_triggered"] == "SAFE-001" for f in events["telemetry_analysis"])
    entry = agent.ledger.get_case(events["maintenance_playbook"][0]["entry_id"])
    assert "ALM-410" in entry["error_codes"]


# ── step 4: limits file and pre-action checks ───────────────────────────────

from plc_adapter.limits import MachineLimits, pre_action_checks
from plc_adapter.predictor import TrendPredictor


def _failed(checks):
    return {c["id"] for c in checks if not c["ok"]}


def test_limits_propose_action_by_alarm_code_and_metric():
    lim = MachineLimits()
    assert lim.action_for("CNC-04", alarm_code="ALM-410")["action_id"] == "reduce_feed_50"
    assert lim.action_for("CNC-01", metric="spindle_vibration_mm_per_s")["tag"] == "spindle_speed_override_percent"
    assert lim.action_for("CNC-99", alarm_code="ALM-410") is None
    assert lim.machine("CNC-03")["write_enabled"] is False


def test_pre_action_checks_pass_and_block():
    lim = MachineLimits()
    row = {"machine_id": "CNC-04", "setpoints": {"feed_override_percent": 100.0}, "estop": False, "lockout": False}
    kw = dict(machine_id="CNC-04", tag="feed_override_percent", row=row, sap_status="RUNNING")
    assert _failed(pre_action_checks(lim, value=50, approved_by="Budi", **kw)) == set()
    assert _failed(pre_action_checks(lim, value=30, approved_by="Budi", **kw)) == {"range", "step"}
    assert "approval" in _failed(pre_action_checks(lim, value=50, approved_by="", **kw))
    locked = {**row, "lockout": True, "estop": True}
    assert {"lockout", "estop"} <= _failed(pre_action_checks(lim, value=50, approved_by="Budi", **{**kw, "row": locked}))
    assert "sap_status" in _failed(pre_action_checks(lim, value=50, approved_by="Budi", **{**kw, "sap_status": "MAINTENANCE"}))
    # Autonomy may only run the listed action, and only as a reduction.
    assert _failed(pre_action_checks(lim, value=50, approved_by="x", autonomous=True, action_id="reduce_feed_50", **kw)) == set()
    raised = {**row, "setpoints": {"feed_override_percent": 40.0}}
    assert "direction" in _failed(pre_action_checks(lim, value=50, approved_by="x", autonomous=True,
                                                    action_id="reduce_feed_50", **{**kw, "row": raised}))
    assert "policy" in _failed(pre_action_checks(lim, value=50, approved_by="x", autonomous=True,
                                                 action_id="reduce_spindle_60", **kw))
    cnc03 = pre_action_checks(lim, machine_id="CNC-03", tag="feed_override_percent", value=50, row=row,
                              sap_status="IDLE", approved_by="Budi")
    assert "machine" in _failed(cnc03)


# ── simulator: setpoints, mitigation, interlocks ────────────────────────────

def test_mitigating_write_cools_the_spindle_and_clears_the_alarm():
    clock = FakeClock()
    plc = SimulatedPLC(ramp_s=40, clock=clock, noise=False)
    plc.start("cnc04_overheat")
    clock.t += 35
    hot = _by_id(plc.scan(LINE))["CNC-04"]
    assert hot["alarm"]["code"] == "ALM-410"
    assert plc.write("CNC-04", "feed_override_percent", 50) == 50
    clock.t += 60
    cool = _by_id(plc.scan(LINE))["CNC-04"]
    assert cool["alarm"] is None and cool["tags"]["motor_temp_celsius"] < 82
    assert cool["setpoints"]["feed_override_percent"] == 50
    assert plc.info()["mitigated"] is True


def test_feed_hold_stops_the_machine_and_maintenance_is_locked_out():
    clock = FakeClock()
    plc = SimulatedPLC(ramp_s=40, clock=clock, noise=False)
    plc.start("cnc02_coolant")
    clock.t += 40
    plc.write("CNC-02", "feed_hold", 1)
    row = _by_id(plc.scan(LINE))["CNC-02"]
    assert row["run"] is False and row["status"] == "HOLD"
    assert _by_id(plc.scan(LINE))["CNC-05"]["lockout"] is True
    plc.set_interlock("CNC-04", estop=True)
    assert _by_id(plc.scan(LINE))["CNC-04"]["estop"] is True


# ── step 3: trend prediction ────────────────────────────────────────────────

def test_predictor_gives_eta_for_a_rising_tag_and_ignores_a_flat_one():
    p = TrendPredictor()
    limits = {"warning": 85.0, "critical": 95.0, "direction": "rising"}
    for i in range(10):
        p.observe("CNC-04", "motor_temp_celsius", float(i), 80.0 + 0.5 * i)
        p.observe("CNC-01", "motor_temp_celsius", float(i), 72.0 + (0.1 if i % 2 else -0.1))
    pred = p.predict("CNC-04", "motor_temp_celsius", limits)
    assert pred and pred["eta_warning_s"] == pytest.approx(1.0) and pred["eta_critical_s"] == pytest.approx(21.0)
    assert p.predict("CNC-01", "motor_temp_celsius", limits) is None
    falling = {"warning": 2.5, "critical": 2.0, "direction": "falling"}
    for i in range(10):
        p.observe("CNC-02", "coolant_pressure_bar", float(i), 4.0 - 0.1 * i)
    assert p.predict("CNC-02", "coolant_pressure_bar", falling)["eta_warning_s"] == pytest.approx(6.0)


# ── step 4 + 5 through the API ──────────────────────────────────────────────

@pytest.fixture
def fresh_plc(monkeypatch):
    clock = FakeClock()
    plc = SimulatedPLC(ramp_s=10, clock=clock, noise=False)
    monkeypatch.setattr(backend_main, "_plc", plc)
    monkeypatch.setattr(backend_main, "_PLC_SCAN_S", 0.01)
    monkeypatch.setattr(backend_main, "_telemetry_last", {})
    monkeypatch.setattr(backend_main, "_plc_autonomy", {"enabled": False, "by": None, "since": None})

    async def fake_llm(prompt, max_tokens=150, interactive=True):
        return "analisis uji"
    monkeypatch.setattr(backend_main, "_call_gemini", fake_llm)
    client.post("/api/state/reset")
    return plc, clock


def _ledger_types():
    return [e.get("action_type") for e in client.get("/api/ledger").json()]


def test_approved_write_is_checked_applied_and_ledgered(fresh_plc):
    plc, _ = fresh_plc
    body = {"machine_id": "CNC-04", "tag": "feed_override_percent", "value": 50,
            "approved_by": "Budi Santoso", "action_id": "reduce_feed_50"}
    res = client.post("/api/plc/actions", json=body)
    assert res.status_code == 200, res.text
    out = res.json()
    assert out["ok"] and out["before"] == 100 and out["after"] == 50
    assert all(c["ok"] for c in out["checks"]) and out["checks"][-1]["id"] == "readback"
    assert _ledger_types()[-1] == "PLC_WRITE_APPLIED"
    assert client.get("/api/ledger/verify").json()["valid"]
    assert client.post("/api/plc/actions", json={**body, "approved_by": ""}).status_code == 422


def test_write_is_blocked_by_lockout_and_by_read_only_machine(fresh_plc):
    plc, _ = fresh_plc
    assert client.post("/api/plc/interlock", json={"machine_id": "CNC-04", "lockout": True}).json()["lockout"] is True
    res = client.post("/api/plc/actions", json={"machine_id": "CNC-04", "tag": "feed_override_percent",
                                                "value": 50, "approved_by": "Budi Santoso"})
    assert res.status_code == 409
    assert {c["id"] for c in res.json()["detail"]["checks"] if not c["ok"]} == {"lockout"}
    assert _ledger_types()[-1] == "PLC_WRITE_BLOCKED"
    assert _by_id(plc.scan(LINE))["CNC-04"]["setpoints"]["feed_override_percent"] == 100
    res = client.post("/api/plc/actions", json={"machine_id": "CNC-03", "tag": "feed_override_percent",
                                                "value": 50, "approved_by": "Budi Santoso"})
    assert res.status_code == 409


def test_ws_alarm_proposes_action_and_autonomy_runs_it(fresh_plc):
    plc, clock = fresh_plc
    on = client.post("/api/plc/autonomy", json={"enabled": True, "by": "Budi Santoso"}).json()
    assert on["autonomy"]["enabled"] and _ledger_types()[-1] == "PLC_AUTONOMY_ENABLED"
    client.post("/api/plc/scenario", json={"scenario": "cnc04_overheat"})
    clock.t += 10
    events: dict[str, list] = {}
    with client.websocket_connect("/ws/plc") as ws:
        for _ in range(60):
            f = ws.receive_json()
            events.setdefault(f["event"], []).append(f)
            if "plc_action" in events:
                break
    alarm = events["plc_alarm"][0]
    assert alarm["proposed_action"]["action_id"] == "reduce_feed_50" and alarm["proposed_action"]["autonomous"]
    act = events["plc_action"][0]
    assert act["ok"] and act["autonomous"] and act["after"] == 50
    assert act["approved_by"] == backend_main._PLC_AUTONOMY_ACTOR
    assert "PLC_WRITE_APPLIED" in _ledger_types()
    assert plc.info()["mitigated"] is True
    client.post("/api/plc/scenario", json={"scenario": "normal"})


def test_ws_predicts_the_trend_before_the_alarm(monkeypatch):
    plc = SimulatedPLC(ramp_s=6, noise=False)
    monkeypatch.setattr(backend_main, "_plc", plc)
    monkeypatch.setattr(backend_main, "_PLC_SCAN_S", 0.1)
    monkeypatch.setattr(backend_main, "_plc_autonomy", {"enabled": False, "by": None, "since": None})
    client.post("/api/state/reset")
    plc.start("cnc04_overheat")
    seen = []
    with client.websocket_connect("/ws/plc") as ws:
        for _ in range(80):
            f = ws.receive_json()
            if f["event"] in ("plc_prediction", "plc_alarm"):
                seen.append(f)
                break
    assert seen and seen[0]["event"] == "plc_prediction"
    pred = seen[0]
    assert pred["machine_id"] == "CNC-04" and pred["metric"] == "motor_temp_celsius"
    assert pred["eta_warning_s"] > 0 and pred["proposed_action"]["action_id"] == "reduce_feed_50"
    plc.start("normal")


def test_simulator_only_controls_refuse_other_drivers(monkeypatch):
    class Real:
        name = "opcua"

        def info(self):
            return {"driver": "opcua"}
    monkeypatch.setattr(backend_main, "_plc", Real())
    assert client.post("/api/plc/scenario", json={"scenario": "normal"}).status_code == 409
    assert client.post("/api/plc/interlock", json={"machine_id": "CNC-04", "lockout": True}).status_code == 409


# ── step 1: OPC UA driver against a local OPC UA server ─────────────────────

def test_opcua_driver_reads_tags_alarm_and_writes_a_setpoint():
    import socket

    from asyncua.sync import Server

    from plc_adapter.opcua_driver import OpcUaDriver

    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        port = s.getsockname()[1]
    url = f"opc.tcp://127.0.0.1:{port}/skillforge/"
    srv = Server()
    srv.set_endpoint(url)
    idx = srv.register_namespace("urn:skillforge:test")
    obj = srv.nodes.objects.add_object(idx, "CNC04")
    temp = obj.add_variable(idx, "MotorTemp", 81.5)
    feed = obj.add_variable(idx, "FeedOverride", 100.0)
    feed.set_writable()
    lockout = obj.add_variable(idx, "Lockout", False)
    code = obj.add_variable(idx, "AlarmCode", "")
    srv.start()
    try:
        node_map = {"CNC-04": {
            "tags": {"motor_temp_celsius": temp.nodeid.to_string()},
            "setpoints": {"feed_override_percent": feed.nodeid.to_string()},
            "lockout": lockout.nodeid.to_string(),
            "alarm_code": code.nodeid.to_string(),
        }}
        drv = OpcUaDriver(url, node_map)
        rows = _by_id(drv.scan([{"id": "CNC-04", "status": "RUNNING"}, {"id": "CNC-01", "status": "RUNNING"}]))
        assert rows["CNC-04"]["tags"]["motor_temp_celsius"] == 81.5 and rows["CNC-04"]["alarm"] is None
        assert rows["CNC-01"]["tags"] == {}
        assert drv.write("CNC-04", "feed_override_percent", 50) == 50
        assert feed.read_value() == 50
        code.write_value("ALM-410")
        assert drv.scan([{"id": "CNC-04", "status": "RUNNING"}])[0]["alarm"]["code"] == "ALM-410"
        with pytest.raises(Exception):
            drv.write("CNC-04", "spindle_speed_override_percent", 60)
        drv.close()
    finally:
        srv.stop()


def test_predictor_does_not_fire_on_sensor_noise():
    import random

    rng = random.Random(7)
    p = TrendPredictor(horizon_s=600)
    falling = {"warning": 2.5, "critical": 2.0, "direction": "falling"}
    hits = 0
    for t in range(3000):
        p.observe("CNC-02", "coolant_pressure_bar", float(t), 4.1 + rng.uniform(-0.04, 0.04))
        if t >= 10 and t % 5 == 0:
            hits += p.predict("CNC-02", "coolant_pressure_bar", falling) is not None
    assert hits == 0
