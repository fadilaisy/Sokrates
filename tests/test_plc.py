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
    monkeypatch.setattr(backend_main, "_call_llm", fake_llm)

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
    monkeypatch.setattr(backend_main, "_call_llm", fake_llm)
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

    from plc_adapter.simulator import TAG_NOISE

    # Every limited tag at its simulator noise level, five seeds each: about 9,000 prediction checks.
    cases = [("motor_temp_celsius", 81.2), ("spindle_vibration_mm_per_s", 3.5), ("coolant_pressure_bar", 4.1)]
    limits = MachineLimits().tag_limits("CNC-04")
    checks = hits = 0
    for seed in range(5):
        rng = random.Random(seed)
        for metric, base in cases:
            p = TrendPredictor(horizon_s=600)
            amp = TAG_NOISE[metric]
            for t in range(3000):
                p.observe("CNC-04", metric, float(t), base + rng.uniform(-amp, amp))
                if t >= 10 and t % 5 == 0:
                    checks += 1
                    hits += p.predict("CNC-04", metric, limits[metric]) is not None
    assert checks > 8000 and hits == 0


# ── review fixes: server-side loop, degraded PLC, typed writes, fail-closed limits ──

import asyncio
import time

from plc_adapter.limits import LimitsError


def _wait_for(pred, timeout=5.0):
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        if pred():
            return True
        time.sleep(0.05)
    return False


def test_unknown_driver_and_missing_opcua_config_fail_loudly(monkeypatch, tmp_path):
    monkeypatch.setenv("PLC_DRIVER", "opc-ua")
    with pytest.raises(RuntimeError, match="PLC_DRIVER='opc-ua'"):
        backend_main._make_plc_driver()
    monkeypatch.setenv("PLC_DRIVER", "opcua")
    monkeypatch.delenv("PLC_OPCUA_ENDPOINT", raising=False)
    monkeypatch.delenv("PLC_OPCUA_MAP", raising=False)
    with pytest.raises(RuntimeError, match="PLC_OPCUA_ENDPOINT and PLC_OPCUA_MAP"):
        backend_main._make_plc_driver()
    monkeypatch.setenv("PLC_OPCUA_ENDPOINT", "opc.tcp://127.0.0.1:4840")
    monkeypatch.setenv("PLC_OPCUA_MAP", str(tmp_path / "missing.json"))
    with pytest.raises(RuntimeError, match="opcua_map.example.json"):
        backend_main._make_plc_driver()
    monkeypatch.setenv("PLC_OPCUA_MAP", "config/opcua_map.example.json")
    drv = backend_main._make_plc_driver()
    assert drv.name == "opcua" and "CNC-04" in drv.node_map
    monkeypatch.setenv("PLC_DRIVER", "simulated")
    assert backend_main._make_plc_driver().name == "simulated"


def test_limits_file_fails_closed():
    good = MachineLimits().raw
    bad = {**good, "defaults": {**good["defaults"], "setpoints": {"feed_override_percent": {"min": 50}}}}
    with pytest.raises(LimitsError, match="needs numeric min, max and max_step"):
        MachineLimits(raw=bad)
    no_dir = {**good, "defaults": {**good["defaults"], "actions": {
        "x": {"tag": "feed_hold", "value": 1.0, "label": "x"}}}}
    with pytest.raises(LimitsError, match="direction"):
        MachineLimits(raw=no_dir)
    # A machine is read-only unless the file says otherwise.
    unflagged = {**good, "defaults": {k: v for k, v in good["defaults"].items() if k != "write_enabled"}}
    lim = MachineLimits(raw=unflagged)
    row = {"setpoints": {"feed_override_percent": 100.0}, "estop": False, "lockout": False}
    kw = dict(machine_id="CNC-04", tag="feed_override_percent", value=50, approved_by="Budi")
    assert "machine" in _failed(pre_action_checks(lim, row=row, sap_status="RUNNING", **kw))
    lim = MachineLimits()
    for status_ in (None, "", "SETUP?", "BREAKDOWN"):
        assert "sap_status" in _failed(pre_action_checks(lim, row=row, sap_status=status_, **kw))
    # Unreadable setpoint, e-stop or lockout bit blocks; so does a repeat of the same value.
    assert "step" in _failed(pre_action_checks(lim, row={**row, "setpoints": {}}, sap_status="RUNNING", **kw))
    assert {"estop", "lockout"} <= _failed(pre_action_checks(lim, row={**row, "estop": None, "lockout": None},
                                                            sap_status="RUNNING", **kw))
    assert _failed(pre_action_checks(lim, row={**row, "setpoints": {"feed_override_percent": 50.0}},
                                     sap_status="RUNNING", **kw)) == {"change"}
    assert _failed(pre_action_checks(lim, row=row, sap_status="RUNNING", writes_enabled=False, **kw)) == {"server"}
    assert _failed(pre_action_checks(lim, row=row, sap_status="RUNNING", action_id="reduce_spindle_60", **kw)) == {"action"}


