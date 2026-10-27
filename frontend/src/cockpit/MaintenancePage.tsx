import { useCallback, useEffect, useState } from "react";
import type { ReactNode } from "react";
import { API_BASE, maintApi } from "../lib/api";
import type { DocRef, MaintCase, MaintChat, MaintFlow, MaintSearch, Playbook } from "../lib/api";
import { chainVerdict } from "../lib/shift";
import { cn } from "../lib/utils";
import { Header } from "./Shell";
import { SourceCitations } from "./Citations";
import { Btn, Card, Mono, Spinner } from "./ui";

/*
 * Proactive maintenance: a new ledger entry triggers similar-case search (RAG over the
 * maintenance ledger), a cited repair playbook, and a message to the best-matched mechanic.
 * Every case chip opens the ledger entry with its SHA-256 so a reviewer can check the source.
 * Chat is secondary and collapsed at the bottom.
 */

const CHANNEL = { whatsapp: "WhatsApp", telegram: "Telegram" } as const;
const MACHINES = ["CNC-01", "CNC-02", "CNC-03", "CNC-04", "CNC-05"];
const field =
  "min-h-12 w-full rounded-lg border border-line bg-surface px-3 text-[16px] text-navy focus:outline-none focus:ring-2 focus:ring-accent-blue";

const CONF_STYLE: Record<Playbook["confidence"], string> = {
  high: "bg-ok-bg text-running border-ok-line",
  medium: "bg-warn-bg text-warn border-warn",
  low: "bg-danger-bg text-fault border-danger-line",
  none: "bg-page text-muted-ink border-line",
};
const CONF_LABEL: Record<Playbook["confidence"], string> = {
  high: "keyakinan tinggi", medium: "keyakinan sedang", low: "keyakinan rendah", none: "tidak ada preseden",
};

/** Plant-document sources of a chat answer: the cited ones, or all retrieved ones when nothing was cited. */
function chatDocs(chat: MaintChat) {
  const docs = (chat.sources ?? []).filter((s) => !s.case_id);
  const cited = docs.filter((s) => s.cited);
  return cited.length ? cited : docs;
}

function CaseChip({ id, onOpen }: { id: string; onOpen: (id: string) => void }) {
  return (
    <button
      type="button"
      onClick={() => onOpen(id)}
      title="Buka entri ledger sumber"
      className="inline-flex min-h-8 items-center rounded-md border border-line bg-page px-2 font-mono text-[13px] font-medium text-accent-blue hover:bg-surface"
    >
      {id}
    </button>
  );
}

function DocChip({ d }: { d: DocRef }) {
  return (
    <a
      href={`${API_BASE}/api/documents/${encodeURIComponent(d.doc_id)}/file`}
      target="_blank"
      rel="noreferrer"
      title={d.excerpt}
      className={cn(
        "inline-flex min-h-8 items-center gap-1.5 rounded-md border px-2 text-[13px] hover:bg-page",
        d.constraint_tier === "Safety" ? "border-danger-line text-fault" : "border-line text-navy",
      )}
    >
      {d.doc_title} <Mono className="text-[12px] text-muted-ink">p{d.page}</Mono>
    </a>
  );
}

function SourceTag({ c }: { c: Pick<MaintCase, "source" | "ocr_confidence" | "language"> }) {
  const scanned = c.source.includes("scan");
  return (
    <span className={cn("rounded px-1.5 py-0.5 text-[12px]", scanned ? "bg-warn-bg text-warn" : "bg-page text-muted-ink")}>
      {c.source.replace("_", " ")}
      {scanned && c.ocr_confidence ? ` · OCR ${Math.round(c.ocr_confidence * 100)}%` : ""} · {c.language}
    </span>
  );
}

