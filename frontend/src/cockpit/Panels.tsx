import { useState, useSyncExternalStore } from "react";
import { Volume2 } from "lucide-react";
import { cn } from "../lib/utils";
import type { PlcActionResult, SkillGuidance } from "../lib/api";
import { fmt1, shortHash, telemetryOf, windowLabel } from "../lib/shift";
import { audioReady, onAudioReady, playAlarmNow } from "../lib/sound";
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
    </Card>
  );
}

// Tags shown per machine while the line is quiet, with the WARNING line of the shipped skills.
const READING_TAGS: { tag: string; label: string; unit: string; warn: (v: number) => boolean }[] = [
  { tag: "motor_temp_celsius", label: "Suhu", unit: "°C", warn: (v) => v >= 85 },
  { tag: "spindle_vibration_mm_per_s", label: "Getaran", unit: "mm/s", warn: (v) => v >= 5 },
  { tag: "coolant_pressure_bar", label: "Coolant", unit: "bar", warn: (v) => v > 0 && v < 2.5 },
];

/** Live tags per machine, so the quiet line still shows what the PLC is reading. */
function PlcReadings() {
  const { plcMachines } = useCockpit();
  const rows = Object.values(plcMachines).sort((a, b) => a.machine_id.localeCompare(b.machine_id));
  if (!rows.length) return null;
  return (
    <div className="grid gap-3 sm:grid-cols-2 xl:grid-cols-3">
      {rows.map((m) => (
        <div key={m.machine_id} className="flex flex-col gap-2 rounded-md border border-line px-3 py-2">
          <div className="flex items-center justify-between gap-2">
            <Mono className="text-[14px] font-semibold">{m.machine_id}</Mono>
            <span className={cn("text-[13px] font-semibold", m.run ? "text-running" : "text-muted-ink")}>
              {m.run ? "Berjalan" : m.status}
            </span>
          </div>
          <dl className="grid grid-cols-3 gap-2">
            {READING_TAGS.map((t) => {
              const v = m.tags[t.tag];
              return (
                <div key={t.tag} className="flex min-w-0 flex-col">
                  <dt className="truncate text-[12px] text-muted-ink">{t.label}</dt>
                  <dd className={cn("font-mono text-[15px]", v != null && t.warn(v) && "font-bold text-warn")}>
                    {v == null ? "–" : `${fmt1(v)} ${t.unit}`}
                  </dd>
                </div>
              );
            })}
          </dl>
        </div>
      ))}
    </div>
  );
}

/** The PLC block under the schedule: controls on the left, what the machines are saying on the right. */
export function PlcPanel() {
  const { plcOn, plcPrediction, plcAlarm, plcAction } = useCockpit();
  const quiet = !plcPrediction && !plcAlarm && !plcAction;
  return (
    <Card className="grid gap-4 p-4 lg:grid-cols-[minmax(0,380px)_minmax(0,1fr)]">
      <PlcControls />
      <div className="flex min-w-0 flex-col gap-3">
        {!plcOn ? (
          <p className="text-[14px] text-muted-ink">
            Sambungkan PLC untuk membaca tag mesin langsung, melihat prediksi sebelum alarm, dan mengirim aksi yang sudah disetujui.
          </p>
        ) : quiet ? (
          <>
            <p className="rounded-md bg-page px-3 py-2 text-[14px] text-muted-ink">
              Belum ada prediksi atau alarm. Semua mesin terbaca normal; pilih skenario untuk demo.
            </p>
            <PlcReadings />
          </>
        ) : (
          <>
            <PlcPredictionCard />
            <PlcAlarmCard />
            <PlcActionReceipt />
          </>
        )}
      </div>
    </Card>
  );
}

// A fitted line can reach the limit a moment before the reading does; say "any moment" rather than "~0 s".
const fmtEta = (s: number | null) =>
  s == null ? "–" : s < 5 ? "sebentar lagi" : s < 90 ? `dalam ~${Math.round(s)} detik` : `dalam ~${Math.round(s / 60)} menit`;

