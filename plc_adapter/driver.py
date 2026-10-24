"""
plc_adapter/driver.py
─────────────────────
The contract every PLC connection implements, simulated or real.

A driver reads the whole line in one `scan()` and writes one setpoint at a
time with `write()`. Everything above it (alarm lookup, trend prediction,
pre-action checks, the audit ledger) only sees these two calls, so swapping
the simulator for a plant's OPC UA server is a configuration change.

Scan row shape (one per machine):
    {
      "machine_id": "CNC-04",
      "status": "RUNNING",          # SAP / controller status
      "run": True,                  # spindle running
      "fault": False,               # an alarm is active
      "tags": {"motor_temp_celsius": 81.2, ...},
      "setpoints": {"feed_override_percent": 100, ...},
      "estop": False,               # emergency stop pressed
      "lockout": False,             # lockout/tagout applied
      "alarm": {"code": "ALM-410", "text": "..."} | None,
    }
"""

from __future__ import annotations

from typing import Any, Protocol


class PlcWriteError(Exception):
    """The controller refused or failed the write; nothing may be assumed about the setpoint."""


class PlcDriver(Protocol):
    name: str

    def scan(self, work_centers: list[dict[str, Any]]) -> list[dict[str, Any]]:
        """Read every machine once. `work_centers` comes from SAP and names the line."""
        ...

    def write(self, machine_id: str, tag: str, value: float) -> float:
        """Write one setpoint and return the value read back from the controller."""
        ...
