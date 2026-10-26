"""tests/test_chat_context.py — the extra data blocks in the AI Supervisor chat prompt."""

from backend import chat_context as cc


def _approved(order_id, frm, to, source, scenario="RUN-AB12:scenario_c", name=None, who="Supervisor Budi"):
    return {
        "id": "abcdef1234", "action_type": "SCENARIO_APPROVED", "timestamp": "2026-10-26T10:12:00+00:00",
        "scenario_chosen": scenario, "scenario_name": name, "approved_by": who, "source": source,
        "delta_applied": {
            "order_updates": [{"id": order_id, "work_center_id": to,
                               "planned_start": "2025-01-15T09:00:00", "planned_end": "2025-01-15T12:00:00"}],
            "order_snapshots": {order_id: {"work_center_id": frm, "planned_start": "2025-01-15T07:00:00",
                                           "planned_end": "2025-01-15T10:00:00"}},
        },
    }


AGENT = {"kind": "agent_run", "run_id": "RUN-AB12", "machine_id": "CNC-04",
         "alarm_code": "ALM-410", "alarm_text": "SPINDLE OVERHEAT"}


def test_agent_reroute_says_which_alarm_moved_the_order():
    block = cc.order_history_block([_approved("WO-2025-006", "CNC-04", "CNC-02", AGENT)],
                                   ["WO-2025-002", "WO-2025-006"])
    line = next(l for l in block.splitlines() if l.startswith("- WO-2025-006"))
    assert "dari CNC-04 07:00–10:00 ke CNC-02 09:00–12:00" in line
    assert "agen SkillForge RUN-AB12 setelah ALM-410 (SPINDLE OVERHEAT) di CNC-04" in line
    assert "C · Rerute optimal" in line and "Supervisor Budi" in line
    assert "WO-2025-002" not in block and "rencana awal dari SAP" in block


def test_dashboard_and_unknown_sources():
    assert cc.describe_source({"kind": "dashboard"}) == "analisis gangguan di dasbor"
    assert "tidak tercatat" in cc.describe_source(None)   # entries written before sources were recorded


def test_history_starts_after_the_last_reset_and_includes_reverts():
    old = _approved("WO-2025-001", "CNC-01", "CNC-03", {"kind": "dashboard"})
    new = _approved("WO-2025-006", "CNC-04", "CNC-02", AGENT)
    revert = {"action_type": "SCENARIO_REVERTED", "original_entry_id": "abcdef1234", "approved_by": "Budi",
              "timestamp": "2026-10-26T10:30:00+00:00",
              "delta_applied": {"order_updates": [{"id": "WO-2025-006", "work_center_id": "CNC-04",
                                                   "planned_start": "2025-01-15T07:00:00",
                                                   "planned_end": "2025-01-15T10:00:00"}]}}
    hist = cc.order_history([old, {"action_type": "STATE_RESET"}, new, revert])
    assert "WO-2025-001" not in hist
    assert len(hist["WO-2025-006"]) == 2 and hist["WO-2025-006"][1].startswith("dikembalikan ke CNC-04")
    assert cc.order_history_block([old, {"action_type": "STATE_RESET"}], ["WO-2025-001"]).startswith(
        "- Belum ada order yang dipindah")


def test_plc_block_lists_readings_alarms_and_predictions():
    scan = {"scenario": "cnc04_overheat", "timestamp": "t", "machines": [
        {"machine_id": "CNC-04", "status": "RUNNING", "tags": {"motor_temp_celsius": 91.2},
         "estop": False, "lockout": True, "alarm": {"code": "ALM-410", "text": "SPINDLE OVERHEAT"}}]}
    alarms = [{"machine_id": "CNC-04", "code": "ALM-410", "text": "SPINDLE OVERHEAT", "past_fixes": [1, 2]}]
    preds = [{"machine_id": "CNC-02", "metric": "spindle_vibration_mm_per_s", "label": "Vibrasi spindle",
              "current": 3.1, "unit": "mm/s", "slope_per_min": 0.4, "eta_warning_s": 170, "eta_critical_s": 400}]
    block = cc.plc_block(scan, None, alarms, preds)
    assert "CNC-04: RUNNING LOCKOUT, alarm ALM-410 SPINDLE OVERHEAT; motor_temp_celsius=91.2" in block
    assert "2 perbaikan serupa" in block
    assert "Vibrasi spindle: 3.1 mm/s, naik 0.4/menit, mencapai batas warning dalam ~3 menit" in block
    assert "PLC OFFLINE" in cc.plc_block(scan, {"timestamp": "t", "error": "timeout"}, [], [])
    assert "Tidak ada bacaan PLC" in cc.plc_block(None, None, [], [])


