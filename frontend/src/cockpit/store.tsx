import { createContext, useCallback, useContext, useEffect, useMemo, useRef, useState } from "react";
import type { ReactNode } from "react";
import { api, ApiError, formatIDR, WS_TELEMETRY } from "../lib/api";
import type { ApproveReceipt, DisruptResponse, DriftDetail, LedgerEntry, SapState, Scenario, TelemetryFrame } from "../lib/api";
import { normalizeStatus, windowLabel } from "../lib/shift";
import type { MachineStatus } from "../lib/shift";
import type { SkillStudioFormData, SkillStudioDraft, SkillStudioLintResult } from "../lib/api";

export type Page = "dashboard" | "cockpit" | "floor";
export type Phase = "normal" | "detecting" | "proposing" | "resolved";

export interface DisruptionParams {
  machine_id: string;
  disruption_type: string;
  start_hour: number; // shift-relative, 0 = 07:00
  end_hour: number;
}

/** The PRD's one-click demo: always CNC-02, hours 2–5, BREAKDOWN. */
export const DEMO_DISRUPTION: DisruptionParams = { machine_id: "CNC-02", disruption_type: "BREAKDOWN", start_hour: 2, end_hour: 5 };

export const APPROVER = "Production Supervisor — Budi Santoso";

export interface SafetyAlert {
  machine_id: string;
  rule: string;
  metric: string;
  value: number;
  message: string;
  timestamp?: string;
}

export interface ChatMessage {
  id: number;
  from: "ai" | "user";
  text: string;
  kind?: "progress" | "result";
  scenario?: Scenario;
}

type WsStatus = "connecting" | "open" | "closed";

interface CockpitValue {
  // data
  state: SapState | null;
  online: boolean;
  aiConfigured: boolean;
  error: string | null;
  ledger: LedgerEntry[];
  sapVersion: number;
  // navigation
  page: Page;
  setPage: (p: Page) => void;
  // flow
  phase: Phase;
  /** 0 = loading skill, 1 = solver, 2 = scenarios + summary, 3 = done */
  detectStep: number;
  params: DisruptionParams | null;
  result: DisruptResponse | null;
  receipt: (ApproveReceipt & { scenario: Scenario }) | null;
  runDisruption: (p: DisruptionParams) => Promise<void>;
  // approval
  approvalFor: Scenario | null;
  approving: boolean;
  conflict: DriftDetail | null;
  approver: string;
  setApprover: (v: string) => void;
  openApproval: (s: Scenario) => void;
  closeApproval: () => void;
  confirmApproval: () => Promise<void>;
  // failure drill (Phase 4): inject a real second disruption via EXTERNAL_CHANGE
  injecting: boolean;
  injectDrill: () => Promise<void>;
  // machines
  displayStatus: (machineId: string, raw: string) => MachineStatus;
  live: Record<string, Record<string, number>>;
  // telemetry + safety
  wsStatus: WsStatus;
  lastAnalysis: TelemetryFrame | null;
  safety: SafetyAlert | null;
  sendTelemetry: (machine_id: string, metric: string, value: number) => void;
  runFullAnalysis: () => Promise<void>;
  // ledger
  ledgerOpen: boolean;
  setLedgerOpen: (v: boolean) => void;
  verifying: boolean;
  verifyResult: { valid: boolean; total_entries: number; message: string } | null;
  verify: () => Promise<void>;
  // chat
  chatOpen: boolean;
  chatMode: "general" | "recalc";
  /** SAP versions of the conflict that opened the recalc chat (kept after the conflict clears). */
  recalcVersions: { from: number; to: number } | null;
  messages: ChatMessage[];
  chatBusy: boolean;
  openChat: (mode: "general" | "recalc") => void;
  closeChat: () => void;
  sendChat: (text: string) => Promise<void>;
  // misc
  toast: string | null;
  reset: () => Promise<void>;
  resetting: boolean;
  skillStudioOpen: boolean;
  setSkillStudioOpen: (v: boolean) => void;
  skillStudioState: "interview" | "draft" | "lint" | "approved";
  setSkillStudioState: (s: "interview" | "draft" | "lint" | "approved") => void;
  skillStudioFormData: SkillStudioFormData;
  setSkillStudioFormData: (f: SkillStudioFormData) => void;
  skillStudioDraft: SkillStudioDraft | null;
  setSkillStudioDraft: (d: SkillStudioDraft | null) => void;
  skillStudioLintResult: SkillStudioLintResult | null;
  setSkillStudioLintResult: (r: SkillStudioLintResult | null) => void;
}

