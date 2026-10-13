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
  sources?: RagSource[];        // RAG grounding passages (may be empty)
  grounding_abstained?: boolean;
}

// ── RAG ────────────────────────────────────────────────────────────────────

export type ConstraintTier = "Safety" | "Quality" | "Cost";
export type DocStatus = "pending" | "processing" | "indexed" | "failed" | "superseded";

export interface ScoreBreakdown {
  total: number;
  R: number;
  M: number;
  F: number;
  A: number;
  P: number;
  weighted: { R: number; M: number; F: number; A: number; P: number };
  reasons: string[];
}

/** A passage the cockpit can open in the source viewer. */
export interface RagSource {
  chunk_id: string;
  doc_id: string;
  doc_title: string;
  page: number;
  citation: string;
  bbox?: number[] | null;
  constraint_tier?: ConstraintTier | string | null;
  forced_safety?: boolean;
  score?: number | null;
  section_path?: string | null;
  text?: string;
  breakdown?: ScoreBreakdown | null;
  sid?: string;
}

export interface RagDoc {
  doc_id: string;
  family_id: string;
  version: number;
  title: string;
  filename: string;
  doc_type: string | null;
  doc_type_confidence: number | null;
  doc_type_source: string | null;
  summary: string | null;
  language: string | null;
  plant: string;
  machine_ids: string[];
  machine_models: string[];
  effective_date: string | null;
  supersedes: string | null;
  superseded_by: string | null;
  authority_tier: number | null;
  status: DocStatus;
  error: string | null;
  warnings: string[];
  page_count: number | null;
  ocr_pages: number[];
  low_conf_pages: { page: number; confidence: number }[];
  chunk_count: number;
  uploaded_by: string;
  uploaded_at: string;
  // detail-only
  chunks_by_tier?: Record<string, number>;
  fault_codes?: string[];
  part_numbers?: string[];
  has_viewer?: boolean;
}

export interface RagIncident {
  machine_id?: string;
  fault_code?: string;
  severity?: string;
  disruption_type?: string;
  shift?: string;
}

export interface RetrieveResult extends RagSource {
  doc_type: string;
  version: number;
  kind: string;
  fault_codes: string[];
  part_numbers: string[];
  ocr_confidence: number | null;
  superseded: boolean;
}

export interface RetrieveResponse {
  abstained: boolean;
  message: string | null;
  results: RetrieveResult[];
  near_miss: RagSource[];
  filters: string[];
  candidates: number;
  threshold: number;
  weights: Record<string, number>;
  machine: { id: string; model: string } | null;
  fault_code: string | null;
  latency_ms: number;
}

export interface AnswerClaim {
  text: string;
  sources: string[];
  citations: RagSource[];
  support: number;
}

export interface AnswerResponse {
  status: "answered" | "insufficient" | "abstained" | "llm_unavailable" | "error";
  message: string | null;
  answer: string | null;
  claims: AnswerClaim[];
  rejected_claims: { text: string; reason: string; sources: string[] }[];
  sources: RagSource[];
  injection_flags?: string[];
  provider?: string;
  model?: string;
  latency_ms: number;
  retrieval: { abstained: boolean; filters: string[]; candidates: number; latency_ms: number };
}

export interface RagStatus {
  documents: Record<string, number>;
  chunks: number;
  ocr: { available: boolean; languages: string | null };
  llm_providers: string[];
  provider_order: string;
  models: { gemini: string; claude: string };
}

/** The cockpit acts as a supervisor (uploads allowed). No real auth in the backend yet. */
export const COCKPIT_ROLE = "supervisor";

export function ragPageUrl(docId: string, page: number, chunkId?: string): string {
  const q = new URLSearchParams({ role: COCKPIT_ROLE, dpi: "120" });
  if (chunkId) q.set("chunk_id", chunkId);
  return `${API_BASE}/api/documents/${encodeURIComponent(docId)}/pages/${page}.png?${q}`;
}

