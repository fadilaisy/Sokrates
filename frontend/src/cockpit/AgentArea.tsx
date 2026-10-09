import { ChevronDown, TriangleAlert } from "lucide-react";
import { useState } from "react";
import type { ReactNode } from "react";
import React from "react";
import { formatIDR } from "../lib/api";
import type { CostBreakdown, Scenario } from "../lib/api";
import { cn } from "../lib/utils";
import { affectedOrders, DISRUPTION_TYPES, fmt1, shortHash, windowLabel } from "../lib/shift";
import { useCockpit } from "./store";
import { Btn, Card, Mono, Spinner, StateBanner } from "./ui";

const WINDOWS: [number, number][] = [
  [0, 3],
  [2, 5],
  [4, 7],
  [5, 8],
  [0, 8],
];

function Select({ label, value, onChange, children }: { label: string; value: string; onChange: (v: string) => void; children: ReactNode }) {
  return (
    <label className="flex flex-1 flex-col gap-1.5">
      <span className="text-[15px] font-medium text-muted-ink">{label}</span>
      <span className="relative">
        <select
          value={value}
          onChange={(e) => onChange(e.target.value)}
          className="min-h-14 w-full appearance-none rounded-md border border-line bg-surface pr-10 pl-4 text-[18px] font-semibold text-navy"
        >
          {children}
        </select>
        <ChevronDown className="pointer-events-none absolute top-1/2 right-3 size-5 -translate-y-1/2" aria-hidden />
      </span>
    </label>
  );
}

function FreePlay() {
  const { state, runDisruption } = useCockpit();
  const machines = state?.work_centers ?? [];
  const [machine, setMachine] = useState("CNC-01");
  const [dtype, setDtype] = useState("BREAKDOWN");
  const [win, setWin] = useState("2-5");
  const [sh, eh] = win.split("-").map(Number);
  const hit = affectedOrders(state, machine, sh, eh);
  const none = state != null && hit.length === 0;
  return (
    <>
      <StateBanner tone="ok" label="NORMAL" text="Lantai produksi sehat. Tidak ada gangguan." />
      <div className="flex flex-col gap-3">
        <div>
          <h2 className="text-[20px] font-semibold">Mode bebas · Simulasi Gangguan</h2>
          <p className="text-[15px] text-muted-ink">Pilih mesin, jenis gangguan, dan jendela waktu. Semua langkah sebelum persetujuan hanya analisis.</p>
        </div>
        <div className="flex flex-col gap-4 md:flex-row md:items-end">
          <Select label="Mesin" value={machine} onChange={setMachine}>
            {machines.map((w) => (
              <option key={w.id} value={w.id}>
                {w.id}
              </option>
            ))}
          </Select>
          <Select label="Jenis gangguan" value={dtype} onChange={setDtype}>
            {DISRUPTION_TYPES.map((d) => (
              <option key={d.value} value={d.value}>
                {d.label}
              </option>
            ))}
          </Select>
          <Select label="Jendela waktu" value={win} onChange={setWin}>
            {WINDOWS.map(([a, b]) => (
              <option key={`${a}-${b}`} value={`${a}-${b}`}>
                {windowLabel(a, b)}
              </option>
            ))}
          </Select>
          <Btn size="lg" className="md:w-[280px]" disabled={none} onClick={() => runDisruption({ machine_id: machine, disruption_type: dtype, start_hour: sh, end_hour: eh })}>
            Simulasi Gangguan
          </Btn>
        </div>
        {none ? (
          <p className="flex items-center gap-2.5 rounded-md border border-warn bg-warn-bg px-4 py-3 text-[16px] font-semibold text-warn" role="status">
            <TriangleAlert className="size-5 shrink-0" aria-hidden />
            {machine} tidak punya order di {windowLabel(sh, eh)}, jadi simulasi tidak mengubah jadwal. Pilih mesin atau jam lain.
          </p>
        ) : (
          <Mono className="text-[15px] text-muted-ink">
            {hit.length} order terdampak: {hit.map((o) => o.id).join(", ")}
          </Mono>
        )}
      </div>
    </>
  );
}

const STEP_LABELS = ["Memuat skill cnc_milling", "Menjalankan CP-SAT solver", "Menyusun 3 skenario + ringkasan Gemini"];

