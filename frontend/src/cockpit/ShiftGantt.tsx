import { isOvertimeLabel, parseHour } from "../lib/api";
import type { GanttChange, SapState } from "../lib/api";
import { cn } from "../lib/utils";
import { maintenanceToday, NIGHT_END, ordersToday, SHIFT_END, SHIFT_START } from "../lib/shift";
import type { DisruptionParams } from "./store";

type BarKind = "order" | "rescheduled" | "maintenance" | "disruption" | "proposed" | "pending";

interface Bar {
  key: string;
  start: number;
  end: number;
  label: string;
  kind: BarKind;
  title: string;
}

const BAR_STYLE: Record<BarKind, string> = {
  order: "bg-[#d4e1f0] border border-accent-blue text-navy font-semibold",
  rescheduled: "bg-accent-blue border border-accent-blue text-white font-semibold",
  maintenance: "bg-warn-bg border border-warn text-warn",
  disruption: "bg-danger-bg border border-danger-line text-fault",
  proposed: "bg-surface border-2 border-dashed border-accent-blue text-navy",
  pending: "bg-surface border border-dashed border-disabled text-muted-ink",
};

// What each bar style means, for judges and visitors reading the chart cold.
const LEGEND: Record<BarKind, string> = {
  order: "Order terjadwal di SAP",
  rescheduled: "Order sudah dipindah (disetujui)",
  pending: "Posisi lama (menunggu)",
  proposed: "Usulan baru (belum disetujui)",
  disruption: "Jendela gangguan",
  maintenance: "Perawatan terjadwal",
};

/** "WO-2025-004" → "WO-004"; "Gearbox Housing Cover — Astra" → "Gearbox Housing Cover". */
const shortId = (id: string) => id.replace(/^WO-\d{4}-/, "WO-");
const shortProduct = (name?: string) => (name ?? "").split(" — ")[0];

function lanes(bars: Bar[]): Bar[][] {
  const sorted = [...bars].sort((a, b) => a.start - b.start);
  const out: Bar[][] = [];
  for (const b of sorted) {
    const lane = out.find((l) => l[l.length - 1].end <= b.start + 1e-6);
    if (lane) lane.push(b);
    else out.push([b]);
  }
  return out.length ? out : [[]];
}

