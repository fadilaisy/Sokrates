# PLC integration

SkillForge reads each machine's PLC or CNC controller, predicts faults from trends, finds the
fix when an alarm fires, and can change a small set of setpoints after a supervisor approves.
Safety stays in the PLC's own hardware interlocks: SkillForge never writes e-stops, interlocks
or safety functions, and refuses to write at all while a machine is e-stopped or locked out.

| Step | What happens | Where |
| --- | --- | --- |
| 1. Watch | Every `PLC_SCAN_S` seconds the driver reads each machine's tags, setpoints, e-stop and lockout bits, and alarm code. | `plc_adapter/driver.py`, `simulator.py`, `opcua_driver.py`, `/ws/plc` |
| 2. Alarm → fix | A new alarm code pulls up past fixes for that exact code and plant documents, and runs the maintenance playbook (mechanic routing). | `_alarm_references`, `_analyze_reading` in `backend/main.py` |
| 3. Predict | A line fitted to the last 20 s of each tag gives the time until it reaches its warning and critical values from the limits file. The cockpit offers the limits-file action or books a maintenance window through the solver and the SAP approval gate. | `plc_adapter/predictor.py`, `plc_prediction` frame |
| 4. Act with approval | A supervisor approves the proposed setpoint change. Pre-action checks run, the value is written and read back, and the outcome (applied, blocked or failed) is hash-chained into the audit ledger. | `plc_adapter/limits.py`, `machine_limits.json`, `POST /api/plc/actions` |
| 5. Limited autonomy | When a supervisor switches it on, actions marked `autonomous` in the limits file run by themselves on a trend or alarm, only as a reduction, with the same checks, logged as `SkillForge · autonomi terbatas`. | `POST /api/plc/autonomy`, `plc_action` frame |

## Limits file

`plc_adapter/machine_limits.json` holds, per machine (defaults plus overrides):

- `tags`: warning and critical values and direction, used for prediction.
- `setpoints`: what may be written, with `min`, `max` and `max_step`.
- `actions`: the change SkillForge proposes for an alarm code or a trending tag, and whether
  limited autonomy may run it.
- `write_enabled: false` makes a machine read-only (CNC-03 in the demo).

Pre-action checks, all of which must pass: machine writable, setpoint allowed, value in range,
step within `max_step`, PLC readable, no e-stop, no lockout/tagout, SAP status not
MAINTENANCE/BREAKDOWN/FAULT, and a named supervisor approval (or, for autonomy, an action on the
autonomous list moving in the safe direction). After the write the value is read back.

## Connecting real controllers (OPC UA)

The default driver is the simulator. To read real machines through OPC UA (Siemens S7-1500,
Fanuc/Haas via a gateway, Kepware and most modern controllers):

```
PLC_DRIVER=opcua
PLC_OPCUA_ENDPOINT=opc.tcp://192.168.10.20:4840
PLC_OPCUA_MAP=/app/config/opcua_map.json
```

The node map names the OPC UA node for each value; leave out anything a machine does not have:

```json
{
  "CNC-04": {
    "tags": {"motor_temp_celsius": "ns=2;s=CNC04.Spindle.MotorTemp",
             "spindle_vibration_mm_per_s": "ns=2;s=CNC04.Spindle.Vibration"},
    "setpoints": {"feed_override_percent": "ns=2;s=CNC04.FeedOverride"},
    "run": "ns=2;s=CNC04.Running",
    "estop": "ns=2;s=CNC04.EStop",
    "lockout": "ns=2;s=CNC04.Lockout",
    "alarm_code": "ns=2;s=CNC04.AlarmCode",
    "alarm_text": "ns=2;s=CNC04.AlarmText"
  }
}
```

Only setpoints that are both in the node map and in the limits file can be written. Scenario,
lockout and e-stop demo controls (`/api/plc/scenario`, `/api/plc/interlock`) work only with the
simulator. Older machines that speak Modbus or vendor protocols are usually bridged to OPC UA by
an edge gateway (Kepware, an industrial PC or a PAC).

## Demo script

1. Cockpit → **Sambungkan PLC** → **CNC-04 motor spindel overheat**.
2. After about 10 s the prediction card shows the temperature climbing and the time to 85 °C.
3. **Setujui & kirim ke PLC**: the receipt lists every check and the ledger hash; the spindle cools
   and the alarm never fires.
4. Run it again with **Uji: pasang lockout CNC-04** to show a blocked write.
5. Switch **Autonomi terbatas** on and run it again: SkillForge reduces the feed by itself.
6. **CNC-01 bearing** → **Jadwalkan perawatan sebelum rusak** books a maintenance window through
   the solver and the approval gate.