function Detecting() {
  const { params, detectStep } = useCockpit();
  if (!params) return null;
  const steps = STEP_LABELS.map((label, i) => {
    const state = i < detectStep ? "done" : i === detectStep ? "active" : "pending";
    return { state, label, meta: state === "done" ? "selesai" : state === "active" ? "berjalan…" : "menunggu" };
  });
  return (
    <>
      <StateBanner
        tone="warn"
        label="DETECTING"
        text={`Gangguan terdeteksi pada ${params.machine_id} (${params.disruption_type}, ${params.end_hour - params.start_hour} jam). AI Supervisor sedang menganalisis…`}
      />
      <ol className="flex flex-col gap-2">
        {steps.map((s) => (
          <li key={s.label} className="flex min-h-14 items-center gap-4 rounded-md bg-page px-4">
            {s.state === "done" ? (
              <span className="grid size-6 place-items-center rounded-full bg-running text-[14px] text-white" aria-hidden>
                ✓
              </span>
            ) : s.state === "active" ? (
              <Spinner />
            ) : (
              <span className="size-6 rounded-full border-2 border-[#94a3b8]" aria-hidden />
            )}
            <span className={cn("flex-1 text-[18px] font-semibold", s.state === "pending" && "text-muted-ink")}>{s.label}</span>
            <Mono className="text-[14px] text-muted-ink">{s.meta}</Mono>
          </li>
        ))}
      </ol>
      <p className="text-[15px] text-muted-ink">Analisis hanya-baca. Tidak ada perubahan ke SAP sebelum Anda menyetujui skenario.</p>
    </>
  );
}

const LETTER: Record<string, string> = { scenario_a: "A", scenario_b: "B", scenario_c: "C" };

function sums(s: Scenario) {
  const penalty = s.gantt_changes.reduce((a, c) => a + (c.sla_penalty_idr ?? 0), 0);
  return { penalty };
}

function ScenarioCard({ s }: { s: Scenario }) {
  const { openApproval } = useCockpit();
  const { penalty } = sums(s);
  const empty = s.gantt_changes.length === 0;
  return (
    <Card className={cn("flex flex-col gap-3 p-4", s.recommended && "border-2 border-accent-blue")}>
      <div className="flex flex-wrap items-center gap-2">
        <h3 className="text-[18px] font-semibold">
          {LETTER[s.id] ?? "?"} · {s.name_id}
        </h3>
        {s.recommended && <span className="rounded border border-navy px-2 py-0.5 text-[14px] font-semibold">Rekomendasi</span>}
      </div>
      <dl className="grid grid-cols-[1fr_auto] gap-y-1 text-[15px]">
        <dt>Biaya total</dt>
        <dd className="text-right font-mono font-semibold">{formatIDR(s.total_cost_idr)}</dd>
        <dt>Penalti SLA</dt>
        <dd className="text-right font-mono">{formatIDR(penalty)}</dd>
        <dt>Net hemat</dt>
        <dd className={cn("text-right font-mono font-semibold",
          s.net_savings_idr > 0 && "text-running",
          s.net_savings_idr < 0 && "text-danger"
        )}>
          {s.net_savings_idr < 0 ? "-" : "+"} {formatIDR(Math.abs(s.net_savings_idr))}
        </dd>
      </dl>
      {s.cost_breakdown && (
        <div className="mt-2 rounded bg-page px-3 py-2">
          <dl className="grid grid-cols-[1fr_1fr] gap-x-3 gap-y-0.5 text-[13px]">
            {(Object.entries(s.cost_breakdown) as [keyof CostBreakdown, number][]).map(([k, v]) => (
              <React.Fragment key={k}>
                <dt className="text-muted-ink capitalize">{k.replace(/_/g, " ")}</dt>
                <dd className="text-right font-mono">{formatIDR(v)}</dd>
              </React.Fragment>
            ))}
          </dl>
        </div>
      )}
      <p className="flex-1 text-[15px]">{s.rationale_template}</p>
      <Btn size="lg" variant={s.recommended ? "primary" : "secondary"} disabled={empty} onClick={() => openApproval(s)}>
        Setujui skenario ini
      </Btn>
    </Card>
  );
}

function Proposing() {
  const { result } = useCockpit();
  if (!result) return null;
  const best = result.scenarios.find((s) => s.recommended);
  const noImpact = result.scenarios.every((s) => s.gantt_changes.length === 0);
  return (
    <>
      <StateBanner
        tone="neutral"
        label="PROPOSING"
        text={noImpact ? "Tidak ada order terdampak. Tidak perlu tindakan." : `3 skenario siap. Rekomendasi: ${best?.name_id ?? "-"}.`}
      />
      {result.claude_summary && (
        <div className="rounded-md bg-page px-4 py-3">
          <p className="text-[14px] font-semibold text-muted-ink">Ringkasan AI (Gemini)</p>
          <p className="text-[16px]">{result.claude_summary}</p>
        </div>
      )}
      <div className="grid gap-3 lg:grid-cols-3">
        {result.scenarios.map((s) => (
          <ScenarioCard key={s.id} s={s} />
        ))}
      </div>
    </>
  );
}

