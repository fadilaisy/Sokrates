# SKILL.md — Tekanan Coolant Turun / Low Coolant Pressure
<!-- skill_metadata
name: Tekanan coolant turun
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

**ID:** Skill ini menangani tekanan coolant yang turun pada mesin CNC. Pemicu, pemeriksaan,
akar penyebab dan tugas mekanik ada di `skill.json`; setiap akar penyebab merujuk ke kasus
perawatan yang sudah selesai (CASE-0009, CASE-0012, CASE-0025).

**EN:** Handles falling coolant pressure on CNC machines. Trigger, checks, root causes and
mechanic tasks live in `skill.json`; every root cause cites a resolved maintenance case.

## Keselamatan / Safety
Aturan Tier 1 disalin utuh dari skill dasar `cnc_milling` dan tidak dilemahkan. Interlock
SAFE-003 (coolant < 2.0 bar) tetap menghentikan spindel. Aturan Tier 1 skill dasar juga selalu
dievaluasi, jadi skill ini hanya bisa menambah aturan, tidak mengurangi.

## Aksi / Actions
Feed hold ke PLC hanya setelah disetujui (lihat `plc_adapter/machine_limits.json`).
Perubahan jadwal di SAP selalu menunggu persetujuan supervisor.