/** The evidence behind a citation: the ledger entry and its hash. */
function CaseDrawer({ id, onClose }: { id: string; onClose: () => void }) {
  const [c, setC] = useState<MaintCase | null>(null);
  const [err, setErr] = useState<string | null>(null);
  useEffect(() => {
    setC(null);
    maintApi.entry(id).then(setC).catch((e) => setErr(String(e)));
  }, [id]);
  return (
    <div className="fixed inset-0 z-50 flex justify-end bg-navy/30" onClick={onClose}>
      <aside
        className="h-full w-full max-w-lg overflow-y-auto bg-surface p-6 shadow-2xl"
        onClick={(e) => e.stopPropagation()}
        aria-label={`Entri ledger ${id}`}
      >
        <div className="mb-4 flex items-center justify-between">
          <Mono className="text-[18px] font-bold">{id}</Mono>
          <Btn variant="secondary" onClick={onClose}>Tutup</Btn>
        </div>
        {err && <p className="text-fault">{err}</p>}
        {!c && !err && <Spinner />}
        {c && (
          <div className="flex flex-col gap-3 text-[15px]">
            <div className="flex flex-wrap gap-2">
              <SourceTag c={c} />
              {c.category && <span className="rounded bg-page px-1.5 py-0.5 text-[12px]">{c.category}</span>}
              <span className="rounded bg-page px-1.5 py-0.5 text-[12px]">{c.status}</span>
              {c.outcome === "RECURRED" && <span className="rounded bg-danger-bg px-1.5 py-0.5 text-[12px] text-fault">BERULANG</span>}
            </div>
            <p className="text-muted-ink">
              {c.machine_id} · {c.machine_model} · {c.logged_at.slice(0, 16).replace("T", " ")}
            </p>
            <p className="text-[17px] font-semibold">{c.symptom}</p>
            <p className="rounded-md bg-page p-3 whitespace-pre-wrap">{c.text}</p>
            {c.root_cause && <p><b>Akar masalah:</b> {c.root_cause}</p>}
            {c.fix_steps && c.fix_steps.length > 0 && (
              <ol className="list-decimal space-y-1 pl-5">{c.fix_steps.map((s, i) => <li key={i}>{s}</li>)}</ol>
            )}
            {(c.error_codes.length > 0 || c.part_codes.length > 0) && (
              <Mono className="text-[14px] text-accent-blue">{[...c.error_codes, ...c.part_codes].join(" · ")}</Mono>
            )}
            <p className="break-all font-mono text-[12px] text-muted-ink">sha256 {c.ledger_hash}</p>
          </div>
        )}
      </aside>
    </div>
  );
}

function PlaybookView({ pb, onOpen }: { pb: Playbook; onOpen: (id: string) => void }) {
  if (pb.steps.length === 0) return <p className="text-[16px] text-muted-ink">{pb.note ?? "Tidak ada kasus serupa di ledger."}</p>;
  return (
    <div className="flex flex-col gap-3">
      <div className="flex flex-wrap items-center gap-3">
        <span className={cn("rounded-full border px-3 py-0.5 text-[14px] font-bold", CONF_STYLE[pb.confidence])}>
          {CONF_LABEL[pb.confidence]}
        </span>
        {pb.est_downtime_hours != null && <span className="text-[15px] text-muted-ink">estimasi downtime ~{pb.est_downtime_hours} jam</span>}
      </div>
      <ol className="flex flex-col gap-2">
        {pb.steps.map((s) => (
          <li key={s.n} className="flex flex-wrap items-start gap-2 text-[16px]">
            <Mono className="w-6 shrink-0 text-muted-ink">{s.n}.</Mono>
            <span className="min-w-0 flex-1">
              {s.text}
              {s.also_from_other_cases && <span className="ml-1 text-[13px] text-muted-ink">(dari kasus lain)</span>}
              {s.scan_confidence != null && <span className="ml-1 text-[13px] text-warn">(dari scan — verifikasi)</span>}
            </span>
            <span className="flex flex-wrap gap-1">{s.source_case_ids.map((id) => <CaseChip key={id} id={id} onOpen={onOpen} />)}</span>
          </li>
        ))}
      </ol>
      {pb.warnings.map((w, i) => (
        <p key={i} className="flex flex-wrap items-center gap-2 rounded-md border border-danger-line bg-danger-bg p-3 text-[15px] text-fault">
          ⚠ {w.text} {w.source_case_ids.map((id) => <CaseChip key={id} id={id} onOpen={onOpen} />)}
        </p>
      ))}
      {pb.safety.slice(0, 2).map((w, i) => (
        <p key={i} className="flex flex-wrap items-center gap-2 rounded-md border border-warn bg-warn-bg p-3 text-[15px]">
          <b>Keselamatan:</b> {w.text} {w.source_case_ids.map((id) => <CaseChip key={id} id={id} onOpen={onOpen} />)}
        </p>
      ))}
      {pb.parts.length > 0 && (
        <p className="text-[15px]">
          <b>Suku cadang:</b> {pb.parts.map((p) => <Mono key={p.code} className="mr-3 text-accent-blue">{p.code}</Mono>)}
        </p>
      )}
    </div>
  );
}

function Section({ title, sub, children }: { title: string; sub?: string; children: ReactNode }) {
  return (
    <Card className="flex flex-col gap-4 p-5">
      <div>
        <h2 className="text-[20px] font-bold">{title}</h2>
        {sub && <p className="mt-1 text-[15px] text-muted-ink">{sub}</p>}
      </div>
      {children}
    </Card>
  );
}