export function ShiftGantt({
  state,
  disruption,
  changes,
  note,
}: {
  state: SapState | null;
  disruption?: DisruptionParams | null;
  changes?: GanttChange[];
  note?: string;
}) {
  const machines = state?.work_centers ?? [];
  const orders = ordersToday(state);
  const changed = new Set((changes ?? []).map((c) => c.order_id));
  const product = new Map((state?.production_orders ?? []).map((o) => [o.id, shortProduct(o.product_name)]));

  const rows = machines.map((w) => {
    const bars: Bar[] = [];
    for (const o of orders.filter((x) => x.work_center_id === w.id)) {
      const s = parseHour(o.planned_start);
      const e = parseHour(o.planned_end);
      if (s == null || e == null) continue;
      const pend = changed.has(o.id);
      bars.push({
        key: o.id,
        start: s,
        end: e,
        label: `${shortId(o.id)} · ${shortProduct(o.product_name)}${pend ? " (tertunda)" : ""}`,
        kind: pend ? "pending" : o.status === "RESCHEDULED" ? "rescheduled" : "order",
        title: `${o.id}${o.sap_order_number ? ` (SAP ${o.sap_order_number})` : ""} · ${o.product_name} · ${o.customer}`,
      });
    }
    for (const m of maintenanceToday(state).filter((x) => x.work_center_id === w.id)) {
      const s = parseHour(m.start);
      const e = parseHour(m.end);
      if (s == null || e == null) continue;
      bars.push({ key: m.id, start: s, end: e, label: "Perawatan terjadwal", kind: "maintenance", title: m.description });
    }
    if (disruption && disruption.machine_id === w.id) {
      const s = SHIFT_START + disruption.start_hour;
      const e = SHIFT_START + disruption.end_hour;
      bars.push({ key: "disrupt", start: s, end: e, label: `Gangguan · ${e - s} jam`, kind: "disruption", title: disruption.disruption_type });
    }
    for (const c of (changes ?? []).filter((x) => x.to_machine === w.id)) {
      const s = parseHour(c.new_start_time);
      const e = parseHour(c.new_end_time);
      if (s == null || e == null) continue;
      const ot = isOvertimeLabel(c.new_start_time) ? " · lembur" : "";
      const move = c.from_machine !== c.to_machine ? `dari ${c.from_machine}` : "digeser";
      bars.push({
        key: "p-" + c.order_id, start: s, end: e, kind: "proposed",
        label: `${shortId(c.order_id)} · ${move}${ot}`,
        title: `${c.order_id} · ${product.get(c.order_id) ?? ""} · ${c.from_machine} → ${c.to_machine}`,
      });
    }
    return { id: w.id, name: w.name, type: w.machine_type, lanes: lanes(bars) };
  });

  const latest = Math.max(SHIFT_END, ...rows.flatMap((r) => r.lanes.flat().map((b) => b.end)));
  const end = latest > SHIFT_END ? NIGHT_END : SHIFT_END;
  const span = end - SHIFT_START;
  const pct = (h: number) => ((Math.min(Math.max(h, SHIFT_START), end) - SHIFT_START) / span) * 100;
  const kinds = new Set<BarKind>(["order", ...rows.flatMap((r) => r.lanes.flat().map((b) => b.kind))]);
  const ticks = Array.from({ length: span + 1 }, (_, i) => SHIFT_START + i).filter((h) => span <= 8 || h % 2 === 1);

  return (
    <div className="-mx-1 overflow-x-auto px-1">
    <div className="flex min-w-[720px] flex-col gap-2">
      <div className="grid grid-cols-[136px_1fr] items-end">
        <span />
        <div className="relative h-6">
          {ticks.map((h) => (
            <span key={h} className="absolute -translate-x-1/2 font-mono text-[14px] text-navy" style={{ left: `${pct(h)}%` }}>
              {String(h).padStart(2, "0")}:00
            </span>
          ))}
        </div>
      </div>

      <div className="flex flex-col gap-1.5" role="table" aria-label="Jadwal shift per mesin">
        {rows.map((r) => (
          <div key={r.id} role="row" className="grid grid-cols-[136px_1fr] items-stretch">
            <span role="rowheader" className="flex min-w-0 flex-col justify-center pr-2" title={r.type}>
              <span className="font-mono text-[15px] font-medium">{r.id}</span>
              <span className="truncate text-[13px] text-muted-ink">{r.name}</span>
            </span>
            <div className="relative rounded-sm bg-[#f4f6f9]" style={{ height: r.lanes.length * 36 + 8 }}>
              {end > SHIFT_END && (
                <div
                  className="absolute inset-y-0 bg-[repeating-linear-gradient(135deg,transparent_0_6px,rgba(72,98,132,.07)_6px_12px)]"
                  style={{ left: `${pct(SHIFT_END)}%`, right: 0 }}
                  title="Shift lembur 15:00–23:00"
                />
              )}
              {ticks.map((h) => (
                <div key={h} className="absolute inset-y-0 w-px bg-line" style={{ left: `${pct(h)}%` }} />
              ))}
              {r.lanes.map((lane, li) =>
                lane.map((b) => (
                  <div
                    key={b.key}
                    role="cell"
                    title={b.title}
                    className={cn(
                      "absolute flex h-8 items-center justify-center overflow-hidden rounded-sm px-2 font-mono text-[14px] whitespace-nowrap",
                      BAR_STYLE[b.kind],
                      b.kind === "disruption" && "z-10 font-sans font-semibold",
                    )}
                    style={{ left: `calc(${pct(b.start)}% + 2px)`, width: `calc(${pct(b.end) - pct(b.start)}% - 4px)`, top: 4 + li * 36 }}
                  >
                    <span className="truncate">{b.label}</span>
                  </div>
                )),
              )}
            </div>
          </div>
        ))}
        {rows.length === 0 && <p className="py-6 text-center text-[15px] text-muted-ink">Memuat jadwal dari SAP…</p>}
      </div>


      {note && <p className="text-[15px]">{note}</p>}
    </div>
      <ul className="mt-2 flex flex-wrap gap-x-4 gap-y-1.5 text-[14px] text-muted-ink" aria-label="Keterangan warna">
        {(Object.keys(LEGEND) as BarKind[]).filter((k) => kinds.has(k)).map((k) => (
          <li key={k} className="flex items-center gap-1.5">
            <span className={cn("inline-block h-3.5 w-6 rounded-sm", BAR_STYLE[k])} aria-hidden />
            {LEGEND[k]}
          </li>
        ))}
        {end > SHIFT_END && (
          <li className="flex items-center gap-1.5">
            <span className="inline-block h-3.5 w-6 rounded-sm border border-line bg-[repeating-linear-gradient(135deg,transparent_0_3px,rgba(72,98,132,.25)_3px_6px)]" aria-hidden />
            Shift lembur 15:00–23:00
          </li>
        )}
      </ul>
    </div>
  );
}
