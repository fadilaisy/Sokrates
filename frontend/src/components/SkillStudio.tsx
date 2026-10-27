import { useEffect, useState } from "react";
import { useCockpit } from "../cockpit/store";
import { cn } from "../lib/utils";
import { api } from "../lib/api";
import type { CostRow, PlantCosts } from "../lib/api";
import { Btn, Spinner } from "../cockpit/ui";
import { SkillLibraryView, SkillTeach, SkillToolsView } from "./SkillTeach";
import type { TeachTarget } from "./SkillTeach";

const errText = (e: unknown) => (e instanceof Error ? e.message : String(e));
const rp = (v: number | null | undefined) => (v == null ? "–" : `Rp ${v.toLocaleString("id-ID")}`);
const today = () => new Date().toISOString().slice(0, 10);
const NAME_KEY = "sf.costs.name";

function readName(): string {
  try {
    return localStorage.getItem(NAME_KEY) ?? "";
  } catch {
    return "";
  }
}

const field = "min-h-12 rounded-md border border-line bg-surface px-3 text-[15px] text-navy";

/**
 * Biaya pabrik: the costs the solver reads, one row at a time. A change waits for
 * a second person's approval and its date; then what-if scenarios, agent reroutes
 * and the chat use it on their next solve.
 */
function CostRows() {
  const [data, setData] = useState<PlantCosts | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [editing, setEditing] = useState<string | null>(null);
  const [name, setName] = useState(readName);

  const load = () => api.costs().then(setData).catch((e) => setError(errText(e)));
  useEffect(() => {
    load();
  }, []);
  const saveName = (v: string) => {
    setName(v);
    try {
      localStorage.setItem(NAME_KEY, v);
    } catch {
      /* per-viewer convenience only */
    }
  };

  if (!data) {
    return error ? <p role="alert" className="text-fault">{error}</p>
      : <p className="flex items-center gap-2 text-muted-ink"><Spinner className="size-5" /> Memuat biaya…</p>;
  }
  return (
    <div className="flex max-w-[720px] flex-col gap-4">
      <div className="flex flex-wrap items-end justify-between gap-3">
        <div className="flex flex-col gap-0.5">
          <h3 className="text-[22px] font-bold">Biaya pabrik</h3>
          <span className="text-[14px] text-muted-ink">Dipakai solver untuk what-if, reroute oleh agent, dan jawaban chatbot.</span>
        </div>
        <label className="flex flex-col gap-1 text-[13px] text-muted-ink">Nama Anda
          <input value={name} onChange={(e) => saveName(e.target.value)} placeholder="Nama dan jabatan" className={field} />
        </label>
      </div>
      {error && <p role="alert" className="rounded-md border border-danger-line bg-danger-bg px-3 py-2 text-[15px] text-fault">{error}</p>}
      <section className="flex flex-col rounded-md border border-line bg-surface px-4 sm:px-6">
        {data.rows.map((row, i) => (
          <Row
            key={row.key}
            row={row}
            last={i === data.rows.length - 1}
            name={name.trim()}
            editing={editing === row.key}
            onEdit={(on) => setEditing(on ? row.key : null)}
            onChanged={() => { setError(null); setEditing(null); load(); }}
            onError={setError}
          />
        ))}
      </section>
      <p className="text-[14px] text-muted-ink">
        Setiap baris diubah sendiri dan butuh persetujuan orang kedua. Tercatat di audit ledger. Ambang keselamatan Tier 1 ada di skill dasar dan tidak diubah di sini.
      </p>
    </div>
  );
}

