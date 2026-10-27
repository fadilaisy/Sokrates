import { formatIDR } from "../lib/api";
import type { LedgerEntry } from "../lib/api";
import { timeRange } from "../lib/shift";

/** Scenario names as the solver labels them, so a ledger line can say which plan was chosen. */
const SCENARIO_NAME: Record<string, string> = {
  scenario_a: "A · Status Quo — Terima Keterlambatan",
  scenario_b: "B · Lembur — Shift Malam",
  scenario_c: "C · Rerute Optimal",
};

const DISRUPTION_LABEL: Record<string, string> = {
  BREAKDOWN: "rusak",
  MAINTENANCE: "perawatan terjadwal",
  OVERHEAT: "overheat",
  VIBRATION: "vibrasi berlebih",
  FAULT: "fault",
};

export const scenarioName = (id: unknown) => SCENARIO_NAME[String(id ?? "").split(":").pop() ?? ""] ?? String(id ?? "");

const person = (v: unknown) => String(v ?? "").replace("Production Supervisor — ", "") || "–";
const sap = (e: LedgerEntry) =>
  e.sap_version_before != null && e.sap_version_after != null ? `SAP v${e.sap_version_before} → v${e.sap_version_after}` : "";

type OrderUpdate = { id?: string; work_center_id?: string; planned_start?: string; planned_end?: string };

function movedOrders(delta: unknown): string[] {
  const updates = ((delta as { order_updates?: OrderUpdate[] } | undefined)?.order_updates ?? []).slice(0, 4);
  return updates.map((o) => `${o.id} → ${o.work_center_id ?? "?"} ${timeRange(o.planned_start, o.planned_end)}`);
}

/** One short headline after the action label, e.g. the scenario or machine it is about. */
export function ledgerSubject(e: LedgerEntry): string {
  if (e.scenario_chosen) return scenarioName(e.scenario_chosen);
  if (e.machine_id) return String(e.machine_id);
  if (e.skill_id) return String(e.skill_id);
  return "";
}

const UPDATE_LABEL: Record<string, string> = { approved: "rencana disetujui", refused: "rencana ditolak", reverted: "persetujuan dibatalkan" };

