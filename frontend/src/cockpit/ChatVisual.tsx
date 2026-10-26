import { useRef, useState } from "react";

// Scripts never run (no allow-scripts) and the CSP blocks every network request;
// allow-same-origin only lets the cockpit read the frame's height to size it.
const CSP = "default-src 'none'; style-src 'unsafe-inline'; img-src data:; font-src data:";

function frameDoc(html: string): string {
  return `<!doctype html><html><head><meta charset="utf-8"><meta http-equiv="Content-Security-Policy" content="${CSP}">
<style>
  html,body{margin:0;padding:0;background:#fff;color:#0f1d2e;font:14px/1.4 Inter,system-ui,sans-serif}
  body{padding:8px}
  svg{max-width:100%;height:auto}
  table{border-collapse:collapse;width:100%}
  td,th{border-bottom:1px solid #d9dee5;padding:4px 6px;text-align:left}
</style></head><body>${html}</body></html>`;
}

/** The chat answer's optional HTML/SVG visual (backend/chat_visual.py), in a sandboxed frame. */
export function ChatVisual({ html }: { html: string }) {
  const ref = useRef<HTMLIFrameElement>(null);
  const [height, setHeight] = useState(160);
  const fit = () => {
    const body = ref.current?.contentDocument?.body;
    if (body) setHeight(Math.min(480, Math.max(60, body.scrollHeight + 4)));
  };
  return (
    <iframe
      ref={ref}
      title="Visual dari AI"
      sandbox="allow-same-origin"
      srcDoc={frameDoc(html)}
      onLoad={fit}
      className="mt-3 w-full rounded-md border border-line bg-white"
      style={{ height }}
    />
  );
}
