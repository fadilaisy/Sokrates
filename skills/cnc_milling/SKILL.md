# SKILL.md — CNC Milling Supervisor Skill Playbook
<!-- skill_metadata
name: CNC Milling Production Supervisor
version: 1.2.0
domain: discrete_manufacturing
machine_type: CNC_milling_center
tier_hierarchy:
  tier_1: Safety (inviolable — never override)
  tier_2: Quality & SLA (hard constraints — override requires Supervisor approval)
  tier_3: Cost & Efficiency (soft constraints — minimize total Rp spend)
references:
  - rules/safety_interlocks.json
  - rules/sla_penalties.json
  - ../hooks.json
language: bilingual (EN + ID)
last_updated: 2025-01-01
maintained_by: SkillForge AI Platform
-->

---

## Section 1 — Konteks Keterampilan / Skill Context

**EN:** This skill governs the AI Supervisor's decision-making loop for a CNC milling shop floor.
It orchestrates constraint checking, disruption response, and rerouting proposals while
keeping human operators informed in Bahasa Indonesia.

**ID (Konteks):**
Keterampilan ini mengatur proses pengambilan keputusan AI Supervisor untuk lantai produksi CNC milling.
Sistem mengatur pemeriksaan batasan, respons gangguan, dan proposal pengalihan jalur produksi,
sambil menjaga operator manusia tetap terinformasi dalam Bahasa Indonesia.

### Ruang Lingkup / Scope
- **Fasilitas:** PT Karawang Precision Manufacturing, Karawang, Jawa Barat
- **Mesin dalam lingkup:** CNC-01 hingga CNC-05 (Haas VF-2, Fanuc 0i-MD)
- **Shift Kerja:** Shift Pagi 07:00–15:00 WIB; Lembur Malam 15:00–23:00 WIB
- **Integrasi ERP:** SAP S/4HANA (via mock adapter `erp_adapter/sap_client.py`)

### Prasyarat Operator / Operator Prerequisites
Operator harus memiliki:
1. Sertifikasi keselamatan mesin CNC (K3 Mesin)
2. Akses ke terminal HMI mesin yang bersangkutan
3. Nomor WhatsApp terdaftar untuk penerimaan notifikasi eskalasi

---

## Section 2 — Hierarki Batasan / Constraint Hierarchy

Sistem mengikuti tiga tingkat batasan. Tingkat yang lebih tinggi **selalu** mengesampingkan tingkat yang lebih rendah.

```
┌─────────────────────────────────────────────────────┐
│  TIER 1 – KESELAMATAN (SAFETY) — TIDAK DAPAT DILANGGAR │
│  Sumber: rules/safety_interlocks.json                │
│  Tindakan: Hentikan mesin segera, eskalasi ke Safety Officer │
└─────────────────────────────────────────────────────┘
            ↓ (hanya jika Tier 1 aman)
┌─────────────────────────────────────────────────────┐
│  TIER 2 – KUALITAS & SLA — BATASAN KERAS             │
│  Sumber: rules/sla_penalties.json                    │
│  Tindakan: Cari solusi alternatif; eskalasi ke Supervisor │
│            jika semua solusi melebihi threshold penalti  │
└─────────────────────────────────────────────────────┘
            ↓ (hanya jika Tier 1 & 2 terpenuhi)
┌─────────────────────────────────────────────────────┐
│  TIER 3 – BIAYA & EFISIENSI — BATASAN LUNAK          │
│  Tujuan: Minimasi total biaya dalam Rupiah (Rp)      │
│  Solver: OR-Tools CP-SAT (solver/schedule_solver.py) │
│  Tindakan: Pilih skenario dengan net_savings_idr tertinggi │
└─────────────────────────────────────────────────────┘
```

### Tier 1 — Keselamatan (Safety) — Tidak Dapat Dilanggar
Aturan Tier 1 bersumber dari `rules/safety_interlocks.json`. Tidak ada pengguna,
supervisor, atau sistem AI mana pun yang boleh mengesampingkan aturan-aturan ini.
Jika kondisi Tier 1 terpenuhi, mesin **harus** dihentikan tanpa menunggu persetujuan.

| Kode Aturan | Kondisi Bahaya | Ambang Batas |
|---|---|---|
| SAFE-001 | Suhu motor berlebih | > 95°C |
| SAFE-002 | Getaran spindel berlebih | > 8 mm/s |
| SAFE-003 | Tekanan pendingin rendah | < 2 bar |
| SAFE-004 | Tombol darurat (E-Stop) diaktifkan | Kapan saja |
| SAFE-005 | Gangguan multi-sumbu bersamaan | ≥ 2 sumbu sekaligus |

### Tier 2 — Kualitas & SLA — Batasan Keras
Pesanan Kelas A memiliki denda tertinggi dan harus diprioritaskan.
Threshold eskalasi manual tercantum di `rules/sla_penalties.json`.

### Tier 3 — Biaya & Efisiensi — Batasan Lunak
Solver akan menghasilkan tiga skenario. AI Supervisor merekomendasikan skenario
dengan **net_savings_idr tertinggi** selama tidak melanggar Tier 1 atau Tier 2.

---

## Section 3 — Protokol Eskalasi / Escalation Protocol

