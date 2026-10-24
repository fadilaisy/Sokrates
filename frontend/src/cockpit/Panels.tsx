import { cn } from "../lib/utils";
import { fmt1, shortHash, telemetryOf, windowLabel } from "../lib/shift";
import { DEMO_DISRUPTION, useCockpit } from "./store";
import { Btn, Card, Mono, StatusBadge } from "./ui";

/** CNC-01 → CNC-05 left to right, same order as the physical line. */
export function MachineStrip() {
  const { state, displayStatus, live } = useCockpit();
  const machines = state?.work_centers ?? [];
  return (
    <div className="grid grid-cols-2 gap-3 md:grid-cols-3 xl:grid-cols-5">
      {machines.map((w) => {
        const st = displayStatus(w.id, w.status);
        const { temp, vib } = telemetryOf(w, live[w.id]);
        const offline = st === "MAINTENANCE" || (temp != null && temp < 30 && (vib ?? 0) === 0);
        const alert = st === "FAULT" || st === "DISRUPTED";
        return (
          <Card
            key={w.id}
            className={cn("flex flex-col gap-2 p-4", st === "FAULT" && "sf-pulse border-2 border-danger-line", st === "DISRUPTED" && "border-2 border-warn")}
          >
            <div className="flex items-baseline justify-between gap-2">
              <h3 className="text-[24px] font-bold">{w.id}</h3>
              {alert && <span className="text-[14px] font-semibold text-fault">perlu tindakan</span>}
            </div>
            <StatusBadge status={st} />
            <Mono className="text-[15px]">{offline ? "– · –" : `${fmt1(temp)}°C · ${fmt1(vib)} mm/s`}</Mono>
            <Mono className={cn("truncate text-[14px]", st === "DISRUPTED" ? "text-warn" : "text-muted-ink")}>
              {st === "DISRUPTED" ? "UNDER ANALYSIS · cnc_milling" : "skill: cnc_milling"}
            </Mono>
          </Card>
        );
      })}
    </div>
  );
}

const TEST_FRAMES = [
  { label: "Uji SAFE-001", machine: "CNC-02", metric: "motor_temp_celsius", value: 96 },
  { label: "Uji SAFE-002", machine: "CNC-04", metric: "spindle_vibration_mm_per_s", value: 8.6 },
];

export function TelemetryPanel() {
  const { state, live, wsStatus, lastAnalysis, safety, sendTelemetry, plcOn } = useCockpit();
  const machines = state?.work_centers ?? [];
  const shown = machines.filter((w) => w.status.toUpperCase() === "RUNNING" || live[w.id] || safety?.machine_id === w.id).slice(0, 4);
  return (
    <Card className="flex flex-col gap-3 p-4">
      <div className="flex items-center justify-between">
        <h2 className="text-[17px] font-semibold">Telemetri live</h2>
        <span className="flex items-center gap-2 text-[14px]">
          <span className={cn("size-2.5 rounded-full", wsStatus === "open" ? "bg-ok-line" : wsStatus === "connecting" ? "bg-warn" : "bg-danger-line")} />
          WebSocket {wsStatus === "open" ? "" : wsStatus === "connecting" ? "· menyambung" : "· terputus"}
        </span>
      </div>
      <ul className="flex flex-col gap-1.5">
        {shown.map((w) => {
          const { temp, vib } = telemetryOf(w, live[w.id]);
          const hot = (temp ?? 0) >= 95 || (vib ?? 0) >= 8;
          const warm = (temp ?? 0) >= 85 || (vib ?? 0) >= 5;
          return (
            <li key={w.id} className={cn("grid grid-cols-[72px_1fr_1fr] font-mono text-[15px]", hot ? "font-bold text-fault" : warm && "text-warn")}>
              <span>{w.id}</span>
              <span>{fmt1(temp)}°C</span>
              <span>{fmt1(vib)} mm/s</span>
            </li>
          );
        })}
      </ul>
      {safety ? (
        <p className="rounded-md border border-danger-line bg-danger-bg px-3 py-2 text-[15px] font-semibold text-fault">
          {safety.rule} · {safety.machine_id} melewati batas aman
        </p>
      ) : (
        <p className="rounded-md border border-ok-line bg-ok-bg px-3 py-2 text-center text-[15px] text-running">Semua parameter normal</p>
      )}
      {lastAnalysis?.analysis && <p className="text-[14px] text-muted-ink">AI: {lastAnalysis.analysis}</p>}
      <div className="flex flex-wrap gap-2 border-t border-line pt-3">
        <span className="w-full text-[14px] text-muted-ink">Uji telemetri manual:</span>
        {TEST_FRAMES.map((t) => (
          <button
            key={t.label}
            type="button"
            onClick={() => sendTelemetry(t.machine, t.metric, t.value)}
            className="min-h-12 rounded-md border border-line px-3 text-[15px] font-semibold hover:bg-page"
          >
            {t.label}
          </button>
        ))}
      </div>
      <PlcControls />
      {plcOn && (
        <>
          <PlcPredictionCard />
          <PlcAlarmCard />
          <PlcActionReceipt />
        </>
      )}
    </Card>
  );
}

