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
node may be left out; only setpoints listed in the map can be written. An
optional per-machine "alarm_code_map" turns numeric controller codes into the
codes the limits file and past fixes use, e.g. {"410": "ALM-410"}.

A node that cannot be read (wrong id, bad value) is left out of that scan and
logged once; the rest of the line keeps reading. A mapped e-stop or lockout
bit that cannot be read is reported as None, which blocks every write. Only a
connection-level failure drops the session, and it is disconnected first.
"""

from __future__ import annotations

import json
import math
import sys
import threading
from pathlib import Path
from typing import Any, Optional

from plc_adapter.driver import PlcWriteError

# Values a controller uses for "no alarm".
_NO_ALARM = {"", "0", "NONE", "NULL", "FALSE", "OK"}

# Status codes that mean the session or channel is gone, not that one node is bad.
_CONNECTION_STATUS = {"BadSessionIdInvalid", "BadSessionClosed", "BadSessionNotActivated", "BadConnectionClosed",
                      "BadSecureChannelClosed", "BadSecureChannelIdInvalid", "BadTimeout", "BadServerNotConnected",
                      "BadCommunicationError", "BadServerHalted", "BadShutdown", "BadTooManySessions"}


def _connection_level(exc: BaseException) -> bool:
    code = getattr(exc, "code", None)
    if code is None:
        return False
    from asyncua import ua

    try:
        return ua.StatusCode(code).name in _CONNECTION_STATUS
    except Exception:
        return False


class OpcUaDriver:
    name = "opcua"

    def __init__(self, endpoint: str, node_map: dict[str, Any], *, timeout_s: float = 4.0) -> None:
        self.endpoint = endpoint
        self.node_map = node_map
        self.timeout_s = timeout_s
        self._client = None
        self._lock = threading.Lock()
        self._bad_nodes: set[str] = set()

    @classmethod
    def from_files(cls, endpoint: str, map_path: str) -> "OpcUaDriver":
        return cls(endpoint, json.loads(Path(map_path).read_text(encoding="utf-8")))

    def info(self) -> dict[str, Any]:
        return {"driver": self.name, "endpoint": self.endpoint, "machines": sorted(self.node_map),
                "connected": self._client is not None, "unreadable_nodes": sorted(self._bad_nodes)}

    def _connect(self):
        if self._client is None:
            # Imported here so the simulator-only deployment does not need the OPC UA stack loaded.
            from asyncua.sync import Client

            client = Client(self.endpoint, timeout=self.timeout_s)
            client.connect()
            self._client = client
        return self._client

    def _drop(self) -> None:
        """Disconnect and forget the session, so the next call reconnects instead of reusing a dead socket."""
        client, self._client = self._client, None
        if client is not None:
            try:
                client.disconnect()
            except Exception:
                pass

    def close(self) -> None:
        with self._lock:
            self._drop()

    @staticmethod
    def _node_error() -> tuple[type[BaseException], ...]:
        """Errors that belong to one node (bad id, bad value), not to the connection."""
        from asyncua import ua

        return (ua.UaError, ValueError, TypeError)

    def _read(self, client, node_id: Optional[str], *, missing: Any = None, cast=None) -> Any:
        """One node's value; `missing` when it is not mapped, None (logged once) when it cannot be read."""
        if not node_id:
            return missing
        try:
            value = client.get_node(node_id).read_value()
            value = cast(value) if cast else value
            self._bad_nodes.discard(node_id)
            return value
        except self._node_error() as exc:
            if _connection_level(exc):
                raise
            if node_id not in self._bad_nodes:
                self._bad_nodes.add(node_id)
                print(f"[plc] OPC UA node {node_id} unreadable: {exc}", file=sys.stderr)
            return None

    def _values(self, client, nodes: dict[str, str]) -> dict[str, float]:
        out = {}
        for key, node_id in nodes.items():
            v = self._read(client, node_id, cast=float)
            if v is not None and math.isfinite(v):
                out[key] = v
        return out

    @staticmethod
    def _alarm_code(raw: Any, code_map: dict[str, str]) -> Optional[str]:
        if raw is None or isinstance(raw, bool) and not raw:
            return None
        code = str(int(raw)) if isinstance(raw, float) and raw.is_integer() else str(raw).strip()
        if code.upper() in _NO_ALARM:
            return None
        return code_map.get(code, code)

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
                    tags = self._values(client, m.get("tags", {}))
                    setpoints = self._values(client, m.get("setpoints", {}))
                    run = self._read(client, m.get("run"))
                    code = self._alarm_code(self._read(client, m.get("alarm_code")), m.get("alarm_code_map", {}))
                    text = self._read(client, m.get("alarm_text")) if code else None
                    alarm = {"code": code, "text": str(text or "")} if code else None
                    estop = self._read(client, m.get("estop"), missing=False)
                    lockout = self._read(client, m.get("lockout"), missing=False)
                    out.append({
                        "machine_id": mid,
                        "status": status,
                        "run": bool(run) if run is not None else status == "RUNNING",
                        "fault": alarm is not None,
                        "tags": {k: round(v, 2) for k, v in tags.items()},
                        "setpoints": setpoints,
                        "estop": None if estop is None else bool(estop),
                        "lockout": None if lockout is None else bool(lockout),
                        "alarm": alarm,
                    })
                return out
            except Exception:
                self._drop()
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
                if vtype in (None, ua.VariantType.Null):
                    # Some servers declare a generic type; fall back to the type of the value it holds.
                    vtype = node.read_data_value().Value.VariantType
                variant = _variant(ua, vtype, value, machine_id, tag)
                node.write_value(ua.DataValue(variant))
                return float(node.read_value())
            except PlcWriteError:
                raise
            except self._node_error() as exc:
                if _connection_level(exc):
                    self._drop()
                raise PlcWriteError(f"{machine_id}: write {tag}={value:g} failed: {exc}") from exc
            except Exception as exc:
                self._drop()
                raise PlcWriteError(f"{machine_id}: write {tag}={value:g} failed: {exc}") from exc


def _variant(ua, vtype, value: float, machine_id: str, tag: str):
    """The value as the node's own type; refuses anything that would be silently rounded or wrapped."""
    V = ua.VariantType
    if vtype in (V.Double, V.Float):
        return ua.Variant(float(value), vtype)
    if vtype == V.Boolean:
        if value not in (0, 1):
            raise PlcWriteError(f"{machine_id}: {tag} is a boolean node; {value:g} is not 0 or 1")
        return ua.Variant(bool(value), vtype)
    ranges = {V.SByte: (-2**7, 2**7 - 1), V.Byte: (0, 2**8 - 1), V.Int16: (-2**15, 2**15 - 1),
              V.UInt16: (0, 2**16 - 1), V.Int32: (-2**31, 2**31 - 1), V.UInt32: (0, 2**32 - 1),
              V.Int64: (-2**63, 2**63 - 1), V.UInt64: (0, 2**64 - 1)}
    if vtype not in ranges:
        raise PlcWriteError(f"{machine_id}: {tag} has OPC UA type {getattr(vtype, 'name', vtype)}, which SkillForge does not write")
    lo, hi = ranges[vtype]
    if not float(value).is_integer() or not lo <= value <= hi:
        raise PlcWriteError(f"{machine_id}: {tag} is an {vtype.name} node; {value:g} would not be written as given")
    return ua.Variant(int(value), vtype)
