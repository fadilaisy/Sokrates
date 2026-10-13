import { useCallback, useEffect, useRef, useState } from "react";
import type { ChangeEvent, ReactNode } from "react";
import { rag } from "../lib/api";
import type {
  AnswerResponse,
  RagDoc,
  RagIncident,
  RagSource,
  RagStatus,
  RetrieveResponse,
  WorkCenter,
} from "../lib/api";
import { ScoreBar, SourceChips, TierBadge } from "./SourceViewer";

const DOC_TYPES: { value: string; label: string }[] = [
  { value: "", label: "Deteksi otomatis" },
  { value: "oem_manual", label: "Manual OEM" },
  { value: "sop", label: "SOP" },
  { value: "maintenance_log", label: "Log perawatan" },
  { value: "quality", label: "Kualitas" },
  { value: "safety", label: "Keselamatan (K3)" },
  { value: "other", label: "Lainnya" },
];
const TYPE_LABEL = Object.fromEntries(DOC_TYPES.map((d) => [d.value, d.label]));

const STATUS_STYLE: Record<string, string> = {
  pending: "bg-[#353A50] text-[#9A8C98]",
  processing: "bg-[#F2A900]/15 text-[#F2A900] animate-pulse",
  indexed: "bg-[#CBF3F0]/15 text-[#CBF3F0]",
  failed: "bg-[#E76F51]/15 text-[#E76F51]",
  superseded: "bg-[#22223B] text-[#9A8C98] line-through",
};

const input =
  "mt-1.5 w-full rounded-lg border border-[#353A50] bg-[#22223B] px-3 py-2 text-xs text-[#F9F7F7] placeholder-[#9A8C98]/50 focus:border-[#F2A900] focus:outline-none";
const card = "overflow-hidden rounded-2xl border border-[#353A50] bg-[#2A2A40] shadow-xl shadow-black/30";

