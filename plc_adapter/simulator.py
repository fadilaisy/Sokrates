"""
plc_adapter/simulator.py
────────────────────────
Simulated PLC for the SkillForge demo.

Stands in for the PLC / CNC controller on each machine: every scan it returns
the machine's tags (motor temperature, spindle vibration, coolant pressure,
spindle speed), its writable setpoints, the e-stop and lockout bits, and the
active alarm code, the way an OPC UA or Modbus driver would (see
plc_adapter/driver.py for the shape). Baselines come from the SAP mock's
`telemetry` block, so the simulated line matches the cockpit.

A scenario makes one machine creep steadily towards a fault over `ramp_s`
seconds and raise the alarm
code a real controller would show — the same codes as the maintenance seed
cases, so the alarm finds past fixes. With `alarm_after_s` set the creep is
paced instead so that every scenario reaches its alarm after that many seconds
(the demo uses ~10 s: the trend prediction shows first, then the alarm). Writing the scenario's mitigating
setpoint (e.g. feed override down to 50% for an overheating spindle) makes the
tag settle back towards normal. While the scenario machine is stopped (e-stop
or feed hold) the fault stops progressing and the reading holds where it was.

Readings are a function of time since the scenario started, so any number of
sockets can scan the same PLC.
"""

from __future__ import annotations

import math
import random
import threading
import time
from dataclasses import dataclass
from typing import Any, Callable, Optional

from plc_adapter.driver import PlcWriteError

# Tags each simulated controller exposes, with the noise added per scan.
TAG_NOISE: dict[str, float] = {
    "motor_temp_celsius": 0.3,
    "spindle_vibration_mm_per_s": 0.08,
    "coolant_pressure_bar": 0.04,
    "spindle_speed_rpm": 15.0,
}

# Writable setpoints and their power-on values.
DEFAULT_SETPOINTS: dict[str, float] = {
    "feed_override_percent": 100.0,
    "spindle_speed_override_percent": 100.0,
    "feed_hold": 0.0,
}

# After a mitigating write the tag relaxes towards its baseline with this time constant.
SETTLE_TAU_S = 8.0


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
    # The setpoint write that takes the load off: (tag, "<=" | ">=", value).
    mitigation: Optional[tuple[str, str, float]] = None


SCENARIOS: dict[str, Scenario] = {
    s.id: s for s in (
        Scenario("normal", "Semua mesin normal"),
        Scenario("cnc04_overheat", "CNC-04 motor spindel overheat", "CNC-04",
                 "motor_temp_celsius", 98.0, "ALM-410", "SPINDLE MOTOR OVERHEAT", 85.0,
                 ("feed_override_percent", "<=", 60.0)),
        Scenario("cnc01_bearing", "CNC-01 bearing spindel aus", "CNC-01",
                 "spindle_vibration_mm_per_s", 8.8, "ALM-162", "SPINDLE OVERLOAD", 5.0,
                 ("spindle_speed_override_percent", "<=", 70.0)),
        Scenario("cnc02_coolant", "CNC-02 tekanan coolant turun", "CNC-02",
                 "coolant_pressure_bar", 1.7, "Alarm 970", "COOLANT LOW PRESSURE", 2.5,
                 ("feed_hold", ">=", 1.0)),
    )
}


def _satisfies(rule: tuple[str, str, float], tag: str, value: float) -> bool:
    rtag, op, limit = rule
    return tag == rtag and (value <= limit if op == "<=" else value >= limit)


