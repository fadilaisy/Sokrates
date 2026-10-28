import { ArrowRight } from "lucide-react";
import type { ReactNode } from "react";
import { API_BASE, isOvertimeLabel, parseHour } from "../lib/api";
import type { LedgerEntry, SapState } from "../lib/api";
import { cn } from "../lib/utils";
import { fmt1, ordersToday, STATUS_STYLE, telemetryOf } from "../lib/shift";
import type { MachineStatus } from "../lib/shift";
import { AgentArea } from "./AgentArea";
import { AgentRunPanel } from "./AgentRun";
import { ApprovalModal, ChatDrawer, LedgerBar, LedgerDrawer } from "./Overlays";
import { MaintenancePage } from "./MaintenancePage";
import { MachineStrip, PlcPanel, TelemetryPanel, WhatIfPanel } from "./Panels";
import { Header } from "./Shell";
import { ShiftGantt } from "./ShiftGantt";
import { useCockpit } from "./store";
import { Btn, Card, Mono, StatusBadge } from "./ui";

/*
 * Downtime and OEE come from GET /api/metrics (backend/metrics.py): maintenance slots,
 * machine status and supervisor-approved disruptions for availability, feed-rate telemetry
 * for performance. Quality is the one assumed figure (the mock has no scrap data).
 */

/** Hours → "1J 12M" / "30M" / "0M". */
function fmtDuration(hours: number): string {
  const total = Math.round(hours * 60);
  const h = Math.floor(total / 60);
  const m = total % 60;
  return h ? `${h}J ${String(m).padStart(2, "0")}M` : `${m}M`;
}

/** 25 → "25,0%" (Indonesian decimal comma). */
function fmtPct(v: number): string {
  return `${v.toFixed(1).replace(".", ",")}%`;
}

function Donut({ pct, label }: { pct: number; label: string }) {
  const r = 46;
  const c = 2 * Math.PI * r;
  return (
    <svg viewBox="0 0 110 110" className="size-[110px] shrink-0" role="img" aria-label={label}>
      <circle cx="55" cy="55" r={r} fill="none" stroke="var(--sf-disabled)" strokeWidth="14" />
      <circle
        cx="55"
        cy="55"
        r={r}
        fill="none"
        stroke="var(--sf-navy)"
        strokeWidth="14"
        strokeDasharray={`${(c * Math.min(pct, 100)) / 100} ${c}`}
        transform="rotate(-90 55 55)"
      />
      <text x="55" y="62" textAnchor="middle" fontSize="22" fontWeight="700" fill="var(--sf-navy)">
        {label}
      </text>
    </svg>
  );
}

function OrderCount({ label, value, tone, highlight = false }: { label: string; value: number; tone: string; highlight?: boolean }) {
  return (
    <div className={cn("flex flex-col-reverse gap-1 rounded-lg border bg-white p-3", highlight ? "border-2 border-maint bg-warn-bg" : "border-line")}>
      <dt className={cn("text-[14px] font-semibold", highlight && "text-maint")}>{label}</dt>
      <dd className={cn("text-[34px] font-bold leading-none", tone)}>{value}</dd>
    </div>
  );
}

interface MovedOrder {
  id: string;
  from: string | null;
  to: string;
  start: string;
  overtime: boolean;
  approvedBy: string | null;
}

/**
 * Today's RESCHEDULED orders, newest approval first. Where each one came from and who
 * approved it come from the SCENARIO_APPROVED ledger entry that moved it (its order snapshots).
 */
