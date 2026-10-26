import { useCallback, useEffect, useRef, useState } from "react";
import { Bot, CircleCheck, Circle, CircleMinus, CircleX, ShieldCheck } from "lucide-react";
import { api, type AgentRun, type AgentRunStep } from "../lib/api";
import { cn } from "../lib/utils";
import { useCockpit } from "./store";
import { Btn, Card, Mono, Spinner } from "./ui";

const ACTIVE = new Set(["planning", "running"]);
const idr = (n: number | undefined | null) => `Rp ${Math.round(n ?? 0).toLocaleString("id-ID")}`;

const STATUS_TEXT: Record<AgentRun["status"], string> = {
  planning: "Menyusun rencana",
  running: "Menjalankan tool",
  awaiting_approval: "Menunggu persetujuan",
  approved: "Disetujui",
  done: "Selesai",
  failed: "Gagal",
  rejected: "Ditolak",
};

/** Latest run, polled fast while it works and slowly otherwise; a PLC "agent_run" frame refreshes at once. */
function useLatestRun(ping: number) {
  const [run, setRun] = useState<AgentRun | null>(null);
  const timer = useRef<number | undefined>(undefined);
  const refresh = useCallback(async () => {
    try {
      setRun(await api.agentRunLatest());
    } catch {
      /* backend offline: keep the last run on screen */
    }
  }, []);
  useEffect(() => {
    void refresh();
  }, [refresh, ping]);
  useEffect(() => {
    const active = run != null && ACTIVE.has(run.status);
    timer.current = window.setInterval(() => void refresh(), active ? 600 : 4000);
    return () => window.clearInterval(timer.current);
  }, [run, refresh]);
  return { run, setRun, refresh };
}

/**
 * The agent's plan-act-orchestrate loop for one alarm: the plan it chose, each tool call as it runs,
 * and one approval for the SAP writes (parts order + reroute).
 */
