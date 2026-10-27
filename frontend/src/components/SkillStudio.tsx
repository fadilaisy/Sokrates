import { useState } from "react";
import { useCockpit } from "../cockpit/store";
import { cn } from "../lib/utils";
import { CostRows } from "./CostRows";
import { SkillLibraryView, SkillTeach, SkillToolsView } from "./SkillTeach";
import type { TeachTarget } from "./SkillTeach";

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