export default function DocumentLibrary({
  workCenters,
  onOpenSource,
}: {
  workCenters: WorkCenter[];
  onOpenSource: (s: RagSource) => void;
}) {
  const [docs, setDocs] = useState<RagDoc[]>([]);
  const [status, setStatus] = useState<RagStatus | null>(null);
  const [err, setErr] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);
  const [seeding, setSeeding] = useState(false);
  const [detail, setDetail] = useState<RagDoc | null>(null);

  const refresh = useCallback(async () => {
    try {
      const [d, s] = await Promise.all([rag.listDocs(), rag.status()]);
      setDocs(d);
      setStatus(s);
      setErr(null);
    } catch (e) {
      setErr(e instanceof Error ? e.message : String(e));
    }
  }, []);

  useEffect(() => {
    refresh();
  }, [refresh]);

  // Poll while anything is still being processed.
  const busy = docs.some((d) => d.status === "pending" || d.status === "processing");
  useEffect(() => {
    if (!busy) return;
    const t = window.setInterval(refresh, 1500);
    return () => window.clearInterval(t);
  }, [busy, refresh]);

  async function onSeed() {
    setSeeding(true);
    try {
      const r = await rag.seedDemo();
      setNotice(`Korpus demo dimuat: ${r.length} dokumen.`);
      await refresh();
    } catch (e) {
      setErr(e instanceof Error ? e.message : String(e));
    } finally {
      setSeeding(false);
    }
  }

  async function openDetail(id: string) {
    try {
      setDetail(await rag.getDoc(id));
    } catch (e) {
      setErr(e instanceof Error ? e.message : String(e));
    }
  }

  const counts = status?.documents ?? {};
  return (
    <div className="space-y-6">
      {/* Header */}
      <div className={card}>
        <div className="flex flex-wrap items-center justify-between gap-3 border-b border-[#353A50] bg-[#22223B]/50 px-6 py-4">
          <div>
            <div className="flex items-center gap-2">
              <span className="text-base font-semibold text-[#F9F7F7]">Dokumen Pabrik & RAG</span>
              <span className="rounded bg-[#F2A900]/20 px-2 py-0.5 text-[10px] font-bold text-[#F2A900]">
                METADATA-AUGMENTED
              </span>
            </div>
            <p className="mt-1 max-w-3xl text-xs text-[#9A8C98]">
              Manual, SOP, log perawatan, dan dokumen K3 diindeks dengan metadata (mesin, fault code, tier).
              Saat insiden, scoring layer memilih passage paling relevan dan setiap klaim AI wajib bersitasi.
            </p>
          </div>
          <div className="flex items-center gap-2">
            <button
              onClick={refresh}
              className="rounded-lg bg-[#353A50] px-3 py-1.5 text-xs text-[#F9F7F7] hover:bg-[#4A4E69] active:scale-95"
            >
              Muat ulang
            </button>
            <button
              onClick={onSeed}
              disabled={seeding}
              className="rounded-lg border border-[#F2A900]/50 bg-[#F2A900]/10 px-3 py-1.5 text-xs font-semibold text-[#F2A900] hover:bg-[#F2A900]/20 active:scale-95 disabled:opacity-50"
            >
              {seeding ? "Memuat…" : "Muat korpus demo"}
            </button>
          </div>
        </div>
        <div className="grid grid-cols-2 gap-px bg-[#353A50] sm:grid-cols-5">
          {[
            ["Terindeks", counts.indexed ?? 0],
            ["Diproses", (counts.pending ?? 0) + (counts.processing ?? 0)],
            ["Gagal", counts.failed ?? 0],
            ["Chunk", status?.chunks ?? 0],
            ["LLM", status?.llm_providers.length ? status.llm_providers.join(" + ") : "belum diset"],
          ].map(([k, v]) => (
            <div key={String(k)} className="bg-[#2A2A40] px-5 py-3">
              <p className="text-[10px] uppercase tracking-wide text-[#9A8C98]">{k}</p>
              <p className="mt-0.5 text-sm font-semibold text-[#F9F7F7]">{v}</p>
            </div>
          ))}
        </div>
        {status && !status.ocr.languages?.includes("ind") && (
          <p className="border-t border-[#353A50] px-6 py-2 text-[11px] text-[#F2A900]">
            OCR {status.ocr.available ? `aktif (${status.ocr.languages})` : "tidak tersedia"} — pasang data Tesseract
            bahasa Indonesia (<span className="font-mono">tesseract-ocr-ind</span>) untuk hasil scan Bahasa yang lebih akurat.
          </p>
        )}
      </div>

      {err && (
        <div className="rounded-xl border border-[#E76F51]/30 bg-[#E76F51]/5 p-3 text-xs text-[#E76F51]">{err}</div>
      )}
      {notice && (
        <div className="flex justify-between rounded-xl border border-[#CBF3F0]/30 bg-[#CBF3F0]/10 p-3 text-xs text-[#CBF3F0]">
          <span>{notice}</span>
          <button onClick={() => setNotice(null)}>✕</button>
        </div>
      )}

      <div className="grid gap-6 lg:grid-cols-[380px_1fr]">
        <UploadCard
          workCenters={workCenters}
          onUploaded={(m) => {
            setNotice(m);
            refresh();
          }}
          onError={setErr}
        />
        <DocTable docs={docs} onOpen={openDetail} />
      </div>

      {detail && <DocDetail doc={detail} onClose={() => setDetail(null)} onOpenSource={onOpenSource} onReindexed={refresh} />}

      <RetrievalLab workCenters={workCenters} onOpenSource={onOpenSource} />
    </div>
  );
}

// ─────────────────────────────────────────────────────────────────────────────