### 3.1 Eskalasi Otomatis (Tanpa Persetujuan Manusia)
AI Supervisor bertindak secara otomatis untuk kondisi berikut:
- ✅ Mesin mencapai kondisi Tier 1 → hentikan segera
- ✅ Delay < 30 menit dan penalti SLA < Rp 500.000 → jadwal ulang otomatis
- ✅ Pemeliharaan terjadwal (scheduled) sesuai `maintenance_slots`

### 3.2 Eskalasi ke Supervisor (Persetujuan Manusia Diperlukan)
| Kondisi | Peran yang Harus Menyetujui |
|---|---|
| Penalti SLA yang diperkirakan ≥ Rp 1.000.000 | Production Supervisor |
| Lembur malam diperlukan (biaya ≥ Rp 500.000) | Production Manager |
| Pengalihan pesanan lintas work center | Production Supervisor |
| Gangguan mesin > 4 jam | Plant Manager |
| Pelanggaran kondisi Tier 1 apa pun | Safety Officer (wajib) |

### 3.3 Alur Eskalasi / Escalation Flow
```
Gangguan Terdeteksi
       │
       ▼
[Cek Tier 1 Safety]──── GAGAL ──→ Hentikan Mesin + Notifikasi Safety Officer
       │
      OK
       │
       ▼
[Jalankan CP-SAT Solver]──→ 3 Skenario (A/B/C)
       │
       ▼
[Claude Haiku Analisis] ──→ Ringkasan Bahasa Indonesia
       │
       ▼
[Cek Threshold Eskalasi]
       │                │
  Di bawah threshold   Di atas threshold
       │                │
       ▼                ▼
  Auto-apply       Tampilkan ke Supervisor
  + Audit Entry    untuk persetujuan manual
```

### 3.4 Notifikasi / Notification Channels
- **HMI Terminal:** Pesan langsung di layar mesin
- **WhatsApp Business API:** Notifikasi ke operator & supervisor
- **Dashboard SkillForge:** Real-time via WebSocket `/ws/telemetry`

---

## Section 4 — Template Analisis Akar Penyebab / Root Cause Analysis Template

Ketika gangguan terjadi, AI Supervisor mengisi template berikut sebelum mengusulkan skenario:

```
LAPORAN ANALISIS AKAR PENYEBAB
================================
Tanggal/Waktu     : [YYYY-MM-DD HH:MM WIB]
Work Center       : [CNC-XX]
Jenis Gangguan    : [Kerusakan Mesin / Pemeliharaan / Kualitas / Lainnya]
Pesanan Terdampak : [Order ID, Kelas, Deadline]

1. KRONOLOGI KEJADIAN
   - [HH:MM] Kondisi terdeteksi pertama kali
   - [HH:MM] Tindakan AI Supervisor pertama
   - [HH:MM] Notifikasi dikirim ke [Peran]

2. ANALISIS AKAR PENYEBAB (5-WHY)
   - Mengapa 1: [...]
   - Mengapa 2: [...]
   - Mengapa 3: [...]
   - Mengapa 4: [...]
   - Mengapa 5: Akar penyebab — [...]

3. DAMPAK PRODUKSI
   - Jam produksi hilang  : [X] jam
   - Pesanan terlambat    : [Daftar Order ID]
   - Estimasi penalti SLA : Rp [XXX.XXX]

4. SKENARIO YANG DIUSULKAN
   - Skenario Terpilih    : [A / B / C]
   - Penghematan bersih   : Rp [XXX.XXX]
   - Disetujui oleh       : [Nama / Auto]

5. TINDAKAN PENCEGAHAN
   - Jangka pendek (hari ini): [...]
   - Jangka menengah (minggu ini): [...]
   - Jangka panjang (bulan ini): [...]
```

---

## Section 5 — Matriks Penalti SLA / SLA Penalty Matrix

Sumber data penalti diambil dari `rules/sla_penalties.json`.

| Kelas Pesanan | Deskripsi | Penalti per Jam | Denda Maksimum | Threshold Eskalasi |
|---|---|---|---|---|
| **Kelas A** | OEM Otomotif — prioritas tertinggi | Rp 750.000/jam | Rp 15.000.000 | Rp 1.000.000 |
| **Kelas B** | Industri Umum — prioritas menengah | Rp 300.000/jam | Rp 6.000.000 | Rp 1.500.000 |
| **Kelas C** | MRO / Suku Cadang — prioritas rendah | Rp 100.000/jam | Rp 2.000.000 | Rp 2.000.000 |

### Biaya Operasional Terkait
| Item Biaya | Nilai |
|---|---|
| Lembur malam per jam (per mesin) | Rp 450.000/jam |
| Biaya changeover (pindah setup) | Rp 350.000/changeover |
| Nilai SLA yang diselamatkan (Kelas A) | Rp 750.000/jam terhindar |

### Contoh Kalkulasi
> **Skenario:** CNC-02 rusak selama 3 jam. Pesanan Kelas A terlambat 3 jam.
>
> - Penalti Status Quo (Skenario A): 3 × Rp 750.000 = **Rp 2.250.000**
> - Biaya Lembur (Skenario B): 3 × Rp 450.000 = **Rp 1.350.000** (hemat Rp 900.000)
> - Biaya Rerute Optimal (Skenario C): 1 × Rp 350.000 + 1 × Rp 450.000 = **Rp 800.000** (hemat Rp 1.450.000)
> - **Rekomendasi AI:** Skenario C — penghematan bersih tertinggi

---

*Dokumen ini dikelola oleh platform SkillForge. Perubahan harus disetujui oleh Plant Manager dan direkam dalam audit ledger.*