function rescheduledOrders(state: SapState | null, ledger: LedgerEntry[]): MovedOrder[] {
  const shiftEnd = parseHour(state?.shop_floor_config?.shift_end ?? "15:00") ?? 15;
  const approvals = ledger.filter((e) => e.action_type === "SCENARIO_APPROVED").reverse();
  return ordersToday(state)
    .filter((o) => o.status === "RESCHEDULED")
    .map((o) => {
      const entry = approvals.find((e) => {
        const snaps = (e.delta_applied as { order_snapshots?: Record<string, unknown> } | undefined)?.order_snapshots;
        return !!snaps && o.id in snaps;
      });
      const snap = ((entry?.delta_applied as { order_snapshots?: Record<string, { work_center_id?: string }> } | undefined)
        ?.order_snapshots ?? {})[o.id];
      const startH = parseHour(o.planned_start);
      const endH = parseHour(o.planned_end);
      return {
        id: o.id,
        from: snap?.work_center_id ?? null,
        to: o.work_center_id,
        start: startH == null ? "?" : `${String(Math.floor(startH)).padStart(2, "0")}:${String(Math.round((startH % 1) * 60)).padStart(2, "0")}`,
        overtime: isOvertimeLabel(o.planned_start) || isOvertimeLabel(o.planned_end) || (endH ?? 0) > shiftEnd || (startH ?? 0) >= shiftEnd,
        approvedBy: entry?.approved_by ?? null,
      };
    });
}

function Pill({ children, tone = "neutral" }: { children: ReactNode; tone?: "neutral" | "ok" | "warn" }) {
  const t = { neutral: "bg-[#8ca2c0] text-navy", ok: "bg-running text-white", warn: "bg-maint text-white" }[tone];
  return <span className={cn("inline-flex min-w-14 justify-center rounded-full px-2.5 py-0.5 text-[14px] font-bold", t)}>{children}</span>;
}

