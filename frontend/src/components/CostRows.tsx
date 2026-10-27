import { useEffect, useState } from "react";
import { api } from "../lib/api";
import type { CostRow, PlantCosts } from "../lib/api";
import { Btn, Spinner } from "../cockpit/ui";
import { cn } from "../lib/utils";

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
export function CostRows() {
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