export function AgentRunPanel() {
  const { agentRunPing, plcAlarm, approver, setApprover, reload, showToast } = useCockpit();
  const { run, setRun, refresh } = useLatestRun(agentRunPing);
  const [busy, setBusy] = useState(false);
  // SAP writes are signed by a real person, never the placeholder signer (same rule as scenario approval).
  const named = approver.trim().length >= 3 && approver.trim().toLowerCase() !== "production supervisor";

  const start = async (machineId: string, code?: string, text?: string) => {
    setBusy(true);
    try {
      setRun(await api.agentRun({ machine_id: machineId, alarm_code: code, alarm_text: text }));
    } catch (e) {
      showToast(`Agen gagal dimulai: ${e instanceof Error ? e.message : String(e)}`);
    } finally {
      setBusy(false);
    }
  };

  const approve = async () => {
    if (!run || !named) return;
    setBusy(true);
    try {
      const done = await api.agentRunApprove(run.id, approver.trim());
      setRun(done);
      showToast("Disetujui: suku cadang dipesan dan work order dialihkan. Tercatat di audit ledger.");
      await reload();
    } catch (e) {
      showToast(`Persetujuan gagal: ${e instanceof Error ? e.message : String(e)}`);
      void refresh();
    } finally {
      setBusy(false);
    }
  };

  const alarmWithoutRun =
    plcAlarm && (!run || run.trigger.machine_id !== plcAlarm.machine_id || run.trigger.alarm_code !== plcAlarm.code);

  if (!run && !alarmWithoutRun) return null;

  return (
    <Card className="flex flex-col gap-3 p-4">
      <div className="flex flex-wrap items-center gap-3">
        <Bot className="size-6 text-navy" aria-hidden />
        <h2 className="text-[18px] font-bold">Agen SkillForge</h2>
        {run && (
          <>
            <Mono className="text-[14px] text-muted-ink">{run.id}</Mono>
            <StatusChip status={run.status} />
          </>
        )}
        {alarmWithoutRun && plcAlarm && (
          <Btn
            variant="secondary"
            className="ml-auto px-4 text-[14px]"
            disabled={busy}
            onClick={() => void start(plcAlarm.machine_id, plcAlarm.code, plcAlarm.text)}
          >
            Jalankan agen untuk {plcAlarm.machine_id} · {plcAlarm.code}
          </Btn>
        )}
      </div>

      {run && (
        <>
          <p className="text-[15px]">
            <span className="font-semibold">Pemicu:</span> <Mono>{run.trigger.machine_id}</Mono>{" "}
            {run.trigger.alarm_code ? <Mono>{run.trigger.alarm_code}</Mono> : "alarm manual"} {run.trigger.alarm_text ?? ""}
            {run.trigger.tag && run.trigger.value != null && (
              <span className="text-muted-ink">
                {" "}
                ({run.trigger.tag} = {run.trigger.value})
              </span>
            )}
          </p>
          <PlannerLine run={run} />
          <ol className="flex flex-col gap-2">
            {run.plan.map((p) => (
              <StepRow key={p.tool} label={p.label} why={p.why} step={run.steps.find((s) => s.tool === p.tool)} />
            ))}
          </ol>
          {run.error && <p className="rounded-md bg-danger-bg px-3 py-2 text-[14px] text-fault">{run.error}</p>}
          {run.summary && (
            <div className="rounded-md bg-page px-4 py-3">
              <p className="text-[14px] font-semibold text-muted-ink">
                Ringkasan agen{run.summary_model ? ` (${run.summary_model} · SAP AI Core)` : ""}
              </p>
              <p className="text-[16px]">{run.summary}</p>
            </div>
          )}
          {run.status === "awaiting_approval" && (
            <ApprovalBox
              run={run}
              busy={busy}
              named={named}
              approver={approver}
              setApprover={setApprover}
              onApprove={() => void approve()}
            />
          )}
          {run.status === "approved" && run.approval && <ApprovedBox run={run} />}
        </>
      )}
    </Card>
  );
}

function StatusChip({ status }: { status: AgentRun["status"] }) {
  const tone =
    status === "failed" || status === "rejected"
      ? "bg-danger-bg text-fault"
      : status === "awaiting_approval"
        ? "bg-warn-bg text-warn"
        : status === "approved" || status === "done"
          ? "bg-ok-bg text-running"
          : "bg-page text-navy";
  return (
    <span className={cn("inline-flex items-center gap-1.5 rounded-md px-2.5 py-1 text-[13px] font-bold uppercase", tone)}>
      {ACTIVE.has(status) && <Spinner className="size-4" />}
      {STATUS_TEXT[status]}
    </span>
  );
}

function PlannerLine({ run }: { run: AgentRun }) {
  if (!run.planner) return <p className="text-[14px] text-muted-ink">Agen sedang menyusun rencana di SAP AI Core…</p>;
  return (
    <p className="text-[14px] text-muted-ink">
      {run.planner.source === "llm" ? (
        <>
          Rencana disusun oleh <Mono>{run.planner.model}</Mono> di SAP AI Core ({(run.planner.ms / 1000).toFixed(1)} dtk)
          {run.planner.reasoning ? `: “${run.planner.reasoning}”` : "."}
        </>
      ) : (
        <>Rencana baku dipakai: {run.planner.reasoning}</>
      )}
    </p>
  );
}