function DashboardPage() {
  const { state, metrics, ledger, displayStatus, setPage } = useCockpit();
  const machines = state?.work_centers ?? [];
  const bucket = (s: MachineStatus): MachineStatus => (s === "DISRUPTED" ? "FAULT" : s === "RECOVERING" ? "MAINTENANCE" : s);
  const counts: Record<string, number> = { RUNNING: 0, FAULT: 0, IDLE: 0, MAINTENANCE: 0 };
  for (const w of machines) counts[bucket(displayStatus(w.id, w.status))]++;

  const today = ordersToday(state);
  const running = today.filter((o) => o.status === "IN_PROGRESS").length;
  const queued = today.filter((o) => o.status === "QUEUED" || o.status === "PLANNED").length;
  const moved = rescheduledOrders(state, ledger);
  const dt = metrics?.downtime ?? null;
  const ox = metrics?.oee ?? null;
  const oee = ox ? Math.round(ox.oee) : 0;

  return (
    <>
      <Header />
      <Card className="flex flex-col gap-4 p-5">
        <div className="flex flex-wrap items-center justify-between gap-3">
          <div>
            <h1 className="text-[24px] font-bold">Status Mesin</h1>
            <p className="text-[16px]">{machines.length} mesin · Shift 1 · status saat ini</p>
          </div>
          <Btn variant="secondary" onClick={() => setPage("cockpit")}>
            Buka cockpit <ArrowRight className="size-5" aria-hidden />
          </Btn>
        </div>
        <div className="grid grid-cols-2 gap-4 lg:grid-cols-4">
          {(["RUNNING", "FAULT", "IDLE", "MAINTENANCE"] as const).map((k) => {
            const s = STATUS_STYLE[k];
            const zeroFault = k === "FAULT" && counts[k] === 0;
            return (
              <button
                key={k}
                type="button"
                onClick={() => setPage("cockpit")}
                className={cn("flex min-h-[120px] flex-col justify-between rounded-lg p-4 text-left sm:min-h-[132px] sm:p-5 transition-transform hover:-translate-y-0.5", zeroFault && "border-[3px] border-fault bg-danger-bg")}
                style={zeroFault ? undefined : { background: s.bg, color: s.fg }}
              >
                <span className={cn("text-[15px] font-bold tracking-tight sm:text-[24px] sm:tracking-normal", zeroFault && "text-fault")}>{s.label}</span>
                <span className={cn("text-[52px] leading-none font-bold", zeroFault && "text-fault")}>{counts[k]}</span>
              </button>
            );
          })}
        </div>
      </Card>

      <Card className="flex flex-col gap-4 p-5">
        <h2 className="text-[24px] font-bold">Jadwal produksi · Shift 1</h2>
        <ShiftGantt state={state} note={`${today.length} order di ${new Set(today.map((o) => o.work_center_id)).size} mesin hari ini`} />
      </Card>

      <div className="grid gap-4 lg:grid-cols-3">
        <Card className="flex flex-col gap-3 bg-page p-4">
          <h3 className="border-b border-accent-blue/40 pb-2 text-[17px] font-semibold">
            Order hari ini{" "}
            <span className="text-[14px] font-normal text-muted-ink">
              · {today.length} order{state?.shop_floor_config ? ` · shift ${state.shop_floor_config.shift_start ?? "07:00"}–${state.shop_floor_config.shift_end ?? "15:00"}` : ""}
            </span>
          </h3>
          <dl className="grid grid-cols-3 gap-2">
            <OrderCount label="Berjalan" value={running} tone="text-running" />
            <OrderCount label="Antri" value={queued} tone="text-idle" />
            <OrderCount label="Dijadwal ulang" value={moved.length} tone={moved.length > 0 ? "text-maint" : "text-muted-ink"} highlight={moved.length > 0} />
          </dl>
          {moved.length === 0 ? (
            <p className="text-[14px] text-muted-ink">Belum ada order yang dipindah agen hari ini.</p>
          ) : (
            <div className="flex flex-col gap-1.5 text-[14px]">
              <p className="font-semibold text-muted-ink">
                Dipindah agen{moved[0].approvedBy ? ` · disetujui ${moved[0].approvedBy}` : ""}
              </p>
              <ul className="flex flex-col gap-1">
                {moved.map((m) => (
                  <li key={m.id}>
                    <Mono>{m.id}</Mono> {m.from && m.from !== m.to ? `${m.from} → ` : ""}{m.to} · {m.start}
                    {m.overtime && <span className="font-semibold text-maint"> lembur</span>}
                  </li>
                ))}
              </ul>
            </div>
          )}
        </Card>

        <Card className="flex flex-col gap-3 bg-page p-4">
          <h3 className="border-b border-accent-blue/40 pb-2 text-[17px] font-semibold">
            Downtime{" "}
            <span className="text-[14px] font-normal text-muted-ink">
              · {metrics ? `${metrics.shift.machines} mesin × ${metrics.shift.hours_per_machine} jam` : "memuat…"}
            </span>
          </h3>
          {dt ? (
            <>
              <p className="flex items-center gap-2 text-[16px]">
                {fmtDuration(dt.total_hours)} <Pill>{fmtPct(dt.percent_of_shift)}</Pill>
              </p>
              <ul className="flex flex-col gap-3">
                {dt.by_category.map((c) => {
                  const share = dt.total_hours > 0 ? (c.hours / dt.total_hours) * 100 : 0;
                  const machines = dt.by_machine
                    .filter((m) => (c.key === "changeover" ? m.changeover_hours : c.key === "maintenance" ? m.maintenance_hours : m.unplanned_hours) > 0)
                    .map((m) => m.machine_id);
                  return (
                    <li key={c.key} className="flex flex-col gap-1 text-[15px]" title={machines.join(", ") || undefined}>
                      <span className="flex justify-between">
                        {c.label} <span>{fmtDuration(c.hours)}</span>
                      </span>
                      <span className="h-3.5 rounded-sm bg-[#9cb0c9]" style={{ width: `${share}%` }} />
                      {machines.length > 0 && <span className="text-[13px] text-muted-ink">{machines.join(", ")}</span>}
                    </li>
                  );
                })}
              </ul>
            </>
          ) : (
            <p className="text-[15px] text-muted-ink">Data downtime belum tersedia dari backend.</p>
          )}
        </Card>

        <Card className="flex flex-col gap-3 bg-page p-4">
          <h3 className="flex items-center justify-between border-b border-accent-blue/40 pb-2 text-[17px] font-semibold">
            <span>
              OEE <span className="text-[14px] font-normal text-muted-ink">· shift {metrics ? `${metrics.shift.start}–${metrics.shift.end}` : "memuat…"}</span>
            </span>
          </h3>
          {ox ? (
            <>
              <p className="text-[16px]">Target OEE {ox.target}%</p>
              <div className="flex items-center gap-4">
                <Donut pct={oee} label={`${oee}%`} />
                <dl className="grid flex-1 grid-cols-[1fr_auto] items-center gap-y-2 text-[15px]">
                  {(
                    [
                      ["Ketersediaan", ox.availability, "dari status mesin & jadwal perawatan"],
                      ["Performa", ox.performance, "rata-rata feed rate mesin RUNNING"],
                      [ox.sources.quality === "assumption" ? "Kualitas (asumsi)" : "Kualitas", ox.quality, "belum ada data scrap di SAP"],
                    ] as const
                  ).map(([l, v, hint]) => (
                    <div key={l} className="contents">
                      <dt title={hint}>{l}</dt>
                      <dd>
                        <Pill tone={v >= ox.target ? "ok" : "warn"}>{Math.round(v)}%</Pill>
                      </dd>
                    </div>
                  ))}
                </dl>
              </div>
            </>
          ) : (
            <p className="text-[15px] text-muted-ink">Data OEE belum tersedia dari backend.</p>
          )}
        </Card>
      </div>
    </>
  );
}

