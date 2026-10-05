export const API_BASE = "http://localhost:8000";

export interface WorkCenter {
  id: string;
  name: string;
  machine_type: string;
  status: string;
  capacity_hours_per_day: number;
  current_order_id: string | null;
  operator_name: string | null;
  telemetry?: Record<string, number>;
}

export interface ProdOrder {
  id: string;
  order_class: string;
  work_center_id: string;
  product_name: string;
  product_code: string;
  quantity: number;
  quantity_unit: string;
  planned_start: string;
  planned_end: string;
  deadline: string;
  sla_penalty_per_hour_idr: number;
  status: string;
  progress_percent: number;
  customer: string;
  priority: number;
  // approve may write display strings back into these fields — tolerate it
  [k: string]: unknown;
}

export interface SapState {
  _meta: { version: number; facility?: string; [k: string]: unknown };
  work_centers: WorkCenter[];
  production_orders: ProdOrder[];
  [k: string]: unknown;
}

export interface GanttChange {
  order_id: string;
  from_machine: string;
  to_machine: string;
  new_start_time: string;
  new_end_time: string;
  delay_hours?: number;
  sla_penalty_idr?: number;
  overtime_cost_idr?: number;
  changeover_cost_idr?: number;
}

export interface Scenario {
  id: string;
  name_en: string;
  name_id: string;
  recommended: boolean;
  total_cost_idr: number;
  net_savings_idr: number;
  affected_orders: string[];
  gantt_changes: GanttChange[];
  rationale_template: string;
}

export interface DisruptResponse {
  machine_id: string;
  disruption_type: string;
  disruption_hours: number;
  scenarios: Scenario[];
  claude_summary: string;  // kept as-is for backward compatibility
  sap_version: number;
}

export interface LedgerEntry {
  id: string;
  timestamp: string;
  sha256_hash: string;
  action_type: string;
  scenario_chosen: string;
  sap_version_before: number;
  sap_version_after: number;
  approved_by: string;
  [k: string]: unknown;
}

export function formatIDR(n: number): string {
  return "Rp " + Number(n || 0).toLocaleString("id-ID");
}

/**
 * Parse backend time values into a 0-24 hour float.
 * Accepts ISO ("2025-01-15T07:00:00") AND display strings
 * ("09:00 WIB", "15:00 WIB (Lembur)") — the latter appear in
 * scenario gantt_changes and, after /api/approve, inside
 * planned_start/planned_end itself. Returns null if unparseable.
 */
export function parseHour(v: unknown): number | null {
  if (v == null) return null;
  const s = String(v).trim();
  // ISO first
  const iso = s.match(/T(\d{1,2}):(\d{2})/);
  if (iso) return Number(iso[1]) + Number(iso[2]) / 60;
  // "HH:MM ..." display string
  const hm = s.match(/(\d{1,2})[:.](\d{2})/);
  if (hm) return Number(hm[1]) + Number(hm[2]) / 60;
  return null;
}

export function isOvertimeLabel(v: unknown): boolean {
  return /lembur|overtime/i.test(String(v ?? ""));
}

async function req<T>(path: string, init?: RequestInit): Promise<T> {
  const res = await fetch(API_BASE + path, {
    ...init,
    headers: { "Content-Type": "application/json", ...(init?.headers ?? {}) },
  });
  if (!res.ok) {
    let detail: unknown = await res.text();
    try {
      detail = JSON.parse(detail as string);
    } catch {
      /* keep text */
    }
    throw new Error(`HTTP ${res.status} ${path}: ${JSON.stringify(detail).slice(0, 400)}`);
  }
  return res.json() as Promise<T>;
}

export const api = {
  state: () => req<SapState>("/api/state"),
  health: () => req<Record<string, string>>("/health"),
  disrupt: (b: { machine_id: string; disruption_type: string; start_hour: number; end_hour: number }) =>
    req<DisruptResponse>("/api/disrupt", { method: "POST", body: JSON.stringify(b) }),
  approve: (b: { scenario_id: string; scenario_data: unknown; expected_sap_version: number; approved_by: string }) =>
    req<{ receipt_id: string; sap_version_before: number; sap_version_after: number; timestamp: string; sha256_hash: string; message: string }>(
      "/api/approve",
      { method: "POST", body: JSON.stringify(b) },
    ),
  ledger: () => req<LedgerEntry[]>("/api/ledger"),
  verify: () => req<{ valid: boolean; total_entries: number; first_broken_index: number | null; message: string }>("/api/ledger/verify"),
};
