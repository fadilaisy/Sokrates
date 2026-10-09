import { ArrowRight, ShieldCheck, X } from "lucide-react";
import { useEffect, useRef, useState } from "react";
import type { ReactNode } from "react";
import { formatIDR } from "../lib/api";
import { cn } from "../lib/utils";
import { changeKind, shortHash, timeRange, wibTime } from "../lib/shift";
import { useCockpit } from "./store";
import { AssistantFace, Btn, Card, Mono, Spinner } from "./ui";

function useEscape(onClose: () => void, active: boolean) {
  useEffect(() => {
    if (!active) return;
    const h = (e: KeyboardEvent) => e.key === "Escape" && onClose();
    window.addEventListener("keydown", h);
    return () => window.removeEventListener("keydown", h);
  }, [onClose, active]);
}

function Scrim({ onClick, tone = 0.45 }: { onClick: () => void; tone?: number }) {
  return <div className="sf-fade-in fixed inset-0 z-40" style={{ background: `rgb(24 50 79 / ${tone})` }} onClick={onClick} aria-hidden />;
}

/* ── Approval modal (field-level diff + optimistic lock) ─────────────────── */
export function ApprovalModal() {
  const { approvalFor, conflict, approving, sapVersion, closeApproval, confirmApproval, openChat, setLedgerOpen, verify } = useCockpit();
  useEscape(closeApproval, !!approvalFor && !approving);
  if (!approvalFor) return null;
  const c = conflict;
  const letter = approvalFor.id.replace("scenario_", "").toUpperCase();
  return (
    <>
      <Scrim onClick={() => !approving && closeApproval()} />
      <div role="dialog" aria-modal="true" aria-labelledby="appr-title" className="fixed inset-0 z-50 grid place-items-center p-4 pointer-events-none">
        <Card className="sf-fade-in pointer-events-auto flex w-full max-w-[720px] flex-col gap-4 p-6 shadow-2xl">
          <h2 id="appr-title" className="text-[22px] font-bold">
            {c ? "Tidak dapat disetujui · jadwal SAP berubah" : `Setujui skenario ${letter} · ${approvalFor.name_id}`}
          </h2>
          <p className="text-[16px]">
            {c
              ? "Jadwal di SAP berubah setelah analisis dibuat. Tidak ada yang ditulis ke SAP. Hitung ulang agar skenario cocok dengan jadwal terbaru."
              : "Tinjau perubahan field sebelum ditulis ke SAP. Persetujuan adalah satu-satunya aksi tulis; semua langkah sebelumnya hanya analisis."}
          </p>

          <div className="overflow-x-auto rounded-md border border-line">
            <table className="w-full text-left text-[15px]">
              <thead className="bg-page">
                <tr>
                  <th className="px-3 py-2 font-semibold">Order</th>
                  <th className="px-3 py-2 font-semibold">Mesin</th>
                  <th className="px-3 py-2 font-semibold">Jam</th>
                  <th className="px-3 py-2 font-semibold">Perubahan</th>
                  <th className="px-3 py-2 text-right font-semibold">Biaya</th>
                </tr>
              </thead>
              <tbody>
                {approvalFor.gantt_changes.map((g) => {
                  const cost = (g.sla_penalty_idr ?? 0) + (g.overtime_cost_idr ?? 0) + (g.changeover_cost_idr ?? 0);
                  return (
                    <tr key={g.order_id} className={cn("border-t border-line", c && "bg-danger-bg")}>
                      <td className="px-3 py-2.5 font-mono">{g.order_id}</td>
                      <td className="px-3 py-2.5 font-mono">{g.from_machine === g.to_machine ? g.to_machine : `${g.from_machine} → ${g.to_machine}`}</td>
                      <td className="px-3 py-2.5 font-mono">{timeRange(g.new_start_time, g.new_end_time)}</td>
                      <td className={cn("px-3 py-2.5", c && "font-semibold text-fault")}>{c ? "Konflik" : changeKind(g)}</td>
                      <td className="px-3 py-2.5 text-right font-mono">{formatIDR(cost)}</td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </div>

          <p
            className={cn(
              "flex items-center gap-2 rounded-md border px-3 py-2 font-mono text-[15px]",
              c ? "border-danger-line bg-danger-bg text-fault" : "border-ok-line bg-ok-bg text-running",
            )}
          >
            <span className={cn("size-2.5 rounded-full", c ? "bg-danger-line" : "bg-ok-line")} aria-hidden />
            {c
              ? `sap_version ${c.expected_version} → ${c.actual_version} · pemeriksaan drift: TIDAK COCOK`
              : `sap_version ${sapVersion} · pemeriksaan drift saat disetujui`}
          </p>

          {c?.receipt_id && (
            <div className="flex items-center gap-3 rounded-md bg-page py-2 pr-2 pl-4">
              <span className="flex-1 text-[15px] font-medium">Penolakan dicatat di audit ledger · {c.receipt_id.slice(0, 8)}</span>
              <Btn
                variant="secondary"
                onClick={() => {
                  closeApproval();
                  setLedgerOpen(true);
                  verify();
                }}
              >
                Lihat entri
              </Btn>
            </div>
          )}

          <div className="flex flex-wrap justify-end gap-3">
            <Btn variant="secondary" onClick={closeApproval} disabled={approving}>
              Batal
            </Btn>
            {c ? (
              <>
                <Btn variant="disabled" disabled>
                  Setujui skenario ini
                </Btn>
                <Btn onClick={() => openChat("recalc")}>Hitung ulang skenario</Btn>
              </>
            ) : (
              <Btn onClick={confirmApproval} disabled={approving}>
                {approving ? <Spinner className="size-5" /> : null}
                Setujui skenario ini
              </Btn>
            )}
          </div>
        </Card>
      </div>
    </>
  );
}

/* ── Audit ledger: bottom bar + drawer ───────────────────────────────────── */
const ACTION_LABEL: Record<string, string> = {
  SCENARIO_APPROVED: "Skenario disetujui",
  APPROVAL_REJECTED_DRIFT: "Persetujuan ditolak · drift SAP",
};

export function LedgerBar() {
  const { ledger, setLedgerOpen, verify, verifying } = useCockpit();
  const last = ledger[ledger.length - 1];
  return (
    <Card className="flex flex-wrap items-center justify-between gap-3 py-2 pr-20 pl-4">
      <button type="button" onClick={() => setLedgerOpen(true)} className="flex min-h-12 items-center gap-3 text-left">
        <span className="text-[16px] font-semibold">Audit ledger</span>
        <Mono className="text-[15px] text-muted-ink">
          {ledger.length} entri · hash terakhir {shortHash(last?.sha256_hash)}
        </Mono>
      </button>
      <Btn
        variant="secondary"
        onClick={() => {
          setLedgerOpen(true);
          verify();
        }}
        disabled={verifying}
      >
        <ShieldCheck className="size-5" aria-hidden />
        Verifikasi rantai hash
      </Btn>
    </Card>
  );
}

function Drawer({ open, onClose, width, label, children }: { open: boolean; onClose: () => void; width: number; label: string; children: ReactNode }) {
  useEscape(onClose, open);
  if (!open) return null;
  return (
    <>
      <Scrim onClick={onClose} tone={0.3} />
      <aside
        role="dialog"
        aria-modal="true"
        aria-label={label}
        className="sf-slide-in fixed inset-y-0 right-0 z-50 flex flex-col gap-4 bg-surface p-6 shadow-[-8px_0_24px_rgba(0,0,0,.2)]"
        style={{ width: `min(${width}px, 100vw)` }}
      >
        {children}
      </aside>
    </>
  );
}

function CloseBtn({ onClick }: { onClick: () => void }) {
  return (
    <button type="button" onClick={onClick} aria-label="Tutup" className="grid size-14 shrink-0 place-items-center rounded-md border border-line hover:bg-page">
      <X className="size-6" />
    </button>
  );
}

export function LedgerDrawer() {
  const { ledgerOpen, setLedgerOpen, ledger, verify, verifying, verifyResult } = useCockpit();
  const close = () => setLedgerOpen(false);
  const entries = [...ledger].reverse();
  return (
    <Drawer open={ledgerOpen} onClose={close} width={520} label="Audit ledger">
      <div className="flex items-center gap-3">
        <div className="flex-1">
          <h2 className="text-[24px] font-bold">Audit ledger</h2>
          <Mono className="text-[14px] text-muted-ink">{ledger.length} entri · rantai hash SHA-256</Mono>
        </div>
        <CloseBtn onClick={close} />
      </div>
      <ol className="flex flex-1 flex-col gap-2.5 overflow-y-auto">
        {entries.length === 0 && <li className="text-[15px] text-muted-ink">Belum ada entri. Setujui sebuah skenario untuk membuat entri pertama.</li>}
        {entries.map((e, i) => {
          const rejected = e.action_type === "APPROVAL_REJECTED_DRIFT";
          return (
            <li
              key={e.id}
              className={cn("flex flex-col gap-1 rounded-md px-4 py-3", rejected ? "border border-danger-line bg-danger-bg" : i === 0 ? "border border-ok-line bg-ok-bg" : "bg-page")}
            >
              <span className={cn("text-[16px] font-semibold", rejected && "text-fault")}>
                {ACTION_LABEL[e.action_type] ?? e.action_type} · {String(e.scenario_chosen ?? "").replace("scenario_", "Skenario ").toUpperCase().replace("SKENARIO", "Skenario")}
              </span>
              <span className="text-[14px] text-muted-ink">
                {wibTime(e.timestamp)} · {String(e.approved_by ?? "").replace("Production Supervisor — ", "")} · SAP v{e.sap_version_before}
                {rejected ? " ≠ " : " → "}v{e.sap_version_after}
                {rejected && " · tidak ada penulisan"}
              </span>
              <Mono className="text-[14px]">{shortHash(e.sha256_hash)}</Mono>
            </li>
          );
        })}
      </ol>
      {verifyResult && (
        <p
          role="status"
          className={cn(
            "flex items-center gap-2.5 rounded-md border px-4 py-3 font-mono text-[15px]",
            verifyResult.valid ? "border-ok-line bg-ok-bg text-running" : "border-danger-line bg-danger-bg text-fault",
          )}
        >
          valid: {String(verifyResult.valid)} · {verifyResult.total_entries} entri terverifikasi
        </p>
      )}
      <Btn size="lg" onClick={verify} disabled={verifying}>
        {verifying && <Spinner className="size-5" />}
        Verifikasi rantai hash
      </Btn>
    </Drawer>
  );
}

/* ── Chat drawer (AI Supervisor) ─────────────────────────────────────────── */
export function ChatDrawer() {
  const { chatOpen, closeChat, chatMode, messages, chatBusy, sendChat, recalcVersions, params } = useCockpit();
  const [text, setText] = useState("");
  const listRef = useRef<HTMLDivElement>(null);
  useEffect(() => {
    listRef.current?.scrollTo({ top: listRef.current.scrollHeight, behavior: "smooth" });
  }, [messages]);
  const recalc = chatMode === "recalc";
  const chips = recalc ? [{ label: "Hitung otomatis tanpa solusi saya", send: "__auto__" }] : [
    { label: "Status semua mesin", send: "Status semua mesin" },
    { label: "What-if: CNC-02 rusak 3 jam", send: "What-if: CNC-02 rusak 3 jam" },
    { label: "Buka audit ledger", send: "Buka audit ledger" },
  ];
  const submit = async (t: string) => {
    if (chatBusy) return;
    setText("");
    await sendChat(t);
  };
  return (
    <Drawer open={chatOpen} onClose={closeChat} width={480} label="AI Supervisor">
      <div className="flex items-center gap-3">
        <span className="grid size-12 shrink-0 place-items-center rounded-full border border-line bg-[#e9e9e7]">
          <AssistantFace className="size-7" />
        </span>
        <div className="flex-1">
          <h2 className="text-[22px] font-bold">AI Supervisor</h2>
          <p className="flex items-center gap-1.5 text-[14px] text-muted-ink">
            <span className="size-2 rounded-full bg-ok-line" aria-hidden />
            {recalc ? "Hitung ulang · solusi engineer" : "Online · hanya-baca"}
          </p>
        </div>
        <CloseBtn onClick={closeChat} />
      </div>
      {recalc && params && (
        <Mono className="rounded-md bg-page px-3 py-2 text-[14px] text-muted-ink">
          {params.machine_id} · SAP v{recalcVersions?.from ?? "?"} → v{recalcVersions?.to ?? "?"} · konflik jadwal
        </Mono>
      )}
      <div ref={listRef} className="flex flex-1 flex-col gap-3 overflow-y-auto" aria-live="polite">
        {messages.map((m) =>
          m.from === "user" ? (
            <p key={m.id} className="ml-auto max-w-[85%] rounded-xl rounded-tr-sm bg-navy px-4 py-3 text-[16px] font-medium text-white">
              {m.text}
            </p>
          ) : (
            <div key={m.id} className="max-w-[90%] rounded-xl rounded-tl-sm bg-page px-4 py-3 text-[16px]">
              {m.kind === "progress" ? (
                <span className="flex items-center gap-3">
                  <Spinner className="size-5" /> {m.text}
                </span>
              ) : (
                <p>{m.text}</p>
              )}
              {m.kind === "result" && m.scenario && (
                <div className="mt-3 flex flex-col gap-2 rounded-lg border-2 border-accent-blue bg-surface p-3">
                  <p className="text-[16px] font-semibold">{m.scenario.name_id}</p>
                  <p className="flex justify-between text-[15px]">
                    <span>Biaya total</span>
                    <Mono className="font-semibold">{formatIDR(m.scenario.total_cost_idr)}</Mono>
                  </p>
                  <p className="text-[15px]">{m.scenario.rationale_template}</p>
                  <Btn onClick={closeChat}>
                    Tinjau skenario baru <ArrowRight className="size-5" aria-hidden />
                  </Btn>
                </div>
              )}
            </div>
          ),
        )}
      </div>
      <div className="flex flex-wrap gap-2">
        {chips.map((c) => (
          <button
            key={c.label}
            type="button"
            disabled={chatBusy}
            onClick={() => submit(c.send)}
            className="min-h-12 rounded-full border border-accent-blue px-4 text-[15px] font-semibold hover:bg-page disabled:opacity-50"
          >
            {c.label}
          </button>
        ))}
      </div>
      <form
        className="flex items-end gap-2"
        onSubmit={(e) => {
          e.preventDefault();
          submit(text);
        }}
      >
        <label className="sr-only" htmlFor="chat-input">
          Pesan
        </label>
        <textarea
          id="chat-input"
          value={text}
          onChange={(e) => setText(e.target.value)}
          onKeyDown={(e) => {
            if (e.key === "Enter" && !e.shiftKey) {
              e.preventDefault();
              submit(text);
            }
          }}
          rows={recalc ? 3 : 1}
          placeholder={recalc ? "Contoh: pindahkan WO-2025-002 ke CNC-03 jam 09:00–12:00, tanpa lembur." : "Tanya AI Supervisor…"}
          className="min-h-14 flex-1 resize-none rounded-lg border border-line bg-surface px-4 py-3.5 text-[16px] placeholder:text-muted-ink"
          disabled={chatBusy}
        />
        <button type="submit" aria-label="Kirim" disabled={chatBusy || !text.trim()} className="grid size-14 shrink-0 place-items-center rounded-lg bg-navy text-white disabled:bg-disabled">
          <ArrowRight className="size-6" />
        </button>
      </form>
      <p className="text-[14px] text-muted-ink">Chat hanya membaca data. Perubahan jadwal tetap lewat tombol “Setujui skenario ini”.</p>
    </Drawer>
  );
}