function CockpitPage() {
  const { state, phase, params, result } = useCockpit();
  const best = result?.scenarios.find((s) => s.recommended);
  const showProposal = phase === "proposing" && best;
  const note =
    phase === "proposing" && best
      ? `Garis putus-putus biru = usulan ${best.name_id} (belum disetujui)`
      : phase === "resolved"
        ? "Jadwal sudah diperbarui di SAP."
        : "Jadwal sehat, tanpa gangguan";
  return (
    <>
      <Header />
      <MachineStrip />
      <div className="grid gap-4 xl:grid-cols-[minmax(0,1fr)_360px]">
        <Card className="flex min-w-0 flex-col gap-2 p-4">
          <h2 className="text-[17px] font-semibold">Gantt · jadwal shift 1</h2>
          <ShiftGantt
            state={state}
            disruption={phase === "detecting" || phase === "proposing" || phase === "resolved" ? params : null}
            changes={showProposal ? best.gantt_changes : undefined}
            note={note}
          />
        </Card>
        <div className="flex flex-col gap-4">
          <TelemetryPanel />
          <WhatIfPanel />
        </div>
      </div>
      <PlcPanel />
      <AgentRunPanel />
      <AgentArea />
      <LedgerBar />
      <ApprovalModal />
      <LedgerDrawer />
    </>
  );
}

function FloorCell({ id }: { id: string }) {
  const { state, displayStatus, live } = useCockpit();
  const w = state?.work_centers.find((x) => x.id === id);
  if (!w) return <div className="rounded-md border border-dashed border-line p-4 text-muted-ink">{id}</div>;
  const st = displayStatus(w.id, w.status);
  const { temp } = telemetryOf(w, live[w.id]);
  const running = st === "RUNNING";
  const standby = st === "IDLE" || st === "MAINTENANCE";
  return (
    <div className="grid place-items-center rounded-md bg-[repeating-linear-gradient(135deg,transparent_0_8px,rgba(239,68,68,.08)_8px_16px)] p-4" title="Area bahaya / jaga jarak 1,5 m">
      <div className={cn("flex w-full max-w-[240px] flex-col gap-2 rounded-md border-2 bg-surface p-3", st === "FAULT" ? "sf-pulse border-danger-line" : running ? "border-ok-line" : "border-line")}>
        <div className="flex items-baseline justify-between">
          <span className="font-mono text-[20px] font-bold">{w.id}</span>
          <span className="font-mono text-[18px]">{standby ? "SIAGA" : `${fmt1(temp)}°C`}</span>
        </div>
        <StatusBadge status={st} />
      </div>
    </div>
  );
}

function Buffer({ title, sub }: { title: string; sub: string }) {
  return (
    <div className="grid place-items-center p-4">
      <div className="w-full max-w-[180px] rounded-md border-4 border-dashed border-[#e0b13c] bg-surface p-3">
        <p className="text-[17px] font-bold">{title}</p>
        <p className="text-[14px]">{sub}</p>
      </div>
    </div>
  );
}