function StepRow({ label, why, step }: { label: string; why: string; step?: AgentRunStep }) {
  const state = step?.status ?? "pending";
  const icon =
    state === "done" ? (
      <CircleCheck className="size-5 text-running" aria-label="selesai" />
    ) : state === "running" ? (
      <Spinner className="size-5" />
    ) : state === "skipped" ? (
      <CircleMinus className="size-5 text-muted-ink" aria-label="dilewati" />
    ) : state === "failed" ? (
      <CircleX className="size-5 text-fault" aria-label="gagal" />
    ) : (
      <Circle className="size-5 text-line" aria-label="menunggu" />
    );
  return (
    <li className={cn("flex gap-3 rounded-md border border-line px-3 py-2", state === "pending" && "opacity-60")}>
      <span className="mt-0.5 shrink-0">{icon}</span>
      <div className="min-w-0 flex-1">
        <p className="flex flex-wrap items-baseline gap-x-2 text-[15px] font-semibold">
          {label}
          {step?.ms != null && <Mono className="text-[12px] font-normal text-muted-ink">{step.ms} ms</Mono>}
        </p>
        {step?.summary ? <p className="text-[14px]">{step.summary}</p> : <p className="text-[13px] text-muted-ink">{why}</p>}
      </div>
    </li>
  );
}

function ApprovalBox({
  run,
  busy,
  named,
  approver,
  setApprover,
  onApprove,
}: {
  run: AgentRun;
  busy: boolean;
  named: boolean;
  approver: string;
  setApprover: (v: string) => void;
  onApprove: () => void;
}) {
  const parts = run.pending_approval?.parts;
  const sc = run.pending_approval?.scenario;
  const moves = (sc?.gantt_changes ?? []).filter((c) => c.to_machine !== c.from_machine);
  return (
    <div className="flex flex-col gap-2 rounded-md border border-warn bg-page px-4 py-3 text-[14px]">
      <p className="font-semibold">Satu persetujuan untuk penulisan ke SAP</p>
      {parts?.reservations.map((r) => (
        <p key={r.id}>
          Reservasi <Mono>{r.part_no}</Mono> {r.description} ×{r.qty} dari {r.storage_location}
        </p>
      ))}
      {parts?.purchase_requisitions.map((p) => (
        <p key={p.id}>
          Purchase requisition <Mono>{p.part_no}</Mono> {p.description} ×{p.qty} ke {p.supplier}, {idr(p.value_idr)}, tiba ~{p.lead_time_days} hari
        </p>
      ))}
      {sc && (
        <p>
          Alihkan {moves.length} work order ({moves.map((m) => `${m.order_id} → ${m.to_machine}`).join(", ") || "jadwal ulang"}),
          biaya {idr(sc.total_cost_idr)}, hemat {idr(sc.net_savings_idr)}
        </p>
      )}
      <label className="mt-1 flex flex-col gap-1">
        <span className="text-[14px] font-medium text-muted-ink">Disetujui oleh (nama Anda, tercatat di audit ledger)</span>
        <input
          value={approver}
          onChange={(e) => setApprover(e.target.value)}
          disabled={busy}
          placeholder="Production Supervisor — Nama Anda"
          className="min-h-11 rounded-md border border-line bg-surface px-3 text-[15px] disabled:opacity-60"
        />
      </label>
      <Btn className="mt-1 self-start" disabled={busy || !named} onClick={onApprove}>
        <ShieldCheck className="size-5" aria-hidden />
        Setujui semua
      </Btn>
    </div>
  );
}

function ApprovedBox({ run }: { run: AgentRun }) {
  const a = run.approval!;
  return (
    <div className="flex flex-col gap-1 rounded-md border border-ok-line bg-ok-bg px-4 py-3 text-[14px]">
      <p className="font-semibold text-running">Disetujui oleh {a.approved_by}</p>
      {a.parts && (
        <p>
          Suku cadang: {[...a.parts.reservations, ...a.parts.purchase_requisitions].join(", ")} · hash{" "}
          <Mono>{a.parts.sha256_hash.slice(0, 12)}…</Mono>
        </p>
      )}
      {a.scenario && (
        <p>
          Rerute: receipt <Mono>{a.scenario.receipt_id.slice(0, 8)}</Mono> · hash <Mono>{a.scenario.sha256_hash.slice(0, 12)}…</Mono>
        </p>
      )}
    </div>
  );
}
