import { useState } from "react";
import { api } from "../lib/api";

const PRESETS = [
  {
    title: "Robot Las Chassis Otomotif",
    desc: "Robot pengelasan chassis otomotif (welding robot) untuk lini perakitan PT Astra Honda Motor di Cikarang dengan pemantauan suhu elektroda dan integritas arus las.",
  },
  {
    title: "Stamping Press High-Tonnage",
    desc: "Mesin stamping press mekanis 1000 ton untuk pembentukan bodi mobil di PT Toyota Motor Karawang, mencakup batasan beban tonase hidrolik dan interlock optik tirai cahaya keselamatan.",
  },
  {
    title: "CNC 5-Axis Komponen Dirgantara",
    desc: "Pusat permesinan 5-sumbu untuk paduan titanium dan turbin mesin pesawat di PT Dirgantara Indonesia Bandung, dengan toleransi mikro-meter dan pemantauan aus pahat cryogenic.",
  },
  {
    title: "High-Speed Bottling FMCG",
    desc: "Lini pengisian dan pengemasan botol minuman berkecepatan tinggi di Cikarang, fokus pada sinkronisasi konveyor, deteksi sumbatan tutup botol, dan denda keterlambatan ritel modern.",
  },
];

const ACTIVE_PLAYBOOK_SNIPPET = `# SKILL.md — CNC Milling Supervisor Playbook
Metadata:
  name: CNC Milling Production Supervisor
  facility: PT Karawang Precision Manufacturing
  domain: discrete_manufacturing
  tier_hierarchy:
    Tier 1: Safety (Inviolable — E-Stop, Spindle Vibration > 8mm/s, Temp > 95°C)
    Tier 2: Quality & SLA (Hard — Toyota, Honda, Pertamina OEM Penalti)
    Tier 3: Cost & Efficiency (Soft — Diminimalkan oleh OR-Tools Solver)

[Tier 1 Safety Interlocks]
- SAFE-001: Motor temp > 95°C → Emergency stop seketika
- SAFE-002: Spindle vibration > 8 mm/s → Emergency stop seketika
- SAFE-003: Coolant pressure < 2 bar → Hentikan siklus pemotongan
- SAFE-004: E-Stop ditekan → Penghentian penuh mesin
- SAFE-005: ≥2 gangguan sumbu serentak → E-Stop + Hubungi OEM

[Tier 2 SLA Penalty Hierarchy]
- Kelas A (OEM Otomotif - Toyota/Honda): Denda Rp 750.000 / jam keterlambatan (Maks Rp 15 jt)
- Kelas B (Industri Umum - Pertamina/Astra): Denda Rp 300.000 / jam (Maks Rp 6 jt)
- Kelas C (Internal MRO / Spare Parts): Denda Rp 100.000 / jam (Maks Rp 2 jt)

[Protokol Otorisasi Supervisor]
- Denda perkiraan < Rp 1.000.000: Auto-resolusi dapat diizinkan
- Denda perkiraan ≥ Rp 1.000.000: Wajib persetujuan 1-klik Supervisor Lantai
- Denda perkiraan ≥ Rp 5.000.000: Wajib eskalasi Plant Manager`;