const fmtEta = (s: number | null) => (s == null ? "–" : s < 90 ? `${Math.round(s)} detik` : `${Math.round(s / 60)} menit`);

/** Simulated PLC: the machines stream their own tags and alarm codes; a scenario makes one drift into a fault. */
function PlcControls() {
  const { plcOn, setPlcOn, plcStatus, plcInfo, startPlcScenario, setPlcAutonomy, plcMachines, setPlcLockout } = useCockpit();
  const faults = plcInfo?.scenarios?.filter((s) => s.id !== "normal") ?? [];
  const active = faults.find((s) => s.id === plcInfo?.scenario);
  const target = active?.machine_id ? plcMachines[active.machine_id] : undefined;
  const autonomy = plcInfo?.autonomy;
  return (
    <div className="flex flex-col gap-2 border-t border-line pt-3">
      <div className="flex items-center justify-between gap-2">
        <span className="text-[15px] font-semibold">PLC {plcInfo?.driver === "opcua" ? "(OPC UA)" : "(simulasi)"}</span>
        <button
          type="button"
          onClick={() => setPlcOn(!plcOn)}
          className={cn("min-h-10 rounded-md border px-3 text-[14px] font-semibold", plcOn ? "border-ok-line bg-ok-bg text-running" : "border-line hover:bg-page")}
        >
          {plcOn ? (plcStatus === "open" ? "Tersambung · putuskan" : "Menyambung…") : "Sambungkan PLC"}
        </button>
      </div>
      {plcOn && (
        <div className="flex flex-wrap gap-2">
          {faults.map((s) => (
            <button
              key={s.id}
              type="button"
              onClick={() => startPlcScenario(s.id)}
              className={cn("min-h-12 rounded-md border px-3 text-left text-[14px] font-semibold hover:bg-page", plcInfo?.scenario === s.id ? "border-warn" : "border-line")}
            >
              {s.label}
              <Mono className="block text-[13px] font-normal text-muted-ink">{s.alarm_code}</Mono>
            </button>
          ))}
          {plcInfo?.scenario && plcInfo.scenario !== "normal" && (
            <button type="button" onClick={() => startPlcScenario("normal")} className="min-h-12 rounded-md border border-line px-3 text-[14px] hover:bg-page">
              Pulihkan normal
            </button>
          )}
        </div>
      )}
      {plcOn && autonomy && (
        <div className="flex flex-col gap-1.5 rounded-md bg-page px-3 py-2">
          <div className="flex items-center justify-between gap-2">
            <span className="text-[14px] font-semibold">Autonomi terbatas</span>
            <button
              type="button"
              onClick={() => setPlcAutonomy(!autonomy.enabled)}
              className={cn("min-h-10 rounded-md border px-3 text-[14px] font-semibold", autonomy.enabled ? "border-warn bg-surface text-warn" : "border-line hover:bg-surface")}
            >
              {autonomy.enabled ? "Aktif · matikan" : "Mati · aktifkan"}
            </button>
          </div>
          <p className="text-[13px] text-muted-ink">
            Hanya aksi pra-disetujui di file batas (turunkan feed override ke 50% saat motor spindel panas), hanya ke arah aman, dan
            hanya jika semua pemeriksaan lolos. Setiap aksi tercatat di audit ledger.
          </p>
          {target && (
            <button
              type="button"
              onClick={() => setPlcLockout(target.machine_id, !target.lockout)}
              className="min-h-10 self-start rounded-md border border-line px-3 text-[13px] hover:bg-surface"
            >
              {target.lockout ? `Lepas lockout ${target.machine_id}` : `Uji: pasang lockout ${target.machine_id}`}
            </button>
          )}
        </div>
      )}
    </div>
  );
}

