import { useState } from "react";
import { api, API_BASE } from "../lib/api";
import { useCockpit } from "../cockpit/store";
import { Btn, Mono, Spinner } from "../cockpit/ui";
import { cn } from "../lib/utils";

function NumField({
  label, value, onChange, min, step,
}: {
  label: string; value: number; onChange: (v: number) => void; min?: number; step?: number;
}) {
  return (
    <label className="flex flex-col gap-1">
      <span className="text-[14px] font-medium text-muted-ink">{label}</span>
      <input
        type="number"
        value={value}
        min={min}
        step={step ?? 1000}
        onChange={(e) => onChange(Number(e.target.value))}
        className="min-h-12 rounded-md border border-line bg-surface px-3 font-mono text-[15px]"
      />
    </label>
  );
}

/**
 * Skill Studio — guided interview → draft → lint → approve overlay.
 * Uses the Phase 3 backend (/api/skills/draft, /lint, /approve) via the
 * store's skillStudio* state. Mounted globally in Shell; opened from sidebar.
 */
export default function SkillStudio() {
  const {
    skillStudioOpen, setSkillStudioOpen,
    skillStudioState, setSkillStudioState,
    skillStudioFormData, setSkillStudioFormData,
    skillStudioDraft, setSkillStudioDraft,
    skillStudioLintResult, setSkillStudioLintResult,
  } = useCockpit();
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [draftErrors, setDraftErrors] = useState<string[]>([]);
  const [skillId, setSkillId] = useState("cnc_milling_v2");
  const [approveResult, setApproveResult] = useState<{ version: string; ledger_entry_id: string } | null>(null);

  if (!skillStudioOpen) return null;
  const f = skillStudioFormData;
  const set = (patch: Partial<typeof f>) => setSkillStudioFormData({ ...f, ...patch });

  const close = () => setSkillStudioOpen(false);
  const resetAll = () => {
    setSkillStudioDraft(null);
    setSkillStudioLintResult(null);
    setApproveResult(null);
    setDraftErrors([]);
    setError(null);
    setSkillStudioState("interview");
  };

  async function runDraft() {
    setBusy(true);
    setError(null);
    setDraftErrors([]);
    try {
      const r = await api.draftSkill(f);
      if (r.validation_errors.length > 0) {
        setDraftErrors(r.validation_errors);
        return; // stay in interview, show Tier 1 / hierarchy errors
      }
      setSkillStudioDraft({
        skill_md: r.skill_md,
        sla_penalties: r.sla_penalties,
        hooks: r.hooks,
        interlocks: r.interlocks,
      });
      setSkillStudioState("draft");
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setBusy(false);
    }
  }

  async function runLint() {
    if (!skillStudioDraft) return;
    setBusy(true);
    setError(null);
    try {
      const r = await api.lintSkill({
        skill_md: skillStudioDraft.skill_md,
        sla_penalties: skillStudioDraft.sla_penalties,
        hooks: skillStudioDraft.hooks,
        interlocks: skillStudioDraft.interlocks,
      });
      setSkillStudioLintResult(r);
      setSkillStudioState("lint");
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setBusy(false);
    }
  }

  async function runApprove() {
    if (!skillStudioDraft || !skillId.trim()) return;
    setBusy(true);
    setError(null);
    try {
      const r = await api.approveSkill({
        skill_id: skillId.trim(),
        skill_md: skillStudioDraft.skill_md,
        sla_penalties: skillStudioDraft.sla_penalties,
        hooks: skillStudioDraft.hooks,
        interlocks: skillStudioDraft.interlocks,
      });
      setApproveResult({ version: r.version, ledger_entry_id: r.ledger_entry_id });
      setSkillStudioState("approved");
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setBusy(false);
    }
  }

  const steps = ["interview", "draft", "lint", "approved"] as const;
  const stepIdx = steps.indexOf(skillStudioState);

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
        className="sf-slide-in fixed inset-y-0 right-0 z-50 flex flex-col gap-4 overflow-y-auto bg-surface p-6 shadow-[-8px_0_24px_rgba(0,0,0,.2)]"
        style={{ width: "min(640px, 100vw)" }}
        onKeyDown={(e) => e.key === "Escape" && close()}
      >
        <div className="flex items-center gap-3">
          <div className="flex-1">
            <h2 className="text-[24px] font-bold">Skill Studio</h2>
            <Mono className="text-[14px] text-muted-ink">
              Wawancara → draft → lint Tier 1 → approve
            </Mono>
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

        <ol className="flex gap-2 text-[14px]" aria-label="Langkah">
          {["Wawancara", "Draft", "Lint", "Aktif"].map((label, i) => (
            <li
              key={label}
              className={cn(
                "flex-1 rounded-md px-2 py-1.5 text-center font-semibold",
                i < stepIdx && "bg-ok-bg text-running",
                i === stepIdx && "bg-navy text-white",
                i > stepIdx && "bg-page text-muted-ink",
              )}
            >
              {i + 1}. {label}
            </li>
          ))}
        </ol>

        {error && (
          <p role="alert" className="rounded-md border border-danger-line bg-danger-bg px-3 py-2 text-[15px] text-fault">
            {error}
          </p>
        )}

        {skillStudioState === "interview" && (
          <div className="flex flex-col gap-3">
            {draftErrors.length > 0 && (
              <div role="alert" className="rounded-md border border-danger-line bg-danger-bg px-3 py-2 text-[15px] text-fault">
                <p className="font-bold">Draft ditolak (hierarki Tier tidak boleh dilonggarkan):</p>
                <ul className="list-disc pl-5">
                  {draftErrors.map((e) => <li key={e}>{e}</li>)}
                </ul>
              </div>
            )}
            <label className="flex flex-col gap-1">
              <span className="text-[14px] font-medium text-muted-ink">Tipe mesin</span>
              <input
                value={f.machine_type}
                onChange={(e) => set({ machine_type: e.target.value })}
                className="min-h-12 rounded-md border border-line bg-surface px-3 text-[16px]"
              />
            </label>
            <label className="flex flex-col gap-1">
              <span className="text-[14px] font-medium text-muted-ink">Mode gangguan</span>
              <input
                value={f.failure_mode}
                onChange={(e) => set({ failure_mode: e.target.value })}
                className="min-h-12 rounded-md border border-line bg-surface px-3 text-[16px]"
              />
            </label>
            <div className="grid grid-cols-1 gap-3 sm:grid-cols-3">
              <NumField label="SLA A / jam (Rp)" value={f.sla_class_a_penalty_per_hour_idr} onChange={(v) => set({ sla_class_a_penalty_per_hour_idr: v })} />
              <NumField label="SLA B / jam (Rp)" value={f.sla_class_b_penalty_per_hour_idr} onChange={(v) => set({ sla_class_b_penalty_per_hour_idr: v })} />
              <NumField label="SLA C / jam (Rp)" value={f.sla_class_c_penalty_per_hour_idr} onChange={(v) => set({ sla_class_c_penalty_per_hour_idr: v })} />
            </div>
            <div className="grid grid-cols-1 gap-3 sm:grid-cols-2">
              <NumField label="Lembur / jam (Rp)" value={f.overtime_cost_per_hour_idr} onChange={(v) => set({ overtime_cost_per_hour_idr: v })} />
              <NumField label="Changeover (Rp)" value={f.changeover_cost_idr} onChange={(v) => set({ changeover_cost_idr: v })} />
            </div>
            <fieldset className="rounded-md border border-line p-3">
              <legend className="px-1 text-[14px] font-semibold text-muted-ink">
                Ambang keselamatan (Tier 1 — tidak boleh dilonggarkan)
              </legend>
              <div className="grid grid-cols-1 gap-3 sm:grid-cols-3">
                <NumField label="Suhu motor (°C)" value={f.safety_thresholds.motor_temp_celsius ?? 95} step={1} min={0}
                  onChange={(v) => set({ safety_thresholds: { ...f.safety_thresholds, motor_temp_celsius: v } })} />
                <NumField label="Getaran (mm/s)" value={f.safety_thresholds.spindle_vibration_mm_per_s ?? 8} step={0.5} min={0}
                  onChange={(v) => set({ safety_thresholds: { ...f.safety_thresholds, spindle_vibration_mm_per_s: v } })} />
                <NumField label="Tekanan coolant (bar)" value={f.safety_thresholds.coolant_pressure_bar ?? 2} step={0.5} min={0}
                  onChange={(v) => set({ safety_thresholds: { ...f.safety_thresholds, coolant_pressure_bar: v } })} />
              </div>
            </fieldset>
            <Btn size="lg" onClick={runDraft} disabled={busy}>
              {busy && <Spinner className="size-5" />} Buat draft skill
            </Btn>
          </div>
        )}

        {skillStudioState === "draft" && skillStudioDraft && (
          <div className="flex flex-col gap-3">
            <p className="text-[15px]">
              Draft siap: {skillStudioDraft.hooks.length} hooks · {skillStudioDraft.interlocks.length} interlock.
              Periksa SKILL.md lalu jalankan lint Tier 1.
            </p>
            <pre className="max-h-72 overflow-y-auto rounded-md bg-page p-3 font-mono text-[12px] whitespace-pre-wrap">
              {skillStudioDraft.skill_md}
            </pre>
            <div className="flex flex-wrap gap-2">
              <Btn variant="secondary" onClick={() => setSkillStudioState("interview")} disabled={busy}>Kembali</Btn>
              <Btn size="lg" onClick={runLint} disabled={busy}>
                {busy && <Spinner className="size-5" />} Jalankan lint Tier 1
              </Btn>
            </div>
          </div>
        )}

        {skillStudioState === "lint" && skillStudioLintResult && (
          <div className="flex flex-col gap-3">
            <p className={cn(
              "rounded-md border px-3 py-2 text-[15px] font-semibold",
              skillStudioLintResult.valid ? "border-ok-line bg-ok-bg text-running" : "border-danger-line bg-danger-bg text-fault",
            )}>
              {skillStudioLintResult.valid ? "Lint lolos — Tier 1 aman." : "Lint gagal — Tier 1 dilanggar."}
            </p>
            {skillStudioLintResult.errors.map((e) => (
              <p key={e} className="rounded-md bg-danger-bg px-3 py-2 text-[14px] text-fault">{e}</p>
            ))}
            {skillStudioLintResult.warnings.map((w) => (
              <p key={w} className="rounded-md bg-warn-bg px-3 py-2 text-[14px] text-warn">{w}</p>
            ))}
            {skillStudioLintResult.valid && (
              <label className="flex flex-col gap-1">
                <span className="text-[14px] font-medium text-muted-ink">ID skill (folder di skills/)</span>
                <input
                  value={skillId}
                  onChange={(e) => setSkillId(e.target.value)}
                  className="min-h-12 rounded-md border border-line bg-surface px-3 font-mono text-[15px]"
                />
              </label>
            )}
            <div className="flex flex-wrap gap-2">
              <Btn variant="secondary" onClick={() => setSkillStudioState("draft")} disabled={busy}>Kembali</Btn>
              {!skillStudioLintResult.valid && (
                <Btn variant="secondary" onClick={() => setSkillStudioState("interview")} disabled={busy}>
                  Perbaiki di wawancara
                </Btn>
              )}
              {skillStudioLintResult.valid && (
                <Btn size="lg" onClick={runApprove} disabled={busy || !skillId.trim()}>
                  {busy && <Spinner className="size-5" />} Setujui &amp; aktifkan
                </Btn>
              )}
            </div>
          </div>
        )}

        {skillStudioState === "approved" && approveResult && (
          <div className="flex flex-col gap-3">
            <p className="rounded-md border border-ok-line bg-ok-bg px-3 py-2 text-[15px] font-semibold text-running">
              Skill {skillId} v{approveResult.version} aktif. Registry dimuat ulang, entri audit dicatat.
            </p>
            <Mono className="text-[14px]">ledger: {approveResult.ledger_entry_id.slice(0, 8)}</Mono>
            <p className="text-[14px] text-muted-ink">
              Lihat dokumentasi endpoint di <a className="underline" href={`${API_BASE}/docs`} target="_blank" rel="noreferrer">{API_BASE}/docs</a>.
            </p>
            <div className="flex flex-wrap gap-2">
              <Btn variant="secondary" onClick={resetAll}>Buat skill baru</Btn>
              <Btn onClick={close}>Tutup</Btn>
            </div>
          </div>
        )}
      </aside>
    </>
  );
}