const Ctx = createContext<CockpitValue | null>(null);

// oxlint-disable-next-line react/only-export-components
export function useCockpit(): CockpitValue {
  const v = useContext(Ctx);
  if (!v) throw new Error("useCockpit must be used inside <CockpitProvider>");
  return v;
}

let msgSeq = 1;

export function CockpitProvider({ children }: { children: ReactNode }) {
  const [state, setState] = useState<SapState | null>(null);
  const [online, setOnline] = useState(false);
  const [aiConfigured, setAiConfigured] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [ledger, setLedger] = useState<LedgerEntry[]>([]);
  const [sapVersion, setSapVersion] = useState(1);

  const [page, setPage] = useState<Page>(() => {
    const h = window.location.hash.replace("#", "");
    return h === "cockpit" || h === "floor" ? h : "dashboard";
  });

  const [phase, setPhase] = useState<Phase>("normal");
  const [detectStep, setDetectStep] = useState(0);
  const [params, setParams] = useState<DisruptionParams | null>(null);
  const [result, setResult] = useState<DisruptResponse | null>(null);
  const [receipt, setReceipt] = useState<CockpitValue["receipt"]>(null);

  const [approvalFor, setApprovalFor] = useState<Scenario | null>(null);
  const [approving, setApproving] = useState(false);
  const [conflict, setConflict] = useState<DriftDetail | null>(null);
  const [approver, setApprover] = useState(APPROVER);
  const [injecting, setInjecting] = useState(false);

  const [live, setLive] = useState<Record<string, Record<string, number>>>({});
  const [wsStatus, setWsStatus] = useState<WsStatus>("connecting");
  const [lastAnalysis, setLastAnalysis] = useState<TelemetryFrame | null>(null);
  const [safety, setSafety] = useState<SafetyAlert | null>(null);
  const wsRef = useRef<WebSocket | null>(null);

  const [ledgerOpen, setLedgerOpen] = useState(false);
  const [verifying, setVerifying] = useState(false);
  const [verifyResult, setVerifyResult] = useState<CockpitValue["verifyResult"]>(null);

  const [chatOpen, setChatOpen] = useState(false);
  const [chatMode, setChatMode] = useState<"general" | "recalc">("general");
  const [messages, setMessages] = useState<ChatMessage[]>([]);
  const [recalcVersions, setRecalcVersions] = useState<{ from: number; to: number } | null>(null);
  const [chatBusy, setChatBusy] = useState(false);

  const [toast, setToast] = useState<string | null>(null);
  const [resetting, setResetting] = useState(false);

  // Phase 3: Skill Studio
  const [skillStudioOpen, setSkillStudioOpen] = useState(false);
  const [skillStudioState, setSkillStudioState] = useState<"interview" | "draft" | "lint" | "approved">("interview");
  const [skillStudioFormData, setSkillStudioFormData] = useState<SkillStudioFormData>({
    machine_type: "Vertical Machining Center",
    failure_mode: "Motor overload",
    sla_class_a_penalty_per_hour_idr: 20_000_000,
    sla_class_b_penalty_per_hour_idr: 300_000,
    sla_class_c_penalty_per_hour_idr: 100_000,
    overtime_cost_per_hour_idr: 450_000,
    changeover_cost_idr: 350_000,
    safety_thresholds: { motor_temp_celsius: 95, spindle_vibration_mm_per_s: 8, coolant_pressure_bar: 2 },
  });
  const [skillStudioDraft, setSkillStudioDraft] = useState<SkillStudioDraft | null>(null);
  const [skillStudioLintResult, setSkillStudioLintResult] = useState<SkillStudioLintResult | null>(null);

  const showToast = useCallback((t: string) => {
    setToast(t);
    window.setTimeout(() => setToast((cur) => (cur === t ? null : cur)), 4500);
  }, []);

  const load = useCallback(async () => {
    try {
      const [s, h, l] = await Promise.all([api.state(), api.health(), api.ledger().catch(() => [] as LedgerEntry[])]);
      setState(s);
      setSapVersion(s._meta.version);
      setLedger(l);
      setOnline(h.status === "ok");
      setAiConfigured(String(h.gemini_configured ?? "").startsWith("yes"));
      setError(null);
    } catch (e) {
      setOnline(false);
      setError(e instanceof Error ? e.message : String(e));
    }
  }, []);

  useEffect(() => {
    load();
    const t = window.setInterval(load, 15000);
    return () => window.clearInterval(t);
  }, [load]);

  useEffect(() => {
    window.location.hash = page;
  }, [page]);

  // ── telemetry websocket ──────────────────────────────────────────────────
  useEffect(() => {
    let stopped = false;
    let retry: number | undefined;
    const connect = () => {
      const ws = new WebSocket(WS_TELEMETRY);
      wsRef.current = ws;
      ws.onopen = () => setWsStatus("open");
      ws.onclose = () => {
        // A stale socket (e.g. React StrictMode's first mount) must not clear the live one.
        if (wsRef.current !== ws) return;
        setWsStatus("closed");
        wsRef.current = null;
        if (!stopped)
          retry = window.setTimeout(() => {
            setWsStatus("connecting");
            connect();
          }, 5000);
      };
      ws.onmessage = (ev) => {
        if (wsRef.current !== ws) return;
        let f: TelemetryFrame;
        try {
          f = JSON.parse(ev.data) as TelemetryFrame;
        } catch {
          return;
        }
        if (f.event === "telemetry_analysis" && f.machine_id && f.metric && typeof f.value === "number") {
          const { machine_id, metric, value } = f;
          setLive((m) => ({ ...m, [machine_id]: { ...(m[machine_id] ?? {}), [metric]: value } }));
          setLastAnalysis(f);
          if (f.severity === "CRITICAL" && f.rule_triggered) {
            setSafety({ machine_id, rule: f.rule_triggered, metric, value, message: f.analysis ?? "", timestamp: f.timestamp });
          }
        }
        if (f.event === "safety_alert" && f.machine_id) {
          const id = f.machine_id;
          setSafety((cur) => ({
            machine_id: id,
            rule: f.rule_triggered ?? cur?.rule ?? "SAFE",
            metric: cur?.metric ?? "",
            value: cur?.value ?? 0,
            message: f.message ?? cur?.message ?? "",
            timestamp: f.timestamp,
          }));
        }
      };
    };
    connect();
    return () => {
      stopped = true;
      if (retry) window.clearTimeout(retry);
      wsRef.current?.close();
    };
  }, []);

  const sendTelemetry = useCallback((machine_id: string, metric: string, value: number) => {
    if (wsRef.current?.readyState === WebSocket.OPEN) {
      wsRef.current.send(JSON.stringify({ machine_id, metric, value }));
    } else {
      showToast("Telemetri terputus. Pastikan backend berjalan di port 8000.");
    }
  }, [showToast]);

  // ── disruption → analysis → proposals ────────────────────────────────────
  const runDisruption = useCallback(
    async (p: DisruptionParams) => {
      setPage("cockpit");
      setParams(p);
      setResult(null);
      setReceipt(null);
      setConflict(null);
      setApprovalFor(null);
      setPhase("detecting");
      setDetectStep(0);
      // The solver can answer in milliseconds; keep the acknowledgement and steps on screen
      // long enough to read (PRD: agent state is never silent).
      const t1 = window.setTimeout(() => setDetectStep(1), 700);
      const t2 = window.setTimeout(() => setDetectStep(2), 1500);
      try {
        const [r] = await Promise.all([api.disrupt(p), new Promise((ok) => window.setTimeout(ok, 2300))]);
        setDetectStep(3);
        setResult(r);
        setSapVersion(r.sap_version);
        setPhase("proposing");
      } catch (e) {
        setPhase("normal");
        setError(e instanceof Error ? e.message : String(e));
        showToast("Analisis gagal. Periksa koneksi ke backend.");
      } finally {
        window.clearTimeout(t1);
        window.clearTimeout(t2);
      }
    },
    [showToast],
  );

  const runFullAnalysis = useCallback(async () => {
    if (!safety) return;
    const m = safety.machine_id;
    setSafety(null);
    await runDisruption({ ...DEMO_DISRUPTION, machine_id: m, disruption_type: safety.metric.includes("vibration") ? "VIBRATION" : "OVERHEAT" });
  }, [safety, runDisruption]);

  // ── approval with optimistic lock ────────────────────────────────────────
  const openApproval = useCallback((s: Scenario) => {
    setConflict(null);
    setApprovalFor(s);
  }, []);
  const closeApproval = useCallback(() => {
    setApprovalFor(null);
    setConflict(null);
  }, []);

  const confirmApproval = useCallback(async () => {
    if (!approvalFor) return;
    setApproving(true);
    // Real optimistic lock: the expected version is the SAP version at
    // analysis time. If a second disruption was injected since, this is
    // stale and the backend returns 409 — no fake ?drift query param.
    const expected = result?.sap_version ?? sapVersion;
    try {
      const r = await api.approve({ scenario_id: approvalFor.id, scenario_data: approvalFor, expected_sap_version: expected, approved_by: approver });
      setReceipt({ ...r, scenario: approvalFor });
      setSapVersion(r.sap_version_after);
      setApprovalFor(null);
      setConflict(null);
      setPhase("resolved");
      showToast("Skenario diterapkan. Audit ledger diperbarui.");
      await load();
    } catch (e) {
      if (e instanceof ApiError && e.status === 409) {
        const detail = e.detail as DriftDetail;
        setConflict(detail);
        setRecalcVersions({ from: detail.expected_version, to: detail.actual_version });
        await load();
        // Automatic re-solve on 409: recompute scenarios against the newest
        // SAP state so the supervisor sees fresh proposals immediately.
        if (params) {
          try {
            const r = await api.disrupt(params);
            setResult(r);
            setSapVersion(r.sap_version);
            setPhase("proposing");
            setApprovalFor(null);
            showToast(`SAP berubah (v${detail.expected_version} → v${detail.actual_version}). Skenario dihitung ulang otomatis.`);
          } catch {
            showToast("SAP berubah. Hitung ulang otomatis gagal — coba lagi manual.");
          }
        }
      } else {
        setError(e instanceof Error ? e.message : String(e));
        showToast("Persetujuan gagal dikirim. Tidak ada perubahan di SAP.");
      }
    } finally {
      setApproving(false);
    }
  }, [approvalFor, result, sapVersion, params, approver, load, showToast]);

  // ── failure drill: inject a real second disruption ───────────────────────
  const injectDrill = useCallback(async () => {
    setInjecting(true);
    try {
      const r = await api.injectDisruption({ machine_id: "CNC-03", new_status: "FAULT", disruption_type: "MOTOR_OVERLOAD" });
      await load();
      setConflict(null);
      showToast(
        result
          ? `Gangguan susulan diinjeksikan: CNC-03 FAULT (SAP v${r.sap_version_before} → v${r.sap_version_after}). Skenario lama basi — setujui untuk melihat 409 + hitung ulang otomatis.`
          : `Gangguan susulan diinjeksikan: CNC-03 FAULT (SAP v${r.sap_version_before} → v${r.sap_version_after}).`,
      );
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
      showToast("Injeksi gangguan gagal. Periksa koneksi ke backend.");
    } finally {
      setInjecting(false);
    }
  }, [load, result, showToast]);

  // ── machine status as the supervisor should see it right now ─────────────
  const displayStatus = useCallback(
    (machineId: string, raw: string): MachineStatus => {
      if (safety?.machine_id === machineId) return "FAULT";
      if (params?.machine_id === machineId) {
        if (phase === "detecting" || phase === "proposing") return "DISRUPTED";
        if (phase === "resolved") return "RECOVERING";
      }
      return normalizeStatus(raw);
    },
    [safety, params, phase],
  );

  // ── ledger ───────────────────────────────────────────────────────────────
  const verify = useCallback(async () => {
    setVerifying(true);
    try {
      const [v, l] = await Promise.all([api.verify(), api.ledger()]);
      setVerifyResult(v);
      setLedger(l);
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setVerifying(false);
    }
  }, []);

  // ── chat ─────────────────────────────────────────────────────────────────
  const push = (m: Omit<ChatMessage, "id">) => {
    const id = msgSeq++;
    setMessages((cur) => [...cur, { ...m, id }]);
    return id;
  };

  const openChat = useCallback(
    (mode: "general" | "recalc") => {
      setChatMode(mode);
      setChatOpen(true);
      if (mode === "recalc") {
        setApprovalFor(null);
        if (conflict) setRecalcVersions({ from: conflict.expected_version, to: conflict.actual_version });
        setMessages([
          {
            id: msgSeq++,
            from: "ai",
            text: `Jadwal SAP berubah sejak analisis (v${conflict?.expected_version ?? "?"} → v${conflict?.actual_version ?? "?"}), jadi skenario tadi tidak bisa disetujui. Tulis solusi Anda di bawah, atau minta saya menghitung ulang otomatis terhadap jadwal terbaru.`,
          },
        ]);
      } else if (messages.length === 0 || chatMode !== "general") {
        const firstName = approver.split("—").pop()?.trim().split(" ")[0] ?? "Budi";
        setMessages([
          {
            id: msgSeq++,
            from: "ai",
            text: `Halo, ${firstName}. Saya bisa menjelaskan status mesin, jadwal shift, dan skenario. Saya tidak mengubah apa pun tanpa persetujuan Anda.`,
          },
        ]);
      }
    },
    [conflict, messages.length, chatMode, approver],
  );
  const closeChat = useCallback(() => setChatOpen(false), []);

  const machineSummary = useCallback(() => {
    if (!state) return "Data mesin belum termuat. Periksa koneksi ke backend.";
    const counts: Record<string, string[]> = {};
    for (const w of state.work_centers) {
      const st = displayStatus(w.id, w.status);
      (counts[st] ??= []).push(w.id);
    }
    return Object.entries(counts)
      .map(([k, ids]) => `${k}: ${ids.join(", ")}`)
      .join(" · ");
  }, [state, displayStatus]);

  const recalc = useCallback(
    async (engineerNote: string | null) => {
      if (!params) {
        push({ from: "ai", text: "Tidak ada gangguan aktif untuk dihitung ulang." });
        return;
      }
      setChatBusy(true);
      const pid = push({ from: "ai", kind: "progress", text: "Menghitung ulang terhadap jadwal SAP terbaru…" });
      try {
        const s = await api.state();
        setState(s);
        const r = await api.disrupt(params);
        setResult(r);
        setSapVersion(r.sap_version);
        setConflict(null);
        const best = r.scenarios.find((x) => x.recommended) ?? r.scenarios[0];
        setMessages((cur) => cur.filter((m) => m.id !== pid));
        if (engineerNote) {
          push({
            from: "ai",
            text: "Solusi Anda tercatat di percakapan ini. Backend belum bisa menghitung batasan dari teks bebas, jadi saya jalankan solver lagi terhadap SAP terbaru. Bandingkan hasilnya dengan solusi Anda sebelum menyetujui.",
          });
        }
        push({
          from: "ai",
          kind: "result",
          scenario: best,
          text: `Hasil baru di atas SAP v${r.sap_version}: rekomendasi ${best?.name_id ?? "-"}, biaya ${formatIDR(best?.total_cost_idr ?? 0)}.`,
        });
      } catch (e) {
        setMessages((cur) => cur.filter((m) => m.id !== pid));
        push({ from: "ai", text: "Hitung ulang gagal: " + (e instanceof Error ? e.message : String(e)) });
      } finally {
        setChatBusy(false);
      }
    },
    [params],
  );

  const sendChat = useCallback(
    async (text: string) => {
      const t = text.trim();
      if (!t) return;
      push({ from: "user", text: t });
      if (chatMode === "recalc") {
        await recalc(t === "__auto__" ? null : t);
        return;
      }
      const q = t.toLowerCase();
      if (q.includes("status")) {
        push({ from: "ai", text: machineSummary() });
      } else if (q.includes("what-if") || q.includes("rusak")) {
        push({ from: "ai", text: `Menjalankan simulasi: CNC-02 BREAKDOWN ${windowLabel(2, 5)}.` });
        setChatOpen(false);
        await runDisruption(DEMO_DISRUPTION);
      } else if (q.includes("ledger") || q.includes("audit")) {
        setChatOpen(false);
        setLedgerOpen(true);
      } else {
        // Hybrid fallback: free-form questions go to Gemini via /api/chat,
        // grounded on live SAP state. Scripted fast paths above stay instant
        // and key-free; the solver/SAP paths are never touched by the LLM.
        setChatBusy(true);
        const pid = push({ from: "ai", kind: "progress", text: "AI Supervisor meninjau data SAP terbaru…" });
        try {
          const r = await api.chat(t);
          setMessages((cur) => cur.filter((m) => m.id !== pid));
          push({ from: "ai", text: r.reply });
        } catch {
          setMessages((cur) => cur.filter((m) => m.id !== pid));
          push({
            from: "ai",
            text: "AI tidak dapat dihubungi (periksa backend / GEMINI_API_KEY). Untuk sekarang saya bisa: status semua mesin, simulasi What-if, dan membuka audit ledger.",
          });
        } finally {
          setChatBusy(false);
        }
      }
    },
    [chatMode, recalc, machineSummary, runDisruption],
  );

  // ── reset ────────────────────────────────────────────────────────────────
  const reset = useCallback(async () => {
    setResetting(true);
    try {
      const s = await api.resetState();
      setState(s);
      setSapVersion(s._meta.version);
      setPhase("normal");
      setParams(null);
      setResult(null);
      setReceipt(null);
      setApprovalFor(null);
      setConflict(null);
      setSafety(null);
      setLive({});
      setRecalcVersions(null);
      showToast(`State dimuat ulang ke SAP v${s._meta.version}. Audit ledger tetap utuh.`);
      await load();
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setResetting(false);
    }
  }, [load, showToast]);

  const value = useMemo<CockpitValue>(
    () => ({
      state, online, aiConfigured, error, ledger, sapVersion,
      page, setPage,
      phase, detectStep, params, result, receipt, runDisruption,
      approvalFor, approving, conflict, approver, setApprover, openApproval, closeApproval, confirmApproval,
      injecting, injectDrill,
      displayStatus, live,
      wsStatus, lastAnalysis, safety, sendTelemetry, runFullAnalysis,
      ledgerOpen, setLedgerOpen, verifying, verifyResult, verify,
      chatOpen, chatMode, recalcVersions, messages, chatBusy, openChat, closeChat, sendChat,
      toast, reset, resetting,
      // Phase 3: Skill Studio
      skillStudioOpen, setSkillStudioOpen, skillStudioState, setSkillStudioState,
      skillStudioFormData, setSkillStudioFormData, skillStudioDraft, setSkillStudioDraft,
      skillStudioLintResult, setSkillStudioLintResult,
    }),
    [
      state, online, aiConfigured, error, ledger, sapVersion, page, phase, detectStep, params, result, receipt, runDisruption,
      approvalFor, approving, conflict, approver, openApproval, closeApproval, confirmApproval, injecting, injectDrill, displayStatus, live, wsStatus,
      lastAnalysis, safety, sendTelemetry, runFullAnalysis, ledgerOpen, verifying, verifyResult, verify, chatOpen,
      chatMode, recalcVersions, messages, chatBusy, openChat, closeChat, sendChat, toast, reset, resetting,
      skillStudioOpen, skillStudioState, skillStudioFormData, skillStudioDraft, skillStudioLintResult,
    ],
  );

  return <Ctx.Provider value={value}>{children}</Ctx.Provider>;
}