function UploadCard({
  workCenters,
  onUploaded,
  onError,
}: {
  workCenters: WorkCenter[];
  onUploaded: (msg: string) => void;
  onError: (msg: string) => void;
}) {
  const [file, setFile] = useState<File | null>(null);
  const [title, setTitle] = useState("");
  const [docType, setDocType] = useState("");
  const [machines, setMachines] = useState<string[]>([]);
  const [effDate, setEffDate] = useState("");
  const [busy, setBusy] = useState(false);
  const fileRef = useRef<HTMLInputElement>(null);

  async function submit() {
    if (!file) return;
    setBusy(true);
    try {
      const r = await rag.upload(file, {
        title: title || undefined,
        doc_type: docType || undefined,
        machine_ids: machines.length ? machines.join(",") : undefined,
        effective_date: effDate || undefined,
      });
      onUploaded(
        r.duplicate
          ? `${file.name}: dokumen identik sudah ada (${r.doc_id}).`
          : `${file.name} diunggah sebagai versi ${r.version}. Sedang diproses…`,
      );
      setFile(null);
      setTitle("");
      setMachines([]);
      setEffDate("");
      if (fileRef.current) fileRef.current.value = "";
    } catch (e) {
      onError(e instanceof Error ? e.message : String(e));
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className={`${card} p-5`}>
      <h3 className="text-sm font-semibold text-[#F9F7F7]">Unggah dokumen</h3>
      <p className="mt-1 text-[11px] text-[#9A8C98]">PDF digital, hasil scan (PNG/JPG/TIFF), atau TXT/MD. Hanya supervisor/admin.</p>
      <div className="mt-4 space-y-3">
        <label
          className={`flex cursor-pointer flex-col items-center justify-center rounded-xl border border-dashed px-4 py-5 text-center text-xs transition-all ${
            file ? "border-[#F2A900] bg-[#F2A900]/5 text-[#F9F7F7]" : "border-[#4A4E69] text-[#9A8C98] hover:border-[#F2A900]"
          }`}
        >
          <input
            ref={fileRef}
            type="file"
            accept=".pdf,.png,.jpg,.jpeg,.tif,.tiff,.txt,.md"
            className="hidden"
            onChange={(e) => setFile(e.target.files?.[0] ?? null)}
          />
          {file ? (
            <>
              <span className="font-semibold">{file.name}</span>
              <span className="mt-0.5 text-[10px] text-[#9A8C98]">{(file.size / 1024).toFixed(0)} KB · klik untuk ganti</span>
            </>
          ) : (
            <span>Klik untuk memilih file</span>
          )}
        </label>
        <label className="block text-[11px] text-[#9A8C98]">
          Judul (opsional)
          <input className={input} value={title} onChange={(e) => setTitle(e.target.value)} placeholder="Diambil dari heading jika kosong" />
        </label>
        <div className="grid grid-cols-2 gap-3">
          <label className="block text-[11px] text-[#9A8C98]">
            Jenis
            <select className={input} value={docType} onChange={(e) => setDocType(e.target.value)}>
              {DOC_TYPES.map((d) => (
                <option key={d.value} value={d.value}>
                  {d.label}
                </option>
              ))}
            </select>
          </label>
          <label className="block text-[11px] text-[#9A8C98]">
            Tanggal berlaku
            <input type="date" className={input} value={effDate} onChange={(e) => setEffDate(e.target.value)} />
          </label>
        </div>
        <div>
          <p className="text-[11px] text-[#9A8C98]">Mesin (kosong = deteksi otomatis / berlaku semua)</p>
          <div className="mt-1.5 flex flex-wrap gap-1.5">
            {workCenters.map((w) => {
              const on = machines.includes(w.id);
              return (
                <button
                  key={w.id}
                  type="button"
                  title={w.name}
                  onClick={() => setMachines((m) => (on ? m.filter((x) => x !== w.id) : [...m, w.id]))}
                  className={`rounded-md border px-2 py-1 text-[10px] transition-all ${
                    on ? "border-[#F2A900] bg-[#F2A900]/15 text-[#F2A900]" : "border-[#353A50] text-[#9A8C98] hover:text-[#F9F7F7]"
                  }`}
                >
                  {w.id}
                </button>
              );
            })}
          </div>
        </div>
        <button
          onClick={submit}
          disabled={!file || busy}
          className="w-full rounded-lg bg-[#F2A900] px-4 py-2.5 text-xs font-bold text-[#22223B] transition-all hover:bg-[#E29B00] active:scale-[0.98] disabled:opacity-50"
        >
          {busy ? "Mengunggah…" : "Unggah & indeks"}
        </button>
      </div>
    </div>
  );
}

function DocTable({ docs, onOpen }: { docs: RagDoc[]; onOpen: (id: string) => void }) {
  return (
    <div className={card}>
      <div className="border-b border-[#353A50] px-5 py-3">
        <h3 className="text-sm font-semibold text-[#F9F7F7]">Pustaka dokumen</h3>
      </div>
      {docs.length === 0 ? (
        <p className="p-8 text-center text-xs text-[#9A8C98]">
          Belum ada dokumen. Unggah file atau klik <span className="text-[#F2A900]">Muat korpus demo</span>.
        </p>
      ) : (
        <div className="max-h-[460px] overflow-auto">
          <table className="w-full text-left text-xs">
            <thead className="sticky top-0 bg-[#2A2A40] text-[10px] uppercase tracking-wide text-[#9A8C98]">
              <tr>
                <th className="px-5 py-2 font-medium">Dokumen</th>
                <th className="px-3 py-2 font-medium">Jenis</th>
                <th className="px-3 py-2 font-medium">Mesin</th>
                <th className="px-3 py-2 font-medium">Halaman</th>
                <th className="px-3 py-2 font-medium">Status</th>
              </tr>
            </thead>
            <tbody>
              {docs.map((d) => (
                <tr
                  key={d.doc_id}
                  onClick={() => onOpen(d.doc_id)}
                  className="cursor-pointer border-t border-[#353A50]/60 transition-colors hover:bg-[#353A50]/40"
                >
                  <td className="px-5 py-2.5">
                    <p className="font-medium text-[#F9F7F7]">{d.title}</p>
                    <p className="text-[10px] text-[#9A8C98]">
                      v{d.version} · {d.language ?? "?"} · {d.effective_date ? `berlaku ${d.effective_date}` : "tanpa tanggal berlaku"}
                    </p>
                  </td>
                  <td className="px-3 py-2.5 text-[#F9F7F7]/80">{TYPE_LABEL[d.doc_type ?? ""] ?? d.doc_type ?? "—"}</td>
                  <td className="px-3 py-2.5 text-[10px] text-[#F9F7F7]/80">
                    {d.machine_ids.length ? d.machine_ids.join(", ") : <span className="text-[#9A8C98]">semua</span>}
                  </td>
                  <td className="px-3 py-2.5 text-[10px] text-[#F9F7F7]/80">
                    {d.page_count ?? "—"}
                    {d.ocr_pages.length > 0 && <span className="ml-1 text-[#F2A900]">OCR {d.ocr_pages.length}</span>}
                    {d.low_conf_pages.length > 0 && <span className="ml-1 text-[#E76F51]">⚠ {d.low_conf_pages.length}</span>}
                  </td>
                  <td className="px-3 py-2.5">
                    <span className={`rounded px-2 py-0.5 text-[10px] font-semibold ${STATUS_STYLE[d.status] ?? ""}`}>{d.status}</span>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </div>
  );
}

function DocDetail({
  doc,
  onClose,
  onOpenSource,
  onReindexed,
}: {
  doc: RagDoc;
  onClose: () => void;
  onOpenSource: (s: RagSource) => void;
  onReindexed: () => void;
}) {
  const rows: [string, ReactNode][] = [
    ["Doc ID", <span key="id" className="font-mono">{doc.doc_id}</span>],
    ["Jenis", `${TYPE_LABEL[doc.doc_type ?? ""] ?? doc.doc_type} (${doc.doc_type_source ?? "?"}, conf ${doc.doc_type_confidence ?? "?"})`],
    ["Versi", `v${doc.version}${doc.supersedes ? ` · menggantikan ${doc.supersedes}` : ""}${doc.superseded_by ? ` · diganti oleh ${doc.superseded_by}` : ""}`],
    ["Model mesin", doc.machine_models.length ? doc.machine_models.join(", ") : "semua (plant-wide)"],
    ["Fault code", doc.fault_codes?.length ? doc.fault_codes.join(", ") : "—"],
    ["Part number", doc.part_numbers?.length ? doc.part_numbers.join(", ") : "—"],
    ["Chunk per tier", doc.chunks_by_tier ? Object.entries(doc.chunks_by_tier).map(([k, v]) => `${k} ${v}`).join(" · ") : "—"],
    ["Halaman OCR", doc.ocr_pages.length ? doc.ocr_pages.join(", ") : "tidak ada"],
    ["Diunggah", `${doc.uploaded_by} · ${doc.uploaded_at}`],
  ];
  return (
    <div className={card}>
      <div className="flex items-start justify-between border-b border-[#353A50] px-5 py-3">
        <div>
          <p className="text-sm font-semibold text-[#F9F7F7]">{doc.title}</p>
          <p className="text-[11px] text-[#9A8C98]">{doc.filename}</p>
        </div>
        <div className="flex gap-2">
          {doc.status !== "superseded" && (
            <button
              onClick={async () => {
                await rag.reindex(doc.doc_id);
                onReindexed();
                onClose();
              }}
              className="rounded bg-[#353A50] px-3 py-1 text-[11px] text-[#F9F7F7] hover:bg-[#4A4E69]"
            >
              Proses ulang
            </button>
          )}
          <button onClick={onClose} className="rounded bg-[#353A50] px-3 py-1 text-[11px] text-[#F9F7F7] hover:bg-[#4A4E69]">
            Tutup
          </button>
        </div>
      </div>
      <div className="grid gap-5 p-5 md:grid-cols-[1fr_auto]">
        <dl className="grid grid-cols-[130px_1fr] gap-x-4 gap-y-1.5 text-xs">
          {rows.map(([k, v]) => (
            <div key={k} className="contents">
              <dt className="text-[#9A8C98]">{k}</dt>
              <dd className="text-[#F9F7F7]">{v}</dd>
            </div>
          ))}
        </dl>
        {doc.has_viewer && (
          <button
            onClick={() =>
              onOpenSource({ chunk_id: "", doc_id: doc.doc_id, doc_title: doc.title, page: 1, citation: `${doc.doc_id}#p1` })
            }
            className="self-start rounded-lg border border-[#353A50] bg-[#22223B] px-3 py-2 text-[11px] text-[#F9F7F7] hover:border-[#F2A900]"
          >
            Lihat halaman 1
          </button>
        )}
      </div>
      {(doc.error || doc.warnings.length > 0 || doc.low_conf_pages.length > 0) && (
        <div className="space-y-1 border-t border-[#353A50] px-5 py-3 text-[11px]">
          {doc.error && <p className="text-[#E76F51]">Error: {doc.error}</p>}
          {doc.low_conf_pages.map((p) => (
            <p key={p.page} className="text-[#E76F51]">
              ⚠ Halaman {p.page}: confidence OCR {(p.confidence * 100).toFixed(0)}% — perlu ditinjau manual.
            </p>
          ))}
          {doc.warnings.map((w) => (
            <p key={w} className="text-[#F2A900]">{w}</p>
          ))}
        </div>
      )}
    </div>
  );
}

// ─────────────────────────────────────────────────────────────────────────────

const PRESETS: { label: string; incident: RagIncident; query: string }[] = [
  { label: "CNC-02 Alarm 108", incident: { machine_id: "CNC-02", fault_code: "Alarm 108", disruption_type: "OVERHEAT", severity: "HIGH" }, query: "langkah pemulihan setelah alarm overheat spindle" },
  { label: "CNC-01 coolant", incident: { machine_id: "CNC-01", fault_code: "Alarm 970" }, query: "berapa tekanan coolant minimum?" },
  { label: "CNC-03 servo", incident: { machine_id: "CNC-03", fault_code: "SV0401" }, query: "servo ready signal dropped" },
  { label: "Di luar topik", incident: { machine_id: "CNC-02" }, query: "jadwal kalibrasi printer label gudang" },
];

function RetrievalLab({ workCenters, onOpenSource }: { workCenters: WorkCenter[]; onOpenSource: (s: RagSource) => void }) {
  const [inc, setInc] = useState<RagIncident>(PRESETS[0].incident);
  const [query, setQuery] = useState(PRESETS[0].query);
  const [ret, setRet] = useState<RetrieveResponse | null>(null);
  const [ans, setAns] = useState<AnswerResponse | null>(null);
  const [busy, setBusy] = useState<"retrieve" | "answer" | null>(null);
  const [err, setErr] = useState<string | null>(null);

  const clean = (i: RagIncident): RagIncident =>
    Object.fromEntries(Object.entries(i).filter(([, v]) => v)) as RagIncident;

  async function run(kind: "retrieve" | "answer") {
    setBusy(kind);
    setErr(null);
    try {
      if (kind === "retrieve") {
        setRet(await rag.retrieve(clean(inc), query));
        setAns(null);
      } else {
        const [r, a] = await Promise.all([rag.retrieve(clean(inc), query), rag.answer(clean(inc), query)]);
        setRet(r);
        setAns(a);
      }
    } catch (e) {
      setErr(e instanceof Error ? e.message : String(e));
    } finally {
      setBusy(null);
    }
  }

  const set = (k: keyof RagIncident) => (e: ChangeEvent<HTMLInputElement | HTMLSelectElement>) =>
    setInc((i) => ({ ...i, [k]: e.target.value }));

  return (
    <div className={card}>
      <div className="flex flex-wrap items-center justify-between gap-2 border-b border-[#353A50] bg-[#22223B]/50 px-6 py-4">
        <div>
          <h3 className="text-sm font-semibold text-[#F9F7F7]">Uji retrieval & tanya dokumen</h3>
          <p className="mt-0.5 text-[11px] text-[#9A8C98]">
            Konteks insiden menentukan filter dan skor: S = w₁R + w₂M + w₃F + w₄A − w₅P
          </p>
        </div>
        <div className="flex flex-wrap gap-1.5">
          {PRESETS.map((p) => (
            <button
              key={p.label}
              onClick={() => {
                setInc(p.incident);
                setQuery(p.query);
              }}
              className="rounded-lg border border-[#353A50] bg-[#22223B] px-2.5 py-1 text-[10px] text-[#F9F7F7] hover:border-[#F2A900] hover:text-[#F2A900]"
            >
              {p.label}
            </button>
          ))}
        </div>
      </div>

      <div className="grid gap-3 p-5 sm:grid-cols-4">
        <label className="text-[11px] text-[#9A8C98]">
          Mesin
          <select className={input} value={inc.machine_id ?? ""} onChange={set("machine_id")}>
            <option value="">(tanpa mesin)</option>
            {workCenters.map((w) => (
              <option key={w.id} value={w.id}>
                {w.id} — {w.name}
              </option>
            ))}
          </select>
        </label>
        <label className="text-[11px] text-[#9A8C98]">
          Fault code
          <input className={input} value={inc.fault_code ?? ""} onChange={set("fault_code")} placeholder="mis. Alarm 108" />
        </label>
        <label className="text-[11px] text-[#9A8C98]">
          Jenis gangguan
          <select className={input} value={inc.disruption_type ?? ""} onChange={set("disruption_type")}>
            {["", "BREAKDOWN", "OVERHEAT", "VIBRATION", "COOLANT", "MAINTENANCE", "TOOL_WEAR", "POWER_DIP", "QUALITY"].map((t) => (
              <option key={t} value={t}>
                {t || "(tidak ada)"}
              </option>
            ))}
          </select>
        </label>
        <label className="text-[11px] text-[#9A8C98]">
          Severity
          <select className={input} value={inc.severity ?? ""} onChange={set("severity")}>
            {["", "WARNING", "HIGH", "CRITICAL"].map((t) => (
              <option key={t} value={t}>
                {t || "(tidak ada)"}
              </option>
            ))}
          </select>
        </label>
        <label className="text-[11px] text-[#9A8C98] sm:col-span-4">
          Pertanyaan
          <div className="mt-1.5 flex flex-col gap-2 sm:flex-row">
            <input
              className={`${input} mt-0 flex-1`}
              value={query}
              onChange={(e) => setQuery(e.target.value)}
              onKeyDown={(e) => e.key === "Enter" && run("answer")}
            />
            <button
              onClick={() => run("retrieve")}
              disabled={busy !== null}
              className="rounded-lg bg-[#353A50] px-4 py-2 text-xs font-semibold text-[#F9F7F7] hover:bg-[#4A4E69] disabled:opacity-50"
            >
              {busy === "retrieve" ? "Mencari…" : "Retrieve"}
            </button>
            <button
              onClick={() => run("answer")}
              disabled={busy !== null}
              className="rounded-lg bg-[#F2A900] px-4 py-2 text-xs font-bold text-[#22223B] hover:bg-[#E29B00] disabled:opacity-50"
            >
              {busy === "answer" ? "Menjawab…" : "Tanya (bersitasi)"}
            </button>
          </div>
        </label>
      </div>

      {err && <p className="mx-5 mb-4 rounded-lg bg-[#E76F51]/10 p-3 text-xs text-[#E76F51]">{err}</p>}

      {ans && <AnswerPanel ans={ans} onOpenSource={onOpenSource} />}

      {ret && (
        <div className="border-t border-[#353A50] p-5">
          <div className="mb-3 flex flex-wrap items-center gap-x-4 gap-y-1 text-[11px] text-[#9A8C98]">
            <span className="font-semibold text-[#F9F7F7]">Hasil retrieval</span>
            <span>{ret.candidates} kandidat</span>
            <span>{ret.latency_ms} ms</span>
            <span>ambang abstain {ret.threshold}</span>
            <span className="font-mono text-[10px]">{ret.filters.join(" · ")}</span>
          </div>
          {ret.abstained && (
            <div className="mb-3 rounded-lg border border-[#F2A900]/30 bg-[#F2A900]/5 p-3 text-xs text-[#F2A900]">
              Abstain: {ret.message}
              {ret.results.length > 0 && " Passage keselamatan di bawah tetap disertakan."}
            </div>
          )}
          <div className="space-y-2">
            {ret.results.map((r, i) => (
              <button
                key={r.chunk_id}
                onClick={() => onOpenSource(r)}
                className={`block w-full rounded-xl border p-3 text-left transition-all hover:border-[#F2A900] ${
                  r.forced_safety ? "border-[#E76F51]/40 bg-[#E76F51]/5" : "border-[#353A50] bg-[#22223B]"
                }`}
              >
                <div className="mb-1.5 flex flex-wrap items-center gap-2">
                  <span className="font-mono text-[10px] text-[#9A8C98]">#{i + 1}</span>
                  <TierBadge tier={r.constraint_tier} />
                  {r.forced_safety && <span className="text-[10px] font-bold text-[#E76F51]">SLOT KESELAMATAN</span>}
                  <span className="truncate text-xs font-medium text-[#F9F7F7]">{r.doc_title}</span>
                  <span className="text-[10px] text-[#9A8C98]">hal. {r.page} · {r.section_path}</span>
                </div>
                <p className="mb-2 line-clamp-2 text-[11px] leading-relaxed text-[#F9F7F7]/80">{r.text}</p>
                {r.breakdown && <ScoreBar b={r.breakdown} threshold={ret.threshold} />}
              </button>
            ))}
          </div>
        </div>
      )}
    </div>
  );
}

function AnswerPanel({ ans, onOpenSource }: { ans: AnswerResponse; onOpenSource: (s: RagSource) => void }) {
  const tone =
    ans.status === "answered"
      ? "border-[#CBF3F0]/30 bg-[#CBF3F0]/5"
      : ans.status === "abstained" || ans.status === "insufficient"
        ? "border-[#F2A900]/30 bg-[#F2A900]/5"
        : "border-[#E76F51]/30 bg-[#E76F51]/5";
  return (
    <div className={`mx-5 mb-5 rounded-xl border p-4 ${tone}`}>
      <div className="mb-2 flex flex-wrap items-center justify-between gap-2">
        <span className="text-xs font-semibold text-[#F9F7F7]">
          Jawaban bersitasi · <span className="font-mono text-[#9A8C98]">{ans.status}</span>
        </span>
        <span className="text-[10px] text-[#9A8C98]">
          {ans.model ? `${ans.provider}/${ans.model} · ` : ""}
          {ans.latency_ms} ms
        </span>
      </div>
      {ans.message && <p className="mb-2 text-xs text-[#F2A900]">{ans.message}</p>}
      {ans.claims.length > 0 && (
        <ol className="space-y-2.5">
          {ans.claims.map((c, i) => (
            <li key={i} className="text-xs leading-relaxed text-[#F9F7F7]">
              <span className="mr-1 font-mono text-[#9A8C98]">{i + 1}.</span>
              {c.text}
              <div className="mt-1">
                <SourceChips sources={c.citations} onOpen={onOpenSource} />
              </div>
            </li>
          ))}
        </ol>
      )}
      {ans.rejected_claims.length > 0 && (
        <details className="mt-3 text-[11px] text-[#9A8C98]">
          <summary className="cursor-pointer">
            {ans.rejected_claims.length} klaim ditolak post-check (tidak didukung sumber)
          </summary>
          <ul className="mt-1.5 space-y-1">
            {ans.rejected_claims.map((r, i) => (
              <li key={i} className="line-through decoration-[#E76F51]/60">
                {r.text} <span className="no-underline">— {r.reason}</span>
              </li>
            ))}
          </ul>
        </details>
      )}
      {ans.injection_flags && ans.injection_flags.length > 0 && (
        <p className="mt-2 text-[11px] text-[#E76F51]">
          ⚠ Teks bergaya instruksi dinetralkan di: {ans.injection_flags.join(", ")}
        </p>
      )}
      {(ans.status !== "answered" || ans.claims.length === 0) && ans.sources.length > 0 && (
        <div className="mt-3">
          <p className="mb-1.5 text-[11px] text-[#9A8C98]">Sumber teratas:</p>
          <SourceChips sources={ans.sources} onOpen={onOpenSource} />
        </div>
      )}
    </div>
  );
}
