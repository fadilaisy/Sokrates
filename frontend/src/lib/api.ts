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
  resetState: () => req<SapState>("/api/state/reset", { method: "POST" }),
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
  generateSkill: (supervisor_description: string) =>
    req<{ skill_md: string; model_used: string }>("/api/skills/generate", {
      method: "POST",
      body: JSON.stringify({ supervisor_description }),
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
