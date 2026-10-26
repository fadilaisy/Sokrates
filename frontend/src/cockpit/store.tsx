import { createContext, useCallback, useContext, useEffect, useMemo, useRef, useState } from "react";
import type { ReactNode } from "react";
import { api, ApiError, formatIDR, WS_PLC, WS_TELEMETRY } from "../lib/api";
import type { ApproveReceipt, DisruptResponse, DriftDetail, LedgerEntry, PlcActionResult, PlcAlarmFrame, PlcInfo, PlcMachine, PlcPredictionFrame, PlcProposedAction, RagSource, SapState, Scenario, ShiftMetrics, TelemetryFrame } from "../lib/api";
import { normalizeStatus, windowLabel } from "../lib/shift";
import { audioReady, isMuted, onAudioReady, playSound, setMuted, unlockAudio } from "../lib/sound";
import type { MachineStatus } from "../lib/shift";
import type { SkillStudioFormData, SkillStudioDraft, SkillStudioLintResult } from "../lib/api";

export type Page = "dashboard" | "cockpit" | "floor" | "maintenance";
export type Phase = "normal" | "detecting" | "proposing" | "resolved";

export interface DisruptionParams {
  machine_id: string;
  disruption_type: string;
  start_hour: number; // shift-relative, 0 = 07:00
  end_hour: number;
}

/** The PRD's one-click demo: always CNC-02, hours 2–5, BREAKDOWN. */
export const DEMO_DISRUPTION: DisruptionParams = { machine_id: "CNC-02", disruption_type: "BREAKDOWN", start_hour: 2, end_hour: 5 };

const APPROVER_KEY = "sf.approver";
/** Signs PLC writes and reverts when nobody has typed a name yet. Scenario approval needs a real name. */
export const DEFAULT_SIGNER = "Production Supervisor";

function savedApprover(): string {
  try {
    return localStorage.getItem(APPROVER_KEY) ?? "";
  } catch {
    return "";
  }
}

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
  /** SOP/plant-document passages the reply cites as [S1], [S2] … */
  sources?: RagSource[];
  /** Optional HTML/SVG visual the AI added to its answer (shown in a sandboxed frame). */
  visual?: string | null;
}

type WsStatus = "connecting" | "open" | "closed";

