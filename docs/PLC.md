# PLC integration

SkillForge reads each machine's PLC or CNC controller, predicts faults from trends, finds the
fix when an alarm fires, and can change a small set of setpoints after a supervisor approves.
Safety stays in the PLC's own hardware interlocks: SkillForge never writes e-stops, interlocks
or safety functions, and refuses to write at all while a machine is e-stopped or locked out.

| Step | What happens | Where |
| --- | --- | --- |
| 1. Watch | Every `PLC_SCAN_S` seconds one server-side loop reads each machine's tags, setpoints, e-stop and lockout bits, and alarm code, whether or not a cockpit is open. `/ws/plc` only subscribes to it. | `plc_adapter/driver.py`, `simulator.py`, `opcua_driver.py`, `_plc_loop`, `/ws/plc` |
| 2. Alarm → fix | A new alarm code pulls up past fixes for that exact code and plant documents, and runs the maintenance playbook (mechanic routing). | `_alarm_references`, `_analyze_reading` in `backend/main.py` |
| 3. Predict | A line fitted to the last 20 s of each tag gives the time until it reaches its warning and critical values from the limits file. The cockpit offers the limits-file action or books a maintenance window through the solver and the SAP approval gate. | `plc_adapter/predictor.py`, `plc_prediction` frame |
| 4. Act with approval | A supervisor approves the proposed setpoint change. Pre-action checks run, the value is written and read back, and the outcome (applied, blocked, failed or unconfirmed) is hash-chained into the audit ledger. | `plc_adapter/limits.py`, `machine_limits.json`, `POST /api/plc/actions` |
| 5. Limited autonomy | When a supervisor switches it on, actions marked `autonomous` in the limits file run by themselves on a trend or alarm, only as a reduction, with the same checks, logged as `SkillForge · autonomi terbatas`. | `POST /api/plc/autonomy`, `plc_action` frame |

## Limits file

`plc_adapter/machine_limits.json` holds, per machine (defaults plus overrides):

- `tags`: warning and critical values and direction, used for prediction.
- `setpoints`: what may be written, with `min`, `max` and `max_step`.
- `actions`: the change SkillForge proposes for an alarm code or a trending tag, and whether
  limited autonomy may run it.
- `actions.*.direction`: `reduce` (lower an override) or `engage` (switch feed hold on). Both take
  load off the machine; limited autonomy only moves a setpoint that way.
- `write_enabled: true` makes a machine writable. A machine without it is read-only (CNC-03 in the
  demo sets it to `false` explicitly).

The file is validated at startup (numeric limits, every action on a listed setpoint and inside its
range, a known direction); a malformed file stops the backend instead of failing open mid-write.

Pre-action checks, all of which must pass: writes enabled on the server, machine writable, setpoint
allowed, the named action (if any) matches, value in range, step within `max_step`, value differs
from the current setpoint (a second approval of the same change does not write again), PLC readable,
no e-stop, no lockout/tagout (an e-stop or lockout bit that cannot be read counts as set), SAP status
RUNNING or IDLE, and a named supervisor approval (or, for autonomy, an action on the autonomous list
moving in its safe direction). After the write the value is read back.

| Outcome | Ledger entry | Meaning |
| --- | --- | --- |
| applied | `PLC_WRITE_APPLIED` | Written and read back as asked. |
| blocked | `PLC_WRITE_BLOCKED` | A check failed; nothing was written. |
| failed | `PLC_WRITE_FAILED` | The write raised an error; the setpoint is uncertain, check the HMI. |
| unconfirmed | `PLC_WRITE_UNCONFIRMED` | Written, but the read-back differs: the PLC changed, check the HMI. |

## Runtime behaviour

- The PLC loop starts with the backend. Prediction and limited autonomy run with no browser open;
  each cockpit tab is a viewer and gets the latest scan, active alarms and fresh predictions when it
  connects.
- If the PLC cannot be read, the loop sends `plc_offline` frames, the cockpit shows "PLC offline",
  every write is blocked by the "PLC terbaca" check, and scanning retries every `PLC_SCAN_S`.
- Limited autonomy is held in the backend process and is off after every restart, so a restart never
  re-arms it. Autonomy and the simulator are per process: run a single uvicorn worker (the
  Dockerfile's default).
- `PLC_WRITES_ENABLED` is the server-side kill switch for every write. It defaults to on for the
  simulator and off for `PLC_DRIVER=opcua`.
- Approver names are typed by the user and are not authenticated. That is fine for the simulator; on
  a plant network, put the backend behind your SSO before turning `PLC_WRITES_ENABLED` on.

## Connecting real controllers (OPC UA)

The default driver is the simulator. To read real machines through OPC UA (Siemens S7-1500,
Fanuc/Haas via a gateway, Kepware and most modern controllers):

```
PLC_DRIVER=opcua
PLC_OPCUA_ENDPOINT=opc.tcp://192.168.10.20:4840
PLC_OPCUA_MAP=config/opcua_map.json
PLC_WRITES_ENABLED=false   # read-only until you have tested writes on the line
```

`PLC_DRIVER` must be `simulated` or `opcua`; anything else, a missing endpoint or a missing map file
stops startup with a message naming the variable. Copy `config/opcua_map.example.json` to
`config/opcua_map.json` and edit it. The node map names the OPC UA node for each value; leave out
anything a machine does not have:

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

Controllers that report numeric alarm codes need an `"alarm_code_map": {"410": "ALM-410"}` entry
per machine so the code matches the limits file and past fixes; `0`, empty and `NONE` mean no alarm.

A node that cannot be read is left out of that scan and logged once, and the rest of the line keeps
reading. Only a lost connection drops the OPC UA session (it is disconnected first, and closed at
shutdown). Writes use the node's own type: a fractional value for an integer node, or anything but
0/1 for a boolean node, is refused before writing, and Float32 read-backs are compared with a
relative tolerance.

Only setpoints that are both in the node map and in the limits file can be written. Scenario,
lockout and e-stop demo controls (`/api/plc/scenario`, `/api/plc/interlock`) work only with the
simulator. Older machines that speak Modbus or vendor protocols are usually bridged to OPC UA by
an edge gateway (Kepware, an industrial PC or a PAC).

### Scope of the OPC UA driver

The driver shows the integration point and is tested against a real OPC UA server, but it is not yet
certified for a plant network. Before writing to production machines:

- Authenticate approvers (SSO in front of the backend) instead of trusting the typed name.
- Add a controller-side permit node (remote-mode bit) as a required check, so the PLC itself must
  allow remote setpoint changes, not only SAP status.
- Re-read the affected tag a while after a write and ledger an ineffective change.
- Use an OPC UA security policy and certificates (the driver connects with none today).

## Demo script

1. Cockpit → **Sambungkan PLC** → **CNC-04 motor spindel overheat**.
2. After about 10 s the prediction card shows the temperature climbing and the time to 85 °C.
3. **Setujui & kirim ke PLC**: the receipt lists every check and the ledger hash; the spindle cools
   and the alarm never fires.
4. Run it again with **Uji: pasang lockout CNC-04** to show a blocked write.
5. Switch **Autonomi terbatas** on and run it again: SkillForge reduces the feed by itself.
6. **CNC-01 bearing** → **Jadwalkan perawatan sebelum rusak** books a maintenance window through
   the solver and the approval gate.
