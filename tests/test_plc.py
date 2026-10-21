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