class SimulatedPLC:
    name = "simulated"

    def __init__(
        self,
        ramp_s: float = 120.0,
        *,
        alarm_after_s: Optional[float] = None,
        clock: Callable[[], float] = time.monotonic,
        noise: bool = True,
        seed: Optional[int] = None,
    ) -> None:
        self.ramp_s = ramp_s
        self.alarm_after_s = alarm_after_s if alarm_after_s and alarm_after_s > 0 else None
        self._clock = clock
        self._noise = noise
        self._rng = random.Random(seed)
        self._lock = threading.Lock()
        self._scenario = SCENARIOS["normal"]
        self._started = clock()
        self._run = 0
        self._reset_machines()

    def _reset_machines(self) -> None:
        self._setpoints: dict[str, dict[str, float]] = {}
        self._estop: dict[str, bool] = {}
        self._lockout: dict[str, bool] = {}
        # When the scenario machine got its mitigating write (clock time), and how far the fault had got (active seconds).
        self._mitigated_at: Optional[float] = None
        self._mitigated_s = 0.0
        # Time the scenario machine spent stopped, during which the fault does not progress.
        self._held_total = 0.0
        self._held_since: Optional[float] = None

    @property
    def scenario(self) -> Scenario:
        return self._scenario

    def info(self) -> dict[str, Any]:
        with self._lock:
            elapsed = self._clock() - self._started
            return {
                "driver": self.name,
                "scenario": self._scenario.id,
                "run": self._run,
                "elapsed_s": round(elapsed, 1),
                "ramp_s": self.ramp_s,
                "alarm_after_s": self.alarm_after_s,
                "mitigated": self._mitigated_at is not None,
                "scenarios": [{"id": s.id, "label": s.label, "machine_id": s.machine_id,
                               "alarm_code": s.alarm_code} for s in SCENARIOS.values()],
            }

    def start(self, scenario_id: str) -> Scenario:
        """Start a scenario from a clean line: setpoints back to 100%, interlocks cleared."""
        if scenario_id not in SCENARIOS:
            raise KeyError(scenario_id)
        with self._lock:
            self._scenario = SCENARIOS[scenario_id]
            self._started = self._clock()
            self._run += 1
            self._reset_machines()
        return self._scenario

    def set_interlock(self, machine_id: str, *, estop: Optional[bool] = None, lockout: Optional[bool] = None) -> None:
        """Demo control: press the e-stop or hang a lockout tag on a machine."""
        with self._lock:
            if estop is not None:
                self._estop[machine_id] = estop
            if lockout is not None:
                self._lockout[machine_id] = lockout
            self._update_hold()

    def write(self, machine_id: str, tag: str, value: float) -> float:
        if tag not in DEFAULT_SETPOINTS:
            raise PlcWriteError(f"{machine_id} has no writable setpoint '{tag}'")
        with self._lock:
            sp = self._setpoints.setdefault(machine_id, dict(DEFAULT_SETPOINTS))
            sp[tag] = float(value)
            sc = self._scenario
            if (sc.machine_id == machine_id and sc.mitigation and self._mitigated_at is None
                    and _satisfies(sc.mitigation, tag, float(value))):
                self._mitigated_at = self._clock()
                self._mitigated_s = self._active_s(self._mitigated_at)
            self._update_hold()
            return sp[tag]

    def _update_hold(self) -> None:
        """Start or stop the pause clock when the scenario machine stops or restarts (call under the lock)."""
        mid = self._scenario.machine_id
        if not mid:
            return
        sp = self._setpoints.get(mid) or DEFAULT_SETPOINTS
        held = sp["feed_hold"] >= 1 or self._estop.get(mid, False)
        now = self._clock()
        if held and self._held_since is None:
            self._held_since = now
        elif not held and self._held_since is not None:
            self._held_total += now - self._held_since
            self._held_since = None

    def _active_s(self, at: float) -> float:
        """Seconds the fault has been progressing at clock time `at`, not counting time the machine was stopped."""
        held = self._held_total + (max(0.0, at - self._held_since) if self._held_since is not None else 0.0)
        return max(0.0, at - self._started - held)

    def _jitter(self, tag: str) -> float:
        amp = TAG_NOISE.get(tag, 0.0)
        return self._rng.uniform(-amp, amp) if self._noise and amp else 0.0

    def _drift(self, start: float, active_s: float) -> float:
        """Scenario tag value after `active_s` seconds: a steady creep from baseline to the fault value."""
        sc = self._scenario
        ramp_s = self.ramp_s
        if self.alarm_after_s and sc.target != start and (sc.alarm_at - start) * (sc.target - start) > 0:
            # Same steady creep, paced so the alarm value is crossed after alarm_after_s seconds.
            ramp_s = self.alarm_after_s * (sc.target - start) / (sc.alarm_at - start)
        return start + (sc.target - start) * min(1.0, active_s / ramp_s)

    def scan(self, work_centers: list[dict[str, Any]]) -> list[dict[str, Any]]:
        """One PLC scan across the line: tags, setpoints, interlocks and the active alarm per machine."""
        with self._lock:
            now, sc = self._clock(), self._scenario
            out = []
            for wc in work_centers:
                mid = wc.get("id")
                status = str(wc.get("status") or "").upper()
                base = {k: float(v) for k, v in (wc.get("telemetry") or {}).items() if isinstance(v, (int, float))}
                sp = dict(self._setpoints.get(mid) or DEFAULT_SETPOINTS)
                estop = self._estop.get(mid, False)
                # A machine SAP has in maintenance is locked out until someone says otherwise.
                lockout = self._lockout.get(mid, status == "MAINTENANCE")
                held = sp["feed_hold"] >= 1 or estop
                running = status == "RUNNING" and not held
                tags: dict[str, float] = {}
                for tag, value in base.items():
                    # A stopped machine reads flat: no noise on a spindle that isn't turning.
                    tags[tag] = value + (self._jitter(tag) if running else 0.0)
                if "feed_rate_percent" in tags:
                    tags["feed_rate_percent"] *= sp["feed_override_percent"] / 100.0
                if "spindle_speed_rpm" in tags:
                    tags["spindle_speed_rpm"] *= sp["spindle_speed_override_percent"] / 100.0
                alarm = None
                if status == "RUNNING" and sc.machine_id == mid and sc.metric in base:
                    start = base[sc.metric]
                    if self._mitigated_at is None or (sc.mitigation and sc.mitigation[0] == "feed_hold"):
                        # Feed hold or e-stop stops the cut; the reading holds while the machine is stopped.
                        value = self._drift(start, self._active_s(now))
                    else:
                        peak = self._drift(start, self._mitigated_s)
                        value = start + (peak - start) * math.exp(-(now - self._mitigated_at) / SETTLE_TAU_S)
                    tags[sc.metric] = value + (self._jitter(sc.metric) * 0.5 if running else 0.0)
                    rising = sc.target >= start
                    if (value >= sc.alarm_at) if rising else (value <= sc.alarm_at):
                        alarm = {"code": sc.alarm_code, "text": sc.alarm_text}
                out.append({
                    "machine_id": mid,
                    "status": "HOLD" if status == "RUNNING" and held else status,
                    "run": running,
                    "fault": alarm is not None,
                    "tags": {k: round(max(0.0, v), 2) for k, v in tags.items()},
                    "setpoints": sp,
                    "estop": estop,
                    "lockout": lockout,
                    "alarm": alarm,
                })
            return out