/** GET /api/metrics — shift downtime + OEE computed by backend/metrics.py. */
export interface ShiftMetrics {
  shift: { date: string | null; start: string; end: string; hours_per_machine: number; machines: number; total_hours: number };
  downtime: {
    total_hours: number;
    percent_of_shift: number;
    by_category: { key: "changeover" | "maintenance" | "unplanned"; label: string; hours: number }[];
    by_machine: {
      machine_id: string;
      maintenance_hours: number;
      unplanned_hours: number;
      changeover_hours: number;
      total_hours: number;
      reasons: string[];
    }[];
  };
  oee: {
    target: number;
    availability: number;
    performance: number;
    quality: number;
    oee: number;
    sources: { availability: string; performance: string; quality: string };
  };
  sap_version: number | null;
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
  metrics: () => req<ShiftMetrics>("/api/metrics"),
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
  chat: (message: string) =>
    req<{ reply: string; model_used: string }>("/api/chat", {
      method: "POST",
      body: JSON.stringify({ message }),
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
  approveSkill: (b: { skill_id: string; skill_md: string; sla_penalties: any; hooks: any[]; interlocks: any[]; approved_by?: string }) =>
    req<{ success: boolean; skill_id: string; version: string; ledger_entry_id: string }>("/api/skills/approve", {
      method: "POST",
      body: JSON.stringify(b),
    }),
  revertLedger: (entry_id: string, approved_by: string) =>
    req<ApproveReceipt>(`/api/ledger/${entry_id}/revert`, {
      method: "POST",
      body: JSON.stringify({ id: entry_id, approved_by }),
    }),
  injectDisruption: (b: { machine_id: string; new_status: string; disruption_type: string }) =>
    req<{ success: boolean; sap_version_before: number; sap_version_after: number; ledger_entry_id: string }>("/api/disrupt/inject", {
      method: "POST",
      body: JSON.stringify(b),
    }),
};

const roleHeaders = (role: string = COCKPIT_ROLE) => ({ "X-User-Role": role });

export const rag = {
  status: () => req<RagStatus>("/api/rag/status"),
  listDocs: () => req<RagDoc[]>("/api/documents", { headers: roleHeaders() }),
  getDoc: (id: string) => req<RagDoc>(`/api/documents/${encodeURIComponent(id)}`, { headers: roleHeaders() }),
  reindex: (id: string) =>
    req<{ doc_id: string; status: string }>(`/api/documents/${encodeURIComponent(id)}/reindex`, {
      method: "POST",
      headers: roleHeaders(),
    }),
  seedDemo: () =>
    req<{ file: string; doc_id: string; status: string }[]>("/api/rag/seed-demo", {
      method: "POST",
      headers: roleHeaders("admin"),
    }),
  retrieve: (incident: RagIncident, query: string) =>
    req<RetrieveResponse>("/api/retrieve", {
      method: "POST",
      headers: roleHeaders(),
      body: JSON.stringify({ incident, query }),
    }),
  answer: (incident: RagIncident, query: string) =>
    req<AnswerResponse>("/api/answer", {
      method: "POST",
      headers: roleHeaders(),
      body: JSON.stringify({ incident, query }),
    }),
  /** Multipart upload — can't use req() because it forces a JSON content type. */
  upload: async (file: File, meta: Record<string, string | undefined>) => {
    const fd = new FormData();
    fd.append("file", file);
    for (const [k, v] of Object.entries(meta)) if (v) fd.append(k, v);
    const res = await fetch(API_BASE + "/api/documents", { method: "POST", body: fd, headers: roleHeaders() });
    const body = await res.json().catch(() => ({}));
    if (!res.ok) throw new Error((body as { detail?: string }).detail ?? `HTTP ${res.status}`);
    return body as { doc_id: string; status: string; duplicate: boolean; version: number; message?: string };
  },
};