function Row({ row, last, name, editing, onEdit, onChanged, onError }: {
  row: CostRow; last: boolean; name: string; editing: boolean;
  onEdit: (on: boolean) => void; onChanged: () => void; onError: (e: string) => void;
}) {
  const [value, setValue] = useState("");
  const [from, setFrom] = useState(today());
  const [busy, setBusy] = useState(false);
  const req = row.open_request;
  const parsed = Number(value.replace(/[^0-9]/g, ""));

  async function run(fn: () => Promise<unknown>) {
    setBusy(true);
    try {
      await fn();
      onChanged();
    } catch (e) {
      onError(errText(e));
    } finally {
      setBusy(false);
    }
  }
  const needName = !name ? "Isi nama Anda di atas dulu." : undefined;

  return (
    <div className={cn("flex flex-col gap-3 py-4", !last && "border-b border-line")}>
      <div className="flex min-h-11 flex-wrap items-center gap-x-3 gap-y-2">
        <span className={cn("flex-[1_1_200px] text-[16px]", editing && "font-semibold")}>{row.label}</span>
        {req && (
          <span className="rounded-full bg-warn-bg px-2.5 py-1 text-[13px] font-semibold text-maint">
            {rp(req.value)} {req.status === "approved" ? `mulai ${req.effective_from}` : "menunggu persetujuan"}
          </span>
        )}
        <strong className="text-[17px]">{rp(row.value)} <span className="font-normal text-muted-ink">{row.unit}</span></strong>
        {!req && !editing && <Btn variant="secondary" onClick={() => { setValue(""); onEdit(true); }}>Ubah</Btn>}
      </div>

      {editing && !req && (
        <div className="flex flex-wrap items-end gap-2.5">
          <label className="flex flex-[1_1_160px] flex-col gap-1 text-[13px] text-muted-ink">Nilai baru (Rp)
            <input
              autoFocus
              inputMode="numeric"
              value={value}
              onChange={(e) => setValue(e.target.value)}
              placeholder={row.value?.toLocaleString("id-ID")}
              className={cn(field, "border-2 border-navy font-semibold")}
            />
          </label>
          <label className="flex flex-[1_1_140px] flex-col gap-1 text-[13px] text-muted-ink">Berlaku mulai
            <input type="date" value={from} min={today()} onChange={(e) => setFrom(e.target.value)} className={field} />
          </label>
          <Btn
            title={needName}
            disabled={busy || !name || !parsed || parsed === row.value}
            onClick={() => run(() => api.requestCostChange({ key: row.key, value: parsed, effective_from: from, requested_by: name }))}
          >
            {busy && <Spinner className="size-5" />} Minta persetujuan
          </Btn>
          <Btn variant="secondary" onClick={() => onEdit(false)}>Batal</Btn>
          {needName && <span className="w-full text-[13px] text-maint">{needName}</span>}
        </div>
      )}

      {req?.status === "pending" && (
        <div className="flex flex-wrap items-center gap-2.5 rounded-md bg-page px-3 py-2 text-[14px]">
          <span className="flex-[1_1_240px]">
            Diminta {req.requested_by}: {rp(req.old_value)} → <strong>{rp(req.value)}</strong>, berlaku {req.effective_from}.
          </span>
          {name && name.toLowerCase() !== req.requested_by.toLowerCase() ? (
            <>
              <Btn variant="secondary" disabled={busy} onClick={() => run(() => api.decideCostChange(req.request_id, "reject", name))}>Tolak</Btn>
              <Btn disabled={busy} onClick={() => run(() => api.decideCostChange(req.request_id, "approve", name))}>Setujui</Btn>
            </>
          ) : (
            <span className="text-muted-ink">{name ? "Menunggu orang kedua menyetujui." : "Isi nama Anda untuk menyetujui."}</span>
          )}
        </div>
      )}
      {!req && row.last_change && !editing && (
        <span className="text-[13px] text-muted-ink">
          Terakhir diubah {row.last_change.requested_by}, disetujui {row.last_change.approved_by}: {rp(row.last_change.old_value)} → {rp(row.last_change.value)}
        </span>
      )}
    </div>
  );
}

type Tab = "library" | "teach" | "tools" | "costs";
const TABS: { id: Tab; label: string }[] = [
  { id: "library", label: "Pustaka skill" },
  { id: "teach", label: "Ajarkan skill" },
  { id: "tools", label: "Tools" },
  { id: "costs", label: "Biaya & SLA" },
];

/**
 * Skill Studio — experts teach skills (story + resolved cases → AI draft with
 * citations → test run on a PLC scenario → approve), and the library shows
 * every skill, the tools they use and their ledger history. Mounted globally
 * in Shell; opened from the sidebar or the copilot card.
 */
export default function SkillStudio() {
  const { skillStudioOpen, setSkillStudioOpen } = useCockpit();
  const [tab, setTab] = useState<Tab>("library");
  const [editId, setEditId] = useState<TeachTarget | null>(null);
  if (!skillStudioOpen) return null;
  const close = () => setSkillStudioOpen(false);
  const openTeach = (id: string | null, clone = false, proposal?: string) => {
    setEditId(id || proposal ? { id: id ?? "", clone, proposal } : null);
    setTab("teach");
  };
  return (
    <>
      <div
        className="sf-fade-in fixed inset-0 z-40"
        style={{ background: "rgb(24 50 79 / 0.3)" }}
        onClick={close}
        aria-hidden
      />
      <aside
        role="dialog"
        aria-modal="true"
        aria-label="Skill Studio"
        className="sf-slide-in fixed inset-y-0 right-0 z-50 flex flex-col gap-4 overflow-y-auto bg-surface p-4 shadow-[-8px_0_24px_rgba(0,0,0,.2)] sm:p-6"
        style={{ width: "min(1080px, 100vw)" }}
        onKeyDown={(e) => e.key === "Escape" && close()}
      >
        <div className="flex items-center gap-3">
          <div className="flex-1">
            <h2 className="text-[24px] font-bold">Skill Studio</h2>
            <p className="text-[14px] text-muted-ink">
              Ahli mengajar sekali dengan bahasanya sendiri; agent menjalankannya di setiap alarm yang cocok.
            </p>
          </div>
          <button
            type="button"
            onClick={close}
            aria-label="Tutup"
            className="grid size-14 shrink-0 place-items-center rounded-md border border-line hover:bg-page"
          >
            ✕
          </button>
        </div>
        <div role="tablist" aria-label="Bagian Skill Studio" className="flex flex-wrap gap-2">
          {TABS.map((t) => (
            <button
              key={t.id}
              type="button"
              role="tab"
              aria-selected={tab === t.id}
              onClick={() => (t.id === "teach" ? openTeach(null) : setTab(t.id))}
              className={cn(
                "min-h-12 rounded-full border px-4 text-[15px] font-semibold",
                tab === t.id ? "border-navy bg-navy text-white" : "border-line bg-surface text-navy hover:bg-page",
              )}
            >
              {t.label}
            </button>
          ))}
        </div>
        {tab === "library" && <SkillLibraryView onTeach={openTeach} />}
        {tab === "teach" && <SkillTeach key={editId ? `${editId.id}-${editId.clone}-${editId.proposal ?? ""}` : "new"} edit={editId} onDone={() => setTab("library")} />}
        {tab === "tools" && <SkillToolsView />}
        {tab === "costs" && <CostRows />}
      </aside>
    </>
  );
}