/** Approve one limits-file action; disabled when the machine is read-only or the action is already applied. */
function ProposedAction({ machineId }: { machineId: string }) {
  const { plcAlarm, plcPrediction, approvePlcAction, plcBusy } = useCockpit();
  const p = (plcAlarm?.machine_id === machineId ? plcAlarm.proposed_action : null) ?? (plcPrediction?.machine_id === machineId ? plcPrediction.proposed_action : null);
  if (!p) return null;
  return (
    <div className="flex flex-col gap-1.5 border-t border-line pt-2">
      <p>
        <span className="font-semibold">Usulan aksi:</span> {p.label}{" "}
        <Mono className="text-[13px] text-muted-ink">
          ({p.tag} {p.current ?? "?"} → {p.value})
        </Mono>
      </p>
      {p.write_enabled ? (
        <Btn onClick={() => approvePlcAction(p)} disabled={plcBusy}>
          {plcBusy ? "Memeriksa & menulis…" : "Setujui & kirim ke PLC"}
        </Btn>
      ) : (
        <p className="text-muted-ink">Mesin ini hanya-baca untuk SkillForge; lakukan manual di panel mesin.</p>
      )}
    </div>
  );
}

/** A tag heading for its limit before any alarm, with the time left and what to do about it. */
function PlcPredictionCard() {
  const { plcPrediction: p, plcAlarm, planMaintenance, phase } = useCockpit();
  if (!p || (plcAlarm && plcAlarm.machine_id === p.machine_id)) return null;
  const rate = `${p.slope_per_min > 0 ? "+" : ""}${fmt1(p.slope_per_min)}${p.unit ?? ""}/menit`;
  return (
    <div className="flex flex-col gap-1.5 rounded-md border border-navy bg-page px-3 py-2 text-[14px]">
      <p className="font-semibold">
        Prediksi · <Mono>{p.machine_id}</Mono> {p.label ?? p.metric}
      </p>
      <p>
        Sekarang {fmt1(p.current)}
        {p.unit} ({rate}). Mencapai batas peringatan {p.warning}
        {p.unit} dalam ~{fmtEta(p.eta_warning_s)}, batas kritis {p.critical}
        {p.unit} dalam ~{fmtEta(p.eta_critical_s)}.
      </p>
      <ProposedAction machineId={p.machine_id} />
      <Btn variant="secondary" onClick={() => planMaintenance(p.machine_id)} disabled={phase === "detecting"}>
        Jadwalkan perawatan sebelum rusak
      </Btn>
    </div>
  );
}