def test_estop_stops_the_fault_from_progressing():
    clock = FakeClock()
    plc = SimulatedPLC(ramp_s=40, clock=clock, noise=False)
    plc.start("cnc04_overheat")
    clock.t += 5  # 83.3 °C, below the 85 °C alarm
    plc.set_interlock("CNC-04", estop=True)
    held = _by_id(plc.scan(LINE))["CNC-04"]["tags"]["motor_temp_celsius"]
    clock.t += 60
    row = _by_id(plc.scan(LINE))["CNC-04"]
    assert row["tags"]["motor_temp_celsius"] == pytest.approx(held) and row["alarm"] is None
    plc.set_interlock("CNC-04", estop=False)
    clock.t += 10
    assert _by_id(plc.scan(LINE))["CNC-04"]["tags"]["motor_temp_celsius"] > held


def _opcua_server():
    import socket

    from asyncua import ua
    from asyncua.sync import Server

    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        port = s.getsockname()[1]
    url = f"opc.tcp://127.0.0.1:{port}/skillforge/"
    srv = Server()
    srv.set_endpoint(url)
    idx = srv.register_namespace("urn:skillforge:test")
    obj = srv.nodes.objects.add_object(idx, "CNC04")
    nodes = {
        "temp": obj.add_variable(idx, "MotorTemp", 81.5),
        "feed": obj.add_variable(idx, "FeedOverride", 100, varianttype=ua.VariantType.Int16),
        "spindle": obj.add_variable(idx, "SpindleOverride", 100.0, varianttype=ua.VariantType.Float),
        "hold": obj.add_variable(idx, "FeedHold", False),
        "code": obj.add_variable(idx, "AlarmCode", 0, varianttype=ua.VariantType.Int32),
    }
    for k in ("feed", "spindle", "hold"):
        nodes[k].set_writable()
    srv.start()
    return srv, url, nodes


