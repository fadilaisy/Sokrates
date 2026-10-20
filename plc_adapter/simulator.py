"""
plc_adapter/simulator.py
────────────────────────
Simulated PLC for the SkillForge demo.

Stands in for the PLC / CNC controller on each machine: every scan it returns
the machine's tags (motor temperature, spindle vibration, coolant pressure,
spindle speed) plus the active alarm code, the way an OPC UA or Modbus driver
would. Baselines come from the SAP mock's `telemetry` block, so the simulated
line matches the cockpit.

A scenario makes one machine drift towards a fault over `ramp_s` seconds and
raise the alarm code a real controller would show (the same codes as the
maintenance seed cases, so the alarm finds past fixes). Readings are a pure
function of time since the scenario started, so any number of sockets can
scan the same PLC.

Swapping this for real hardware means replacing `scan()` with reads from an
OPC UA / Modbus / MQTT client that return the same shape.
"""

from __future__ import annotations

import random
import threading
import time
from dataclasses import dataclass
from typing import Any, Callable, Optional

# Tags each simulated controller exposes, with the noise added per scan.
TAG_NOISE: dict[str, float] = {
    "motor_temp_celsius": 0.3,
    "spindle_vibration_mm_per_s": 0.08,
    "coolant_pressure_bar": 0.04,
    "spindle_speed_rpm": 15.0,
}


@dataclass(frozen=True)
class Scenario:
    id: str
    label: str
    machine_id: Optional[str] = None
    metric: Optional[str] = None
    target: float = 0.0
    alarm_code: Optional[str] = None
    alarm_text: Optional[str] = None
    # The alarm latches once the tag crosses this value (in the drift direction).
    alarm_at: float = 0.0


SCENARIOS: dict[str, Scenario] = {
    s.id: s for s in (
        Scenario("normal", "Semua mesin normal"),
        Scenario("cnc04_overheat", "CNC-04 motor spindel overheat", "CNC-04",
                 "motor_temp_celsius", 98.0, "ALM-410", "SPINDLE MOTOR OVERHEAT", 85.0),
        Scenario("cnc01_bearing", "CNC-01 bearing spindel aus", "CNC-01",
                 "spindle_vibration_mm_per_s", 8.8, "ALM-162", "SPINDLE OVERLOAD", 5.0),
        Scenario("cnc02_coolant", "CNC-02 tekanan coolant turun", "CNC-02",
                 "coolant_pressure_bar", 1.7, "Alarm 970", "COOLANT LOW PRESSURE", 2.5),
    )
}


class SimulatedPLC:
    def __init__(
        self,
        ramp_s: float = 30.0,
        *,
        clock: Callable[[], float] = time.monotonic,
        noise: bool = True,
        seed: Optional[int] = None,
    ) -> None:
        self.ramp_s = ramp_s
        self._clock = clock
        self._noise = noise
        self._rng = random.Random(seed)
        self._lock = threading.Lock()
        self._scenario = SCENARIOS["normal"]
        self._started = clock()
        self._run = 0

    @property
    def scenario(self) -> Scenario:
        return self._scenario

    def info(self) -> dict[str, Any]:
        with self._lock:
            elapsed = self._clock() - self._started
            return {
                "scenario": self._scenario.id,
                "run": self._run,
                "elapsed_s": round(elapsed, 1),
                "ramp_s": self.ramp_s,
                "scenarios": [{"id": s.id, "label": s.label, "machine_id": s.machine_id,
                               "alarm_code": s.alarm_code} for s in SCENARIOS.values()],
            }

    def start(self, scenario_id: str) -> Scenario:
        if scenario_id not in SCENARIOS:
            raise KeyError(scenario_id)
        with self._lock:
            self._scenario = SCENARIOS[scenario_id]
            self._started = self._clock()
            self._run += 1
        return self._scenario

    def _jitter(self, tag: str) -> float:
        amp = TAG_NOISE.get(tag, 0.0)
        return self._rng.uniform(-amp, amp) if self._noise and amp else 0.0

    def scan(self, work_centers: list[dict[str, Any]]) -> list[dict[str, Any]]:
        """One PLC scan across the line: tags, run/fault bits and the active alarm per machine."""
        with self._lock:
            sc, progress = self._scenario, min(1.0, max(0.0, (self._clock() - self._started) / self.ramp_s))
            out = []
            for wc in work_centers:
                status = str(wc.get("status") or "").upper()
                base = {k: float(v) for k, v in (wc.get("telemetry") or {}).items() if isinstance(v, (int, float))}
                running = status == "RUNNING"
                tags: dict[str, float] = {}
                for tag, value in base.items():
                    # A stopped machine reads flat: no noise on a spindle that isn't turning.
                    tags[tag] = value + (self._jitter(tag) if running else 0.0)
                alarm = None
                if running and sc.machine_id == wc.get("id") and sc.metric in base:
                    start = base[sc.metric]
                    value = start + (sc.target - start) * progress
                    tags[sc.metric] = value + self._jitter(sc.metric) * 0.5
                    rising = sc.target >= start
                    if (value >= sc.alarm_at) if rising else (value <= sc.alarm_at):
                        alarm = {"code": sc.alarm_code, "text": sc.alarm_text}
                out.append({
                    "machine_id": wc.get("id"),
                    "status": status,
                    "run": running,
                    "fault": alarm is not None,
                    "tags": {k: round(max(0.0, v), 2) for k, v in tags.items()},
                    "alarm": alarm,
                })
            return out