/** What the entry records, in plain words: shown when the operator hovers or taps an entry. */
export function ledgerDetail(e: LedgerEntry): string[] {
  const m = String(e.machine_id ?? "");
  switch (e.action_type) {
    case "SKILLS_LOADED":
      return [`Skill yang dipakai solver saat backend mulai: ${((e.skill_ids as string[]) ?? []).join(", ") || "–"}.`];
    case "DISRUPTION_DETECTED":
      return [
        `${m} ${DISRUPTION_LABEL[String(e.disruption_type)] ?? String(e.disruption_type ?? "").toLowerCase()} selama ${e.disruption_hours ?? "?"} jam.`,
        `SkillForge membaca jadwal dari SAP v${e.sap_version ?? "?"} dan mulai menghitung skenario pemulihan.`,
      ];
    case "SCENARIOS_GENERATED": {
      const costs = (e.total_costs_idr as Record<string, number>) ?? {};
      return [
        `${((e.scenario_ids as string[]) ?? []).length} skenario pemulihan untuk ${m}:`,
        ...((e.scenario_ids as string[]) ?? []).map((id) => `• ${scenarioName(id)}${costs[id] != null ? ` · ${formatIDR(costs[id])}` : ""}`),
        e.recommended_scenario ? `Rekomendasi: ${scenarioName(e.recommended_scenario)}. Belum ada yang ditulis ke SAP.` : "Belum ada yang ditulis ke SAP.",
      ];
    }
    case "SCENARIO_APPROVED": {
      const moved = movedOrders(e.delta_applied);
      const src = e.source as { kind?: string; run_id?: string; alarm_code?: string; machine_id?: string } | undefined;
      return [
        `${person(e.approved_by)} menyetujui skenario ${scenarioName(e.scenario_chosen)}; jadwal ditulis ke SAP (${sap(e)}).`,
        ...(src?.kind === "agent_run"
          ? [`Diusulkan oleh agen ${src.run_id ?? ""} setelah ${src.alarm_code ?? "alarm manual"} di ${src.machine_id ?? "?"}.`]
          : src?.kind === "dashboard"
            ? ["Dari analisis gangguan di dasbor."]
            : []),
        moved.length ? `Order yang dijadwal ulang: ${moved.join("; ")}.` : "Tidak ada order yang dipindah.",
      ];
    }
    case "STATE_RESET":
      return [`Data SAP dimuat ulang ke rencana awal (SAP v${e.sap_version_after ?? "?"}). Riwayat order di chat dihitung mulai dari sini.`];
    case "APPROVAL_REJECTED_DRIFT":
      return [
        `Persetujuan skenario ${scenarioName(e.scenario_chosen)} oleh ${person(e.approved_by)} ditolak: data SAP berubah (v${e.sap_version_before} → v${e.sap_version_after}) sejak skenario dihitung.`,
        "Tidak ada yang ditulis. Skenario perlu dihitung ulang dari data terbaru.",
      ];
    case "EXTERNAL_CHANGE":
      return [
        `Status ${m} diubah di luar SkillForge: ${e.from_status ?? "?"} → ${e.to_status ?? "?"}${e.disruption_type ? ` (${String(e.disruption_type).toLowerCase()})` : ""}.`,
        `Versi SAP naik (${sap(e)}), jadi skenario lama tidak bisa disetujui lagi.`,
      ];
    case "SCENARIO_REVERTED":
      return [
        `${person(e.approved_by)} mengembalikan persetujuan ${String(e.original_entry_id ?? "").slice(0, 8)}; order kembali ke jadwal sebelumnya (${sap(e)}).`,
      ];
    case "REVERT_REJECTED_DRIFT":
      return [`Pengembalian oleh ${person(e.approved_by)} ditolak karena data SAP sudah berubah (v${e.sap_version_before} → v${e.sap_version_after}). Tidak ada yang ditulis.`];
    case "SKILL_APPROVED":
      return [`${person(e.approved_by)} menyetujui skill ${e.skill_id} versi ${e.skill_version}: ${e.hooks_count ?? 0} hook, ${e.interlocks_count ?? 0} interlock.`];
    case "TELEGRAM_SENT":
      if (e.update)
        return [`Kabar "${UPDATE_LABEL[String(e.update)] ?? e.update}" dikirim via Telegram ke ${e.mechanic_name ?? "mekanik"}${m ? ` (${m})` : ""}.`];
      return [`Pesan perbaikan ${m ? `untuk ${m} ` : ""}dikirim via Telegram ke ${e.mechanic_name ?? "mekanik"} (${e.to ?? "?"}).`];
    case "TELEGRAM_FAILED":
      return [`Pesan Telegram ke ${e.mechanic_name ?? "mekanik"} (${e.to ?? "?"}) gagal: ${e.error ?? "tanpa alasan"}.`, "Playbook tetap ada di halaman Perawatan."];
    case "PLC_AUTONOMY_ENABLED":
    case "PLC_AUTONOMY_DISABLED": {
      const acts = (e.autonomous_actions as Record<string, { action_id: string }[]> | undefined) ?? {};
      const on = e.action_type === "PLC_AUTONOMY_ENABLED";
      const ids = [...new Set(Object.values(acts).flat().map((a) => a.action_id))];
      return [
        on
          ? `${person(e.approved_by)} mengizinkan SkillForge menjalankan sendiri: ${ids.join(", ") || "–"} di ${Object.keys(acts).join(", ") || "–"}.`
          : `${person(e.approved_by)} mematikan autonomi; setiap aksi PLC kembali butuh persetujuan.`,
      ];
    }
  }
  if (e.action_type.startsWith("PLC_WRITE")) {
    const checks = (e.checks as { ok: boolean; label: string }[] | undefined) ?? [];
    const failed = checks.filter((c) => !c.ok).map((c) => c.label.toLowerCase());
    const who = e.autonomous ? "Autonomi terbatas" : person(e.approved_by);
    const lines = [`${who}: ${m} ${e.tag} ${e.before ?? "?"} → ${e.after ?? e.value}${e.reason ? ` · ${e.reason}` : ""}.`];
    if (failed.length) lines.push(`Diblokir oleh pemeriksaan: ${failed.join(", ")}.`);
    else if (checks.length) lines.push(`${checks.length} pemeriksaan keselamatan lolos.`);
    if (e.error) lines.push(String(e.error));
    return lines;
  }
  return [];
}