/** The alarm a machine just raised, with how it was fixed last time (from the maintenance ledger). */
function PlcAlarmCard() {
  const { plcAlarm } = useCockpit();
  if (!plcAlarm) return null;
  const fix = plcAlarm.past_fixes[0];
  const doc = plcAlarm.documents[0];
  return (
    <div className="flex flex-col gap-1.5 rounded-md border border-warn bg-page px-3 py-2 text-[14px]">
      <p className="font-semibold">
        <Mono>{plcAlarm.machine_id} · {plcAlarm.code}</Mono> {plcAlarm.text}
      </p>
      {fix ? (
        <>
          <p className="text-muted-ink">
            Pernah terjadi di {fix.machine_id} (<Mono>{fix.case_id}</Mono>){fix.root_cause ? `: ${fix.root_cause}` : ""}
          </p>
          {fix.fix_steps.length > 0 && (
            <ol className="list-decimal pl-5">
              {fix.fix_steps.slice(0, 3).map((step) => <li key={step}>{step}</li>)}
            </ol>
          )}
        </>
      ) : (
        <p className="text-muted-ink">Belum ada kasus serupa di riwayat perawatan.</p>
      )}
      {doc?.citation && <p className="text-muted-ink">Referensi: {doc.citation}</p>}
      <ProposedAction machineId={plcAlarm.machine_id} />
    </div>
  );
}

/** What the last write did: every pre-action check, the read-back, who approved it, and the ledger hash. */
function PlcActionReceipt() {
  const { plcAction: a } = useCockpit();
  if (!a) return null;
  return (
    <div className={cn("flex flex-col gap-1.5 rounded-md border px-3 py-2 text-[14px]", a.ok ? "border-ok-line bg-ok-bg" : "border-danger-line bg-danger-bg")}>
      <p className={cn("font-semibold", !a.ok && "text-fault")}>
        {a.ok ? "Ditulis ke PLC" : "Diblokir"} · <Mono>{a.machine_id}</Mono> {a.tag} {a.before ?? "?"} → {a.ok ? a.after : a.value}
      </p>
      <p className="text-muted-ink">
        {a.autonomous ? "Autonomi terbatas" : `Disetujui ${a.approved_by.replace("Production Supervisor — ", "")}`} · ledger{" "}
        <Mono>{shortHash(a.sha256_hash)}</Mono>
      </p>
      <ul className="flex flex-col gap-0.5">
        {a.checks.map((c) => (
          <li key={c.id} className={cn("flex gap-2", !c.ok && "font-semibold text-fault")}>
            <span aria-hidden>{c.ok ? "✓" : "✗"}</span>
            <span>
              {c.label} <span className="text-muted-ink">· {c.detail}</span>
            </span>
          </li>
        ))}
      </ul>
      {a.error && <p className="text-fault">{a.error}</p>}
    </div>
  );
}

export function WhatIfPanel() {
  const { runDisruption, phase, injectDrill, injecting, result, autoIn } = useCockpit();
  // During an alert countdown the agent is about to analyse; a what-if now would race it.
  const busy = phase === "detecting" || autoIn != null;
  return (
    <Card className="flex flex-col gap-3 p-4">
      <h2 className="text-[17px] font-semibold">Gangguan / What-if</h2>
      <div className="flex gap-2">
        <div className="flex-1 rounded-md bg-page px-3 py-2.5 text-[15px]">
          Mesin <Mono>{DEMO_DISRUPTION.machine_id} · {DEMO_DISRUPTION.disruption_type}</Mono>
        </div>
        <div className="rounded-md bg-page px-3 py-2.5 font-mono text-[15px]">
          {windowLabel(DEMO_DISRUPTION.start_hour, DEMO_DISRUPTION.end_hour)}
        </div>
      </div>
      <Btn size="lg" onClick={() => runDisruption(DEMO_DISRUPTION)} disabled={busy}>
        What-if: CNC-02 rusak 3 jam
      </Btn>
      <Btn variant="secondary" onClick={injectDrill} disabled={busy || injecting}>
        {injecting ? "Menginjeksikan…" : "Drill: gangguan susulan CNC-03 FAULT"}
      </Btn>
      <p className="text-[14px] text-muted-ink">
        {result
          ? "Drill membuat skenario di atas basi (SAP version naik). Setujui skenario lama untuk melihat 409 + hitung ulang otomatis."
          : "Mode bebas tersedia di area AI Supervisor di bawah."}
      </p>
    </Card>
  );
}
