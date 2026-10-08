import { useEffect, useState } from "react";
import { ragPageUrl } from "../lib/api";
import type { RagSource, ScoreBreakdown } from "../lib/api";

/** Colored badge for the solver's three constraint tiers. */
export function TierBadge({ tier }: { tier?: string | null }) {
  if (!tier) return null;
  const cls =
    tier === "Safety"
      ? "bg-[#E76F51]/20 text-[#E76F51] border-[#E76F51]/40"
      : tier === "Quality"
        ? "bg-[#F2A900]/15 text-[#F2A900] border-[#F2A900]/40"
        : "bg-[#353A50] text-[#9A8C98] border-[#4A4E69]";
  return <span className={`rounded border px-1.5 py-0.5 text-[10px] font-semibold ${cls}`}>{tier}</span>;
}

const PARTS: { key: keyof ScoreBreakdown["weighted"]; label: string; color: string }[] = [
  { key: "R", label: "Relevansi", color: "#F2A900" },
  { key: "M", label: "Metadata", color: "#CBF3F0" },
  { key: "F", label: "Kebaruan", color: "#9A8C98" },
  { key: "A", label: "Otoritas", color: "#C9ADA7" },
];

/** Stacked bar of the weighted score terms, plus the OCR penalty in red. */
export function ScoreBar({ b, threshold }: { b: ScoreBreakdown; threshold?: number }) {
  const max = 1;
  const pen = Math.abs(b.weighted.P);
  return (
    <div className="space-y-1">
      <div className="relative flex h-2 w-full overflow-hidden rounded-full bg-[#22223B]">
        {PARTS.map((p) => (
          <div
            key={p.key}
            title={`${p.label}: ${b.weighted[p.key].toFixed(3)} (nilai ${b[p.key].toFixed(2)})`}
            style={{ width: `${(Math.max(b.weighted[p.key], 0) / max) * 100}%`, background: p.color }}
          />
        ))}
        {pen > 0 && (
          <div title={`Penalti OCR: -${pen.toFixed(3)}`} style={{ width: `${(pen / max) * 100}%`, background: "#E76F51" }} />
        )}
        {threshold != null && (
          <div className="absolute top-0 h-full w-px bg-[#F9F7F7]/70" style={{ left: `${threshold * 100}%` }} title={`Ambang abstain ${threshold}`} />
        )}
      </div>
      <div className="flex flex-wrap gap-x-3 gap-y-0.5 text-[10px] text-[#9A8C98]">
        {PARTS.map((p) => (
          <span key={p.key}>
            <span className="mr-1 inline-block h-1.5 w-1.5 rounded-full" style={{ background: p.color }} />
            {p.key} {b[p.key].toFixed(2)}
          </span>
        ))}
        {b.P > 0 && <span className="text-[#E76F51]">P {b.P.toFixed(2)}</span>}
        <span className="font-mono text-[#F9F7F7]">S = {b.total.toFixed(3)}</span>
      </div>
    </div>
  );
}

/** Clickable citation chips. */
export function SourceChips({ sources, onOpen }: { sources: RagSource[]; onOpen: (s: RagSource) => void }) {
  if (!sources.length) return null;
  return (
    <div className="flex flex-wrap gap-1.5">
      {sources.map((s, i) => (
        <button
          key={`${s.chunk_id}-${i}`}
          onClick={() => onOpen(s)}
          title={s.section_path ?? s.doc_title}
          className={`flex items-center gap-1.5 rounded-md border px-2 py-1 text-[10px] transition-all hover:border-[#F2A900] active:scale-95 ${
            s.forced_safety ? "border-[#E76F51]/50 bg-[#E76F51]/10" : "border-[#353A50] bg-[#22223B]"
          }`}
        >
          <span className="max-w-[220px] truncate text-[#F9F7F7]">{s.doc_title}</span>
          <span className="font-mono text-[#9A8C98]">hal. {s.page}</span>
          {s.forced_safety && <span className="font-bold text-[#E76F51]">SAFETY</span>}
        </button>
      ))}
    </div>
  );
}

/** Modal: the page with the cited passage highlighted, so the supervisor can verify before approving. */
export default function SourceViewer({ source, onClose }: { source: RagSource | null; onClose: () => void }) {
  const [imgErr, setImgErr] = useState(false);
  // Parent remounts this component per source (key), so imgErr starts fresh each time.
  useEffect(() => {
    if (!source) return;
    const onKey = (e: KeyboardEvent) => e.key === "Escape" && onClose();
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [source, onClose]);

  if (!source) return null;
  return (
    <div className="fixed inset-0 z-[100] flex items-center justify-center bg-black/70 p-4" onClick={onClose}>
      <div
        className="flex max-h-[92vh] w-full max-w-5xl flex-col overflow-hidden rounded-2xl border border-[#353A50] bg-[#2A2A40] shadow-2xl"
        onClick={(e) => e.stopPropagation()}
      >
        <div className="flex items-start justify-between gap-4 border-b border-[#353A50] px-5 py-3">
          <div className="min-w-0">
            <div className="flex items-center gap-2">
              <TierBadge tier={source.constraint_tier} />
              {source.forced_safety && (
                <span className="text-[10px] font-bold text-[#E76F51]">SLOT KESELAMATAN (selalu disertakan)</span>
              )}
            </div>
            <p className="mt-1 truncate text-sm font-semibold text-[#F9F7F7]">{source.doc_title}</p>
            <p className="truncate text-[11px] text-[#9A8C98]">
              {source.section_path} · halaman {source.page} · <span className="font-mono">{source.citation}</span>
            </p>
          </div>
          <button onClick={onClose} className="rounded bg-[#353A50] px-3 py-1 text-xs text-[#F9F7F7] hover:bg-[#4A4E69]">
            Tutup ✕
          </button>
        </div>
        <div className="grid min-h-0 flex-1 grid-cols-1 gap-0 overflow-hidden md:grid-cols-[1.4fr_1fr]">
          <div className="overflow-auto bg-[#22223B] p-3">
            {imgErr ? (
              <p className="p-6 text-center text-xs text-[#9A8C98]">Pratinjau halaman tidak tersedia untuk dokumen ini.</p>
            ) : (
              <img
                src={ragPageUrl(source.doc_id, source.page, source.chunk_id)}
                alt={`${source.doc_title} halaman ${source.page}`}
                onError={() => setImgErr(true)}
                className="mx-auto w-full max-w-[640px] rounded bg-white shadow"
              />
            )}
          </div>
          <div className="space-y-3 overflow-auto border-t border-[#353A50] p-4 md:border-l md:border-t-0">
            {source.breakdown && (
              <div>
                <p className="mb-1.5 text-[11px] font-semibold text-[#9A8C98]">Kenapa passage ini terpilih</p>
                <ScoreBar b={source.breakdown} />
                <ul className="mt-2 space-y-0.5 text-[11px] text-[#F9F7F7]/80">
                  {source.breakdown.reasons.map((r) => (
                    <li key={r}>• {r}</li>
                  ))}
                </ul>
              </div>
            )}
            {source.text && (
              <div>
                <p className="mb-1.5 text-[11px] font-semibold text-[#9A8C98]">Teks passage</p>
                <pre className="whitespace-pre-wrap rounded-lg bg-[#22223B] p-3 font-sans text-xs leading-relaxed text-[#F9F7F7]">
                  {source.text}
                </pre>
              </div>
            )}
          </div>
        </div>
      </div>
    </div>
  );
}
