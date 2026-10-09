export const API_BASE = (import.meta.env.VITE_API_BASE as string | undefined) ?? "http://localhost:8000";
export const WS_TELEMETRY = API_BASE.replace(/^http/, "ws") + "/ws/telemetry";

/** Error with the HTTP status and parsed body, so callers can react to 409 drift etc. */
export class ApiError extends Error {
  status: number;
  detail: unknown;
  constructor(status: number, path: string, detail: unknown) {
    super(`HTTP ${status} ${path}: ${JSON.stringify(detail).slice(0, 400)}`);
    this.status = status;
    this.detail = detail;
  }
}

export interface DriftDetail {
  error: "DriftError";
  message: string;
  expected_version: number;
  actual_version: number;
  receipt_id?: string;
}

export interface MaintenanceSlot {
  id: string;
  work_center_id: string;
  type: string;
  start: string;
  end: string;
  description: string;
  status: string;
}

export interface TelemetryFrame {
  event: "telemetry_analysis" | "safety_alert" | "error";
  machine_id?: string;
  metric?: string;
  value?: number;
  severity?: "OK" | "WARNING" | "CRITICAL";
  rule_triggered?: string | null;
  analysis?: string;
  action_required?: string;
  message?: string;
  timestamp?: string;
}

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
  maintenance_slots?: MaintenanceSlot[];
  shop_floor_config?: { shift_start?: string; shift_end?: string; [k: string]: unknown };
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

export interface CostBreakdown {
  sla: number;
  overtime: number;
  changeover: number;
  freight: number;
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
  cost_breakdown?: CostBreakdown;
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

export interface ApproveReceipt {
  receipt_id: string;
  scenario_id: string;
  sap_version_before: number;
  sap_version_after: number;
  timestamp: string;
  sha256_hash: string;
  message: string;
}

// ─────────────────────────────────────────────────────────────────────────────
// Phase 3: Skill Studio types
// ─────────────────────────────────────────────────────────────────────────────

export interface SkillStudioFormData {
  machine_type: string;
  failure_mode: string;
  sla_class_a_penalty_per_hour_idr: number;
  sla_class_b_penalty_per_hour_idr: number;
  sla_class_c_penalty_per_hour_idr: number;
  overtime_cost_per_hour_idr: number;
  changeover_cost_idr: number;
  safety_thresholds: Record<string, number>;
}

export interface SkillStudioDraft {
  skill_md: string;
  sla_penalties: Record<string, any>;
  hooks: Array<{ id: string; name: string; trigger_type: string; condition: string }>;
  interlocks: Array<{ id: string; tier: number; name: string; condition: string; threshold_value: number | null }>;
}

export interface SkillStudioLintResult {
  valid: boolean;
  errors: string[];
  warnings: string[];
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
    // FastAPI wraps errors as { detail: ... }
    if (detail && typeof detail === "object" && "detail" in (detail as object)) detail = (detail as { detail: unknown }).detail;
    throw new ApiError(res.status, path, detail);
  }
  return res.json() as Promise<T>;
}

export const api = {
  state: () => req<SapState>("/api/state"),
  resetState: () => req<SapState>("/api/state/reset", { method: "POST" }),
  health: () => req<Record<string, string>>("/health"),
  disrupt: (b: { machine_id: string; disruption_type: string; start_hour: number; end_hour: number }) =>
    req<DisruptResponse>("/api/disrupt", { method: "POST", body: JSON.stringify(b) }),
  approve: (b: { scenario_id: string; scenario_data: unknown; expected_sap_version: number; approved_by: string }) =>
    req<ApproveReceipt>(
      "/api/approve",
      { method: "POST", body: JSON.stringify(b) },
    ),
  ledger: () => req<LedgerEntry[]>("/api/ledger"),
  verify: () => req<{ valid: boolean; total_entries: number; first_broken_index: number | null; message: string }>("/api/ledger/verify"),
  generateSkill: (supervisor_description: string) =>
    req<{ skill_md: string; model_used: string }>("/api/skills/generate", {
      method: "POST",
      body: JSON.stringify({ supervisor_description }),
    }),
  // Phase 3: Skill Studio
  draftSkill: (b: SkillStudioFormData) =>
    req<{ skill_md: string; sla_penalties: any; hooks: any[]; interlocks: any[]; validation_errors: string[] }>("/api/skills/draft", {
      method: "POST",
      body: JSON.stringify(b),
    }),
  lintSkill: (b: { skill_md: string; sla_penalties: any; hooks: any[]; interlocks: any[] }) =>
    req<{ valid: boolean; errors: string[]; warnings: string[] }>("/api/skills/lint", {
      method: "POST",
      body: JSON.stringify(b),
    }),
  approveSkill: (b: { skill_id: string; skill_md: string; sla_penalties: any; hooks: any[]; interlocks: any[] }) =>
    req<{ success: boolean; skill_id: string; version: string; ledger_entry_id: string }>("/api/skills/approve", {
      method: "POST",
      body: JSON.stringify(b),
    }),
};