def test_opcua_bad_node_degrades_and_writes_respect_the_node_type():
    from plc_adapter.driver import PlcWriteError
    from plc_adapter.opcua_driver import OpcUaDriver

    srv, url, n = _opcua_server()
    try:
        node_map = {"CNC-04": {
            "tags": {"motor_temp_celsius": n["temp"].nodeid.to_string(), "spindle_vibration_mm_per_s": "ns=2;s=Typo"},
            "setpoints": {"feed_override_percent": n["feed"].nodeid.to_string(),
                          "spindle_speed_override_percent": n["spindle"].nodeid.to_string(),
                          "feed_hold": n["hold"].nodeid.to_string()},
            "estop": "ns=2;s=NoSuchEStop",
            "alarm_code": n["code"].nodeid.to_string(),
            "alarm_code_map": {"410": "ALM-410"},
        }}
        drv = OpcUaDriver(url, node_map)
        row = drv.scan([{"id": "CNC-04", "status": "RUNNING"}])[0]
        # The typo'd tag is left out, the rest of the machine still reads; an unreadable e-stop is None.
        assert row["tags"] == {"motor_temp_celsius": 81.5}
        assert row["estop"] is None and row["lockout"] is False
        assert row["alarm"] is None  # 0 means no alarm
        assert "ns=2;s=Typo" in drv.info()["unreadable_nodes"] and drv.info()["connected"]
        n["code"].write_value(410, n["code"].read_data_type_as_variant_type())
        assert drv.scan([{"id": "CNC-04", "status": "RUNNING"}])[0]["alarm"]["code"] == "ALM-410"
        # Int16: a fractional value is refused before anything is written.
        with pytest.raises(PlcWriteError, match="Int16"):
            drv.write("CNC-04", "feed_override_percent", 50.7)
        assert n["feed"].read_value() == 100
        assert drv.write("CNC-04", "feed_override_percent", 50) == 50
        # Float32 reads back rounded; the backend's tolerance must still call it applied.
        after = drv.write("CNC-04", "spindle_speed_override_percent", 66.7)
        assert after != 66.7 and __import__("math").isclose(after, 66.7, rel_tol=1e-6)
        assert drv.write("CNC-04", "feed_hold", 1) == 1 and n["hold"].read_value() is True
        with pytest.raises(PlcWriteError, match="boolean"):
            drv.write("CNC-04", "feed_hold", 0.5)
        drv.close()
        assert not drv.info()["connected"]
    finally:
        srv.stop()


def test_opcua_connection_loss_disconnects_and_reconnects():
    from plc_adapter.opcua_driver import OpcUaDriver

    srv, url, n = _opcua_server()
    drv = OpcUaDriver(url, {"CNC-04": {"tags": {"motor_temp_celsius": n["temp"].nodeid.to_string()}}}, timeout_s=1)
    try:
        assert drv.scan([{"id": "CNC-04", "status": "RUNNING"}])[0]["tags"]["motor_temp_celsius"] == 81.5
    finally:
        srv.stop()
    with pytest.raises(Exception):
        drv.scan([{"id": "CNC-04", "status": "RUNNING"}])
    assert not drv.info()["connected"]


def test_write_error_and_readback_mismatch_are_ledgered(fresh_plc, monkeypatch):
    from plc_adapter.driver import PlcWriteError

    plc, _ = fresh_plc
    body = {"machine_id": "CNC-04", "tag": "feed_override_percent", "value": 50, "approved_by": "Budi Santoso"}

    def refuse(*a):
        raise PlcWriteError("controller refused")
    monkeypatch.setattr(plc, "write", refuse)
    res = client.post("/api/plc/actions", json=body)
    assert res.status_code == 409 and res.json()["detail"]["outcome"] == "failed"
    assert _ledger_types()[-1] == "PLC_WRITE_FAILED"

    def boom(*a):
        raise RuntimeError("driver bug")
    monkeypatch.setattr(plc, "write", boom)
    res = client.post("/api/plc/actions", json=body)
    assert res.status_code == 409 and "driver bug" in res.json()["detail"]["error"]
    assert _ledger_types()[-1] == "PLC_WRITE_FAILED"

    monkeypatch.setattr(plc, "write", lambda *a: 70.0)
    res = client.post("/api/plc/actions", json=body)
    detail = res.json()["detail"]
    assert detail["outcome"] == "unconfirmed" and detail["after"] == 70 and not detail["checks"][-1]["ok"]
    assert _ledger_types()[-1] == "PLC_WRITE_UNCONFIRMED"
    assert client.get("/api/ledger/verify").json()["valid"]


def test_plc_outage_blocks_writes_and_shows_offline(fresh_plc, monkeypatch):
    plc, _ = fresh_plc

    def down(*a):
        raise ConnectionError("gateway unreachable")
    monkeypatch.setattr(plc, "scan", down)
    res = client.post("/api/plc/actions", json={"machine_id": "CNC-04", "tag": "feed_override_percent",
                                                "value": 50, "approved_by": "Budi Santoso"})
    assert res.status_code == 409 and "plc" in {c["id"] for c in res.json()["detail"]["checks"] if not c["ok"]}
    with client.websocket_connect("/ws/plc") as ws:
        f = ws.receive_json()
        assert f["event"] == "plc_offline" and "gateway unreachable" in f["error"]
        assert ws.receive_json()["event"] == "plc_offline"  # the socket stays open and keeps retrying