def test_agent_runs_block_shows_steps_and_pending_approval():
    run = {"id": "RUN-AB12", "status": "awaiting_approval", "started_at": "2026-10-26T10:00:00+00:00",
           "trigger": {"machine_id": "CNC-04", "alarm_code": "ALM-410", "alarm_text": "SPINDLE OVERHEAT"},
           "steps": [{"tool": "route_to_expert", "label": "Rutekan ke pakar", "status": "done",
                      "summary": "Tiket dirutekan ke Hendra."}],
           "pending_approval": {"scenario": {"id": "RUN-AB12:scenario_c", "total_cost_idr": 1500000,
                                             "gantt_changes": [{"order_id": "WO-2025-006", "from_machine": "CNC-04",
                                                                "to_machine": "CNC-02"}]},
                                "parts": {"reservations": [{}], "purchase_requisitions": []}}}
    block = cc.agent_runs_block([run])
    assert "RUN-AB12 status awaiting_approval: ALM-410 SPINDLE OVERHEAT di CNC-04" in block
    assert "Rutekan ke pakar (done): Tiket dirutekan ke Hendra." in block
    assert "reroute WO-2025-006 CNC-04→CNC-02 (C · Rerute optimal, biaya Rp 1.500.000)" in block
    assert "reservasi 1 item" in block
    assert cc.agent_runs_block([]) == "- Belum ada agent run."


def test_scenarios_block_skips_agent_plans_and_marks_the_recommended_one():
    issued = {
        "scenario_b": {"id": "scenario_b", "name_id": "Lembur — Shift Malam", "total_cost_idr": 4_000_000,
                       "net_savings_idr": 1_000_000, "gantt_changes": [],
                       "disruption": {"machine_id": "CNC-02", "disruption_type": "BREAKDOWN",
                                      "start_hour": 2, "end_hour": 5}},
        "scenario_c": {"id": "scenario_c", "name_id": "Rerute Optimal", "recommended": True,
                       "total_cost_idr": 600_000, "net_savings_idr": 3_000_000,
                       "gantt_changes": [{"from_machine": "CNC-02", "to_machine": "CNC-03"}]},
        "RUN-X:scenario_a": {"id": "RUN-X:scenario_a", "name_id": "agent plan"},
    }
    block = cc.scenarios_block(issued)
    assert "Gangguan CNC-02 BREAKDOWN jam ke-2–5" in block
    assert "Rerute Optimal [DIREKOMENDASIKAN]: biaya Rp 600.000, hemat Rp 3.000.000, 1 order dipindah" in block
    assert "agent plan" not in block
    assert "Belum ada analisis" in cc.scenarios_block({})


def test_metrics_block_and_history_block():
    metrics = {"shift": {"date": "2025-01-15", "start": "07:00", "end": "15:00", "machines": 5,
                         "hours_per_machine": 8.0},
               "downtime": {"total_hours": 10.0, "percent_of_shift": 25.0,
                            "by_category": [{"label": "Tak terencana", "hours": 10.0}],
                            "by_machine": [{"machine_id": "CNC-05", "total_hours": 8.0, "reasons": ["PM-003 corrective"]},
                                           {"machine_id": "CNC-01", "total_hours": 0.0, "reasons": []}]},
               "oee": {"oee": 66.2, "target": 85.0, "availability": 75.0, "performance": 90.0, "quality": 98.0}}
    block = cc.metrics_block(metrics)
    assert "OEE 66.2% (target 85.0%)" in block and "CNC-05: 8.0 jam (PM-003 corrective)" in block
    assert "CNC-01" not in block
    turns = [{"role": "user", "text": f"q{i}"} for i in range(8)] + [{"role": "assistant", "text": "a  \n b"}]
    hist = cc.history_block(turns)
    assert hist.splitlines()[-1] == "AI Supervisor: a b" and "q2" not in hist and len(hist.splitlines()) == 6


def test_late_note_flags_orders_planned_past_their_deadline():
    late = {"planned_end": "2025-01-15T15:00:00", "deadline": "2025-01-15T12:00:00"}
    assert cc.late_note(late) == ", TERLAMBAT: rencana selesai 15:00 melewati deadline 12:00"
    assert cc.late_note({"planned_end": "2025-01-15T11:00:00", "deadline": "2025-01-15T12:00:00"}) == ""
    assert cc.late_note({"planned_end": "13:00 WIB", "deadline": "2025-01-15T12:00:00"}) == ""
