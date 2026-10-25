import { useEffect, useMemo, useState } from "react";
import { api, maintApi } from "../lib/api";
import type { MaintCase, SkillCompose, SkillLibrary, SkillSpec, SkillTestRun } from "../lib/api";
import { Btn, Mono, Spinner } from "../cockpit/ui";
import { cn } from "../lib/utils";

const errText = (e: unknown) => (e instanceof Error ? e.message : String(e));
const list = (s: string) => s.split(",").map((x) => x.trim()).filter(Boolean);

const HISTORY_LABEL: Record<string, string> = {
  SKILL_APPROVED: "Disetujui",
  SKILL_TEST_RUN: "Uji coba",
  SKILL_FIRED: "Menangani alarm",
};

function useLibrary() {
  const [lib, setLib] = useState<SkillLibrary | null>(null);
  const [error, setError] = useState<string | null>(null);
  useEffect(() => {
    api.skills().then(setLib).catch((e) => setError(errText(e)));
  }, []);
  return { lib, error };
}

function Pill({ tone, children }: { tone: "ok" | "warn" | "neutral" | "ai"; children: React.ReactNode }) {
  const t = {
    ok: "border-ok-line bg-ok-bg text-running",
    warn: "border-warn bg-warn-bg text-maint",
    neutral: "border-disabled bg-page text-idle",
    ai: "border-accent-blue bg-page text-accent-blue",
  }[tone];
  return <span className={cn("inline-block rounded-full border px-2.5 py-0.5 text-[13px] font-semibold", t)}>{children}</span>;
}

/** Library: every skill with its trigger, owner, version, status and ledger history. */
export function SkillLibraryView({ onTeach }: { onTeach: (id: string | null, clone?: boolean) => void }) {
  const { lib, error } = useLibrary();
  if (error) return <p role="alert" className="rounded-md border border-danger-line bg-danger-bg px-3 py-2 text-fault">{error}</p>;
  if (!lib) return <p className="flex items-center gap-2 text-muted-ink"><Spinner className="size-5" /> Memuat pustaka…</p>;
  return (
    <div className="flex flex-col gap-3">
      <div className="flex flex-wrap items-center justify-between gap-2">
        <p className="text-[15px] text-muted-ink">{lib.skills.length} skill. Skill dengan know-how dipilih otomatis per alarm; yang lain memakai skill dasar.</p>
        <Btn onClick={() => onTeach(null)}>+ Ajarkan skill baru</Btn>
      </div>
      {lib.skills.map((s) => (
        <article key={s.skill_id} className="flex flex-col gap-2 rounded-md border border-line p-4">
          <div className="flex flex-wrap items-start justify-between gap-2">
            <div className="flex flex-col gap-0.5">
              <h3 className="text-[18px] font-bold">{s.name}</h3>
              <Mono className="text-[13px] text-muted-ink">
                {s.skill_id}
                {s.version ? ` · v${s.version}` : ""}
                {s.owner ? ` · ${s.owner}` : ""}
              </Mono>
            </div>
            <div className="flex flex-wrap gap-1.5">
              {s.is_default && <Pill tone="neutral">Skill dasar</Pill>}
              {s.has_know_how ? <Pill tone={s.status === "active" ? "ok" : "neutral"}>{s.status === "active" ? "Aktif" : s.status}</Pill> : <Pill tone="neutral">Tanpa know-how</Pill>}
              {s.spec_errors.length > 0 && <Pill tone="warn">skill.json rusak</Pill>}
            </div>
          </div>
          {s.trigger ? (
            <p className="text-[14px]">
              <span className="text-muted-ink">Pemicu: </span>
              <Mono>{[...s.trigger.metrics, ...s.trigger.alarm_codes].join(", ") || "–"}</Mono>
              {" · "}
              {s.trigger.machines.length ? s.trigger.machines.join(", ") : "semua mesin"}
              {s.trigger.predictions ? " · juga dari prediksi tren" : ""}
            </p>
          ) : (
            <p className="text-[14px] text-muted-ink">
              {s.is_default ? "Dipakai bila tidak ada skill lain yang cocok. Aturan Tier 1-nya selalu berlaku." : "Belum punya skill.json."}
            </p>
          )}
          {s.has_know_how && (
            <p className="text-[14px] text-muted-ink">
              {s.root_cause_count} akar penyebab · menangani {s.fired_count} alarm · aksi: {s.allowed_actions.join(", ") || "–"}
            </p>
          )}
          {s.history.length > 0 && (
            <ul className="flex flex-col gap-0.5 border-t border-line pt-2 text-[13px] text-muted-ink">
              {s.history.slice(0, 3).map((h) => (
                <li key={h.id}>
                  <Mono>{h.action_type}</Mono> · {HISTORY_LABEL[h.action_type] ?? ""}
                  {h.action_type === "SKILL_TEST_RUN" ? ` ${h.scenario_id} ${h.passed ? "lulus" : "gagal"}` : ""}
                  {h.action_type === "SKILL_FIRED" ? ` ${h.machine_id} (${h.trigger})` : ""}
                  {h.action_type === "SKILL_APPROVED" && h.approved_by ? ` oleh ${h.approved_by}` : ""}
                  {h.sha256_hash ? <> · <Mono>{h.sha256_hash.slice(0, 8)}</Mono></> : null}
                </li>
              ))}
            </ul>
          )}
          <div className="flex flex-wrap gap-2">
            {s.has_know_how && <Btn variant="secondary" onClick={() => onTeach(s.skill_id)}>Ubah &amp; uji coba</Btn>}
            {s.has_know_how && <Btn variant="secondary" onClick={() => onTeach(s.skill_id, true)}>Salin jadi skill baru</Btn>}
          </div>
        </article>
      ))}
    </div>
  );
}