def test_autonomy_blocked_by_lockout_is_ledgered(fresh_plc):
    plc, clock = fresh_plc
    client.post("/api/plc/autonomy", json={"enabled": True, "by": "Budi Santoso"})
    client.post("/api/plc/scenario", json={"scenario": "cnc04_overheat"})
    client.post("/api/plc/interlock", json={"machine_id": "CNC-04", "lockout": True})
    clock.t += 10
    with client.websocket_connect("/ws/plc") as ws:
        act = next(f for f in (ws.receive_json() for _ in range(60)) if f["event"] == "plc_action")
    assert act["outcome"] == "blocked" and {c["id"] for c in act["checks"] if not c["ok"]} == {"lockout"}
    assert "PLC_WRITE_BLOCKED" in _ledger_types() and plc.info()["mitigated"] is False
    client.post("/api/plc/scenario", json={"scenario": "normal"})


def test_concurrent_approvals_write_once(fresh_plc, monkeypatch):
    monkeypatch.setattr(backend_main, "_plc_write_lock", asyncio.Lock())

    async def both():
        return await asyncio.gather(*[backend_main._execute_plc_action(
            "CNC-04", "feed_override_percent", 50, approved_by="Budi Santoso") for _ in range(2)])
    results = asyncio.run(both())
    assert sorted(r["outcome"] for r in results) == ["applied", "blocked"]
    blocked = next(r for r in results if r["outcome"] == "blocked")
    assert {c["id"] for c in blocked["checks"] if not c["ok"]} == {"change"}


def test_autonomy_runs_with_no_cockpit_open_and_once_for_many_viewers(fresh_plc, monkeypatch, tmp_path):
    plc, clock = fresh_plc
    # Startup indexes maintenance cases; keep that off the shared RAG index other tests read.
    agent = ProactiveAgent(MaintenanceLedger(tmp_path / "m.jsonl"), store=Store(tmp_path / "rag.sqlite3"))
    monkeypatch.setattr(backend_main, "_agent", agent)
    seen = len(_ledger_types())

    def writes():
        return [t for t in _ledger_types()[seen:] if str(t).startswith("PLC_WRITE")]
    with TestClient(app) as c:  # startup starts the PLC loop
        c.post("/api/plc/autonomy", json={"enabled": True, "by": "Budi Santoso"})
        c.post("/api/plc/scenario", json={"scenario": "cnc04_overheat"})
        clock.t += 10
        assert _wait_for(lambda: plc.info()["mitigated"]), "autonomy did not act without a socket"
        assert writes() == ["PLC_WRITE_APPLIED"]
        # A new viewer joins after the fact and still sees the alarm the loop already handled.
        with c.websocket_connect("/ws/plc") as a, c.websocket_connect("/ws/plc") as b:
            for ws in (a, b):
                frames = [ws.receive_json() for _ in range(2)]
                assert frames[0]["event"] == "plc_scan"
        assert writes() == ["PLC_WRITE_APPLIED"]
        c.post("/api/plc/scenario", json={"scenario": "normal"})


@pytest.mark.parametrize("scenario_id", ["cnc04_overheat", "cnc01_bearing", "cnc02_coolant"])
def test_alarm_after_s_paces_every_scenario_to_alarm_on_time(scenario_id):
    clock = FakeClock()
    plc = SimulatedPLC(ramp_s=120, alarm_after_s=10, clock=clock, noise=False)
    plc.start(scenario_id)
    mid = SCENARIOS[scenario_id].machine_id
    clock.t += 9.5
    assert _by_id(plc.scan(LINE))[mid]["alarm"] is None
    clock.t += 1.0
    assert _by_id(plc.scan(LINE))[mid]["alarm"]["code"] == SCENARIOS[scenario_id].alarm_code
