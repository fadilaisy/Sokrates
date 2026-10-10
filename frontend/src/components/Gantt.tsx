import { isOvertimeLabel, parseHour } from "../lib/api";
import type { GanttChange, ProdOrder } from "../lib/api";

const DAY_START = 7; // 07:00 WIB
const DAY_END = 23; // 23:00 WIB (lembur)

export interface GanttRow {
  label: string;
  sub: string;
  start: number | null;
  end: number | null;
  overtime: boolean;
  status: string;
}

function orderToRow(o: ProdOrder): GanttRow {
  return {
    label: `${o.id} · ${o.work_center_id}`,
    sub: `${String(o.product_name ?? "").slice(0, 42)} — ${o.status}`,
    start: parseHour(o.planned_start),
    end: parseHour(o.planned_end),
    overtime: isOvertimeLabel(o.planned_start) || isOvertimeLabel(o.planned_end),
    status: String(o.status ?? ""),
  };
}

function changeToRow(c: GanttChange): GanttRow {
  return {
    label: `${c.order_id} · ${c.from_machine} → ${c.to_machine}`,
    sub: `${c.new_start_time} → ${c.new_end_time}`,
    start: parseHour(c.new_start_time),
    end: parseHour(c.new_end_time),
    overtime: isOvertimeLabel(c.new_start_time) || isOvertimeLabel(c.new_end_time),
    status: "SCENARIO",
  };
}

function color(status: string, overtime: boolean): string {
  if (overtime) return "#a78bfa"; // lembur — ungu
  if (status === "RESCHEDULED") return "#38bdf8";
  if (status === "IN_PROGRESS") return "#34d399";
  if (status === "SCENARIO") return "#fbbf24";
  if (status === "PLANNED") return "#94a3b8";
  return "#64748b";
}

/** SVG Gantt — pure DOM/SVG so text stays selectable & accessible. */
export default function Gantt({
  orders,
  changes,
  title,
}: {
  orders?: ProdOrder[];
  changes?: GanttChange[];
  title: string;
}) {
  const rows: GanttRow[] =
    changes && changes.length > 0
      ? changes.map(changeToRow)
      : (orders ?? []).map(orderToRow);

  const W = 760;
  const rowH = 34;
  const labelW = 250;
  const H = Math.max(120, rows.length * rowH + 44);
  const x = (h: number) => labelW + ((h - DAY_START) / (DAY_END - DAY_START)) * (W - labelW - 16);
  const barW = (a: number | null, b: number | null) =>
    a == null || b == null ? 0 : Math.max(6, x(b) - x(a));

  const ticks = [7, 9, 11, 13, 15, 17, 19, 21, 23];

  return (
    <div className="rounded-2xl border border-white/10 bg-white/[0.03] p-4">
      <div className="mb-2 flex items-center justify-between">
        <h3 className="text-sm font-semibold text-white">{title}</h3>
        <div className="flex gap-3 text-[11px] text-zinc-400">
          <span><i className="mr-1 inline-block h-2 w-2 rounded-full bg-emerald-400" />Berjalan</span>
          <span><i className="mr-1 inline-block h-2 w-2 rounded-full bg-sky-400" />Rescheduled</span>
          <span><i className="mr-1 inline-block h-2 w-2 rounded-full bg-amber-400" />Skenario</span>
          <span><i className="mr-1 inline-block h-2 w-2 rounded-full bg-violet-400" />Lembur</span>
        </div>
      </div>
      {rows.length === 0 && (
        <p className="py-6 text-center text-sm text-zinc-500">
          Tidak ada order pada rentang ini. Jalankan analisis gangguan untuk melihat skenario.
        </p>
      )}
      <svg viewBox={`0 0 ${W} ${H}`} className="w-full" role="img" aria-label={title}>
        {ticks.map((t) => (
          <g key={t}>
            <line x1={x(t)} y1={28} x2={x(t)} y2={H - 8} stroke="rgba(255,255,255,0.08)" />
            <text x={x(t)} y={18} fill="#71717a" fontSize={10} textAnchor="middle">
              {String(t).padStart(2, "0")}:00
            </text>
          </g>
        ))}
        {/* garis shift lembur 15:00 */}
        <line x1={x(15)} y1={28} x2={x(15)} y2={H - 8} stroke="#a78bfa" strokeDasharray="4 3" opacity={0.6} />
        {rows.map((r, i) => {
          const y = 34 + i * rowH;
          const c = color(r.status, r.overtime);
          const w = barW(r.start, r.end);
          return (
            <g key={i}>
              <text x={4} y={y + 11} fill="#e4e4e7" fontSize={11} fontWeight={600}>
                {r.label.slice(0, 30)}
              </text>
              <text x={4} y={y + 24} fill="#71717a" fontSize={10}>
                {r.sub.slice(0, 40)}
              </text>
              {r.start == null || r.end == null ? (
                <text x={x(7)} y={y + 16} fill="#f87171" fontSize={10}>
                  waktu tak terbaca
                </text>
              ) : (
                <rect x={x(r.start)} y={y + 4} width={w} height={18} rx={9} fill={c} opacity={0.9}>
                  <title>{`${r.label}\n${r.sub}`}</title>
                </rect>
              )}
            </g>
          );
        })}
      </svg>
      <p className="mt-1 text-[11px] text-zinc-500">
        Skala 07:00–23:00 WIB. Garis putus-putus = mulai shift lembur (15:00).
      </p>
    </div>
  );
}