/** Tools: what the code can do, which skills use each one, and which PLC actions may run on their own. */
export function SkillToolsView() {
  const { lib, error } = useLibrary();
  if (error) return <p role="alert" className="rounded-md border border-danger-line bg-danger-bg px-3 py-2 text-fault">{error}</p>;
  if (!lib) return <p className="flex items-center gap-2 text-muted-ink"><Spinner className="size-5" /> Memuat tools…</p>;
  return (
    <div className="flex flex-col gap-2">
      <p className="text-[15px] text-muted-ink">
        Skill memilih dari tools ini. Aksi PLC dan batasnya diatur di file batas mesin; skill tidak bisa melebarkan batas.
      </p>
      <div className="overflow-x-auto rounded-md border border-line">
        <table className="w-full min-w-[560px] border-collapse text-[14px]">
          <thead>
            <tr className="text-left text-muted-ink">
              <th className="border-b border-line px-3 py-2 font-semibold">Tool</th>
              <th className="border-b border-line px-3 py-2 font-semibold">Jenis</th>
              <th className="border-b border-line px-3 py-2 font-semibold">Dipakai oleh</th>
            </tr>
          </thead>
          <tbody>
            {lib.tools.map((t) => (
              <tr key={t.id}>
                <td className="border-b border-line px-3 py-2">
                  <span className="font-semibold">{t.label}</span>
                  <div><Mono className="text-[12px] text-muted-ink">{t.id}</Mono></div>
                </td>
                <td className="border-b border-line px-3 py-2">
                  {t.kind === "plc_action" ? (t.autonomous ? <Pill tone="ok">PLC · otonom terbatas</Pill> : <Pill tone="warn">PLC · setelah disetujui</Pill>) : <Pill tone="neutral">Tool</Pill>}
                </td>
                <td className="border-b border-line px-3 py-2">{t.used_by.length ? `${t.used_by.length} skill: ${t.used_by.join(", ")}` : "–"}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  );
}

type Stage = "source" | "edit" | "test" | "done";
type Prov = SkillCompose["provenance"];

function ProvBadge({ prov, edited }: { prov?: Prov[string]; edited: boolean }) {
  if (edited) return <Pill tone="neutral">Diubah ahli</Pill>;
  if (!prov) return null;
  if (prov.by === "ai") return <Pill tone="ai">Disusun AI dari {prov.from.join(", ")}</Pill>;
  if (prov.by === "expert") return <Pill tone="neutral">Dari ahli</Pill>;
  return <Pill tone="neutral">Dari {prov.from.join(", ")}</Pill>;
}

const field = "min-h-12 rounded-md border border-line bg-surface px-3 text-[15px]";

/** Teach a skill: story + cases → AI draft (cited) → edit → test run on a PLC scenario → approve. */
export function SkillTeach({ edit, onDone }: { edit: { id: string; clone: boolean } | null; onDone: () => void }) {
  const { lib } = useLibrary();
  const [stage, setStage] = useState<Stage>(edit ? "edit" : "source");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [warnings, setWarnings] = useState<string[]>([]);

  const [story, setStory] = useState("");
  const [cases, setCases] = useState<MaintCase[]>([]);
  const [picked, setPicked] = useState<string[]>([]);
  const [caseFilter, setCaseFilter] = useState("");

  const [skillId, setSkillId] = useState("");
  const [spec, setSpec] = useState<SkillSpec | null>(null);
  const [prov, setProv] = useState<Prov>({});
  const [edited, setEdited] = useState<Set<string>>(new Set());
  const [baseFiles, setBaseFiles] = useState<{ sla_penalties: unknown; hooks: unknown[]; interlocks: unknown[] } | null>(null);

  const [scenario, setScenario] = useState("");
  const [test, setTest] = useState<SkillTestRun | null>(null);
  const [approver, setApprover] = useState("");
  const [approved, setApproved] = useState<{ ledger_entry_id: string } | null>(null);

  useEffect(() => {
    if (!edit) {
      maintApi.cases().then((cs) => setCases(cs.filter((c) => c.status === "RESOLVED"))).catch((e) => setError(errText(e)));
      return;
    }
    api.skill(edit.id).then((f) => {
      if (!f.spec) throw new Error(`${edit.id} belum punya skill.json`);
      setSpec(edit.clone ? { ...f.spec, name: `${f.spec.name} (salinan)` } : f.spec);
      setSkillId(edit.clone ? `${edit.id}_salinan` : edit.id);
      setBaseFiles({ sla_penalties: f.sla_penalties, hooks: f.hooks, interlocks: f.interlocks });
    }).catch((e) => setError(errText(e)));
  }, [edit]);

  // A scenario that fits the trigger, so the test run is one tap.
  useEffect(() => {
    if (!spec || !lib || scenario) return;
    const codes = new Set(spec.trigger.alarm_codes.map((c) => c.toUpperCase().replace(/[^A-Z0-9]/g, "")));
    const fit = lib.scenarios.find((s) => s.alarm_code && codes.has(s.alarm_code.toUpperCase().replace(/[^A-Z0-9]/g, "")));
    setScenario((fit ?? lib.scenarios[0])?.id ?? "");
  }, [spec, lib, scenario]);

  const shownCases = useMemo(() => {
    const q = caseFilter.trim().toLowerCase();
    return cases.filter((c) => !q || `${c.id} ${c.machine_id} ${c.symptom} ${c.error_codes.join(" ")} ${c.category}`.toLowerCase().includes(q)).slice(0, 40);
  }, [cases, caseFilter]);

  const update = (section: string, patch: Partial<SkillSpec>) => {
    if (!spec) return;
    setSpec({ ...spec, ...patch });
    setEdited((s) => new Set(s).add(section));
    setTest(null); // any edit needs a fresh test run before approval
  };

  async function compose() {
    setBusy(true);
    setError(null);
    try {
      const r = await api.composeSkill({ story, case_ids: picked });
      setSpec(r.spec);
      setSkillId(r.skill_id);
      setProv(r.provenance);
      setWarnings(r.warnings);
      setEdited(new Set());
      const base = await api.skill("cnc_milling");
      setBaseFiles({ sla_penalties: base.sla_penalties, hooks: base.hooks, interlocks: base.interlocks });
      setStage("edit");
    } catch (e) {
      setError(errText(e));
    } finally {
      setBusy(false);
    }
  }

  async function runTest() {
    if (!spec) return;
    setBusy(true);
    setError(null);
    try {
      setTest(await api.testRunSkill({ skill_id: skillId.trim(), scenario_id: scenario, spec }));
      setStage("test");
    } catch (e) {
      setError(errText(e));
    } finally {
      setBusy(false);
    }
  }

  async function approve() {
    if (!spec || !baseFiles || !test?.passed) return;
    setBusy(true);
    setError(null);
    try {
      const r = await api.approveSkillSpec({
        skill_id: skillId.trim(), spec, approved_by: approver.trim(), skill_version: spec.version ?? "1.0.0", ...baseFiles,
      });
      setApproved({ ledger_entry_id: r.ledger_entry_id });
      setStage("done");
    } catch (e) {
      setError(errText(e));
    } finally {
      setBusy(false);
    }
  }

  const stages: { id: Stage; label: string }[] = [
    { id: "source", label: "Cerita & kasus" },
    { id: "edit", label: "Periksa draf" },
    { id: "test", label: "Uji coba" },
    { id: "done", label: "Aktif" },
  ];
  const idx = stages.findIndex((s) => s.id === stage);

  return (
    <div className="flex flex-col gap-4">
      <ol className="grid grid-cols-2 gap-2 text-[14px] sm:grid-cols-4" aria-label="Langkah">
        {stages.map((s, i) => (
          <li
            key={s.id}
            aria-current={i === idx ? "step" : undefined}
            className={cn(
              "rounded-md px-2 py-1.5 text-center font-semibold",
              i < idx && "bg-ok-bg text-running",
              i === idx && "bg-navy text-white",
              i > idx && "bg-page text-muted-ink",
            )}
          >
            {i + 1}. {s.label}
          </li>
        ))}
      </ol>

      {error && <p role="alert" className="rounded-md border border-danger-line bg-danger-bg px-3 py-2 text-[15px] text-fault">{error}</p>}

      {stage === "source" && (
        <div className="grid gap-4 lg:grid-cols-2">
          <label className="flex flex-col gap-1">
            <span className="text-[14px] font-medium text-muted-ink">Cerita ahli: bagaimana Anda biasanya menangani masalah ini?</span>
            <textarea
              rows={8}
              value={story}
              onChange={(e) => setStory(e.target.value)}
              placeholder="Contoh: Kalau tekanan coolant turun, saya cek filter dulu. Kalau filternya bersih tapi pompa berisik, biasanya impeller."
              className="rounded-md border border-line bg-surface p-3 text-[15px]"
            />
          </label>
          <fieldset className="flex min-w-0 flex-col gap-2">
            <legend className="text-[14px] font-medium text-muted-ink">Kasus selesai yang dipakai (maks. 6) · {picked.length} dipilih</legend>
            <input
              aria-label="Cari kasus"
              value={caseFilter}
              onChange={(e) => setCaseFilter(e.target.value)}
              placeholder="Cari: coolant, ALM-410, CNC-02…"
              className={field}
            />
            <div className="flex max-h-72 flex-col gap-1 overflow-y-auto rounded-md border border-line p-2">
              {shownCases.map((c) => (
                <label key={c.id} className="flex min-h-11 shrink-0 cursor-pointer items-start gap-2 rounded px-1 py-1 text-[14px] hover:bg-page">
                  <input
                    type="checkbox"
                    className="mt-1 size-5 shrink-0"
                    checked={picked.includes(c.id)}
                    disabled={!picked.includes(c.id) && picked.length >= 6}
                    onChange={(e) => setPicked(e.target.checked ? [...picked, c.id] : picked.filter((x) => x !== c.id))}
                  />
                  <span>
                    <Mono>{c.id}</Mono> · {c.machine_id} {c.error_codes.length ? <Mono className="text-muted-ink">({c.error_codes.join(", ")})</Mono> : null}
                    <span className="block text-muted-ink">{c.symptom}</span>
                  </span>
                </label>
              ))}
              {shownCases.length === 0 && <p className="p-2 text-muted-ink">Tidak ada kasus yang cocok.</p>}
            </div>
          </fieldset>
          <div className="flex flex-wrap items-center gap-3 lg:col-span-2">
            <Btn size="lg" onClick={compose} disabled={busy || picked.length === 0}>
              {busy && <Spinner className="size-5" />} Susun draf dengan AI
            </Btn>
            <span className="text-[14px] text-muted-ink">Setiap akar penyebab wajib mengutip kasus yang Anda pilih; draf yang mengutip sumber lain ditolak.</span>
          </div>
        </div>
      )}

      {stage === "edit" && spec && (
        <div className="flex flex-col gap-4">
          {warnings.map((w) => <p key={w} className="rounded-md bg-warn-bg px-3 py-2 text-[14px] text-maint">{w}</p>)}
          <div className="grid gap-3 sm:grid-cols-2">
            <label className="flex flex-col gap-1">
              <span className="flex flex-wrap items-center gap-2 text-[14px] font-medium text-muted-ink">Nama skill <ProvBadge prov={prov.name} edited={edited.has("name")} /></span>
              <input value={spec.name} onChange={(e) => update("name", { name: e.target.value })} className={field} />
            </label>
            <label className="flex flex-col gap-1">
              <span className="text-[14px] font-medium text-muted-ink">ID skill (folder di skills/)</span>
              <input
                value={skillId}
                disabled={!!edit && !edit.clone}
                onChange={(e) => { setSkillId(e.target.value.toLowerCase().replace(/[^a-z0-9_]/g, "_")); setTest(null); }}
                className={cn(field, "font-mono")}
              />
            </label>
          </div>

          <Section title="1 · Pemicu" badge={<ProvBadge prov={prov.trigger} edited={edited.has("trigger")} />}>
            <div className="grid gap-3 sm:grid-cols-3">
              <label className="flex flex-col gap-1 text-[14px] text-muted-ink">Sinyal PLC
                <input value={spec.trigger.metrics.join(", ")} onChange={(e) => update("trigger", { trigger: { ...spec.trigger, metrics: list(e.target.value) } })} className={cn(field, "font-mono text-navy")} />
              </label>
              <label className="flex flex-col gap-1 text-[14px] text-muted-ink">Kode alarm
                <input value={spec.trigger.alarm_codes.join(", ")} onChange={(e) => update("trigger", { trigger: { ...spec.trigger, alarm_codes: list(e.target.value) } })} className={cn(field, "font-mono text-navy")} />
              </label>
              <label className="flex flex-col gap-1 text-[14px] text-muted-ink">Mesin (kosong = semua)
                <input value={spec.trigger.machines.join(", ")} onChange={(e) => update("trigger", { trigger: { ...spec.trigger, machines: list(e.target.value) } })} className={cn(field, "text-navy")} />
              </label>
            </div>
            <label className="flex min-h-11 items-center gap-2 text-[15px]">
              <input type="checkbox" className="size-5" checked={spec.trigger.predictions} onChange={(e) => update("trigger", { trigger: { ...spec.trigger, predictions: e.target.checked } })} />
              Juga berjalan dari prediksi tren, sebelum alarm
            </label>
          </Section>

          <Section title="2 · Pastikan masalah" badge={<ProvBadge prov={prov.confirm} edited={edited.has("confirm")} />}>
            {spec.confirm.map((c, i) => (
              <div key={i} className="flex flex-wrap gap-2">
                <select
                  aria-label="Jenis pemeriksaan"
                  value={c.kind}
                  onChange={(e) => update("confirm", { confirm: spec.confirm.map((x, j) => (j === i ? { ...x, kind: e.target.value as "auto" | "operator" } : x)) })}
                  className={field}
                >
                  <option value="auto">otomatis</option>
                  <option value="operator">operator</option>
                </select>
                <input
                  aria-label="Pemeriksaan"
                  value={c.text}
                  onChange={(e) => update("confirm", { confirm: spec.confirm.map((x, j) => (j === i ? { ...x, text: e.target.value } : x)) })}
                  className={cn(field, "min-w-0 flex-1")}
                />
                <Btn variant="secondary" aria-label="Hapus pemeriksaan" onClick={() => update("confirm", { confirm: spec.confirm.filter((_, j) => j !== i) })}>Hapus</Btn>
              </div>
            ))}
            <AddButton onClick={() => update("confirm", { confirm: [...spec.confirm, { kind: "operator", text: "" }] })}>+ Tambah pemeriksaan</AddButton>
          </Section>

          <Section title="3 · Akar penyebab" badge={<ProvBadge prov={prov.root_causes} edited={edited.has("root_causes")} />}>
            {spec.root_causes.map((c, i) => {
              const set = (patch: Partial<SkillSpec["root_causes"][number]>) =>
                update("root_causes", { root_causes: spec.root_causes.map((x, j) => (j === i ? { ...x, ...patch } : x)) });
              return (
                <div key={i} className="grid gap-2 rounded-md border border-line p-3 sm:grid-cols-[minmax(0,2fr)_minmax(0,2fr)_minmax(0,1fr)_auto]">
                  <input aria-label="Penyebab" value={c.cause} onChange={(e) => set({ cause: e.target.value })} className={field} />
                  <input aria-label="Bukti" placeholder="Bukti" value={c.evidence ?? ""} onChange={(e) => set({ evidence: e.target.value })} className={field} />
                  <input aria-label="Sumber" placeholder="CASE-…" value={c.sources.join(", ")} onChange={(e) => set({ sources: list(e.target.value) })} className={cn(field, "font-mono")} />
                  <Btn variant="secondary" onClick={() => update("root_causes", { root_causes: spec.root_causes.filter((_, j) => j !== i) })}>Hapus</Btn>
                </div>
              );
            })}
            <AddButton onClick={() => update("root_causes", { root_causes: [...spec.root_causes, { cause: "", evidence: "", sources: [] }] })}>+ Tambah penyebab</AddButton>
          </Section>

          <Section title="4 · Tugas mekanik" badge={<ProvBadge prov={prov.tasks} edited={edited.has("tasks")} />}>
            <textarea
              aria-label="Tugas mekanik, satu per baris"
              rows={Math.max(4, spec.tasks.length + 1)}
              value={spec.tasks.join("\n")}
              onChange={(e) => update("tasks", { tasks: e.target.value.split("\n").map((t) => t.trim()).filter(Boolean) })}
              className="rounded-md border border-line bg-surface p-3 text-[15px]"
            />
            <span className="text-[13px] text-muted-ink">Satu langkah per baris, dalam Bahasa Indonesia.</span>
          </Section>

          <Section title="5 · Aksi yang diizinkan" badge={<ProvBadge prov={prov.allowed_actions} edited={edited.has("allowed_actions")} />}>
            {(lib?.tools ?? []).map((t) => (
              <label key={t.id} className="flex min-h-11 items-center gap-2 text-[15px]">
                <input
                  type="checkbox"
                  className="size-5"
                  checked={spec.allowed_actions.includes(t.id)}
                  onChange={(e) => update("allowed_actions", {
                    allowed_actions: e.target.checked ? [...spec.allowed_actions, t.id] : spec.allowed_actions.filter((a) => a !== t.id),
                  })}
                />
                <span>{t.label}</span>
                {t.kind === "plc_action" && (t.autonomous ? <Pill tone="ok">otonom terbatas</Pill> : <Pill tone="warn">setelah disetujui</Pill>)}
              </label>
            ))}
            <p className="rounded-md bg-page px-3 py-2 text-[14px]">
              Perubahan ke SAP selalu menunggu persetujuan supervisor. Aksi PLC tetap di dalam batas mesin, dan aturan Tier 1 skill dasar selalu berlaku.
            </p>
          </Section>

          <div className="flex flex-wrap items-end gap-3">
            <label className="flex flex-col gap-1 text-[14px] text-muted-ink">Skenario PLC untuk uji coba
              <select value={scenario} onChange={(e) => setScenario(e.target.value)} className={cn(field, "text-navy")}>
                {(lib?.scenarios ?? []).map((s) => <option key={s.id} value={s.id}>{s.label}</option>)}
              </select>
            </label>
            {!edit && <Btn variant="secondary" onClick={() => setStage("source")} disabled={busy}>Kembali</Btn>}
            <Btn size="lg" onClick={runTest} disabled={busy || !scenario || !skillId.trim()}>
              {busy && <Spinner className="size-5" />} Uji coba →
            </Btn>
          </div>
        </div>
      )}

      {stage === "test" && test && spec && (
        <div className="flex flex-col gap-4">
          <p className={cn("rounded-md border px-3 py-2 text-[15px] font-semibold", test.passed ? "border-ok-line bg-ok-bg text-running" : "border-danger-line bg-danger-bg text-fault")}>
            {test.passed ? "Uji coba lulus. Tidak ada yang ditulis ke PLC atau SAP." : "Uji coba gagal. Perbaiki skill lalu uji lagi."}
          </p>
          <ol className="flex flex-col">
            {test.steps.map((s, i) => (
              <li key={s.id} className="flex gap-3 border-b border-line py-3 last:border-b-0">
                <span className={cn("grid size-8 shrink-0 place-items-center rounded-full text-[14px] font-semibold", s.ok ? "bg-navy text-white" : "bg-danger-bg text-fault")}>
                  {s.ok ? i + 1 : "!"}
                </span>
                <div className="flex min-w-0 flex-col gap-0.5">
                  <span className="font-semibold">{s.title}</span>
                  <span className="text-[14px] text-muted-ink">{s.detail}</span>
                </div>
              </li>
            ))}
          </ol>
          <ul className="grid gap-2 sm:grid-cols-2">
            {test.checks.map((c) => (
              <li key={c.id} className={cn("flex gap-2 text-[14px]", !c.ok && "font-semibold text-fault")}>
                <span aria-hidden>{c.ok ? "✓" : "✗"}</span>
                <span className="sr-only">{c.ok ? "lulus:" : "gagal:"}</span>
                {c.label}
              </li>
            ))}
          </ul>
          <div className="flex flex-wrap items-end gap-3">
            <label className="flex flex-col gap-1 text-[14px] text-muted-ink">Disetujui oleh
              <input value={approver} onChange={(e) => setApprover(e.target.value)} placeholder="Nama dan jabatan" className={cn(field, "text-navy")} />
            </label>
            <Btn variant="secondary" onClick={() => setStage("edit")} disabled={busy}>Kembali edit</Btn>
            <Btn size="lg" onClick={approve} disabled={busy || !test.passed || !approver.trim()}>
              {busy && <Spinner className="size-5" />} Setujui &amp; aktifkan
            </Btn>
          </div>
          <Mono className="text-[13px] text-muted-ink">uji coba dicatat di ledger · {test.ledger_entry_id.slice(0, 8)}</Mono>
        </div>
      )}

      {stage === "done" && approved && spec && (
        <div className="flex flex-col gap-3">
          <p className="rounded-md border border-ok-line bg-ok-bg px-3 py-2 text-[15px] font-semibold text-running">
            Skill "{spec.name}" aktif. Agent memakainya mulai alarm berikutnya, tanpa restart.
          </p>
          <Mono className="text-[14px]">ledger: {approved.ledger_entry_id.slice(0, 8)}</Mono>
          <div className="flex flex-wrap gap-2">
            <Btn onClick={onDone}>Lihat pustaka</Btn>
          </div>
        </div>
      )}
    </div>
  );
}

function Section({ title, badge, children }: { title: string; badge?: React.ReactNode; children: React.ReactNode }) {
  return (
    <section className="flex flex-col gap-2 rounded-md border border-line p-4">
      <h3 className="flex flex-wrap items-center gap-2 text-[17px] font-bold">{title} {badge}</h3>
      {children}
    </section>
  );
}

function AddButton({ onClick, children }: { onClick: () => void; children: React.ReactNode }) {
  return (
    <button type="button" onClick={onClick} className="min-h-11 self-start rounded-md border border-dashed border-accent-blue px-3 text-[14px] text-accent-blue hover:bg-page">
      {children}
    </button>
  );
}