export default function SkillStudio() {
  const [activeTab, setActiveTab] = useState<"generator" | "active">("generator");
  const [prompt, setPrompt] = useState(PRESETS[0].desc);
  const [loading, setLoading] = useState(false);
  const [generatedMd, setGeneratedMd] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [copied, setCopied] = useState(false);

  async function handleGenerate() {
    if (!prompt.trim()) return;
    setLoading(true);
    setError(null);
    try {
      const res = await api.generateSkill(prompt);
      setGeneratedMd(res.skill_md);
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setLoading(false);
    }
  }

  function handleCopy(text: string) {
    navigator.clipboard.writeText(text);
    setCopied(true);
    setTimeout(() => setCopied(false), 2000);
  }

  return (
    <div className="overflow-hidden rounded-2xl border border-[#353A50] bg-[#2A2A40] shadow-xl shadow-black/30">
      {/* Header bar */}
      <div className="flex flex-wrap items-center justify-between border-b border-[#353A50] bg-[#22223B]/50 px-6 py-4">
        <div>
          <div className="flex items-center gap-2">
            <span className="text-base font-semibold text-[#F9F7F7]">Skill Studio</span>
            <span className="rounded bg-[#F2A900]/20 px-2 py-0.5 text-[10px] font-bold text-[#F2A900]">
              DECLARATIVE AGENT SOP
            </span>
          </div>
          <p className="mt-1 text-xs text-[#9A8C98]">
            Playbook berbasis deklaratif (`SKILL.md`) dengan hierarki Tier 1 (Keselamatan), Tier 2 (SLA Denda IDR), dan Tier 3 (Biaya).
          </p>
        </div>

        <div className="mt-2 flex rounded-lg bg-[#22223B] p-1 sm:mt-0">
          <button
            onClick={() => setActiveTab("generator")}
            className={`rounded-md px-3 py-1.5 text-xs font-semibold transition-all ${
              activeTab === "generator"
                ? "bg-[#F2A900] text-[#22223B] shadow"
                : "text-[#9A8C98] hover:text-[#F9F7F7]"
            }`}
          >
            Hasilkan Playbook Baru
          </button>
          <button
            onClick={() => setActiveTab("active")}
            className={`rounded-md px-3 py-1.5 text-xs font-semibold transition-all ${
              activeTab === "active"
                ? "bg-[#F2A900] text-[#22223B] shadow"
                : "text-[#9A8C98] hover:text-[#F9F7F7]"
            }`}
          >
            Playbook Aktif (CNC Milling)
          </button>
        </div>
      </div>

      <div className="p-6">
        {activeTab === "generator" ? (
          <div className="space-y-5">
            {/* Presets */}
            <div>
              <p className="mb-2 text-xs font-medium text-[#9A8C98]">Pilih Templat Cepat Pabrik / Kasus:</p>
              <div className="flex flex-wrap gap-2">
                {PRESETS.map((p, idx) => (
                  <button
                    key={idx}
                    type="button"
                    onClick={() => setPrompt(p.desc)}
                    className="rounded-lg border border-[#353A50] bg-[#22223B] px-3 py-1.5 text-xs text-[#F9F7F7] transition-all hover:border-[#F2A900] hover:text-[#F2A900] active:scale-95"
                  >
                    {p.title}
                  </button>
                ))}
              </div>
            </div>

            {/* Input prompt */}
            <div>
              <label className="block text-xs font-medium text-[#9A8C98]">
                Deskripsi Kebutuhan AI Supervisor (Natural Language):
              </label>
              <textarea
                rows={3}
                value={prompt}
                onChange={(e) => setPrompt(e.target.value)}
                placeholder="Jelaskan jenis mesin, lokasi pabrik, dan fokus keselamatan/SLA..."
                className="mt-2 w-full rounded-xl border border-[#353A50] bg-[#22223B] p-3 text-xs text-[#F9F7F7] placeholder-[#9A8C98]/50 transition-all focus:border-[#F2A900] focus:outline-none focus:ring-1 focus:ring-[#F2A900]"
              />
            </div>

            {/* Action button */}
            <div className="flex items-center justify-between">
              <span className="text-[11px] text-[#9A8C98]">
                Model AI: <span className="font-mono text-[#F9F7F7]">Gemini 3.5 Flash</span> · Output: SKILL.md
              </span>
              <button
                onClick={handleGenerate}
                disabled={loading || !prompt.trim()}
                className="flex items-center gap-2 rounded-lg bg-[#F2A900] px-5 py-2.5 text-xs font-bold text-[#22223B] shadow-lg shadow-[#F2A900]/20 transition-all hover:bg-[#E29B00] active:scale-95 disabled:cursor-not-allowed disabled:opacity-50"
              >
                {loading ? (
                  <>
                    <span className="h-3.5 w-3.5 animate-spin rounded-full border-2 border-[#22223B] border-t-transparent" />
                    <span>Gemini Merancang SOP…</span>
                  </>
                ) : (
                  <>
                    <span>⚡ Hasilkan SKILL.md Playbook</span>
                  </>
                )}
              </button>
            </div>

            {error && (
              <div className="rounded-xl border border-[#E76F51]/30 bg-[#E76F51]/10 p-3 text-xs text-[#E76F51]">
                <p className="font-semibold">Gagal merancang playbook: {error}</p>
                <p className="mt-1 text-[11px] text-[#E76F51]/80">
                  Pastikan backend aktif dan GEMINI_API_KEY terkonfigurasi.
                </p>
              </div>
            )}

            {/* Output display */}
            {generatedMd && (
              <div className="mt-4 rounded-xl border border-[#F2A900]/30 bg-[#22223B] p-4">
                <div className="mb-3 flex items-center justify-between border-b border-[#353A50] pb-2">
                  <div className="flex items-center gap-2">
                    <span className="h-2 w-2 rounded-full bg-[#CBF3F0]" />
                    <span className="text-xs font-semibold text-[#CBF3F0]">
                      SKILL.md Berhasil Dibuat Secara Deklaratif
                    </span>
                  </div>
                  <button
                    onClick={() => handleCopy(generatedMd)}
                    className="rounded bg-[#353A50] px-3 py-1 text-[11px] font-medium text-[#F9F7F7] hover:bg-[#4A4E69]"
                  >
                    {copied ? "Tersalin!" : "Salin Markdown"}
                  </button>
                </div>
                <pre className="max-h-96 overflow-y-auto whitespace-pre-wrap font-mono text-[11px] leading-relaxed text-[#F9F7F7]">
                  {generatedMd}
                </pre>
              </div>
            )}
          </div>
        ) : (
          /* Active CNC Milling Playbook */
          <div className="space-y-4">
            <div className="flex items-center justify-between border-b border-[#353A50] pb-2">
              <span className="text-xs font-semibold text-[#CBF3F0]">
                Playbook Aktif: skills/cnc_milling/SKILL.md
              </span>
              <button
                onClick={() => handleCopy(ACTIVE_PLAYBOOK_SNIPPET)}
                className="rounded bg-[#353A50] px-3 py-1 text-[11px] font-medium text-[#F9F7F7] hover:bg-[#4A4E69]"
              >
                {copied ? "Tersalin!" : "Salin"}
              </button>
            </div>
            <pre className="max-h-96 overflow-y-auto whitespace-pre-wrap font-mono text-[11px] leading-relaxed text-[#F9F7F7]">
              {ACTIVE_PLAYBOOK_SNIPPET}
            </pre>
          </div>
        )}
      </div>
    </div>
  );
}