function Resolved() {
  const { receipt, setLedgerOpen, verify } = useCockpit();
  if (!receipt) return null;
  const moved = receipt.scenario.gantt_changes.filter((c) => c.from_machine !== c.to_machine);
  const { penalty } = sums(receipt.scenario);
  return (
    <>
      <StateBanner tone="ok" label="RESOLVED" text={`Skenario ${LETTER[receipt.scenario_id] ?? ""} · ${receipt.scenario.name_id} diterapkan. Jadwal dan audit ledger diperbarui.`} />
      <div className="grid gap-4 lg:grid-cols-[1fr_440px]">
        <div className="grid grid-cols-1 gap-4 rounded-lg bg-page p-5 sm:grid-cols-3">
          <Metric label="Biaya total" value={formatIDR(receipt.scenario.total_cost_idr)} />
          <Metric label="Penalti SLA" value={formatIDR(penalty)} />
          <Metric
            label="Order dipindah"
            value={moved.length ? moved.map((c) => `${c.order_id} → ${c.to_machine}`).join(", ") : "Tidak ada (jadwal digeser)"}
          />
        </div>
        <div className="flex flex-col gap-2 rounded-lg bg-page p-5">
          <p className="text-[16px] font-semibold">Audit receipt</p>
          <Mono className="text-[15px]">receipt_id&nbsp;&nbsp; {receipt.receipt_id.slice(0, 8)}</Mono>
          <Mono className="text-[15px]">
            sap_version {receipt.sap_version_before} → {receipt.sap_version_after}
          </Mono>
          <Mono className="text-[15px]">sha256&nbsp;&nbsp;&nbsp;&nbsp;&nbsp; {shortHash(receipt.sha256_hash)}</Mono>
          <Btn
            variant="secondary"
            onClick={() => {
              setLedgerOpen(true);
              verify();
            }}
          >
            Lihat audit ledger
          </Btn>
        </div>
      </div>
    </>
  );
}

function Metric({ label, value }: { label: string; value: string }) {
  return (
    <div className="flex flex-col gap-1">
      <span className="text-[15px] font-medium text-muted-ink">{label}</span>
      <span className="text-[26px] leading-tight font-bold">{value}</span>
    </div>
  );
}

function SafetyAlertView() {
  const { safety, runFullAnalysis, state, live } = useCockpit();
  if (!safety) return null;
  const w = state?.work_centers.find((x) => x.id === safety.machine_id);
  const t = { ...(w?.telemetry ?? {}), ...(live[safety.machine_id] ?? {}) };
  const temp = t.motor_temp_celsius ?? null;
  const vib = t.spindle_vibration_mm_per_s ?? null;
  const tempHot = (temp ?? 0) >= 95;
  const vibHot = (vib ?? 0) >= 8;
  const title = vibHot && !tempHot ? "GETARAN KRITIS" : "TELEMETRI KRITIS";
  return (
    <>
      <div className="flex flex-col gap-4 rounded-lg bg-fault px-5 py-4 text-white md:flex-row md:items-center">
        <TriangleAlert className="size-9 shrink-0" aria-hidden />
        <div className="flex-1">
          <Mono className="text-[14px]">
            {safety.rule} · {title}
          </Mono>
          <p className="text-[20px] font-bold">{safety.machine_id} melewati batas aman. Jaga jarak dan hentikan mesin sesuai SOP.</p>
        </div>
        <Btn variant="danger-inverse" size="lg" onClick={runFullAnalysis}>
          Jalankan analisis penuh
        </Btn>
      </div>
      <div className="grid gap-4 md:grid-cols-3">
        <Readout label={`Suhu motor · ${safety.machine_id}`} value={`${fmt1(temp)} °C`} limit="batas 95 °C" hot={tempHot} />
        <Readout label={`Vibrasi · ${safety.machine_id}`} value={`${fmt1(vib)} mm/s`} limit="batas 8,0 mm/s" hot={vibHot} />
        <div className="flex flex-col gap-1 rounded-lg border border-line bg-page px-5 py-4">
          <span className="text-[15px] text-muted-ink">AI Supervisor</span>
          <span className="text-[32px] font-bold">Siaga</span>
          <span className="text-[15px] text-muted-ink">{safety.message || "Tekan “Jalankan analisis penuh” untuk membuat 3 skenario."}</span>
        </div>
      </div>
    </>
  );
}

function Readout({ label, value, limit, hot }: { label: string; value: string; limit: string; hot: boolean }) {
  return (
    <div className={cn("flex flex-col gap-1 rounded-lg border px-5 py-4", hot ? "border-danger-line bg-danger-bg" : "border-line bg-page")}>
      <span className="text-[15px] text-muted-ink">{label}</span>
      <span className={cn("font-bold", hot ? "text-[40px] text-fault" : "text-[32px]")}>{value}</span>
      <span className="text-[15px] text-muted-ink">
        {limit}
        {hot ? "" : " · normal"}
      </span>
    </div>
  );
}

/** One fixed-size zone whose content changes with the agent's state (doc: "single main view + overlays"). */
export function AgentArea() {
  const { phase, safety } = useCockpit();
  const showSafety = safety && (phase === "normal" || phase === "resolved");
  return (
    <Card className={cn("flex min-h-[312px] flex-col gap-4 p-4", showSafety && "border-2 border-danger-line")}>
      <h2 className="sr-only">Area kerja AI Supervisor</h2>
      {showSafety ? (
        <SafetyAlertView />
      ) : phase === "detecting" ? (
        <Detecting />
      ) : phase === "proposing" ? (
        <Proposing />
      ) : phase === "resolved" ? (
        <Resolved />
      ) : (
        <FreePlay />
      )}
    </Card>
  );
}