interface CockpitValue {
  // data
  state: SapState | null;
  /** Shift downtime + OEE from /api/metrics; null while loading or if the backend lacks the endpoint. */
  metrics: ShiftMetrics | null;
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
  /** Resolves true when the solver answered, false when the analysis failed (already toasted). */
  runDisruption: (p: DisruptionParams) => Promise<boolean>;
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
  /** Seconds left before a critical alert starts the analysis by itself; null when no countdown runs. */
  autoIn: number | null;
  // simulated PLC feed
  plcOn: boolean;
  setPlcOn: (v: boolean) => void;
  plcStatus: WsStatus;
  plcInfo: PlcInfo | null;
  plcAlarm: PlcAlarmFrame | null;
  /** Set while the backend cannot read the PLC (plant network down); cleared by the next scan. */
  plcOffline: { error: string; timestamp: string } | null;
  startPlcScenario: (id: string) => Promise<void>;
  /** Latest PLC scan row per machine (setpoints, e-stop, lockout). */
  plcMachines: Record<string, PlcMachine>;
  plcPrediction: PlcPredictionFrame | null;
  plcAction: PlcActionResult | null;
  plcBusy: boolean;
  approvePlcAction: (p: PlcProposedAction) => Promise<void>;
  setPlcAutonomy: (enabled: boolean) => Promise<void>;
  setPlcLockout: (machineId: string, lockout: boolean) => Promise<void>;
  /** Book a maintenance window for a machine the trend says will fault (solver + approval → SAP). */
  planMaintenance: (machineId: string) => Promise<void>;
  // ledger
  ledgerOpen: boolean;
  setLedgerOpen: (v: boolean) => void;
  verifying: boolean;
  verifyResult: { valid: boolean; total_entries: number; first_broken_index?: number | null; message: string } | null;
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
  muted: boolean;
  toggleMute: () => void;
  toast: string | null;
  reset: () => Promise<void>;
  /** Reload SAP state, metrics and ledger (after a write made outside the store, e.g. an agent run). */
  reload: () => Promise<void>;
  showToast: (t: string) => void;
  /** Bumped when the PLC feed reports a new agent run, so the run panel refreshes at once. */
  agentRunPing: number;
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

/** A critical alert waits this long (so the alarm is seen) before the agent analyses it by itself. */
const AUTO_START_S = 2;

// oxlint-disable-next-line react/only-export-components
export function useCockpit(): CockpitValue {
  const v = useContext(Ctx);
  if (!v) throw new Error("useCockpit must be used inside <CockpitProvider>");
  return v;
}

let msgSeq = 1;

export function CockpitProvider({ children }: { children: ReactNode }) {
  const [state, setState] = useState<SapState | null>(null);
  const [metrics, setMetrics] = useState<ShiftMetrics | null>(null);
  const [online, setOnline] = useState(false);
  const [aiConfigured, setAiConfigured] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [ledger, setLedger] = useState<LedgerEntry[]>([]);
  const [sapVersion, setSapVersion] = useState(1);

  const [page, setPage] = useState<Page>(() => {
    const h = window.location.hash.replace("#", "");
    return h === "cockpit" || h === "floor" || h === "maintenance" ? h : "dashboard";
  });

  const [phase, setPhase] = useState<Phase>("normal");
  const [detectStep, setDetectStep] = useState(0);
  const [params, setParams] = useState<DisruptionParams | null>(null);
  const [result, setResult] = useState<DisruptResponse | null>(null);
  const [receipt, setReceipt] = useState<CockpitValue["receipt"]>(null);

  const [approvalFor, setApprovalFor] = useState<Scenario | null>(null);
  const [approving, setApproving] = useState(false);
  const [conflict, setConflict] = useState<DriftDetail | null>(null);
  const [approver, setApprover] = useState(savedApprover);
  const [agentRunPing, setAgentRunPing] = useState(0);
  const [injecting, setInjecting] = useState(false);

  const [live, setLive] = useState<Record<string, Record<string, number>>>({});
  const [wsStatus, setWsStatus] = useState<WsStatus>("connecting");
  const [lastAnalysis, setLastAnalysis] = useState<TelemetryFrame | null>(null);
  const [safety, setSafety] = useState<SafetyAlert | null>(null);
  const wsRef = useRef<WebSocket | null>(null);

  const [plcOn, setPlcOn] = useState(false);
  const [plcStatus, setPlcStatus] = useState<WsStatus>("closed");
  const [plcInfo, setPlcInfo] = useState<PlcInfo | null>(null);
  const [plcAlarm, setPlcAlarm] = useState<PlcAlarmFrame | null>(null);
  const [plcOffline, setPlcOffline] = useState<{ error: string; timestamp: string } | null>(null);
  const [plcMachines, setPlcMachines] = useState<Record<string, PlcMachine>>({});
  const [plcPrediction, setPlcPrediction] = useState<PlcPredictionFrame | null>(null);
  const [plcAction, setPlcAction] = useState<PlcActionResult | null>(null);
  const [plcBusy, setPlcBusy] = useState(false);

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
  const [muted, setMutedState] = useState(isMuted);

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

  // ── notification sounds ──────────────────────────────────────────────────
  // Both refs stay null until the first successful load, so the initial
  // fetch never beeps.
  const ledgerCount = useRef<number | null>(null);
  const machineStatus = useRef<Map<string, string> | null>(null);

  const noteLedger = useCallback((l: LedgerEntry[]) => {
    if (ledgerCount.current !== null && l.length > ledgerCount.current) playSound("ledger");
    ledgerCount.current = l.length;
    setLedger(l);
  }, []);

  // A machine newly in FAULT/DISRUPTED covers the drill and changes made in other tabs.
  const noteMachines = useCallback((s: SapState) => {
    const next = new Map(s.work_centers.map((w) => [w.id, normalizeStatus(w.status)] as [string, string]));
    const prev = machineStatus.current;
    if (prev && [...next].some(([id, st]) => (st === "FAULT" || st === "DISRUPTED") && prev.get(id) !== st)) {
      playSound("disruption");
    }
    machineStatus.current = next;
  }, []);

  const toggleMute = useCallback(() => {
    unlockAudio();
    setMutedState((m) => {
      setMuted(!m);
      return !m;
    });
  }, []);

  useEffect(() => {
    // iOS Safari only accepts touchend/click as the gesture that allows audio,
    // so listen to all of them until both the beeps and the paging file are unlocked.
    const events = ["pointerdown", "touchend", "click", "keydown"] as const;
    const unlock = () => {
      unlockAudio();
      if (audioReady()) stop();
    };
    const stop = () => events.forEach((ev) => window.removeEventListener(ev, unlock));
    events.forEach((ev) => window.addEventListener(ev, unlock));
    const unsubscribe = onAudioReady(() => audioReady() && stop());
    return () => {
      stop();
      unsubscribe();
    };
  }, []);

  const load = useCallback(async () => {
    try {
      const [s, h, l, m] = await Promise.all([
        api.state(),
        api.health(),
        // A failed ledger fetch keeps the last list rather than blanking it.
        api.ledger().catch(() => null),
        api.metrics().catch(() => null),
      ]);
      noteMachines(s);
      setState(s);
      setMetrics(m);
      setSapVersion(s._meta.version);
      if (l) noteLedger(l);
      setOnline(h.status === "ok");
      setAiConfigured(String(h.aicore_configured ?? "").startsWith("yes"));
      setError(null);
    } catch (e) {
      setOnline(false);
      setError(e instanceof Error ? e.message : String(e));
    }
  }, [noteMachines, noteLedger]);

  useEffect(() => {
    if (phase === "detecting") playSound("disruption");
  }, [phase]);

  const safetyKey = safety ? `${safety.machine_id}|${safety.rule}|${safety.timestamp ?? ""}` : null;
  useEffect(() => {
    if (safetyKey) playSound("telemetry");
  }, [safetyKey]);

  useEffect(() => {
    load();
    const t = window.setInterval(load, 15000);
    return () => window.clearInterval(t);
  }, [load]);

  useEffect(() => {
    window.location.hash = page;
  }, [page]);

  // ── frames from /ws/telemetry and /ws/plc ──────────────────────────────────
  const applyFrame = useCallback((data: string) => {
    let f: TelemetryFrame;
    try {
      f = JSON.parse(data) as TelemetryFrame;
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
  }, []);

  // Once a write lands, the proposal that asked for it is done; keep the cards from offering it again.
  const clearApplied = useCallback((r: PlcActionResult) => {
    if (!r.ok) return;
    setPlcPrediction((cur) => (cur?.machine_id === r.machine_id ? null : cur));
    setPlcAlarm((cur) => (cur?.machine_id === r.machine_id ? { ...cur, proposed_action: null } : cur));
  }, []);

  const applyPlcFrame = useCallback((data: string) => {
    let f: { event?: string; machines?: PlcMachine[]; autonomy?: PlcInfo["autonomy"] };
    try {
      f = JSON.parse(data);
    } catch {
      return;
    }
    if (f.event === "plc_scan" && f.machines) {
      const machines = f.machines;
      const running = machines.filter((m) => m.run);
      setLive((cur) => {
        const next = { ...cur };
        for (const m of running) next[m.machine_id] = { ...(cur[m.machine_id] ?? {}), ...m.tags };
        return next;
      });
      setPlcMachines(Object.fromEntries(machines.map((m) => [m.machine_id, m])));
      setPlcOffline(null);
      // Once the machine's alarm clears (e.g. after mitigation), its card goes too.
      const alarmed = new Set(machines.filter((m) => m.alarm).map((m) => m.machine_id));
      setPlcAlarm((cur) => (cur && !alarmed.has(cur.machine_id) ? null : cur));
      const autonomy = f.autonomy;
      if (autonomy)
        setPlcInfo((cur) =>
          cur ? (JSON.stringify(cur.autonomy) === JSON.stringify(autonomy) ? cur : { ...cur, autonomy }) : { driver: "", autonomy },
        );
      return;
    }
    if (f.event === "plc_offline") {
      const o = f as unknown as { error: string; timestamp: string };
      setPlcOffline({ error: o.error, timestamp: o.timestamp });
      return;
    }
    if (f.event === "plc_alarm") {
      playSound("telemetry");
      setPlcAlarm(f as unknown as PlcAlarmFrame);
      return;
    }
    if (f.event === "agent_run") {
      setAgentRunPing((n) => n + 1);
      return;
    }
    if (f.event === "plc_prediction") {
      setPlcPrediction(f as unknown as PlcPredictionFrame);
      return;
    }
    if (f.event === "plc_action") {
      const r = f as unknown as PlcActionResult;
      setPlcAction(r);
      clearApplied(r);
      return;
    }
    applyFrame(data);
  }, [applyFrame, clearApplied]);

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
        applyFrame(ev.data);
      };
    };
    connect();
    return () => {
      stopped = true;
      if (retry) window.clearTimeout(retry);
      wsRef.current?.close();
    };
  }, [applyFrame]);

  // ── simulated PLC feed (on demand: every scan is a server push) ─────────────
  useEffect(() => {
    if (!plcOn) return;
    let stopped = false;
    let retry: number | undefined;
    let ws: WebSocket | null = null;
    const connect = () => {
      setPlcStatus("connecting");
      ws = new WebSocket(WS_PLC);
      ws.onopen = () => setPlcStatus("open");
      ws.onmessage = (ev) => applyPlcFrame(ev.data);
      ws.onclose = () => {
        if (stopped) return;
        setPlcStatus("closed");
        retry = window.setTimeout(connect, 5000);
      };
    };
    connect();
    // Keep autonomy from a scan frame that arrived first; the rest comes from the API.
    api.plc().then((info) => setPlcInfo((cur) => ({ ...info, autonomy: cur?.autonomy ?? info.autonomy }))).catch(() => undefined);
    return () => {
      stopped = true;
      if (retry) window.clearTimeout(retry);
      ws?.close();
      setPlcStatus("closed");
    };
  }, [plcOn, applyPlcFrame]);

  const startPlcScenario = useCallback(async (id: string) => {
    try {
      const info = await api.plcScenario(id);
      setPlcInfo(info);
      setPlcAlarm(null);
      setPlcPrediction(null);
      setPlcAction(null);
      setSafety(null);
      setPlcOn(true);
    } catch (e) {
      showToast(`Skenario PLC gagal: ${e instanceof Error ? e.message : String(e)}`);
    }
  }, [showToast]);

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
        return true;
      } catch (e) {
        setPhase("normal");
        setError(e instanceof Error ? e.message : String(e));
        showToast("Analisis gagal. Periksa koneksi ke backend.");
        return false;
      } finally {
        window.clearTimeout(t1);
        window.clearTimeout(t2);
      }
    },
    [showToast],
  );

  // A tap on the countdown and the timer can land in the same render; only the first one starts.
  const startedFor = useRef<SafetyAlert | null>(null);
  const runFullAnalysis = useCallback(async () => {
    if (!safety || startedFor.current === safety) return;
    startedFor.current = safety;
    const m = safety.machine_id;
    setSafety(null);
    await runDisruption({ ...DEMO_DISRUPTION, machine_id: m, disruption_type: safety.metric.includes("vibration") ? "VIBRATION" : "OVERHEAT" });
  }, [safety, runDisruption]);

  // ── PLC actions: approved writes, limited autonomy, demo lockout ──────────
  const approvePlcAction = useCallback(async (p: PlcProposedAction) => {
    setPlcBusy(true);
    try {
      const r = await api.plcAction({ machine_id: p.machine_id, tag: p.tag, value: p.value, approved_by: approver.trim() || DEFAULT_SIGNER, action_id: p.action_id, reason: p.label });
      setPlcAction(r);
      clearApplied(r);
      showToast(`${p.machine_id}: ${p.tag} ${r.before ?? "?"} → ${r.after}. Tercatat di audit ledger.`);
    } catch (e) {
      // A refused write comes back as 409 with every check, so the operator sees what happened.
      if (e instanceof ApiError && e.status === 409 && e.detail && typeof e.detail === "object") {
        const r = e.detail as PlcActionResult;
        setPlcAction(r);
        showToast(
          r.outcome === "blocked"
            ? `Penulisan ke ${p.machine_id} diblokir oleh pemeriksaan keselamatan.`
            : `Penulisan ke ${p.machine_id} gagal: ${r.error ?? "periksa di HMI mesin"}.`,
        );
      } else {
        showToast(`Penulisan PLC gagal: ${e instanceof Error ? e.message : String(e)}`);
      }
    } finally {
      setPlcBusy(false);
      void load();
    }
  }, [approver, showToast, load, clearApplied]);

  const setPlcAutonomy = useCallback(async (enabled: boolean) => {
    try {
      setPlcInfo(await api.plcAutonomy(enabled, approver.trim() || DEFAULT_SIGNER));
      void load();
    } catch (e) {
      showToast(`Autonomi gagal diubah: ${e instanceof Error ? e.message : String(e)}`);
    }
  }, [approver, showToast, load]);

  const setPlcLockout = useCallback(async (machineId: string, lockout: boolean) => {
    try {
      const r = await api.plcInterlock({ machine_id: machineId, lockout });
      setPlcMachines((cur) => (cur[machineId] ? { ...cur, [machineId]: { ...cur[machineId], lockout: r.lockout } } : cur));
    } catch (e) {
      showToast(`Lockout gagal diubah: ${e instanceof Error ? e.message : String(e)}`);
    }
  }, [showToast]);

  const planMaintenance = useCallback(async (machineId: string) => {
    // Next full hour of the shift (07:00 = hour 0), a 2-hour window, inside the 8-hour shift.
    // Too late in the shift for a 2-hour window: the start of the next shift instead, never the past.
    const wibHour = (new Date().getUTCHours() + 7) % 24;
    const next = wibHour - 7 + 1;
    const start = next >= 0 && next <= 6 ? next : 0;
    if (start !== next) showToast(`Shift ini tidak cukup untuk 2 jam perawatan; dijadwalkan di awal shift berikutnya (07:00).`);
    if (await runDisruption({ machine_id: machineId, disruption_type: "MAINTENANCE", start_hour: start, end_hour: start + 2 }))
      setPlcPrediction(null);
  }, [runDisruption, showToast]);

  // Proactive start: a critical alert counts down and then analyses on its own. The key leaves out
  // the timestamp so repeated frames for the same fault do not restart the count. Not on the
  // maintenance page, where a mechanic is reading the playbook and must not be pulled away.
  const [autoIn, setAutoIn] = useState<number | null>(null);
  const autoKey =
    safety && (phase === "normal" || phase === "resolved") && page !== "maintenance" ? `${safety.machine_id}|${safety.rule}` : null;
  const runFullRef = useRef(runFullAnalysis);
  runFullRef.current = runFullAnalysis;
  useEffect(() => {
    if (!autoKey) {
      setAutoIn(null);
      return;
    }
    let left = AUTO_START_S;
    setAutoIn(left);
    const t = window.setInterval(() => {
      left -= 1;
      if (left > 0) return setAutoIn(left);
      window.clearInterval(t);
      setAutoIn(null);
      void runFullRef.current();
    }, 1000);
    return () => window.clearInterval(t);
  }, [autoKey]);

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
      try {
        localStorage.setItem(APPROVER_KEY, approver.trim());
      } catch {
        /* private mode: the name is only kept for this visit */
      }
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
      noteLedger(l);
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setVerifying(false);
    }
  }, [noteLedger]);

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
        const firstName = approver.split("—").pop()?.trim().split(" ")[0];
        setMessages([
          {
            id: msgSeq++,
            from: "ai",
            text: `Halo${firstName ? `, ${firstName}` : ""}. Saya bisa menjelaskan status mesin, jadwal shift, dan skenario. Saya tidak mengubah apa pun tanpa persetujuan Anda.`,
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
        // Hybrid fallback: free-form questions go to SAP AI Core via /api/chat,
        // grounded on live SAP state. Scripted fast paths above stay instant
        // and key-free; the solver/SAP paths are never touched by the LLM.
        setChatBusy(true);
        const pid = push({ from: "ai", kind: "progress", text: "AI Supervisor meninjau data SAP terbaru…" });
        try {
          const r = await api.chat(t);
          setMessages((cur) => cur.filter((m) => m.id !== pid));
          push({ from: "ai", text: r.reply, sources: r.sources, visual: r.visual });
        } catch {
          setMessages((cur) => cur.filter((m) => m.id !== pid));
          push({
            from: "ai",
            text: "AI tidak dapat dihubungi (periksa backend / kredensial SAP AI Core). Untuk sekarang saya bisa: status semua mesin, simulasi What-if, dan membuka audit ledger.",
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
      setPlcAlarm(null);
      setPlcPrediction(null);
      setPlcAction(null);
      api.plcScenario("normal").then(setPlcInfo).catch(() => undefined);
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
      state, metrics, online, aiConfigured, error, ledger, sapVersion,
      page, setPage,
      phase, detectStep, params, result, receipt, runDisruption,
      approvalFor, approving, conflict, approver, setApprover, openApproval, closeApproval, confirmApproval,
      injecting, injectDrill,
      displayStatus, live,
      wsStatus, lastAnalysis, safety, sendTelemetry, runFullAnalysis, autoIn,
      plcOn, setPlcOn, plcStatus, plcInfo, plcAlarm, plcOffline, startPlcScenario,
      plcMachines, plcPrediction, plcAction, plcBusy, approvePlcAction, setPlcAutonomy, setPlcLockout, planMaintenance,
      ledgerOpen, setLedgerOpen, verifying, verifyResult, verify,
      chatOpen, chatMode, recalcVersions, messages, chatBusy, openChat, closeChat, sendChat,
      muted, toggleMute, toast, reset, resetting, reload: load, showToast, agentRunPing,
      // Phase 3: Skill Studio
      skillStudioOpen, setSkillStudioOpen, skillStudioState, setSkillStudioState,
      skillStudioFormData, setSkillStudioFormData, skillStudioDraft, setSkillStudioDraft,
      skillStudioLintResult, setSkillStudioLintResult,
    }),
    [
      state, metrics, online, aiConfigured, error, ledger, sapVersion, page, phase, detectStep, params, result, receipt, runDisruption,
      approvalFor, approving, conflict, approver, openApproval, closeApproval, confirmApproval, injecting, injectDrill, displayStatus, live, wsStatus,
      lastAnalysis, safety, sendTelemetry, runFullAnalysis, autoIn, plcOn, plcStatus, plcInfo, plcAlarm, plcOffline, startPlcScenario,
      plcMachines, plcPrediction, plcAction, plcBusy, approvePlcAction, setPlcAutonomy, setPlcLockout, planMaintenance, ledgerOpen, verifying, verifyResult, verify, chatOpen,
      chatMode, recalcVersions, messages, chatBusy, openChat, closeChat, sendChat, muted, toggleMute, toast, reset, resetting,
      load, showToast, agentRunPing,
      skillStudioOpen, skillStudioState, skillStudioFormData, skillStudioDraft, skillStudioLintResult,
    ],
  );

  return <Ctx.Provider value={value}>{children}</Ctx.Provider>;
}