export function MaintenancePage() {
  const [open, setOpen] = useState<string | null>(null);
  const [err, setErr] = useState<string | null>(null);
  const [cases, setCases] = useState<MaintCase[]>([]);
  const [feed, setFeed] = useState<Awaited<ReturnType<typeof maintApi.flows>>>([]);

  const [machine, setMachine] = useState("CNC-02");
  const [symptom, setSymptom] = useState("");
  const [busy, setBusy] = useState(false);
  const [flow, setFlow] = useState<MaintFlow | null>(null);

  const [q, setQ] = useState("");
  const [qMachine, setQMachine] = useState("");
  const [qLang, setQLang] = useState("");
  const [res, setRes] = useState<MaintSearch | null>(null);

  const [chatOpen, setChatOpen] = useState(false);
  const [chatQ, setChatQ] = useState("");
  const [chat, setChat] = useState<MaintChat | null>(null);
  const [chatBusy, setChatBusy] = useState(false);
  const [verify, setVerify] = useState<string | null>(null);

  const refresh = useCallback(async () => {
    try {
      const [c, f] = await Promise.all([maintApi.cases(), maintApi.flows()]);
      setCases(c);
      setFeed(f);
    } catch (e) {
      setErr(e instanceof Error ? e.message : String(e));
    }
  }, []);
  useEffect(() => {
    void refresh();
  }, [refresh]);

  async function submit() {
    if (symptom.trim().length < 3) return;
    setBusy(true);
    setErr(null);
    try {
      const r = await maintApi.create({ machine_id: machine, symptom: symptom.trim(), reported_by: "Supervisor (cockpit)" });
      setFlow(r.flow);
      setSymptom("");
      await refresh();
    } catch (e) {
      setErr(e instanceof Error ? e.message : String(e));
    } finally {
      setBusy(false);
    }
  }

  async function search() {
    if (!q.trim()) return;
    try {
      setRes(await maintApi.search(q.trim(), { machine_id: qMachine || undefined, language: qLang || undefined }));
    } catch (e) {
      setErr(e instanceof Error ? e.message : String(e));
    }
  }

  async function ask() {
    if (!chatQ.trim()) return;
    setChatBusy(true);
    try {
      setChat(await maintApi.chat(chatQ.trim()));
    } catch (e) {
      setErr(e instanceof Error ? e.message : String(e));
    } finally {
      setChatBusy(false);
    }
  }

  const scanned = cases.filter((c) => c.source.includes("scan")).length;
  const bahasa = cases.filter((c) => c.language !== "en").length;
  const totalMs = flow ? Object.values(flow.timings_ms).reduce((a, b) => a + b, 0) : 0;

  return (
    <>
      <Header />
      {open && <CaseDrawer id={open} onClose={() => setOpen(null)} />}
      {err && <p role="alert" className="rounded-lg border border-danger-line bg-danger-bg px-4 py-3 text-[16px] text-fault">{err}</p>}

      <Section
        title="Laporan Kerusakan Baru"
        sub="Entri ledger baru memicu: cari kasus serupa → playbook perbaikan bersitasi → pesan ke mekanik yang tepat."
      >
        <div className="grid gap-3 md:grid-cols-[160px_1fr_auto]">
          <select aria-label="Mesin" value={machine} onChange={(e) => setMachine(e.target.value)} className={field}>
            {MACHINES.map((m) => <option key={m}>{m}</option>)}
          </select>
          <input
            aria-label="Gejala"
            value={symptom}
            onChange={(e) => setSymptom(e.target.value)}
            onKeyDown={(e) => e.key === "Enter" && void submit()}
            placeholder="mis. Alarm 108 muncul lagi, spindel panas"
            className={field}
          />
          <Btn onClick={() => void submit()} disabled={busy || symptom.trim().length < 3}>
            {busy ? <Spinner className="size-5" /> : null} Catat & analisis
          </Btn>
        </div>

        {flow && (
          <div className="flex flex-col gap-4 rounded-lg border border-line bg-page p-4">
            <ol className="grid gap-2 text-[15px] md:grid-cols-3">
              <li className="rounded-md bg-surface p-3">
                <Mono className="text-[13px] text-muted-ink">① CARI KASUS</Mono>
                <p className="mt-1">
                  {flow.retrieval.hits.length} kasus serupa
                  {flow.retrieval.fault_code && <> · kode <Mono>{flow.retrieval.fault_code}</Mono></>}
                </p>
              </li>
              <li className="rounded-md bg-surface p-3">
                <Mono className="text-[13px] text-muted-ink">② PLAYBOOK</Mono>
                <p className="mt-1">{flow.playbook.steps.length} langkah, semua bersitasi</p>
              </li>
              <li className="rounded-md bg-surface p-3">
                <Mono className="text-[13px] text-muted-ink">③ KIRIM KE MEKANIK</Mono>
                <p className="mt-1 font-semibold">{flow.mechanic.name}</p>
                <p className="text-[13px] text-muted-ink">{flow.mechanic.reasons.join(" · ")}</p>
              </li>
            </ol>
            <div className="flex flex-wrap items-center gap-2 text-[14px]">
              <span className="text-muted-ink">Entri baru</span>
              <CaseChip id={flow.trigger_entry_id} onOpen={setOpen} />
              <span className="text-muted-ink">· kasus serupa:</span>
              {flow.retrieval.hits.map((h) => (
                <span key={h.case_id} className="inline-flex items-center gap-1">
                  <CaseChip id={h.case_id} onOpen={setOpen} />
                  {h.exact_match.length > 0 && <Mono className="text-[12px] text-running">= {h.exact_match.join(",")}</Mono>}
                </span>
              ))}
              <Mono className="ml-auto text-[13px] text-muted-ink">{totalMs.toFixed(0)} ms</Mono>
            </div>
            <PlaybookView pb={flow.playbook} onOpen={setOpen} />
            {flow.references.length > 0 && (
              <div className="flex flex-wrap items-center gap-2 text-[14px]">
                <span className="text-muted-ink">Dokumen terkait:</span>
                {flow.references.map((d) => <DocChip key={d.citation} d={d} />)}
              </div>
            )}
            <div className="rounded-lg border border-line bg-surface p-4">
              <p className="mb-1 text-[13px] text-muted-ink">
                Pesan ke {flow.mechanic.name} · {flow.mechanic.whatsapp} · {flow.message_source === "llm" ? "ditulis AI, sitasi diverifikasi" : "templat"}
              </p>
              {flow.delivery && (
                <p className={`mb-1 text-[13px] font-semibold ${flow.delivery.status === "failed" ? "text-red-700" : flow.delivery.status === "sent" ? "text-green-700" : "text-muted-ink"}`}>
                  {(flow.delivery.attempts ?? []).filter((a) => a.status === "failed").map((a) => `${CHANNEL[a.channel]} gagal (${a.error ?? "tanpa alasan"}) · `).join("")}
                  {flow.delivery.status === "sent"
                    ? `Terkirim via ${CHANNEL[flow.delivery.channel]} ke ${flow.delivery.to}`
                    : flow.delivery.status === "failed"
                      ? `${CHANNEL[flow.delivery.channel]} gagal: ${flow.delivery.error ?? "tanpa alasan"}`
                      : "Disiapkan saja (pengiriman WhatsApp/Telegram belum dikonfigurasi)"}
                </p>
              )}
              <p className="text-[16px]">{flow.message}</p>
            </div>
          </div>
        )}
      </Section>

      <Section title="Playbook Terkirim" sub={`${cases.length} kasus di ledger · ${scanned} hasil scan · ${bahasa} berbahasa Indonesia/campuran`}>
        <div className="flex items-center gap-3">
          <Btn variant="secondary" onClick={() => maintApi.verify().then((v) => setVerify(chainVerdict(v))).catch((e) => setVerify(String(e)))}>
            Verifikasi rantai hash
          </Btn>
          {verify && <span className="text-[15px] text-running">{verify}</span>}
        </div>
        {feed.length === 0 && <p className="text-[16px] text-muted-ink">Belum ada — catat laporan di atas, atau kirim telemetri abnormal.</p>}
        {feed.slice(0, 5).map((f) => (
          <div key={f.id} className="flex flex-col gap-2 rounded-lg border border-line p-4">
            <p className="flex flex-wrap items-center gap-2 text-[15px]">
              {f.case && <CaseChip id={f.case.id} onOpen={setOpen} />}
              <span>{f.case?.machine_id} · {f.case?.symptom}</span>
              <span className="text-muted-ink">→ {f.mechanic?.name ?? "—"}</span>
              <span className={cn("ml-auto rounded-full border px-2 text-[12px] font-bold", CONF_STYLE[f.playbook.confidence])}>
                {CONF_LABEL[f.playbook.confidence]}
              </span>
            </p>
            <p className="text-[14px] text-muted-ink">{f.message}</p>
          </div>
        ))}
      </Section>

      <Section title="Cari Riwayat & Kode" sub="Kode alarm dan nomor part dicocokkan persis (tahan salah-baca OCR), lalu dinilai dengan BM25 + metadata.">
        <div className="grid gap-3 md:grid-cols-[1fr_150px_150px_auto]">
          <input
            aria-label="Cari"
            value={q}
            onChange={(e) => setQ(e.target.value)}
            onKeyDown={(e) => e.key === "Enter" && void search()}
            placeholder="SV0401 · HSK-A63-SP-0042 · spindel bunyi kasar"
            className={field}
          />
          <select aria-label="Filter mesin" value={qMachine} onChange={(e) => setQMachine(e.target.value)} className={field}>
            <option value="">Semua mesin</option>
            {MACHINES.map((m) => <option key={m}>{m}</option>)}
          </select>
          <select aria-label="Filter bahasa" value={qLang} onChange={(e) => setQLang(e.target.value)} className={field}>
            <option value="">Semua bahasa</option>
            <option value="id">Indonesia</option>
            <option value="en">English</option>
          </select>
          <Btn onClick={() => void search()}>Cari</Btn>
        </div>
        {res && (
          <div className="flex flex-col gap-2">
            <p className="text-[14px] text-muted-ink">
              {res.fault_code && <>Kode alarm terdeteksi <Mono className="text-navy">{res.fault_code}</Mono> · </>}
              {res.part_keys.length > 0 && <>part <Mono className="text-navy">{res.part_keys.join(", ")}</Mono> · </>}
              filter: {res.filters.join(", ")} · {res.latency_ms} ms
            </p>
            {res.cases.length === 0 && res.documents.length === 0 && (
              <p className="text-[16px] text-muted-ink">{res.message ?? "Tidak ada hasil relevan."}</p>
            )}
            {res.cases.map((h) => (
              <div key={h.case_id} className="flex flex-wrap items-center gap-2 rounded-md border border-line p-3 text-[15px]">
                <CaseChip id={h.case_id} onOpen={setOpen} />
                {h.exact_match.length > 0 ? (
                  <span className="rounded bg-ok-bg px-1.5 py-0.5 text-[12px] font-bold text-running">kode persis: {h.exact_match.join(", ")}</span>
                ) : (
                  <span className="rounded bg-page px-1.5 py-0.5 text-[12px] text-muted-ink">relevan</span>
                )}
                <SourceTag c={h.case} />
                <span className="min-w-0 flex-1">{h.case.machine_id} · {h.case.symptom}</span>
                <Mono className="text-[13px] text-muted-ink" >skor {h.score?.toFixed(2)}</Mono>
              </div>
            ))}
            {res.documents.length > 0 && (
              <div className="flex flex-wrap items-center gap-2 text-[14px]">
                <span className="text-muted-ink">Dokumen pabrik:</span>
                {res.documents.map((d) => <DocChip key={d.citation} d={d} />)}
              </div>
            )}
          </div>
        )}
      </Section>

      <Card className="p-5">
        <button type="button" onClick={() => setChatOpen((o) => !o)} className="min-h-12 text-[16px] font-semibold text-muted-ink hover:text-navy">
          {chatOpen ? "▾" : "▸"} Tanya riwayat perawatan (chat — antarmuka sekunder)
        </button>
        {chatOpen && (
          <div className="mt-3 flex flex-col gap-3">
            <div className="flex gap-3">
              <input
                aria-label="Pertanyaan"
                value={chatQ}
                onChange={(e) => setChatQ(e.target.value)}
                onKeyDown={(e) => e.key === "Enter" && void ask()}
                placeholder="Kenapa Alarm 108 berulang di CNC-02?"
                className={field}
              />
              <Btn onClick={() => void ask()} disabled={chatBusy}>{chatBusy ? <Spinner className="size-5" /> : "Tanya"}</Btn>
            </div>
            {chat && (
              <div className="flex flex-col gap-2 rounded-md bg-page p-3 text-[15px]">
                <p className="whitespace-pre-wrap">{chat.answer ?? chat.message ?? "Tidak ada jawaban."}</p>
                {(chat.sources ?? []).some((s) => s.case_id) && (
                  <p className="flex flex-wrap items-center gap-1.5 text-[13px] text-muted-ink">
                    Kasus ledger:
                    {(chat.sources ?? []).filter((s) => s.case_id).map((s) => (
                      <CaseChip key={s.citation} id={s.case_id!} onOpen={setOpen} />
                    ))}
                  </p>
                )}
                <SourceCitations sources={chatDocs(chat)} />
              </div>
            )}
          </div>
        )}
      </Card>
    </>
  );
}
