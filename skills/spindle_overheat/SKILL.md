# SKILL.md — Motor Spindel Overheat / Spindle Motor Overheat
<!-- skill_metadata
name: Motor spindel overheat
version: 1.0.0
domain: discrete_manufacturing
machine_type: CNC_milling_center
references:
  - skill.json
  - rules/safety_interlocks.json
  - rules/sla_penalties.json
  - hooks.json
language: bilingual (EN + ID)
-->

**ID:** Skill ini menangani motor spindel yang terlalu panas pada mesin CNC. Pemicu, pemeriksaan,
akar penyebab dan tugas mekanik ada di `skill.json`; setiap akar penyebab merujuk ke kasus
perawatan yang sudah selesai (CASE-0010, CASE-0018, CASE-0006).

**EN:** Handles spindle motor overheating on CNC machines. Trigger, checks, root causes and
mechanic tasks live in `skill.json`; every root cause cites a resolved maintenance case.

## Keselamatan / Safety
Aturan Tier 1 disalin utuh dari skill dasar `cnc_milling` dan tidak dilemahkan. Interlock
SAFE-001 (suhu motor > 95 °C) tetap menghentikan spindel. Aturan Tier 1 skill dasar juga selalu
dievaluasi, jadi skill ini hanya bisa menambah aturan, tidak mengurangi.

## Aksi / Actions
Menurunkan feed override ke 50% boleh berjalan sendiri (otonomi terbatas, hanya bila supervisor
menyalakannya dan semua pemeriksaan lolos; lihat `plc_adapter/machine_limits.json`).
Perubahan jadwal di SAP selalu menunggu persetujuan supervisor.