const OUTCOME_LABEL: Record<PlcActionResult["outcome"], string> = {
  applied: "Ditulis ke PLC",
  blocked: "Diblokir, tidak ditulis",
  failed: "Gagal ditulis",
  unconfirmed: "Ditulis, belum terkonfirmasi",
};

/** Simulated PLC: the machines stream their own tags and alarm codes; a scenario makes one drift into a fault. */
function PlcControls() {
  const { plcOn, setPlcOn, plcStatus, plcInfo, plcOffline, startPlcScenario, setPlcAutonomy, plcMachines, setPlcLockout } = useCockpit();
  // Fault scenarios and the lockout test exist only on the simulator; a real line never offers them.
  const simulated = plcInfo?.driver === "simulated";
  const faults = simulated ? (plcInfo?.scenarios?.filter((s) => s.id !== "normal") ?? []) : [];
  const active = faults.find((s) => s.id === plcInfo?.scenario);
  const target = active?.machine_id ? plcMachines[active.machine_id] : undefined;
  const autonomy = plcInfo?.autonomy;
  return (
    <div className="flex min-w-0 flex-col gap-2">
      <div className="flex items-center justify-between gap-2">
        <h2 className="text-[17px] font-semibold">PLC {plcInfo?.driver === "opcua" ? "(OPC UA)" : "(simulasi)"}</h2>
        <Btn
          variant="secondary"
          onClick={() => setPlcOn(!plcOn)}
          className={cn("px-3 text-[14px]", plcOn && "border-ok-line bg-ok-bg text-running")}
        >
          {plcOn ? (plcStatus === "open" ? "Tersambung · putuskan" : "Menyambung…") : "Sambungkan PLC"}
        </Btn>
      </div>
      {plcOn && plcOffline && (
        <p role="status" className="rounded-md border border-danger-line bg-danger-bg px-3 py-2 text-[14px] text-fault">
          PLC offline: server tidak bisa membaca PLC ({plcOffline.error}). Mencoba lagi setiap scan; penulisan diblokir.
        </p>
      )}
      {plcOn && simulated && (
        <div className="flex flex-wrap gap-2">
          {faults.map((s) => (
            <Btn
              key={s.id}
              variant="secondary"
              onClick={() => startPlcScenario(s.id)}
              className={cn("flex-col items-start gap-0 px-3 text-left text-[14px]", plcInfo?.scenario === s.id && "border-warn")}
            >
              {s.label}
              <Mono className="block text-[13px] font-normal text-muted-ink">{s.alarm_code}</Mono>
            </Btn>
          ))}
          {plcInfo?.scenario && plcInfo.scenario !== "normal" && (
            <Btn variant="secondary" onClick={() => startPlcScenario("normal")} className="px-3 text-[14px]">
              Pulihkan normal
            </Btn>
          )}
        </div>
      )}
      {plcOn && autonomy && (
        <div className="flex flex-col gap-1.5 rounded-md bg-page px-3 py-2">
          <div className="flex items-center justify-between gap-2">
            <span className="text-[14px] font-semibold">Autonomi terbatas</span>
            <Btn
              variant="secondary"
              onClick={() => setPlcAutonomy(!autonomy.enabled)}
              className={cn("px-3 text-[14px]", autonomy.enabled && "border-warn text-warn")}
            >
              {autonomy.enabled ? "Aktif · matikan" : "Mati · aktifkan"}
            </Btn>
          </div>
          <p className="text-[13px] text-muted-ink">
            Hanya aksi pra-disetujui di file batas (turunkan feed override ke 50% saat motor spindel panas), hanya ke arah aman, dan
            hanya jika semua pemeriksaan lolos. Setiap aksi tercatat di audit ledger.
          </p>
          {simulated && target && (
            <Btn variant="secondary" onClick={() => setPlcLockout(target.machine_id, !target.lockout)} className="self-start px-4 text-[14px]">
              {target.lockout ? `Lepas lockout ${target.machine_id}` : `Uji: pasang lockout ${target.machine_id}`}
            </Btn>
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
        {p.unit} {fmtEta(p.eta_warning_s)}, batas kritis {p.critical}
        {p.unit} {fmtEta(p.eta_critical_s)}.
      </p>
      {p.skill?.has_know_how && (
        <p className="text-muted-ink">
          Skill <span className="font-semibold text-navy">{p.skill.name}</span> siap
          {p.skill.root_causes[0] ? `; penyebab paling sering: ${p.skill.root_causes[0].cause.toLowerCase()}` : ""}.
        </p>
      )}
      <ProposedAction machineId={p.machine_id} />
      <Btn variant="secondary" onClick={() => planMaintenance(p.machine_id)} disabled={phase === "detecting"}>
        Jadwalkan perawatan sebelum rusak
      </Btn>
    </div>
  );
}

/** The alarm a machine just raised, with how it was fixed last time (from the maintenance ledger). */
function PlcAlarmCard() {
  const { plcAlarm, muted } = useCockpit();
  const canSound = useSyncExternalStore(onAudioReady, audioReady);
  if (!plcAlarm) return null;
  if (plcAlarm.skill?.has_know_how) return <CopilotCard skill={plcAlarm.skill} />;
  const fix = plcAlarm.past_fixes[0];
  const doc = plcAlarm.documents[0];
  return (
    <div className="flex flex-col gap-1.5 rounded-md border border-warn bg-page px-3 py-2 text-[14px]">
      <p className="font-semibold">
        <Mono>{plcAlarm.machine_id} · {plcAlarm.code}</Mono> {plcAlarm.text}
      </p>
      {!canSound && !muted && (
        <Btn variant="secondary" className="self-start px-4 text-[14px]" onClick={playAlarmNow}>
          <Volume2 className="size-5" aria-hidden />
          Ketuk untuk menyalakan suara alarm
        </Btn>
      )}
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

/** Production copilot: the matched skill walks the alarm through issue → root cause → tasks → action. */
function CopilotCard({ skill }: { skill: SkillGuidance }) {
  const { plcAlarm, setSkillStudioOpen } = useCockpit();
  if (!plcAlarm) return null;
  const [top, ...rest] = skill.root_causes;
  const doc = plcAlarm.documents[0];
  return (
    <section aria-label="Production copilot" className="flex flex-col gap-3 rounded-md border border-warn bg-page px-3 py-3 text-[14px]">
      <div className="flex flex-wrap items-baseline justify-between gap-2">
        <p className="font-semibold">
          <Mono>{plcAlarm.machine_id} · {plcAlarm.code}</Mono> {plcAlarm.text}
        </p>
        <button
          type="button"
          onClick={() => setSkillStudioOpen(true)}
          className="min-h-10 rounded-md text-left text-muted-ink underline-offset-2 hover:underline"
        >
          Skill: <span className="font-semibold text-navy">{skill.name}</span>
          {skill.version ? ` v${skill.version}` : ""}
          {skill.owner ? ` · ${skill.owner}` : ""}
          {skill.status === "trial" && <span className="ml-2 font-semibold text-maint">Uji lapangan · saran saja</span>}
        </button>
      </div>
      <div className="grid gap-3 md:grid-cols-3">
        <div className="flex flex-col gap-1.5 rounded-md border border-line bg-surface p-3">
          <h4 className="text-[13px] font-semibold tracking-wide text-muted-ink uppercase">1 · Masalah</h4>
          <ul className="flex flex-col gap-1">
            {skill.confirm.map((c) => (
              <li key={c.text} className="flex gap-2">
                <span className={cn("shrink-0 rounded px-1.5 font-mono text-[12px]", c.kind === "auto" ? "bg-page text-accent-blue" : "bg-warn-bg text-maint")}>
                  {c.kind === "auto" ? "otomatis" : "operator"}
                </span>
                <span>{c.text}</span>
              </li>
            ))}
          </ul>
        </div>
        <div className="flex flex-col gap-1.5 rounded-md border border-line bg-surface p-3">
          <h4 className="text-[13px] font-semibold tracking-wide text-muted-ink uppercase">2 · Akar penyebab</h4>
          {top ? (
            <>
              <p className="font-semibold">{top.cause}</p>
              {top.evidence_found && <p className="font-semibold text-running">Paling mungkin: kasus yang sama ditemukan untuk alarm ini</p>}
              {top.evidence && <p className="text-muted-ink">Bukti: {top.evidence}</p>}
              <SourceChips ids={top.sources} />
              {rest.map((c) => (
                <div key={c.cause} className="border-t border-line pt-1.5">
                  <p>{c.cause}</p>
                  <SourceChips ids={c.sources} />
                </div>
              ))}
            </>
          ) : (
            <p className="text-muted-ink">Skill ini belum punya akar penyebab.</p>
          )}
        </div>
        <div className="flex flex-col gap-1.5 rounded-md border border-line bg-surface p-3">
          <h4 className="text-[13px] font-semibold tracking-wide text-muted-ink uppercase">3 · Tugas mekanik</h4>
          <ol className="list-decimal pl-5">
            {skill.tasks.slice(0, 5).map((t) => <li key={t}>{t}</li>)}
          </ol>
        </div>
      </div>
      {doc?.citation && <p className="text-muted-ink">Referensi: {doc.citation}</p>}
      <div>
        <h4 className="mb-1 text-[13px] font-semibold tracking-wide text-muted-ink uppercase">4 · Aksi di mesin</h4>
        <ProposedAction machineId={plcAlarm.machine_id} />
        {!plcAlarm.proposed_action && <p className="text-muted-ink">Tidak ada aksi PLC yang menunggu persetujuan.</p>}
      </div>
    </section>
  );
}

function SourceChips({ ids }: { ids: string[] }) {
  return (
    <span className="flex flex-wrap gap-1.5">
      {ids.map((id) => (
        <Mono key={id} className="rounded border border-line px-1.5 py-0.5 text-[12px]">{id}</Mono>
      ))}
    </span>
  );
}

/** What the last write did: the outcome, a one-line check summary (every check on demand), and the ledger hash. */
function PlcActionReceipt() {
  const { plcAction: a } = useCockpit();
  if (!a) return null;
  return <ActionReceipt key={a.receipt_id} a={a} />;
}

function ActionReceipt({ a }: { a: PlcActionResult }) {
  const failed = a.checks.filter((c) => !c.ok);
  // A refused write opens on its own, so the operator sees which check stopped it.
  const [open, setOpen] = useState(failed.length > 0);
  return (
    <div className={cn("flex flex-col gap-1.5 rounded-md border px-3 py-2 text-[14px]", a.ok ? "border-ok-line bg-ok-bg" : "border-danger-line bg-danger-bg")}>
      <p className={cn("font-semibold", !a.ok && "text-fault")}>
        {OUTCOME_LABEL[a.outcome] ?? (a.ok ? OUTCOME_LABEL.applied : OUTCOME_LABEL.blocked)} · <Mono>{a.machine_id}</Mono> {a.tag}{" "}
        {a.before ?? "?"} → {a.after ?? a.value}
      </p>
      <p className="text-muted-ink">
        {a.autonomous ? "Autonomi terbatas" : `Disetujui ${a.approved_by.replace("Production Supervisor — ", "")}`} · ledger{" "}
        <Mono>{shortHash(a.sha256_hash)}</Mono>
      </p>
      <button
        type="button"
        onClick={() => setOpen(!open)}
        aria-expanded={open}
        className="flex min-h-10 items-center gap-2 self-start rounded-md text-left font-semibold underline-offset-2 hover:underline"
      >
        <span className={cn(failed.length > 0 && "text-fault")}>
          {a.checks.length - failed.length}/{a.checks.length} pemeriksaan lolos
          {failed.length > 0 && ` · gagal: ${failed.map((c) => c.label.toLowerCase()).join(", ")}`}
        </span>
        <span className="text-muted-ink">· {open ? "sembunyikan" : "lihat detail"}</span>
      </button>
      {open && (
        <ul className="flex flex-col gap-0.5">
          {a.checks.map((c) => (
            <li key={c.id} className={cn("flex gap-2", !c.ok && "font-semibold text-fault")}>
              <span aria-hidden>{c.ok ? "✓" : "✗"}</span>
              <span className="sr-only">{c.ok ? "lulus:" : "gagal:"}</span>
              <span>
                {c.label} <span className="text-muted-ink">· {c.detail}</span>
              </span>
            </li>
          ))}
        </ul>
      )}
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
