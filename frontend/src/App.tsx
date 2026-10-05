import { useCallback, useEffect, useRef, useState } from "react";
import Gantt from "./components/Gantt";
import { api, formatIDR } from "./lib/api";
import type { DisruptResponse, LedgerEntry, SapState, Scenario } from "./lib/api";

const APPROVAL_THRESHOLD = 1_000_000; // AMOEBA rule 2

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
      setReceipt(`Skenario ${sc.id} diterapkan. SAP ${r.sap_version_before} → ${r.sap_version_after}. Receipt ${r.receipt_id}.`);
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
      setVerify(`${v.valid ? "Valid" : "RUSAK"} — ${v.message}`);
    } catch (e) {
      setVerify(e instanceof Error ? e.message : String(e));
    }
  }

  // ── telemetry WS (server only replies; feed disimulasikan client-side) ──
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
      setErr("Nilai telemetri harus angka — backend crash untuk non-numerik.");
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
    // tiap frame memicu 1 call Claude di server → lambat; kirim tiap 8 dtk
    feedRef.current = window.setInterval(() => {
      const temp = 70 + Math.random() * 28; // 70–98 °C, kadang WARNING/CRITICAL
      wsRef.current?.send(JSON.stringify({ machine_id: tMachine, metric: "motor_temp_celsius", value: Number(temp.toFixed(1)) }));
    }, 8000);
    setAutoFeed(true);
  }

  return (
    <div className="min-h-screen bg-zinc-950 text-zinc-200">
      <div className="hero-glow border-b border-white/10">
        <header className="mx-auto flex max-w-6xl flex-wrap items-center gap-3 px-4 py-5">
          <div>
            <h1 className="text-xl font-bold tracking-tight text-white">SkillForge — Supervisor Cockpit</h1>
            <p className="text-xs text-zinc-400">
              {state?._meta.facility ?? "PT Karawang Precision Manufacturing"} · Bahasa Indonesia · Biaya dalam Rupiah
            </p>
          </div>
          <div className="ml-auto flex items-center gap-2 text-xs">
            <span className={`rounded-full px-2.5 py-1 font-medium ${health ? "bg-emerald-500/15 text-emerald-300" : "bg-zinc-700/50 text-zinc-300"}`}>
              Backend: {health ? `OK · v${health.sap_version}` : "…"}
            </span>
            <span className="rounded-full bg-white/5 px-2.5 py-1 text-zinc-300">SAP v{sapVersion}</span>
            <button onClick={load} className="rounded-full bg-white px-3 py-1 font-semibold text-black hover:bg-zinc-200">
              Muat ulang
            </button>
          </div>
        </header>
      </div>

      <main className="mx-auto max-w-6xl space-y-4 px-4 py-4">
        {err && (
          <div className="rounded-xl border border-red-500/30 bg-red-500/10 p-3 text-sm text-red-200">
            <b>Gagal:</b> {err}
            <div className="mt-1 text-xs text-red-300/80">
              Pastikan backend jalan: <code>uvicorn backend.main:app --port 8000</code>. Frontend di :5173 (CORS hanya mengizinkan itu).
            </div>
          </div>
        )}

        {/* ── Mesin ── */}
        <section className="grid grid-cols-2 gap-2 sm:grid-cols-3 lg:grid-cols-5">
          {state?.work_centers.map((w) => (
            <div key={w.id} className="rounded-2xl border border-white/10 bg-white/[0.03] p-3">
              <div className="flex items-center justify-between">
                <b className="text-sm text-white">{w.id}</b>
                <span className={`text-[11px] font-semibold ${w.status === "RUNNING" ? "text-emerald-300" : w.status === "MAINTENANCE" ? "text-red-300" : "text-zinc-400"}`}>
                  {w.status}
                </span>
              </div>
              <p className="truncate text-xs text-zinc-400">{w.name}</p>
              <p className="mt-1 text-[11px] text-zinc-500">Operator: {w.operator_name ?? "—"}</p>
              {w.telemetry && (
                <p className="mt-1 font-mono text-[11px] text-zinc-400">
                  {w.telemetry.motor_temp_celsius ?? "?"}°C · {w.telemetry.spindle_vibration_mm_per_s ?? "?"} mm/s
                </p>
              )}
            </div>
          )) ?? <p className="text-sm text-zinc-500">Memuat state…</p>}
        </section>

        <Gantt orders={state?.production_orders} title="Jadwal saat ini (Gantt)" />

        {/* ── Gangguan ── */}
        <section className="rounded-2xl border border-white/10 bg-white/[0.03] p-4">
          <h2 className="text-sm font-semibold text-white">1 · Simulasi gangguan</h2>
          <div className="mt-2 grid gap-2 sm:grid-cols-2 lg:grid-cols-5">
            <label className="text-xs text-zinc-400">Mesin
              <select value={machine} onChange={(e) => setMachine(e.target.value)} className="mt-1 w-full rounded-lg bg-zinc-900 p-2 text-sm text-white">
                {state?.work_centers.map((w) => <option key={w.id} value={w.id}>{w.id}</option>)}
              </select>
            </label>
            <label className="text-xs text-zinc-400">Jenis
              <select value={dtype} onChange={(e) => setDtype(e.target.value)} className="mt-1 w-full rounded-lg bg-zinc-900 p-2 text-sm text-white">
                <option>BREAKDOWN</option><option>OVERHEAT</option><option>TOOL_WEAR</option><option>POWER_DIP</option>
              </select>
            </label>
            <label className="text-xs text-zinc-400">Mulai (jam ke-)
              <input type="number" min={0} max={7} value={sh} onChange={(e) => setSh(Number(e.target.value))} className="mt-1 w-full rounded-lg bg-zinc-900 p-2 text-sm text-white" />
            </label>
            <label className="text-xs text-zinc-400">Selesai (jam ke-)
              <input type="number" min={1} max={8} value={eh} onChange={(e) => setEh(Number(e.target.value))} className="mt-1 w-full rounded-lg bg-zinc-900 p-2 text-sm text-white" />
            </label>
            <div className="flex items-end">
              <button onClick={onDisrupt} disabled={busy} className="flex w-full items-center justify-center gap-2 rounded-xl bg-white px-4 py-2 text-sm font-semibold text-black hover:bg-zinc-200 disabled:opacity-50">
                {busy && <span className="lattice-spin text-black" />}
                {busy ? "Solver + Claude…" : "Analisis gangguan"}
              </button>
            </div>
          </div>
          {disrupt && (
            <div className="mt-3 rounded-xl border border-sky-500/30 bg-sky-500/10 p-3 text-sm text-sky-100">
              <b>Ringkasan AI (Bahasa Indonesia):</b> {disrupt.claude_summary}
            </div>
          )}
        </section>

        {/* ── Skenario ── */}
        {disrupt && (
          <section className="rounded-2xl border border-white/10 bg-white/[0.03] p-4">
            <h2 className="text-sm font-semibold text-white">2 · Tiga skenario pemulihan</h2>
            <div className="mt-2 grid gap-2 lg:grid-cols-3">
              {disrupt.scenarios.map((sc) => {
                // AMOEBA rule 2: threshold on SLA penalty, not total cost (B's total = overtime cost)
                const penalty = sc.gantt_changes.reduce((s, c) => s + (c.sla_penalty_idr ?? 0), 0);
                const needApproval = penalty >= APPROVAL_THRESHOLD;
                return (
                  <div key={sc.id} className={`rounded-2xl border p-3 ${sc.recommended ? "border-amber-400/50 bg-amber-400/[0.06]" : "border-white/10 bg-black/20"}`}>
                    <div className="flex items-center gap-2">
                      <b className="text-sm text-white">{sc.id === "scenario_a" ? "A · Status Quo" : sc.id === "scenario_b" ? "B · Lembur" : "C · Rerute Optimal"}</b>
                      {sc.recommended && <span className="rounded-full bg-amber-400 px-2 py-0.5 text-[11px] font-bold text-black">Rekomendasi</span>}
                    </div>
                    <p className="text-xs text-zinc-400">{sc.name_id}</p>
                    <p className="mt-2 text-lg font-bold text-white">{formatIDR(sc.total_cost_idr)}</p>
                    <p className="text-xs text-emerald-300">Hemat bersih: {formatIDR(sc.net_savings_idr)}</p>
                    <p className="mt-1 text-xs leading-relaxed text-zinc-300">{sc.rationale_template}</p>
                    <p className="mt-1 text-[11px] text-zinc-500">{sc.affected_orders.length} order · {sc.gantt_changes.length} perubahan Gantt</p>
                    {needApproval && <p className="mt-1 text-[11px] font-semibold text-amber-300">⚠ Denda ≥ Rp 1.000.000 — wajib persetujuan supervisor.</p>}
                    <div className="mt-2">
                      <Gantt changes={sc.gantt_changes} title={`Gantt ${sc.id}`} />
                    </div>
                    <button
                      onClick={() => onApprove(sc)}
                      disabled={approving !== null}
                      className="mt-2 w-full rounded-xl bg-emerald-400 px-4 py-2 text-sm font-bold text-black hover:bg-emerald-300 disabled:opacity-50"
                    >
                      {approving === sc.id ? "Menerapkan…" : `Setujui ${sc.id.replace("scenario_", "Skenario ")} (1 klik)`}
                    </button>
                  </div>
                );
              })}
            </div>
            <label className="mt-2 block text-xs text-zinc-400">Disetujui oleh
              <input value={approvedBy} onChange={(e) => setApprovedBy(e.target.value)} className="mt-1 w-full rounded-lg bg-zinc-900 p-2 text-sm text-white" />
            </label>
            {receipt && <p className="mt-2 rounded-xl bg-emerald-500/10 p-2 text-sm text-emerald-200">{receipt}</p>}
            <p className="mt-2 text-[11px] text-zinc-500">
              Catatan demo: setelah approve, order jadi RESCHEDULED sehingga solver mengabaikannya pada gangguan berikut
              (hanya IN_PROGRESS/QUEUED/PLANNED yang dihitung). Restart backend untuk mengulang demo — tidak ada endpoint reset.
            </p>
          </section>
        )}

        {/* ── Ledger ── */}
        <section className="rounded-2xl border border-white/10 bg-white/[0.03] p-4">
          <div className="flex items-center gap-2">
            <h2 className="text-sm font-semibold text-white">3 · Audit ledger</h2>
            <button onClick={onVerify} className="ml-auto rounded-full bg-white/10 px-3 py-1 text-xs text-white hover:bg-white/20">Verifikasi rantai</button>
          </div>
          {verify && <p className="mt-1 text-xs text-zinc-300">{verify}</p>}
          <div className="mt-2 overflow-x-auto">
            <table className="w-full text-left text-xs">
              <thead><tr className="text-zinc-500"><th className="p-1">Waktu</th><th className="p-1">Aksi</th><th className="p-1">Skenario</th><th className="p-1">v</th><th className="p-1">Oleh</th><th className="p-1">Hash</th></tr></thead>
              <tbody>
                {ledger.map((e) => (
                  <tr key={e.id} className="border-t border-white/5 text-zinc-300">
                    <td className="p-1 font-mono">{String(e.timestamp).slice(0, 19)}</td>
                    <td className="p-1">{e.action_type}</td>
                    <td className="p-1">{e.scenario_chosen}</td>
                    <td className="p-1 font-mono">{e.sap_version_before}→{e.sap_version_after}</td>
                    <td className="p-1">{String(e.approved_by).slice(0, 24)}</td>
                    <td className="p-1 font-mono text-zinc-500">{String(e.sha256_hash).slice(0, 12)}…</td>
                  </tr>
                ))}
                {ledger.length === 0 && <tr><td colSpan={6} className="p-2 text-zinc-500">Belum ada entri — setujui satu skenario untuk mencatat.</td></tr>}
              </tbody>
            </table>
          </div>
        </section>

        {/* ── Telemetri ── */}
        <section className="rounded-2xl border border-white/10 bg-white/[0.03] p-4">
          <div className="flex items-center gap-2">
            <h2 className="text-sm font-semibold text-white">4 · Telemetri langsung (WS)</h2>
            <span className={`rounded-full px-2 py-0.5 text-[11px] ${wsOn ? "bg-emerald-500/15 text-emerald-300" : "bg-zinc-700/50 text-zinc-400"}`}>
              {wsOn ? "Terhubung" : "Terputus"}
            </span>
            <div className="ml-auto flex gap-2">
              {!wsOn
                ? <button onClick={wsConnect} className="rounded-full bg-white px-3 py-1 text-xs font-semibold text-black">Hubungkan</button>
                : <button onClick={wsClose} className="rounded-full bg-white/10 px-3 py-1 text-xs text-white">Putus</button>}
            </div>
          </div>
          <div className="mt-2 grid gap-2 sm:grid-cols-4">
            <label className="text-xs text-zinc-400">Mesin
              <input value={tMachine} onChange={(e) => setTMachine(e.target.value)} className="mt-1 w-full rounded-lg bg-zinc-900 p-2 text-sm text-white" />
            </label>
            <label className="text-xs text-zinc-400">Metrik
              <select value={tMetric} onChange={(e) => setTMetric(e.target.value)} className="mt-1 w-full rounded-lg bg-zinc-900 p-2 text-sm text-white">
                <option value="motor_temp_celsius">motor_temp_celsius</option>
                <option value="spindle_vibration_mm_per_s">spindle_vibration_mm_per_s</option>
                <option value="coolant_pressure_bar">coolant_pressure_bar</option>
                <option value="concurrent_axis_faults">concurrent_axis_faults</option>
              </select>
            </label>
            <label className="text-xs text-zinc-400">Nilai (angka)
              <input value={tValue} onChange={(e) => setTValue(e.target.value)} className="mt-1 w-full rounded-lg bg-zinc-900 p-2 text-sm text-white" />
            </label>
            <div className="flex items-end gap-2">
              <button onClick={wsSend} disabled={!wsOn} className="flex-1 rounded-xl bg-white px-3 py-2 text-xs font-bold text-black disabled:opacity-40">Kirim</button>
              <button onClick={toggleFeed} disabled={!wsOn && !autoFeed} className="flex-1 rounded-xl bg-white/10 px-3 py-2 text-xs text-white">
                {autoFeed ? "Stop auto" : "Auto /8 dtk"}
              </button>
            </div>
          </div>
          <p className="mt-1 text-[11px] text-zinc-500">Server hanya membalas (tidak push). Auto-feed disimulasikan dari browser tiap 8 dtk — tiap frame memicu 1 call Claude sehingga balasan lambat.</p>
          <ul className="mt-2 space-y-1">
            {msgs.map((m, i) => (
              <li key={i} className="rounded-lg bg-black/30 p-2 font-mono text-[11px] text-zinc-300">
                {JSON.stringify(m).slice(0, 280)}
              </li>
            ))}
            {msgs.length === 0 && <li className="text-xs text-zinc-500">Belum ada frame — hubungkan lalu kirim nilai (mis. 91.5 untuk memicu WARNING).</li>}
          </ul>
        </section>
      </main>
    </div>
  );
}
