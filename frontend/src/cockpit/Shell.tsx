import { RefreshCw } from "lucide-react";
import type { ReactNode } from "react";
import { cn } from "../lib/utils";
import { SHIFT_END, SHIFT_START } from "../lib/shift";
import { useCockpit } from "./store";
import type { Page } from "./store";
import { AssistantFace, LogoMark, Mono } from "./ui";

const NAV: { page: Page; label: string }[] = [
  { page: "dashboard", label: "Dasbor" },
  { page: "cockpit", label: "Cockpit" },
  { page: "floor", label: "Lantai Pabrik" },
];

function Sidebar() {
  const { page, setPage } = useCockpit();
  return (
    <aside className="sticky top-0 hidden h-screen w-[280px] shrink-0 flex-col justify-between overflow-y-auto bg-page px-8 py-10 lg:flex">
      <div className="flex flex-col gap-8">
        <div>
          <div className="flex items-center gap-2.5">
            <LogoMark className="h-8 w-10" />
            <span className="text-[30px] font-bold tracking-tight text-navy">Skillforge</span>
          </div>
          <p className="mt-2 text-[14px] text-navy">Manufaktur lancar, pemantauan ringkas.</p>
        </div>

        <nav aria-label="Menu utama" className="flex flex-col gap-3">
          <p className="text-[15px] font-bold text-navy">UMUM</p>
          {NAV.map((n) => {
            const active = n.page === page;
            return (
              <button
                key={n.page}
                type="button"
                onClick={() => setPage(n.page)}
                aria-current={active ? "page" : undefined}
                className={cn(
                  "flex min-h-12 items-center gap-3 rounded-r-md border-l-4 px-4 text-left text-[17px] transition-colors",
                  active ? "border-accent-blue font-bold text-navy" : "border-transparent text-navy hover:bg-white/70",
                )}
              >
                <span className={cn("size-2.5 rounded-full", active ? "bg-accent-blue" : "bg-disabled")} aria-hidden />
                {n.label}
              </button>
            );
          })}
        </nav>

        <div className="flex flex-col gap-3">
          <p className="text-[15px] font-bold text-navy">BANTUAN</p>
          {["Tab 1", "Tab 2", "Tab 3"].map((t) => (
            <span key={t} className="flex min-h-12 items-center px-5 text-[17px] text-navy/80">
              {t}
            </span>
          ))}
        </div>
      </div>

      <div className="mt-8 flex flex-col gap-3 border-t border-accent-blue/40 pt-6 text-[15px] text-navy">
        <a href="#" className="hover:underline">Kebijakan Privasi</a>
        <a href="#" className="hover:underline">Syarat &amp; Ketentuan</a>
        <p>© 2026 SkillForge</p>
      </div>
    </aside>
  );
}

/** Compact top nav for screens narrower than the sidebar layout. */
function MobileNav() {
  const { page, setPage } = useCockpit();
  return (
    <nav aria-label="Menu utama" className="flex gap-2 overflow-x-auto border-b border-line bg-page px-4 py-2 lg:hidden">
      {NAV.map((n) => (
        <button
          key={n.page}
          type="button"
          onClick={() => setPage(n.page)}
          className={cn(
            "min-h-12 shrink-0 rounded-md px-4 text-[16px]",
            n.page === page ? "bg-navy font-bold text-white" : "bg-surface text-navy",
          )}
        >
          {n.label}
        </button>
      ))}
    </nav>
  );
}

export function Header() {
  const { state, sapVersion, online, reset, resetting } = useCockpit();
  const plant = state?._meta.facility ?? "PT Karawang Precision Manufacturing";
  const shift = `Shift 1 · ${String(SHIFT_START).padStart(2, "0")}:00–${SHIFT_END}:00`;
  return (
    <header className="flex flex-wrap items-center justify-between gap-3 rounded-lg border border-line bg-surface px-4 py-2">
      <div className="flex min-w-0 flex-wrap items-baseline gap-x-3 gap-y-1">
        <span className="text-[17px] font-semibold">SkillForge</span>
        <span className="text-[15px]">
          {plant} · {shift}
        </span>
      </div>
      <div className="flex items-center gap-5">
        <span className="flex items-center gap-2 text-[15px]" role="status">
          <span className={cn("size-2.5 rounded-full", online ? "bg-ok-line" : "bg-danger-line")} aria-hidden />
          SAP v{sapVersion} · {online ? "Terhubung" : "Terputus"}
        </span>
        <Mono className="text-[15px]" >
          <span className="font-bold">ID</span>
          <span className="text-muted-ink" title="Bahasa Inggris belum tersedia"> | EN</span>
        </Mono>
        <button
          type="button"
          onClick={reset}
          disabled={resetting}
          className="flex min-h-12 items-center gap-2 rounded-lg border border-line bg-surface px-4 text-[16px] font-bold hover:bg-page disabled:opacity-60"
        >
          <RefreshCw className={cn("size-4", resetting && "sf-spin")} aria-hidden />
          Muat ulang state
        </button>
      </div>
    </header>
  );
}

function Toast() {
  const { toast } = useCockpit();
  if (!toast) return null;
  return (
    <div role="status" className="sf-fade-in fixed top-20 right-6 z-50 flex max-w-md items-center gap-3 rounded-lg bg-navy px-5 py-4 text-[17px] font-semibold text-white shadow-xl">
      <span className="grid size-6 shrink-0 place-items-center rounded-full bg-ok-line text-[14px]" aria-hidden>
        ✓
      </span>
      {toast}
    </div>
  );
}

export function ChatFab() {
  const { openChat, chatOpen } = useCockpit();
  if (chatOpen) return null;
  return (
    <button
      type="button"
      onClick={() => openChat("general")}
      aria-label="Buka AI Supervisor"
      className="fixed right-6 bottom-6 z-40 grid size-14 place-items-center rounded-full border border-black/10 bg-[#e9e9e7] shadow-[0_2px_8px_rgba(0,0,0,.35)] transition-transform hover:scale-105 active:scale-95"
    >
      <AssistantFace className="size-8" />
    </button>
  );
}

export function Shell({ children }: { children: ReactNode }) {
  return (
    <div className="flex min-h-screen bg-page text-navy">
      <Sidebar />
      <div className="min-w-0 flex-1">
        <MobileNav />
        <main className="mx-auto flex max-w-[1440px] flex-col gap-4 p-4 pb-24">{children}</main>
      </div>
      <Toast />
      <ChatFab />
    </div>
  );
}
