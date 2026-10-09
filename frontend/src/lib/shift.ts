import { isOvertimeLabel, parseHour } from "./api";
import type { GanttChange, MaintenanceSlot, ProdOrder, SapState, WorkCenter } from "./api";

/** Display statuses used everywhere in the UI (labels stay in English, as in the PRD). */
export type MachineStatus = "RUNNING" | "FAULT" | "IDLE" | "MAINTENANCE" | "DISRUPTED" | "RECOVERING";

export const SHIFT_START = 7; // 07:00 WIB
export const SHIFT_END = 15; // 15:00 WIB
export const NIGHT_END = 23; // 23:00 WIB (lembur)

export const DISRUPTION_TYPES = [
  { value: "BREAKDOWN", label: "BREAKDOWN · mesin rusak" },
  { value: "OVERHEAT", label: "OVERHEAT · suhu berlebih" },
  { value: "VIBRATION", label: "VIBRATION · getaran tinggi" },
  { value: "MAINTENANCE", label: "MAINTENANCE · perawatan mendadak" },
] as const;

/** Map whatever the backend sends into one of our display statuses. */
export function normalizeStatus(raw: string | null | undefined): MachineStatus {
  const s = String(raw ?? "").toUpperCase();
  if (s === "RUNNING") return "RUNNING";
  if (s === "IDLE") return "IDLE";
  if (s.includes("MAINT")) return "MAINTENANCE";
  if (s.includes("RECOVER")) return "RECOVERING";
  if (s.includes("DISRUPT")) return "DISRUPTED";
  if (s.includes("FAULT") || s.includes("BREAK") || s.includes("DOWN") || s.includes("STOP")) return "FAULT";
  return "IDLE";
}

export const STATUS_STYLE: Record<MachineStatus, { bg: string; fg: string; label: string }> = {
  RUNNING: { bg: "var(--sf-running)", fg: "#fff", label: "RUNNING" },
  FAULT: { bg: "var(--sf-fault)", fg: "#fff", label: "FAULT" },
  IDLE: { bg: "var(--sf-idle)", fg: "#fff", label: "IDLE" },
  MAINTENANCE: { bg: "var(--sf-maint)", fg: "#fff", label: "MAINTENANCE" },
  DISRUPTED: { bg: "var(--sf-warn)", fg: "#fff", label: "DISRUPTED" },
  RECOVERING: { bg: "var(--sf-maint)", fg: "#fff", label: "RECOVERING" },
};

/** Date (YYYY-MM-DD) of the current shift: the earliest planned_start in the plan. */
export function shiftDate(state: SapState | null): string | null {
  if (!state) return null;
  const dates = state.production_orders
    .map((o) => String(o.planned_start ?? "").slice(0, 10))
    .filter((d) => /^\d{4}-\d{2}-\d{2}$/.test(d))
    .sort();
  return dates[0] ?? null;
}

/**
 * Orders that belong to today's shift. After /api/approve the backend writes display strings
 * ("15:00 WIB (Lembur)") into planned_start, which carry no date — those are today's by definition.
 */
export function ordersToday(state: SapState | null): ProdOrder[] {
  if (!state) return [];
  const day = shiftDate(state);
  return state.production_orders.filter((o) => {
    const s = String(o.planned_start ?? "");
    return !/^\d{4}-\d{2}-\d{2}/.test(s) || s.startsWith(day ?? "");
  });
}

export function maintenanceToday(state: SapState | null): MaintenanceSlot[] {
  if (!state) return [];
  const day = shiftDate(state);
  return (state.maintenance_slots ?? []).filter((m) => String(m.start).startsWith(day ?? ""));
}

/** Window in shift-relative hours (as /api/disrupt expects) → "09:00–12:00". */
export function windowLabel(startHour: number, endHour: number): string {
  const f = (h: number) => `${String(SHIFT_START + h).padStart(2, "0")}:00`;
  return `${f(startHour)}–${f(endHour)}`;
}

/** Today's orders on a machine that overlap a shift-relative window. Used for the zero-impact warning. */
export function affectedOrders(state: SapState | null, machineId: string, startHour: number, endHour: number): ProdOrder[] {
  const a = SHIFT_START + startHour;
  const b = SHIFT_START + endHour;
  return ordersToday(state).filter((o) => {
    if (o.work_center_id !== machineId) return false;
    const s = parseHour(o.planned_start);
    const e = parseHour(o.planned_end);
    return s != null && e != null && s < b && e > a;
  });
}

export function telemetryOf(w: WorkCenter, live?: Record<string, number>) {
  const t = { ...(w.telemetry ?? {}), ...(live ?? {}) };
  return {
    temp: typeof t.motor_temp_celsius === "number" ? t.motor_temp_celsius : null,
    vib: typeof t.spindle_vibration_mm_per_s === "number" ? t.spindle_vibration_mm_per_s : null,
  };
}

export function fmt1(n: number | null): string {
  return n == null ? "–" : n.toLocaleString("id-ID", { minimumFractionDigits: 1, maximumFractionDigits: 1 });
}

/** Describe a solver change in plain Bahasa Indonesia for the diff table. */
export function changeKind(c: GanttChange): string {
  if (c.from_machine !== c.to_machine) return isOvertimeLabel(c.new_start_time) ? "Rerute + lembur" : "Rerute";
  if (isOvertimeLabel(c.new_start_time)) return "Lembur";
  if (c.delay_hours) return `Tunda ${c.delay_hours} jam`;
  return "Ubah jadwal";
}

export function stripWib(v: unknown): string {
  return String(v ?? "")
    .replace(/\s*WIB/g, "")
    .replace(/\s*\((Lembur|Overtime)\)/gi, " (lembur)")
    .trim();
}

export function timeRange(start: unknown, end: unknown): string {
  const h = (v: unknown) => {
    const x = parseHour(v);
    if (x == null) return "?";
    const hh = Math.floor(x);
    const mm = Math.round((x - hh) * 60);
    return `${String(hh).padStart(2, "0")}:${String(mm).padStart(2, "0")}`;
  };
  return `${h(start)}–${h(end)}`;
}

export function shortHash(h: string | undefined | null): string {
  if (!h) return "–";
  return `${h.slice(0, 4)}…${h.slice(-4)}`;
}

export function wibTime(iso: string | undefined): string {
  if (!iso) return "–";
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return iso;
  return (
    d.toLocaleString("id-ID", {
      timeZone: "Asia/Jakarta",
      day: "2-digit",
      month: "short",
      hour: "2-digit",
      minute: "2-digit",
    }) + " WIB"
  );
}
