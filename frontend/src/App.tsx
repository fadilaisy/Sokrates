import { useCallback, useEffect, useRef, useState } from "react";
import Gantt from "./components/Gantt";
import LatticeLoader from "./components/LatticeLoader";
import ShapeWaves from "./components/ShapeWaves";
import { api, formatIDR } from "./lib/api";
import type { DisruptResponse, LedgerEntry, SapState, Scenario } from "./lib/api";

const APPROVAL_THRESHOLD = 1_000_000;

// Design system colors
const COLORS = {
  bg: "#22223B",
  card: "#2A2A40",
  text: "#F9F7F7",
  muted: "#9A8C98",
  primary: "#F2A900",
  secondary: "#4A4E69",
  border: "#353A50",
  success: "#CBF3F0",
  warning: "#F2A900",
  error: "#E76F51",
};

export default function App() {
  const [state, setState] = useState<SapState | null>(null);
  const [health, setHealth] = useState<Record<string, string> | null>(null);
  const [err, setErr] = useState<string | null>(null);

  const [machine, setMachine] = useState("CNC-02");
  const [dtype, setDtype] = useState("BREAKDOWN");
  const [sh, setSh] = useState(2);
  const [eh, setEh] = useState(5);
  const [busy, setBusy] = useState(false);
  const [disrupt, setDisrupt] = useState<DisruptResponse | null>(null);
  const [sapVersion, setSapVersion] = useState<number>(1);

  const [approvedBy, setApprovedBy] = useState("Production Supervisor — Budi Santoso");
  const [approving, setApproving] = useState<string | null>(null);
  const [receipt, setReceipt] = useState<string | null>(null);

  const [ledger, setLedger] = useState<LedgerEntry[]>([]);
  const [verify, setVerify] = useState<string | null>(null);

  // telemetry
  const [wsOn, setWsOn] = useState(false);
  const [tMachine, setTMachine] = useState("CNC-04");
  const [tMetric, setTMetric] = useState("motor_temp_celsius");
  const [tValue, setTValue] = useState("91.5");
  const [autoFeed, setAutoFeed] = useState(false);
  const [msgs, setMsgs] = useState<Record<string, unknown>[]>([]);
  const wsRef = useRef<WebSocket | null>(null);
  const feedRef = useRef<number | null>(null);

  const load = useCallback(async () => {
    setErr(null);
    try {
      const [s, h, l] = await Promise.all([api.state(), api.health(), api.ledger().catch(() => [])]);
      setState(s);
      setHealth(h);
      setSapVersion(s._meta.version);
      setLedger(l);
      if (s.work_centers.some((w) => w.id === machine) === false && s.work_centers[0]) {
        setMachine(s.work_centers[0].id);
      }
    } catch (e) {
      setErr(e instanceof Error ? e.message : String(e));
    }
  }, [machine]);

  useEffect(() => {
    load();
  }, [load]);

  async function onDisrupt() {
    setBusy(true);
    setErr(null);
    setReceipt(null);
    try {
      const r = await api.disrupt({ machine_id: machine, disruption_type: dtype, start_hour: sh, end_hour: eh });
      setDisrupt(r);
      setSapVersion(r.sap_version);
    } catch (e) {
      setErr(e instanceof Error ? e.message : String(e));
    } finally {
      setBusy(false);
    }
  }

  async function onApprove(sc: Scenario) {
    setApproving(sc.id);
    setErr(null);
    try {
      const r = await api.approve({
        scenario_id: sc.id,
        scenario_data: sc,
        expected_sap_version: sapVersion,
        approved_by: approvedBy,
      });
      setReceipt(`Skenario ${sc.id} diterapkan. SAP ${r.sap_version_before} -> ${r.sap_version_after}. Receipt ${r.receipt_id}.`);
      setSapVersion(r.sap_version_after);
      await load();
    } catch (e) {
      setErr(e instanceof Error ? e.message : String(e));
    } finally {
      setApproving(null);
    }
  }

  async function onVerify() {
    try {
      const v = await api.verify();
      setVerify(`${v.valid ? "Valid" : "RUSAK"} - ${v.message}`);
    } catch (e) {
      setVerify(e instanceof Error ? e.message : String(e));
    }
  }

  // telemetry WS
  function wsConnect() {
    if (wsRef.current) return;
    const ws = new WebSocket("ws://localhost:8000/ws/telemetry");
    ws.onopen = () => setWsOn(true);
    ws.onmessage = (ev) => {
      try {
        setMsgs((m) => [{ ...(JSON.parse(ev.data) as object) }, ...m].slice(0, 30));
      } catch {
        setMsgs((m) => [{ raw: ev.data }, ...m].slice(0, 30));
      }
    };
    ws.onclose = () => {
      setWsOn(false);
      wsRef.current = null;
      if (feedRef.current) window.clearInterval(feedRef.current);
      setAutoFeed(false);
    };
    wsRef.current = ws;
  }
  function wsClose() {
    wsRef.current?.close();
  }
  function wsSend() {
    const v = Number(tValue);
    if (Number.isNaN(v)) {
      setErr("Nilai telemetri harus angka - backend crash untuk non-numerik.");
      return;
    }
    wsRef.current?.send(JSON.stringify({ machine_id: tMachine, metric: tMetric, value: v }));
  }
  function toggleFeed() {
    if (autoFeed) {
      if (feedRef.current) window.clearInterval(feedRef.current);
      setAutoFeed(false);
      return;
    }
    if (!wsRef.current) wsConnect();
    feedRef.current = window.setInterval(() => {
      const temp = 70 + Math.random() * 28;
      wsRef.current?.send(JSON.stringify({ machine_id: tMachine, metric: "motor_temp_celsius", value: Number(temp.toFixed(1)) }));
    }, 8000);
    setAutoFeed(true);
  }

  return (
    <div className="relative min-h-screen bg-[#22223B] text-[#F9F7F7] overflow-hidden font-sans">
      {/* Background with subtle gradients */}
      <div className="fixed inset-0 z-0 h-[60vh]">
        <ShapeWaves
          text="SkillForge"
          color="#4A4E69"
          hoverColor="#F9F7F7"
          backgroundColor="#22223B"
          speed={1}
          glow={0.25}
        />
      </div>
      <div className="fixed inset-0 z-0 bg-gradient-to-b from-transparent to-[#22223B]/90" />

      <div className="relative z-10">
        {/* Header */}
        <header className="sticky top-0 z-50 border-b border-[#353A50] bg-[#22223B]/80 backdrop-blur-md">
          <div className="mx-auto flex max-w-7xl items-center justify-between px-6 py-4">
            <div className="flex flex-col">
              <h1 className="text-xl font-semibold tracking-tight text-[#F9F7F7]">SkillForge</h1>
              <p className="text-xs text-[#9A8C98]">
                {state?._meta.facility ?? "PT Karawang Precision Manufacturing"} · Bahasa Indonesia · Biaya dalam Rupiah
              </p>
            </div>
            <div className="flex items-center gap-3">
              <span className={`flex items-center gap-2 rounded-lg px-3 py-1.5 text-xs font-medium ${health ? "bg-[#CBF3F0]/10 text-[#CBF3F0]" : "bg-[#4A4E69] text-[#9A8C98]"}`}>
                <span className={`h-2 w-2 rounded-full ${health ? "bg-[#CBF3F0]" : "bg-[#9A8C98]"}`} />
                Backend: {health ? `OK v${health.sap_version}` : "OFFLINE"}
              </span>
              <span className="rounded-lg bg-[#353A50] px-3 py-1.5 text-xs text-[#F9F7F7]">SAP v{sapVersion}</span>
              <button
                onClick={load}
                className="rounded-lg bg-[#F2A900] px-4 py-1.5 text-xs font-semibold text-[#F9F7F7] hover:bg-[#E29B00] active:scale-95 transition-transform shadow-lg shadow-[#F2A900]/20"
              >
                Muat ulang
              </button>
            </div>
          </div>
        </header>

        {/* Main Content */}
        <main className="mx-auto max-w-7xl space-y-6 px-6 py-8">
          {err && (
            <div className="animate-scale-in rounded-xl border border-[#E76F51]/30 bg-[#E76F51]/5 p-4 text-sm text-[#E76F51] shadow-lg shadow-[#E76F51]/10">
              <p className="font-semibold">Gagal: {err}</p>
              <p className="mt-1 text-xs text-[#E76F51]/70">
                Pastikan backend jalan: uvicorn backend.main:app --port 8000
              </p>
            </div>
          )}

          {/* Work Centers Grid */}
          <section className="animate-fade-in">
            <h2 className="mb-4 text-sm font-semibold text-[#F9F7F7]">Status Mesin</h2>
            <div className="grid grid-cols-2 gap-4 sm:grid-cols-3 lg:grid-cols-5">
              {state?.work_centers.map((w, i) => (
                <div
                  key={w.id}
                  className="card-hover group relative overflow-hidden rounded-xl border border-[#353A50] bg-[#2A2A40] p-4 hover:border-[#4A4E69] hover:bg-[#353A50]"
                  style={{ animationDelay: `${i * 50}ms` }}
                >
                  <div className="absolute inset-0 bg-gradient-to-br from-[#F9F7F7]/5 to-transparent opacity-0 transition-opacity group-hover:opacity-100" />
                  <div className="flex items-start justify-between">
                    <div>
                      <div className="flex items-center gap-2">
                        <span className="font-semibold text-[#F9F7F7]">{w.id}</span>
                        <span className={`rounded px-1.5 py-0.5 text-[10px] font-bold transition-colors ${w.status === "RUNNING" ? "bg-[#CBF3F0]/20 text-[#CBF3F0]" : w.status === "MAINTENANCE" ? "bg-[#E76F51]/20 text-[#E76F51]" : "bg-[#4A4E69]/50 text-[#9A8C98]"}`}>
                          {w.status}
                        </span>
                      </div>
                      <p className="mt-1 truncate text-xs text-[#9A8C98]">{w.name}</p>
                      <p className="mt-0.5 text-[11px] text-[#9A8C98]">Operator: {w.operator_name ?? "-"}</p>
                    </div>
                  </div>
                  {w.telemetry && (
                    <div className="mt-3 grid grid-cols-2 gap-2 rounded-lg bg-[#22223B] p-2 font-mono text-[10px] text-[#9A8C98] transition-opacity group-hover:opacity-80">
                      <div>
                        <p className="text-[9px] uppercase">Temp</p>
                        <p>{w.telemetry.motor_temp_celsius ?? "?"}°C</p>
                      </div>
                      <div>
                        <p className="text-[9px] uppercase">Vib</p>
                        <p>{w.telemetry.spindle_vibration_mm_per_s ?? "?"} mm/s</p>
                      </div>
                    </div>
                  )}
                </div>
              ))}
              {state?.work_centers.length === 0 && <p className="text-sm text-[#9A8C98]">Memuat state…</p>}
            </div>
          </section>

          {/* Current Schedule */}
          <section className="animate-fade-in" style={{ animationDelay: '100ms' }}>
            <h2 className="mb-3 text-sm font-semibold text-[#F9F7F7]">Jadwal Saat Ini</h2>
            <Gantt orders={state?.production_orders} title="" />
          </section>

          {/* Disruption Simulation */}
          <section className="animate-fade-in" style={{ animationDelay: '150ms' }}>
            <div className="overflow-hidden rounded-2xl border border-[#353A50] bg-[#2A2A40] shadow-xl shadow-black/30">
              <div className="border-b border-[#353A50] bg-[#22223B]/50 px-6 py-4">
                <h2 className="text-sm font-semibold text-[#F9F7F7]">Simulasi Gangguan</h2>
              </div>
              <div className="p-6">
                <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-5">
                  <label className="text-xs text-[#9A8C98]">
                    Mesin
                    <select
                      value={machine}
                      onChange={(e) => setMachine(e.target.value)}
                      className="mt-2 w-full rounded-lg bg-[#22223B] border border-[#353A50] px-3 py-2 text-sm text-[#F9F7F7] transition-all hover:border-[#4A4E69] focus:border-[#4A4E69] focus:outline-none focus:ring-2 focus:ring-[#4A4E69]/50"
                    >
                      {state?.work_centers.map((w) => (
                        <option key={w.id} value={w.id}>
                          {w.id} - {w.name}
                        </option>
                      ))}
                    </select>
                  </label>
                  <label className="text-xs text-[#9A8C98]">
                    Jenis
                    <select
                      value={dtype}
                      onChange={(e) => setDtype(e.target.value)}
                      className="mt-2 w-full rounded-lg bg-[#22223B] border border-[#353A50] px-3 py-2 text-sm text-[#F9F7F7] transition-all hover:border-[#4A4E69] focus:border-[#4A4E69] focus:outline-none focus:ring-2 focus:ring-[#4A4E69]/50"
                    >
                      <option>BREAKDOWN</option>
                      <option>OVERHEAT</option>
                      <option>TOOL_WEAR</option>
                      <option>POWER_DIP</option>
                    </select>
                  </label>
                  <label className="text-xs text-[#9A8C98]">
                    Mulai (jam ke-)
                    <input
                      type="number"
                      min={0}
                      max={7}
                      value={sh}
                      onChange={(e) => setSh(Number(e.target.value))}
                      className="mt-2 w-full rounded-lg bg-[#22223B] border border-[#353A50] px-3 py-2 text-sm text-[#F9F7F7] transition-all hover:border-[#4A4E69] focus:border-[#4A4E69] focus:outline-none focus:ring-2 focus:ring-[#4A4E69]/50"
                    />
                  </label>
                  <label className="text-xs text-[#9A8C98]">
                    Selesai (jam ke-)
                    <input
                      type="number"
                      min={1}
                      max={8}
                      value={eh}
                      onChange={(e) => setEh(Number(e.target.value))}
                      className="mt-2 w-full rounded-lg bg-[#22223B] border border-[#353A50] px-3 py-2 text-sm text-[#F9F7F7] transition-all hover:border-[#4A4E69] focus:border-[#4A4E69] focus:outline-none focus:ring-2 focus:ring-[#4A4E69]/50"
                    />
                  </label>
                  <div className="flex items-end">
                    <button
                      onClick={onDisrupt}
                      disabled={busy || !state}
                      className="flex w-full items-center justify-center gap-2 rounded-lg bg-[#F2A900] px-4 py-3 font-semibold text-[#F9F7F7] transition-all hover:bg-[#E29B00] hover:shadow-lg active:scale-[0.98] disabled:opacity-50 disabled:cursor-not-allowed disabled:active:scale-100"
                    >
                      {busy ? (
                        <>
                          <span className="animate-spin rounded-full border-2 border-[#9A8C98] border-t-[#F9F7F7] h-4 w-4" />
                          <span>Solver…</span>
                        </>
                      ) : (
                        "Analisis Gangguan"
                      )}
                    </button>
                  </div>
                </div>

                {disrupt && (
                  <div className="mt-4 animate-scale-in rounded-lg border border-[#F2A900]/20 bg-[#F2A900]/5 p-4">
                    <p className="mb-2 text-xs font-medium text-[#F2A900]">Ringkasan AI (Bahasa Indonesia):</p>
                    <p className="text-sm text-[#F9F7F7] leading-relaxed">{disrupt.claude_summary}</p>
                  </div>
                )}
              </div>
            </div>
          </section>

          {/* Recovery Scenarios */}
          {disrupt && (
            <section className="space-y-4 animate-fade-in" style={{ animationDelay: '200ms' }}>
              <h2 className="text-sm font-semibold text-[#F9F7F7]">Skenario Pemulihan</h2>
              <div className="grid gap-4 lg:grid-cols-3">
                {disrupt.scenarios.map((sc, i) => {
                  const penalty = sc.gantt_changes.reduce((s, c) => s + (c.sla_penalty_idr ?? 0), 0);
                  const needApproval = penalty >= APPROVAL_THRESHOLD;
                  return (
                    <div
                      key={sc.id}
                      className={`card-hover relative overflow-hidden rounded-xl border p-5 hover:shadow-2xl hover:shadow-black/30 ${
                        sc.recommended
                          ? "border-[#F2A900]/40 bg-[#F2A900]/5"
                          : "border-[#353A50] bg-[#2A2A40]"
                      }`}
                      style={{ animationDelay: `${i * 100}ms` }}
                    >
                      {sc.recommended && (
                        <div className="absolute -right-6 -top-6 rotate-12 rounded-full bg-[#F2A900] px-3 py-1 text-[10px] font-bold text-[#F9F7F7] shadow-lg shadow-[#F2A900]/20">
                          REKOMENDASI
                        </div>
                      )}
                      <div className="flex items-center justify-between">
                        <h3 className="text-base font-semibold text-[#F9F7F7]">
                          {sc.id === "scenario_a"
                            ? "Status Quo"
                            : sc.id === "scenario_b"
                              ? "Lembur"
                              : "Rerute Optimal"}
                        </h3>
                        <span className="rounded bg-[#22223B] px-2 py-1 text-xs font-mono text-[#9A8C98]">
                          {sc.id.replace("scenario_", "Skenario ")}
                        </span>
                      </div>
                      <p className="mt-1 text-xs text-[#9A8C98]">{sc.name_id}</p>
                      <div className="mt-4 flex items-baseline gap-2">
                        <span className="text-3xl font-bold text-[#F9F7F7]">{formatIDR(sc.total_cost_idr)}</span>
                        <span className="text-sm font-medium text-[#CBF3F0]">
                          Hemat: {formatIDR(sc.net_savings_idr)}
                        </span>
                      </div>
                      <p className="mt-3 text-xs leading-relaxed text-[#F9F7F7]/80">{sc.rationale_template}</p>
                      <div className="mt-3 flex flex-wrap gap-2 text-[11px] text-[#9A8C98]">
                        <span className="rounded bg-[#22223B] px-2 py-0.5">{sc.affected_orders.length} order</span>
                        <span className="rounded bg-[#22223B] px-2 py-0.5">{sc.gantt_changes.length} perubahan</span>
                      </div>
                      {needApproval && (
                        <p className="mt-2 text-[10px] font-semibold text-[#F2A900]">
                          Denda ≥ Rp 1.000.000 — persetujuan supervisor wajib
                        </p>
                      )}
                      <Gantt changes={sc.gantt_changes} title="" />
                      <button
                        onClick={() => onApprove(sc)}
                        disabled={approving !== null || !state}
                        className="mt-4 w-full rounded-lg bg-[#F2A900] px-4 py-2.5 text-sm font-bold text-[#F9F7F7] transition-all hover:bg-[#E29B00] hover:shadow-lg hover:shadow-[#F2A900]/20 active:scale-[0.98] disabled:opacity-50 disabled:cursor-not-allowed"
                      >
                        {approving === sc.id ? (
                          <span className="flex items-center justify-center gap-2">
                            <span className="animate-spin rounded-full border-2 border-[#F9F7F7] border-t-transparent h-4 w-4" />
                            Menerapkan…
                          </span>
                        ) : (
                          `Setujui ${sc.id.replace("scenario_", "Skenario ")} (1 klik)`
                        )}
                      </button>
                    </div>
                  );
                })}
              </div>
              <label className="mt-6 block text-xs text-[#9A8C98]">
                Disetujui oleh
                <input
                  value={approvedBy}
                  onChange={(e) => setApprovedBy(e.target.value)}
                  className="mt-2 w-full rounded-lg bg-[#22223B] border border-[#353A50] px-4 py-2.5 text-sm text-[#F9F7F7] transition-all hover:border-[#4A4E69] focus:border-[#4A4E69] focus:outline-none focus:ring-2 focus:ring-[#4A4E69]/50"
                />
              </label>
              {receipt && (
                <div className="animate-scale-in rounded-lg border border-[#CBF3F0]/20 bg-[#CBF3F0]/5 p-4 text-sm text-[#CBF3F0]">
                  {receipt}
                </div>
              )}
              <p className="text-[10px] text-[#9A8C98]">
                Catatan: Setelah approve, order jadi RESCHEDULED sehingga solver mengabaikannya pada gangguan berikut.
                Restart backend untuk mengulang demo.
              </p>
            </section>
          )}

          {/* Audit Ledger */}
          <section className="animate-fade-in" style={{ animationDelay: '300ms' }}>
            <div className="overflow-hidden rounded-2xl border border-[#353A50] bg-[#2A2A40] shadow-xl shadow-black/30">
              <div className="flex items-center justify-between px-6 py-4 border-b border-[#353A50]">
                <h2 className="text-sm font-semibold text-[#F9F7F7]">Audit Ledger</h2>
                <button
                  onClick={onVerify}
                  className="rounded-full bg-[#353A50] px-3 py-1.5 text-xs text-[#F9F7F7] transition-all hover:bg-[#4A4E69] hover:shadow active:scale-95"
                >
                  Verifikasi rantai
                </button>
              </div>
              {verify && <div className="px-6 pt-3 text-xs text-[#F9F7F7]">{verify}</div>}
              <div className="overflow-x-auto">
                <table className="w-full text-left text-xs">
                  <thead>
                    <tr className="border-b border-[#353A50] text-[#9A8C98]">
                      <th className="px-6 py-3 font-medium">Waktu</th>
                      <th className="px-6 py-3 font-medium">Aksi</th>
                      <th className="px-6 py-3 font-medium">Skenario</th>
                      <th className="px-6 py-3 font-medium">v</th>
                      <th className="px-6 py-3 font-medium">Oleh</th>
                      <th className="px-6 py-3 font-medium">Hash</th>
                    </tr>
                  </thead>
                  <tbody className="divide-y divide-[#353A50]">
                    {ledger.map((e, i) => (
                      <tr key={e.id} className="hover:bg-[#353A50]/50 transition-colors">
                        <td className="px-6 py-3 font-mono text-[10px] text-[#9A8C98]">
                          {String(e.timestamp).slice(0, 19)}
                        </td>
                        <td className="px-6 py-3">{e.action_type}</td>
                        <td className="px-6 py-3">
                          <span className="rounded bg-[#22223B] px-2 py-0.5 text-[10px]">{e.scenario_chosen}</span>
                        </td>
                        <td className="px-6 py-3 font-mono text-[10px] text-[#9A8C98]">
                          {e.sap_version_before} → {e.sap_version_after}
                        </td>
                        <td className="px-6 py-3 truncate max-w-[150px]" title={String(e.approved_by)}>
                          {String(e.approved_by).slice(0, 24)}
                        </td>
                        <td className="px-6 py-3 font-mono text-[10px] text-[#9A8C98]">{String(e.sha256_hash).slice(0, 16)}…</td>
                      </tr>
                    ))}
                    {ledger.length === 0 && (
                      <tr>
                        <td colSpan={6} className="px-6 py-8 text-center text-sm text-[#9A8C98]">
                          Belum ada entri — setujui satu skenario untuk mencatat.
                        </td>
                      </tr>
                    )}
                  </tbody>
                </table>
              </div>
            </div>
          </section>

          {/* Telemetry */}
          <section className="animate-fade-in" style={{ animationDelay: '350ms' }}>
            <div className="overflow-hidden rounded-2xl border border-[#353A50] bg-[#2A2A40] shadow-xl shadow-black/30">
              <div className="flex items-center justify-between px-6 py-4 border-b border-[#353A50]">
                <h2 className="text-sm font-semibold text-[#F9F7F7]">Telemetri Langsung</h2>
                <span
                  className={`rounded-full px-3 py-1.5 text-xs font-medium transition-all ${
                    wsOn ? "bg-[#CBF3F0]/20 text-[#CBF3F0] shadow-sm shadow-[#CBF3F0]/20" : "bg-[#4A4E69] text-[#9A8C98]"
                  }`}
                >
                  {wsOn ? "Terhubung" : "Terputus"}
                </span>
                <div className="flex gap-2">
                  {!wsOn ? (
                    <button
                      onClick={wsConnect}
                      className="rounded-full bg-[#F2A900] px-3 py-1.5 text-xs font-semibold text-[#F9F7F7] transition-all hover:bg-[#E29B00] hover:shadow active:scale-95"
                    >
                      Hubungkan
                    </button>
                  ) : (
                    <button
                      onClick={wsClose}
                      className="rounded-full bg-[#353A50] px-3 py-1.5 text-xs text-[#F9F7F7] transition-all hover:bg-[#4A4E69] active:scale-95"
                    >
                      Putus
                    </button>
                  )}
                </div>
              </div>
              <div className="p-6">
                <div className="grid gap-4 sm:grid-cols-4">
                  <label className="text-xs text-[#9A8C98]">
                    Mesin
                    <input
                      value={tMachine}
                      onChange={(e) => setTMachine(e.target.value)}
                      className="mt-2 w-full rounded-lg bg-[#22223B] border border-[#353A50] px-3 py-2 text-sm text-[#F9F7F7] transition-all hover:border-[#4A4E69] focus:border-[#4A4E69] focus:outline-none focus:ring-2 focus:ring-[#4A4E69]/50"
                    />
                  </label>
                  <label className="text-xs text-[#9A8C98]">
                    Metrik
                    <select
                      value={tMetric}
                      onChange={(e) => setTMetric(e.target.value)}
                      className="mt-2 w-full rounded-lg bg-[#22223B] border border-[#353A50] px-3 py-2 text-sm text-[#F9F7F7] transition-all hover:border-[#4A4E69] focus:border-[#4A4E69] focus:outline-none focus:ring-2 focus:ring-[#4A4E69]/50"
                    >
                      <option value="motor_temp_celsius">motor_temp_celsius</option>
                      <option value="spindle_vibration_mm_per_s">spindle_vibration_mm_per_s</option>
                      <option value="coolant_pressure_bar">coolant_pressure_bar</option>
                      <option value="concurrent_axis_faults">concurrent_axis_faults</option>
                    </select>
                  </label>
                  <label className="text-xs text-[#9A8C98]">
                    Nilai
                    <input
                      value={tValue}
                      onChange={(e) => setTValue(e.target.value)}
                      className="mt-2 w-full rounded-lg bg-[#22223B] border border-[#353A50] px-3 py-2 text-sm text-[#F9F7F7] transition-all hover:border-[#4A4E69] focus:border-[#4A4E69] focus:outline-none focus:ring-2 focus:ring-[#4A4E69]/50"
                    />
                  </label>
                  <div className="flex gap-2">
                    <button
                      onClick={wsSend}
                      disabled={!wsOn}
                      className="flex-1 rounded-lg bg-[#F2A900] px-3 py-2 text-xs font-bold text-[#F9F7F7] transition-all disabled:opacity-40 disabled:cursor-not-allowed active:scale-95"
                    >
                      Kirim
                    </button>
                    <button
                      onClick={toggleFeed}
                      disabled={!wsOn && !autoFeed}
                      className={`flex-1 rounded-lg px-3 py-2 text-xs font-medium transition-all active:scale-95 ${
                        autoFeed
                          ? "bg-[#353A50] text-[#F9F7F7] hover:bg-[#4A4E69]"
                          : "bg-[#F2A900] text-[#F9F7F7] hover:bg-[#E29B00] hover:shadow"
                      }`}
                    >
                      {autoFeed ? "Stop Auto" : "Auto /8 dtk"}
                    </button>
                  </div>
                </div>
                <p className="mt-3 text-[10px] text-[#9A8C98]">
                  Server membalas (tidak push). Auto-feed disimulasikan tiap 8 detik — tiap frame memicu 1 call Gemini.
                </p>
                <div className="mt-4 max-h-48 overflow-y-auto rounded-lg bg-[#22223B] p-3 space-y-2">
                  {msgs.map((m, i) => (
                    <pre
                      key={i}
                      className="rounded bg-[#2A2A40] p-2 text-[10px] font-mono text-[#F9F7F7] overflow-x-auto"
                    >
                      {JSON.stringify(m).slice(0, 300)}
                    </pre>
                  ))}
                  {msgs.length === 0 && (
                    <p className="text-center text-xs text-[#9A8C98] py-4">
                      Belum ada frame — hubungkan lalu kirim nilai (mis. 91.5)
                    </p>
                  )}
                </div>
              </div>
            </div>
          </section>
        </main>
      </div>
    </div>
  );
}
