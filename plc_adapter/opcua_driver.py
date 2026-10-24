"""
plc_adapter/opcua_driver.py
───────────────────────────
Real PLC connection over OPC UA, the standard most modern CNC controllers and
PLC gateways speak (Siemens S7-1500, Fanuc, Haas NGC via a gateway, Kepware...).

Select it with PLC_DRIVER=opcua, PLC_OPCUA_ENDPOINT=opc.tcp://host:4840 and
PLC_OPCUA_MAP=<path to a node map JSON>. The node map says which OPC UA node
holds each tag, setpoint, interlock bit and alarm code per machine:

    {
      "CNC-04": {
        "tags":      {"motor_temp_celsius": "ns=2;s=CNC04.Spindle.MotorTemp", ...},
        "setpoints": {"feed_override_percent": "ns=2;s=CNC04.FeedOverride", ...},
        "run":       "ns=2;s=CNC04.Running",
        "estop":     "ns=2;s=CNC04.EStop",
        "lockout":   "ns=2;s=CNC04.Lockout",
        "alarm_code": "ns=2;s=CNC04.AlarmCode",
        "alarm_text": "ns=2;s=CNC04.AlarmText"
      }
    }

Machines missing from the map are reported from SAP with no live tags. Any
node may be left out; only setpoints listed in the map can be written.
"""

from __future__ import annotations

import json
import threading
from pathlib import Path
from typing import Any, Optional

from plc_adapter.driver import PlcWriteError


class OpcUaDriver:
    name = "opcua"

    def __init__(self, endpoint: str, node_map: dict[str, Any], *, timeout_s: float = 4.0) -> None:
        self.endpoint = endpoint
        self.node_map = node_map
        self.timeout_s = timeout_s
        self._client = None
        self._lock = threading.Lock()

    @classmethod
    def from_files(cls, endpoint: str, map_path: str) -> "OpcUaDriver":
        return cls(endpoint, json.loads(Path(map_path).read_text(encoding="utf-8")))

    def info(self) -> dict[str, Any]:
        return {"driver": self.name, "endpoint": self.endpoint, "machines": sorted(self.node_map),
                "connected": self._client is not None}

    def _connect(self):
        if self._client is None:
            # Imported here so the simulator-only deployment does not need the OPC UA stack loaded.
            from asyncua.sync import Client

            client = Client(self.endpoint, timeout=self.timeout_s)
            client.connect()
            self._client = client
        return self._client

    def close(self) -> None:
        with self._lock:
            if self._client is not None:
                try:
                    self._client.disconnect()
                finally:
                    self._client = None

    def _read(self, client, node_id: Optional[str]) -> Any:
        return None if not node_id else client.get_node(node_id).read_value()

    def scan(self, work_centers: list[dict[str, Any]]) -> list[dict[str, Any]]:
        with self._lock:
            try:
                client = self._connect()
                out = []
                for wc in work_centers:
                    mid = wc.get("id")
                    status = str(wc.get("status") or "").upper()
                    m = self.node_map.get(mid)
                    if not m:
                        out.append({"machine_id": mid, "status": status, "run": False, "fault": False,
                                    "tags": {}, "setpoints": {}, "estop": False,
                                    "lockout": status == "MAINTENANCE", "alarm": None})
                        continue
                    tags = {k: float(self._read(client, n)) for k, n in m.get("tags", {}).items()}
                    setpoints = {k: float(self._read(client, n)) for k, n in m.get("setpoints", {}).items()}
                    run = self._read(client, m.get("run"))
                    code = self._read(client, m.get("alarm_code"))
                    alarm = {"code": str(code), "text": str(self._read(client, m.get("alarm_text")) or "")} if code else None
                    out.append({
                        "machine_id": mid,
                        "status": status,
                        "run": bool(run) if run is not None else status == "RUNNING",
                        "fault": alarm is not None,
                        "tags": {k: round(v, 2) for k, v in tags.items()},
                        "setpoints": setpoints,
                        "estop": bool(self._read(client, m.get("estop"))),
                        "lockout": bool(self._read(client, m.get("lockout"))),
                        "alarm": alarm,
                    })
                return out
            except Exception:
                # Drop the session so the next scan reconnects instead of reusing a dead socket.
                self._client = None
                raise

    def write(self, machine_id: str, tag: str, value: float) -> float:
        node_id = self.node_map.get(machine_id, {}).get("setpoints", {}).get(tag)
        if not node_id:
            raise PlcWriteError(f"{machine_id}: setpoint '{tag}' is not in the OPC UA node map")
        with self._lock:
            try:
                from asyncua import ua

                node = self._connect().get_node(node_id)
                vtype = node.read_data_type_as_variant_type()
                node.write_value(ua.DataValue(ua.Variant(float(value) if vtype in (ua.VariantType.Double, ua.VariantType.Float)
                                                         else int(value), vtype)))
                return float(node.read_value())
            except PlcWriteError:
                raise
            except Exception as exc:
                self._client = None
                raise PlcWriteError(f"{machine_id}: write {tag}={value} failed: {exc}") from exc