function FloorPage() {
  const { state, displayStatus } = useCockpit();
  const machines = state?.work_centers ?? [];
  const tally = (k: MachineStatus) => machines.filter((w) => displayStatus(w.id, w.status) === k).length;
  const critical = tally("FAULT");
  return (
    <>
      <Header />
      <Card className="flex flex-col gap-4 p-5">
        <div className="flex flex-wrap items-end justify-between gap-3">
          <div>
            <Mono className="text-[14px] text-muted-ink">PLANT · MACHINING · LEVEL 01</Mono>
            <h1 className="text-[26px] font-bold">Lantai pabrik / Sel A</h1>
          </div>
          <div className="flex gap-3 text-[15px]">
            <span className="flex items-center gap-2">
              <span className="size-2.5 rounded-full bg-ok-line" /> TELEMETRI LIVE
            </span>
            <span className={cn("rounded-md px-2.5 py-1 font-mono", critical ? "bg-danger-bg text-fault" : "bg-page")}>
              {String(critical).padStart(2, "0")} KRITIS
            </span>
          </div>
        </div>
        <div className="flex flex-col gap-0 rounded-md border border-line bg-[linear-gradient(var(--sf-line)_1px,transparent_1px),linear-gradient(90deg,var(--sf-line)_1px,transparent_1px)] bg-[size:24px_24px]">
          <p className="px-4 pt-3 font-mono text-[14px]">SEL A / SISI UTARA</p>
          <div className="grid grid-cols-2 md:grid-cols-4">
            <Buffer title="Masuk" sub="Buffer WIP · bahan baku" />
            <FloorCell id="CNC-01" />
            <FloorCell id="CNC-02" />
            <FloorCell id="CNC-03" />
          </div>
          <div className="mx-4 my-2 flex min-h-14 items-center justify-center rounded-sm border-y border-line bg-page/90 font-mono text-[15px] tracking-widest">
            LORONG UTAMA · 2,5 m
          </div>
          <div className="grid grid-cols-2 md:grid-cols-4">
            <FloorCell id="CNC-04" />
            <FloorCell id="CNC-05" />
            <Buffer title="Keluar" sub="Buffer WIP · barang jadi" />
            <div />
          </div>
          <p className="px-4 pb-3 font-mono text-[14px]">SEL A / SISI SELATAN</p>
        </div>
        <ul className="flex flex-wrap gap-5 text-[15px]">
          {(["RUNNING", "FAULT", "IDLE", "MAINTENANCE"] as const).map((k) => (
            <li key={k} className="flex items-center gap-2">
              <span className="size-3 rounded-sm" style={{ background: STATUS_STYLE[k].bg }} /> {k} · {tally(k)}
            </li>
          ))}
          <li className="flex items-center gap-2">
            <span className="size-3 rounded-sm bg-[repeating-linear-gradient(135deg,transparent_0_2px,rgba(239,68,68,.5)_2px_4px)]" /> Area bahaya / jaga jarak
          </li>
        </ul>
      </Card>
    </>
  );
}

export function CurrentPage() {
  const { page, error, online } = useCockpit();
  return (
    <>
      {!online && error && (
        <p role="alert" className="rounded-lg border border-danger-line bg-danger-bg px-4 py-3 text-[16px] text-fault">
          Backend tidak terjangkau di <Mono>{API_BASE}</Mono>.{" "}
          {API_BASE.includes("localhost")
            ? <>Jalankan <Mono>python3 -m uvicorn backend.main:app --port 8000</Mono> lalu muat ulang halaman.</>
            : <>Periksa bahwa backend menyala dan mengizinkan origin ini lewat <Mono>CORS_ORIGINS</Mono>.</>}
        </p>
      )}
      {page === "dashboard" ? <DashboardPage /> : page === "cockpit" ? <CockpitPage /> : page === "maintenance" ? <MaintenancePage /> : <FloorPage />}
      <ChatDrawer />
    </>
  );
}
