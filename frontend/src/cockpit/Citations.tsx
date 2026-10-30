import { Fragment, useEffect, useRef, useState } from "react";
import type { ReactNode } from "react";
import { ragPageUrl } from "../lib/api";
import type { RagSource } from "../lib/api";
import { cn } from "../lib/utils";
import { AiText } from "./AiText";
import { Mono } from "./ui";

/** Number shown for a source id: "S2" → 2 (falls back to its list position). */
function num(s: RagSource, i: number) {
  const n = Number(s.sid?.replace(/^S/, ""));
  return Number.isFinite(n) && n > 0 ? n : i + 1;
}

/** Plain text with inline [S1] markers rendered as small numbered badges. */
function citationBadges(text: string): ReactNode {
  const parts: ReactNode[] = [];
  let last = 0;
  for (const m of text.matchAll(/\[(S\d+(?:\s*,\s*S\d+)*)\]/g)) {
    parts.push(text.slice(last, m.index));
    const ids = m[1].split(",").map((x) => x.trim());
    parts.push(
      <Fragment key={m.index}>
        {ids.map((id) => (
          <sup key={id} className="ml-0.5 rounded bg-accent-blue/15 px-1 font-mono text-[12px] font-semibold text-accent-blue">
            {id.slice(1)}
          </sup>
        ))}
      </Fragment>,
    );
    last = (m.index ?? 0) + m[0].length;
  }
  parts.push(text.slice(last));
  return parts;
}

/** Reply text with its Markdown formatted and its [S1] markers as badges. */
export function CitedText({ text }: { text: string }) {
  return <AiText text={text} leaf={citationBadges} />;
}

/** Passage text with the supporting sentences marked. */
function Highlighted({ text, spans }: { text: string; spans: number[][] }) {
  const out: ReactNode[] = [];
  let pos = 0;
  for (const [a, b] of [...spans].sort((x, y) => x[0] - y[0])) {
    if (a < pos || b > text.length) continue;
    out.push(text.slice(pos, a));
    out.push(
      <mark key={a} className="rounded bg-warn-bg px-0.5 font-medium text-navy ring-1 ring-warn">
        {text.slice(a, b)}
      </mark>,
    );
    pos = b;
  }
  out.push(text.slice(pos));
  return <>{out}</>;
}

/** One source: collapsed chip (document · page), expands to the quoted passage with the matched text highlighted. */
function CitationChip({ s, n }: { s: RagSource; n: number }) {
  const [open, setOpen] = useState(false);
  const safety = s.constraint_tier === "Safety";
  const spans = s.highlights ?? [];
  const quoteRef = useRef<HTMLQuoteElement>(null);
  // Long passages: scroll the first highlighted sentence into view when opened.
  useEffect(() => {
    const box = quoteRef.current;
    const mark = box?.querySelector("mark");
    if (open && box && mark) box.scrollTop = Math.max(0, mark.offsetTop - box.offsetTop - 8);
  }, [open]);
  return (
    <li className={cn("rounded-md border bg-surface", safety ? "border-danger-line" : "border-line")}>
      <button
        type="button"
        onClick={() => setOpen((o) => !o)}
        aria-expanded={open}
        className="flex min-h-11 w-full items-center gap-2 px-3 py-1.5 text-left text-[14px] hover:bg-page"
      >
        <Mono className="rounded bg-accent-blue/15 px-1.5 text-[12px] font-semibold text-accent-blue">{n}</Mono>
        <span className="min-w-0 flex-1 truncate font-semibold text-navy">{s.doc_title}</span>
        <Mono className="shrink-0 text-[12px] text-muted-ink">hal. {s.page}</Mono>
        {safety && <span className="shrink-0 text-[11px] font-bold text-fault">SAFETY</span>}
        <span aria-hidden className="shrink-0 text-muted-ink">{open ? "▾" : "▸"}</span>
      </button>
      {open && (
        <div className="flex flex-col gap-2 border-t border-line px-3 py-2.5 text-[14px]">
          {s.section_path && <p className="text-[13px] text-muted-ink">Bagian: {s.section_path}</p>}
          {s.text ? (
            <blockquote ref={quoteRef} className="relative max-h-56 overflow-y-auto whitespace-pre-wrap rounded border-l-4 border-accent-blue bg-page px-3 py-2 leading-relaxed">
              <Highlighted text={s.text} spans={spans} />
            </blockquote>
          ) : null}
          {!spans.length && (
            <p className="text-[13px] text-muted-ink">Kalimat pendukung tidak terdeteksi otomatis. Baca passage lengkap di atas.</p>
          )}
          <div className="flex flex-wrap items-center justify-between gap-2">
            <Mono className="text-[12px] text-muted-ink">{s.citation}</Mono>
            <a
              href={ragPageUrl(s.doc_id, s.page, s.chunk_id)}
              target="_blank"
              rel="noreferrer"
              className="text-[13px] font-semibold text-accent-blue hover:underline"
            >
              Buka halaman asli ↗
            </a>
          </div>
        </div>
      )}
    </li>
  );
}

/** Expandable source list under a chatbot answer, so a supervisor can verify each claim by hand. */
export function SourceCitations({ sources, className }: { sources?: RagSource[]; className?: string }) {
  if (!sources?.length) return null;
  return (
    <div className={cn("flex flex-col gap-1.5", className)}>
      <p className="text-[13px] font-semibold text-muted-ink">Sumber SOP/dokumen ({sources.length})</p>
      <ul className="flex flex-col gap-1.5">
        {sources.map((s, i) => (
          <CitationChip key={`${s.chunk_id}-${i}`} s={s} n={num(s, i)} />
        ))}
      </ul>
    </div>
  );
}
