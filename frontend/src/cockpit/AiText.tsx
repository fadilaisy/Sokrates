import type { ReactNode } from "react";

// Openers and closers must touch their text, so "2 * 3 * 4" stays arithmetic; `_` and `__` also need
// word boundaries, so ids like spindle_temp_c stay whole.
const INLINE =
  /\*\*\*(?=\S)(.+?)(?<=\S)\*\*\*|\*\*(?=\S)(.+?)(?<=\S)\*\*|(?<!\w)__(?=\S)(.+?)(?<=\S)__(?!\w)|~~(?=\S)(.+?)(?<=\S)~~|`([^`\n]+)`|(?<![\w*])\*(?=[^\s*])(.+?)(?<=[^\s*])\*(?![\w*])|(?<![\w_])_(?=[^\s_])(.+?)(?<=[^\s_])_(?![\w_])/g;

type Leaf = (text: string) => ReactNode;

function inline(text: string, leaf: Leaf): ReactNode[] {
  const out: ReactNode[] = [];
  let last = 0;
  for (const m of text.matchAll(INLINE)) {
    const [whole, boldItalic, bold, boldAlt, strike, code, italic, italicAlt] = m;
    out.push(leaf(text.slice(last, m.index)));
    if (boldItalic !== undefined) {
      out.push(<strong key={m.index}><em>{inline(boldItalic, leaf)}</em></strong>);
    } else if (bold !== undefined || boldAlt !== undefined) {
      out.push(<strong key={m.index}>{inline(bold ?? boldAlt, leaf)}</strong>);
    } else if (strike !== undefined) {
      out.push(<del key={m.index}>{inline(strike, leaf)}</del>);
    } else if (code !== undefined) {
      out.push(<code key={m.index} className="rounded bg-surface px-1 font-mono text-[0.9em]">{code}</code>);
    } else {
      out.push(<em key={m.index}>{inline(italic ?? italicAlt, leaf)}</em>);
    }
    last = m.index + whole.length;
  }
  out.push(leaf(text.slice(last)));
  return out;
}

type Block =
  | { kind: "p" | "h"; text: string }
  | { kind: "ul" | "ol"; items: string[]; start: number };

function blocks(text: string): Block[] {
  const out: Block[] = [];
  for (const raw of text.split(/\r?\n/)) {
    const line = raw.trim();
    const prev = out[out.length - 1];
    const heading = line.match(/^#{1,6}\s+(.+?)\s*#*$/);
    const bullet = line.match(/^[-*+]\s+(.+)$/);
    const numbered = line.match(/^(\d+)[.)]\s+(.+)$/);
    if (!line) {
      out.push({ kind: "p", text: "" }); // a blank line ends the block; empty blocks are dropped below
    } else if (heading) {
      out.push({ kind: "h", text: heading[1] });
    } else if (bullet) {
      if (prev?.kind === "ul") prev.items.push(bullet[1]);
      else out.push({ kind: "ul", items: [bullet[1]], start: 1 });
    } else if (numbered) {
      if (prev?.kind === "ol") prev.items.push(numbered[2]);
      else out.push({ kind: "ol", items: [numbered[2]], start: Number(numbered[1]) });
    } else if (prev?.kind === "p" && prev.text) {
      prev.text += `\n${line}`; // single line breaks stay visible
    } else {
      out.push({ kind: "p", text: line });
    }
  }
  return out.filter((b) => "items" in b || b.text);
}

/**
 * AI text with its Markdown shown as real formatting. Builds elements only, so model output can't inject HTML.
 * `leaf` renders the plain runs between formatting (chat uses it for [S1] citation badges).
 */
export function AiText({ text, className, leaf = (t) => t }: { text: string; className?: string; leaf?: Leaf }) {
  return (
    <div className={className}>
      {blocks(text).map((b, i) => {
        const gap = i > 0 ? "mt-2" : "";
        if ("text" in b) return <p key={i} className={`whitespace-pre-line ${b.kind === "h" ? "font-bold" : ""} ${gap}`}>{inline(b.text, leaf)}</p>;
        const items = b.items.map((item, j) => <li key={j}>{inline(item, leaf)}</li>);
        return b.kind === "ul" ? (
          <ul key={i} className={`list-disc pl-5 ${gap}`}>{items}</ul>
        ) : (
          <ol key={i} start={b.start} className={`list-decimal pl-5 ${gap}`}>{items}</ol>
        );
      })}
    </div>
  );
}
