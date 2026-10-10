import { cn } from "../lib/utils";
import { fmt1, telemetryOf, windowLabel } from "../lib/shift";
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
  const { state, live, wsStatus, lastAnalysis, safety, sendTelemetry } = useCockpit();
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
        <span className="w-full text-[14px] text-muted-ink">Uji telemetri (demo, ganti dengan bridge Wokwi):</span>
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
    </Card>
  );
}

export function WhatIfPanel() {
  const { runDisruption, phase, injectDrill, injecting, result } = useCockpit();
  const busy = phase === "detecting";
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
