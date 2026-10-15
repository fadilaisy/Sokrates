#!/usr/bin/env python3
"""
scripts/generate_maintenance_seed.py
────────────────────────────────────
Write knowledge/maintenance_seed.json — the synthetic maintenance-case history
for PT Karawang Precision Manufacturing.

Entries are hand-authored (English, Bahasa Indonesia, and mixed shop-floor
Indonglish). Entries whose `source` is a scan are degraded with a deterministic
OCR-noise model (O/0, l/1, rn/m, S/5 confusions; occasionally a corrupted code in
the structured fields too), so retrieval has to cope with real scan artefacts.

    python3 scripts/generate_maintenance_seed.py
"""

from __future__ import annotations

import json
import random
import re
from pathlib import Path

OUT = Path(__file__).resolve().parent.parent / "knowledge" / "maintenance_seed.json"

MODELS = {
    "CNC-01": ("Haas VF-2", "Haas NGC"),
    "CNC-02": ("Haas VF-2", "Haas NGC"),
    "CNC-03": ("Fanuc Robodrill α-D21MiA", "Fanuc 0i-MD"),   # names match rag.metadata's machine catalog
    "CNC-04": ("Mazak Variaxis i-500", "Mazatrol SmoothX"),
    "CNC-05": ("DMG MORI NLX 2500", "CELOS + Fanuc"),
}

# (machine, date, category, language, source, ocr_conf, mechanic, downtime_h, outcome,
#  symptom, error_codes, part_codes, root_cause, fix_steps, safety_notes, text)
C = []


def case(machine, date, category, lang, source, conf, mech, hours, outcome,
         symptom, ecodes, pcodes, cause, steps, safety, text):
    C.append(dict(machine=machine, date=date, category=category, lang=lang, source=source,
                  conf=conf, mech=mech, hours=hours, outcome=outcome, symptom=symptom,
                  ecodes=ecodes, pcodes=pcodes, cause=cause, steps=steps, safety=safety, text=text))


case("CNC-01", "2023-08-14T09:20:00+07:00", "SPINDLE", "en", "typed", None, "MEK-001", 6.5, "RESOLVED",
     "Spindle vibration 6.8 mm/s with grinding noise above 4000 rpm",
     ["ALM-162"], ["NSK-7014CTYNSULP4", "SEAL-SP-0031", "93-1000"],
     "Front spindle bearing pre-load lost; raceway wear after coolant ingress through worn labyrinth seal.",
     ["Stop spindle and lock out machine", "Record vibration at 2000/3000/4000 rpm with handheld analyser",
      "Remove spindle cartridge and inspect front bearing set", "Replace bearing pair NSK-7014CTYNSULP4 and labyrinth seal SEAL-SP-0031",
      "Re-grease with spindle grease 93-1000 (1.2 g per bearing) and run 30 min warm-up ramp", "Confirm vibration below 2.5 mm/s at 6000 rpm"],
     "Lock out / tag out before opening spindle housing. Do not exceed 4000 rpm until warm-up completed.",
     "Op reported grinding noise from spindle on VF-2 No.1 during roughing of aluminium housings. Vib 6.8 mm/s at 4200 rpm, alarm ALM-162 spindle overload. Front bearing replaced, seal replaced, vibration back to 2.1 mm/s.")

case("CNC-02", "2024-01-22T13:05:00+07:00", "SPINDLE", "id", "handwritten_scan", 0.78, "MEK-001", 5.0, "RESOLVED",
     "Spindel bunyi kasar dan getaran naik setelah jam kerja ke-6",
     ["ALM-162"], ["NSK-7014CTYNSULP4"],
     "Bantalan depan spindel aus, grease kering karena interval pelumasan terlewat.",
     ["Hentikan mesin, kunci power (LOTO)", "Ukur getaran spindel pada 2000 dan 4000 rpm", "Bongkar cartridge spindel, periksa bantalan depan",
      "Ganti set bantalan NSK-7014CTYNSULP4", "Isi grease 93-1000 sesuai spesifikasi dan jalankan warm-up 30 menit", "Catat jam spindel di kartu PM"],
     "Pakai LOTO sebelum membuka spindel.",
     "Spdl VF-2 No.2 bunyi kasar stlh jam ke-6, getaran naik sampai 6.2 mm/s, alarm ALM-162. Bearing depan aus, grease kering krn PM bulanan terlewat. Ganti bearing NSK-7014CTYNSULP4, warm-up 30 mnt, getaran normal 2.0 mm/s.")

case("CNC-04", "2024-05-03T10:40:00+07:00", "SPINDLE", "en", "typed", None, "MEK-001", 3.0, "RESOLVED",
     "Spindle motor temperature reached 92 C, spindle overheat alarm",
     ["ALM-410"], ["CHL-FAN-0220", "FLT-CLN-10U"],
     "Spindle chiller condenser fan stalled; coolant return temperature above 30 C so spindle jacket not cooled.",
     ["Reduce feed override to 50% and let spindle idle at 1500 rpm", "Check chiller display for set-point and return temperature",
      "Inspect condenser fan CHL-FAN-0220 and clean condenser fins", "Replace fan if shaft is seized", "Verify jacket coolant temperature stabilises at 22-25 C",
      "Resume at 70% load for first hour"],
     "Do not run spindle above 6000 rpm while motor temperature exceeds 85 C (SAFE-001 trips at 95 C).",
     "Variaxis spindle motor temp climbed 78 -> 92 C within 20 min, alarm ALM-410 SPINDLE OVERHEAT. Chiller fan CHL-FAN-0220 seized, condenser blocked with oil mist. Cleaned fins, replaced fan; temp stable at 71 C.")

case("CNC-03", "2024-03-09T08:15:00+07:00", "SPINDLE", "id", "typed", None, "MEK-004", 2.5, "RESOLVED",
     "Alarm SP1241 spindel motor overheat saat pengerjaan stainless",
     ["SP1241"], ["FAN-RD-0099"],
     "Kipas pendingin motor spindel tersumbat serbuk logam dan oli; aliran udara berkurang.",
     ["Matikan mesin dan tunggu motor dingin di bawah 50 C", "Lepas cover kipas motor spindel", "Bersihkan kipas dan saluran udara dengan compressed air",
      "Periksa putaran kipas FAN-RD-0099 dengan tangan", "Reset alarm SP1241 dan uji putar 3000 rpm selama 15 menit", "Turunkan feed 10% selama 2 jam pertama"],
     "Pastikan motor sudah dingin sebelum cover dibuka.",
     "Robodrill alarm SP1241 spindel motor overheat waktu frais SUS304. Kipas motor penuh serbuk dan oli. Dibersihkan, alarm di-reset, suhu motor stabil 58 C setelah 15 menit di 3000 rpm.")

case("CNC-03", "2023-11-17T14:30:00+07:00", "SERVO", "en", "pdf_scan", 0.86, "MEK-002", 4.0, "RESOLVED",
     "Alarm SV0401 VRDY off on X axis after power-up, axis will not servo-on",
     ["SV0401"], ["FNC-A06B-6114-H105", "CBL-FB-X-0412"],
     "Feedback cable CBL-FB-X-0412 insulation cut by chip; intermittent short caused amplifier not to reach ready state.",
     ["Power off and wait 5 min for DC link discharge", "Check amplifier status LED and servo diagnostics (DGN 200-205)", "Inspect X-axis feedback cable along cable carrier",
      "Replace damaged cable CBL-FB-X-0412 and re-seat connector", "Power up, clear SV0401, jog X at 10% before returning to production"],
     "DC link stays energised for several minutes after power-off; measure before touching amplifier terminals.",
     "After morning power-up the Robodrill showed SV0401 VRDY OFF on X. Amplifier A06B-6114-H105 LED showed 1. Found cut feedback cable in chain, replaced CBL-FB-X-0412. Axis servo-on normal.")

case("CNC-03", "2024-07-30T11:10:00+07:00", "SERVO", "id", "handwritten_scan", 0.71, "MEK-002", 3.5, "RECURRED",
     "SV0401 muncul lagi di sumbu X, kabel feedback sudah pernah diganti",
     ["SV0401"], ["CBL-FB-X-0412"],
     "Kabel feedback kembali rusak di titik tekuk carrier; routing kabel terlalu pendek sehingga menekuk tajam.",
     ["Cek diagnosa servo X dan LED amplifier", "Periksa kabel di titik tekuk cable carrier", "Ganti kabel CBL-FB-X-0412 dengan jenis flex tinggi",
      "Perpanjang routing dan pasang clamp strain relief", "Uji jog X 10% selama 10 menit tanpa alarm"],
     "Tunggu DC link discharge 5 menit sebelum menyentuh amplifier.",
     "SV0401 muncul lg di X (kejadian sama nov 2023). Kabel CBL-FB-X-0412 rusak di titik tekuk carrier. Ganti kabel flex, routing diperpanjang + clamp. Catatan: prob berulang krn routing, bukan kabelnya saja.")

case("CNC-03", "2024-09-05T16:25:00+07:00", "SERVO", "en", "typed", None, "MEK-002", 1.5, "RESOLVED",
     "SV0430 servo motor overheat on Z axis during deep pocketing",
     ["SV0430"], ["GRS-LM-0007"],
     "Z-axis ballscrew nut dry; friction raised motor current and temperature.",
     ["Let axis cool for 15 minutes", "Check Z axis load meter at rapid and at cut", "Inspect ballscrew nut lubrication line and grease nipple",
      "Pump grease 3 shots GRS-LM-0007 and cycle Z full stroke 10 times", "Compare load meter against baseline (below 40%)"],
     "Keep hands clear of table while cycling axis with covers open.",
     "SV0430 on Z during deep pocket in steel. Load meter 85% at rapid. Ballscrew nut dry - missed greasing. 3 shots GRS-LM-0007, cycled stroke, load dropped to 32%.")

case("CNC-01", "2024-04-18T09:00:00+07:00", "COOLANT", "en", "typed", None, "MEK-003", 1.0, "RESOLVED",
     "Coolant pressure dropped to 1.9 bar, low pressure interlock",
     ["Alarm 970"], ["FLT-CLN-10U", "COOL-PMP-0312"],
     "Coolant filter cartridge clogged with fine swarf; pump cavitating due to restricted suction.",
     ["Stop coolant pump and isolate tank valve", "Remove and inspect suction strainer and filter cartridge FLT-CLN-10U", "Replace cartridge and clean strainer basket",
      "Bleed pump and refill tank to mark", "Verify pressure 3.5-4.2 bar with pump running at full flow"],
     "SAFE-003 interlock stops spindle below 2.0 bar; do not bypass the pressure switch.",
     "Coolant pressure fell 3.9 -> 1.9 bar mid cycle, Alarm 970 coolant pressure low. Filter FLT-CLN-10U blocked with aluminium swarf. Replaced filter, cleaned strainer, pressure back to 3.9 bar.")

case("CNC-02", "2024-06-26T15:45:00+07:00", "COOLANT", "id", "typed", None, "MEK-003", 2.0, "RESOLVED",
     "Tekanan coolant rendah 2.1 bar dan pompa berisik",
     ["Alarm 970"], ["COOL-PMP-0312", "SEAL-PMP-0058"],
     "Impeller pompa coolant aus dan seal bocor, sehingga tekanan turun dan udara masuk.",
     ["Matikan pompa dan tutup valve tangki", "Lepas pompa COOL-PMP-0312 dan periksa impeller", "Ganti impeller dan mechanical seal SEAL-PMP-0058",
      "Pasang kembali, isi tangki, dan bleed udara", "Uji tekanan di atas 3.5 bar selama 20 menit"],
     "Jangan bypass switch tekanan coolant (SAFE-003).",
     "Tekanan coolant VF-2 No.2 turun ke 2.1 bar, muncul Alarm 970, pompa berisik. Impeller aus, seal bocor. Ganti impeller + seal SEAL-PMP-0058, tekanan normal 4.0 bar.")

case("CNC-01", "2024-10-02T07:50:00+07:00", "LUBRICATION", "en", "typed", None, "MEK-005", 0.8, "RESOLVED",
     "Way lube low alarm, oil level OK but no pressure",
     ["ALM-130"], ["LUBE-PMP-0045", "FLT-LUB-0012"],
     "Way-lube pump inlet filter blocked, pump running but not delivering.",
     ["Check reservoir level and oil grade (ISO VG 68)", "Run manual lube cycle and watch for pressure at distributor", "Replace inlet filter FLT-LUB-0012",
      "Prime pump and run 3 manual cycles", "Clear ALM-130 and confirm automatic cycle times"],
     "Do not run axes without way lube; ways wear within hours.",
     "ALM-130 lube low although tank full. Manual cycle showed no pressure. Inlet filter FLT-LUB-0012 clogged with sludge. Replaced, primed, normal.")

case("CNC-03", "2024-03-21T10:05:00+07:00", "ATC", "id", "handwritten_scan", 0.74, "MEK-004", 2.0, "RESOLVED",
     "ATC magazin macet di posisi pot 7, alarm ATC-2031",
     ["ATC-2031"], ["CAM-ATC-0150", "SNS-PRX-0066"],
     "Cam follower ATC aus dan sensor proximity posisi pot bergeser sehingga magazin tidak konfirmasi posisi.",
     ["Pindahkan magazin ke posisi aman secara manual", "Periksa cam follower dan celah cam", "Atur ulang sensor proximity SNS-PRX-0066 (jarak 1.5 mm)",
      "Ganti cam follower CAM-ATC-0150 bila aus", "Jalankan ATC 20 siklus tanpa beban", "Catat torsi baut cam di kartu PM"],
     "Pastikan magazin tidak bisa bergerak otomatis sebelum tangan masuk area ATC.",
     "ATC magazin macet pot 7, alarm ATC-2031. Cam follower aus + sensor prox SNS-PRX-0066 geser. Setel sensor jarak 1.5 mm, ganti follower. 20 siklus lancar.")

case("CNC-03", "2025-01-08T12:30:00+07:00", "ATC", "mixed", "typed", None, "MEK-004", 2.0, "RESOLVED",
     "ATC magazine cam perlu adjustment, tool change lambat dan ada bunyi klik",
     ["ATC-2031"], ["CAM-ATC-0150"],
     "Backlash cam magazine di atas toleransi, tool change time naik dari 2.1 s ke 3.4 s.",
     ["Ukur waktu tool change dan bandingkan dengan baseline 2.1 s", "Periksa backlash cam dengan dial indicator (batas 0.05 mm)", "Setel ulang posisi cam dan kencangkan baut",
      "Lumasi cam dengan grease ATC", "Jalankan 20 siklus ATC dan ukur ulang"],
     "LOTO saat menyetel cam.",
     "PM korektif: ATC magazine cam adjustment sesuai jadwal PM-002. Backlash cam 0.08 mm (batas 0.05). Disetel + dilumasi, tool change 2.2 s. Kode ATC-2031 tidak muncul lagi.")

case("CNC-05", "2024-02-27T17:20:00+07:00", "HYDRAULIC", "en", "typed", None, "MEK-003", 3.0, "RESOLVED",
     "Turret indexing fault, hydraulic pressure low at clamp",
     ["CELOS-2305"], ["HYD-VLV-5521", "SEAL-HYD-0213"],
     "Turret clamp solenoid valve spool sticking and O-ring leak reducing clamp pressure below 30 bar.",
     ["Check hydraulic unit pressure gauge (nominal 35 bar)", "Check clamp pressure switch signal in diagnostics", "Inspect turret clamp valve HYD-VLV-5521 for sticking",
      "Replace valve and O-ring kit SEAL-HYD-0213", "Bleed lines and run 30 index cycles", "Verify clamp pressure holds at 35 bar"],
     "Release hydraulic pressure before disconnecting any line.",
     "NLX 2500 turret failed to index, CELOS-2305 clamp pressure low (28 bar). Clamp valve HYD-VLV-5521 sticky, O-ring cut. Replaced valve + seal kit, 30 index cycles OK.")

case("CNC-05", "2024-08-12T09:35:00+07:00", "HYDRAULIC", "id", "pdf_scan", 0.82, "MEK-003", 2.5, "RESOLVED",
     "Tekanan chuck turun, bocor oli di selang hidrolik chuck",
     ["CELOS-2314"], ["HOS-HYD-0071", "SEAL-HYD-0213"],
     "Selang hidrolik chuck HOS-HYD-0071 retak di dekat fitting karena gesekan.",
     ["Hentikan spindel dan lepaskan tekanan hidrolik", "Periksa semua selang chuck dan fitting dari kebocoran", "Ganti selang HOS-HYD-0071 dan O-ring SEAL-HYD-0213",
      "Isi ulang oli hidrolik dan bleed udara", "Uji clamp chuck dengan benda uji dan cek tekanan 35 bar"],
     "Jangan memutar spindel dengan tekanan chuck tidak stabil; benda kerja bisa terlepas.",
     "Tekanan chuck NLX turun dari 35 ke 22 bar, ada rembesan oli di selang chuck. Selang HOS-HYD-0071 retak dekat fitting. Diganti bersama O-ring, tekanan stabil 35 bar.")

case("CNC-01", "2024-06-04T13:40:00+07:00", "TOOLING", "en", "typed", None, "MEK-004", 1.2, "RESOLVED",
     "Tool unclamp failure, tool stuck in spindle taper, low air pressure",
     ["ALM-318"], ["SOL-AIR-0044"],
     "Shop air pressure dropped to 4 bar (min 5.5 bar) because of compressor drain valve failure; drawbar could not release.",
     ["Check shop air pressure at machine regulator", "Verify compressor output and drain valve", "Restore air to 6 bar and confirm tool release button works",
      "Inspect air solenoid SOL-AIR-0044 for sluggishness", "Clean taper and pull stud before reloading tool"],
     "Do not strike the tool to free it; use unclamp button once air is restored.",
     "ALM-318 tool unclamp fail on CNC-01. Air at 4 bar. Compressor drain valve stuck open. Restored air to 6 bar, tool released. Solenoid SOL-AIR-0044 OK.")

case("CNC-04", "2024-08-20T14:00:00+07:00", "SPINDLE", "en", "typed", None, "MEK-001", 4.5, "RESOLVED",
     "Spindle runout 0.025 mm after tool crash, vibration 4.9 mm/s",
     ["ALM-410"], ["HSK-A63-SP-0042", "SNS-VIB-0110"],
     "Tool crash bent the HSK-A63 taper seating; drawbar clamping force reduced.",
     ["Measure spindle runout at nose and at 100 mm test bar", "Inspect HSK taper for fretting and pull-back force", "Replace clamping set HSK-A63-SP-0042",
      "Re-measure runout (target below 0.005 mm)", "Run vibration baseline and save to PM card"],
     "Do not use the spindle for finishing until runout verified.",
     "After a crash on pallet 2 runout was 0.025 mm and vibration 4.9 mm/s. HSK taper damaged, clamping set HSK-A63-SP-0042 replaced, runout 0.004 mm.")

case("CNC-02", "2024-11-12T10:15:00+07:00", "SPINDLE", "id", "typed", None, "MEK-001", 3.0, "RESOLVED",
     "Bearing spindel panas 78 C, grease berlebih setelah PM",
     [], ["NSK-7014CTYNSULP4", "93-1000"],
     "Grease berlebih setelah PM menyebabkan bantalan panas saat pertama kali jalan.",
     ["Turunkan rpm ke 2000 selama 30 menit warm-up", "Pantau suhu bantalan per 5 menit", "Biarkan grease berlebih terdorong keluar (run-in)",
      "Bersihkan sisa grease di labirin", "Catat jumlah grease ke kartu PM (maks 1.2 g per bantalan)"],
     "Jangan naikkan rpm sebelum suhu bantalan di bawah 60 C.",
     "Setelah PM spindel VF-2 No.2 suhu bantalan naik ke 78 C. Penyebab: grease 93-1000 berlebih. Run-in 30 menit di 2000 rpm, suhu turun ke 52 C.")

case("CNC-04", "2024-09-25T08:50:00+07:00", "ELECTRICAL", "en", "typed", None, "MEK-002", 2.0, "RESOLVED",
     "Intermittent over-voltage alarm on main drive, machine trips at start of shift",
     ["ALM-2113"], ["FUS-AC-0630", "CAP-DC-0475"],
     "Plant voltage swell at shift start; DC bus capacitor aging caused trip threshold margin to shrink.",
     ["Log supply voltage during shift start with power analyser", "Inspect drive cabinet filter and fans", "Measure DC bus capacitor ESR and compare to datasheet",
      "Replace capacitor module CAP-DC-0475 and check fuse FUS-AC-0630", "Fit line reactor request to maintenance planning"],
     "Wait for DC bus discharge before probing drive terminals.",
     "Mazak tripped ALM-2113 over-voltage every morning ~07:05. Plant voltage up to 242 V at start. DC bus cap CAP-DC-0475 aged; replaced. No repeat in 2 weeks.")

case("CNC-05", "2024-04-09T11:30:00+07:00", "CONTROLLER", "en", "typed", None, "MEK-002", 1.0, "RESOLVED",
     "Axis position lost after power-up, battery alarm",
     ["SV0300"], ["BAT-FNC-0003"],
     "Absolute encoder backup battery below 2.6 V; reference position lost after weekend shutdown.",
     ["Check battery voltage alarm on controller screen", "Replace battery BAT-FNC-0003 with machine powered on", "Re-establish reference positions per OEM procedure",
      "Verify with a probing cycle", "Note replacement date in PM card"],
     "Replace battery only while control is powered, otherwise reference is lost.",
     "SV0300 APC alarm on NLX after weekend. Battery BAT-FNC-0003 2.4 V. Replaced, re-referenced axes, probing cycle within 0.003 mm.")

case("CNC-02", "2024-12-03T16:10:00+07:00", "COOLANT", "mixed", "whatsapp", None, "MEK-003", 0.5, "RESOLVED",
     "Coolant bau busuk dan foaming, filter cepat tersumbat",
     [], ["FLT-CLN-10U"],
     "Konsentrasi coolant rendah dan kontaminasi tramp oil, bakteri berkembang.",
     ["Ukur konsentrasi dengan refraktometer (target 7-9%)", "Skim tramp oil dan bersihkan tangki", "Tambah biocide dan atur konsentrasi",
      "Ganti filter FLT-CLN-10U", "Jadwalkan pengukuran konsentrasi mingguan"],
     "Pakai sarung tangan nitril saat menangani biocide.",
     "WA dari operator: coolant VF-2 No.2 baunya busuk + busa banyak. Konsentrasi 3%, banyak tramp oil. Tangki dibersihkan, biocide, konsentrasi 8%, filter FLT-CLN-10U diganti.")

case("CNC-04", "2024-10-28T09:10:00+07:00", "SERVO", "en", "pdf_scan", 0.88, "MEK-002", 5.0, "RESOLVED",
     "Servo error too large on Z axis during 5-axis cutting, ALM-103",
     ["ALM-103"], ["BLS-Z-0902"],
     "Z-axis ballscrew support bearing preload loss; excessive backlash 0.04 mm.",
     ["Check Z backlash with laser or dial indicator", "Inspect ballscrew support bearing and lock nut torque", "Re-torque lock nut and re-preload per spec",
      "Replace support bearing BLS-Z-0902 if play remains", "Run laser calibration and update compensation tables"],
     "Support the Z head before releasing ballscrew coupling.",
     "ALM-103 servo error too large on Z during finishing. Backlash 0.04 mm. Support bearing BLS-Z-0902 replaced and re-preloaded; laser comp updated, error gone.")

case("CNC-01", "2023-12-11T10:30:00+07:00", "ELECTRICAL", "id", "typed", None, "MEK-002", 1.5, "RESOLVED",
     "Pintu mesin tidak terdeteksi tertutup, interlock door switch error",
     ["ALM-150"], ["SW-DOOR-0092"],
     "Door interlock switch SW-DOOR-0092 tertutup serbuk dan posisi actuator bergeser.",
     ["Hentikan mesin dan pastikan pintu aman", "Bersihkan switch dan actuator dari serbuk", "Setel ulang posisi actuator", "Ganti switch SW-DOOR-0092 bila kontak tidak stabil",
      "Uji fungsi interlock: spindle tidak boleh start saat pintu terbuka"],
     "Jangan pernah jumper switch interlock pintu.",
     "ALM-150 door interlock, pintu tertutup tapi tidak terbaca. Switch SW-DOOR-0092 kotor, actuator geser. Dibersihkan dan disetel, tes interlock OK.")

case("CNC-05", "2024-11-26T14:20:00+07:00", "LUBRICATION", "en", "typed", None, "MEK-005", 1.0, "RESOLVED",
     "Chip conveyor jam, conveyor motor overload trip",
     ["ALM-612"], ["MTR-CNV-0.75KW"],
     "Long stringy chips wrapped in conveyor sprocket and caused thermal overload.",
     ["Lock out conveyor and clear chips from hinge belt", "Check chain tension and sprocket teeth", "Reset thermal relay and test run",
      "Add chip breaker insert to turning program", "Schedule weekly conveyor clean in PM"],
     "Never reach into the conveyor trough while powered.",
     "ALM-612 conveyor overload. Long chips wrapped sprocket. Cleared, retensioned chain, reset relay. Suggested chip breaker in program NLX-0207.")

case("CNC-01", "2024-07-09T15:35:00+07:00", "SPINDLE", "mixed", "handwritten_scan", 0.69, "MEK-001", 4.0, "RESOLVED",
     "Spindle vibrasi naik 5.4 mm/s, drawbar spring lemah, pull stud longgar",
     [], ["SP-DRB-0188", "HSK-A63-SP-0042"],
     "Drawbar spring fatigue sehingga clamping force turun dan tool holder bergetar.",
     ["Ukur clamping force drawbar dengan gauge (spec 8 kN)", "Ganti drawbar spring SP-DRB-0188", "Bersihkan taper dan cek pull stud",
      "Ukur runout setelah pasang", "Jalankan vibration baseline"],
     "LOTO sebelum membuka spindle housing.",
     "Spindle vib 5.4 mm/s di 3000 rpm. Drawbar spring SP-DRB-0188 lemah, clamping 5.1 kN (spec 8). Spring diganti, getaran 2.0 mm/s, runout 0.004.")

case("CNC-04", "2024-12-18T10:00:00+07:00", "COOLANT", "en", "typed", None, "MEK-005", 2.0, "RESOLVED",
     "Through-spindle coolant pressure low, tool cooling poor, drill breakage",
     ["ALM-441"], ["FLT-CLN-10U", "COOL-PMP-0312"],
     "High-pressure pump relief valve set too low after maintenance; filter bypass open.",
     ["Check high-pressure pump relief valve setting", "Reset relief to OEM spec (70 bar)", "Inspect filter bypass indicator and replace filter FLT-CLN-10U",
      "Test through-spindle flow with a nozzle plate", "Update PM checklist to record relief setting"],
     "Do not exceed relief setting; high pressure line failure is hazardous.",
     "Through-spindle coolant gave only 22 bar; drills breaking. Relief valve was left at 40 bar after PM. Reset to 70 bar, replaced FLT-CLN-10U, pressure OK.")

case("CNC-02", "2024-12-27T12:40:00+07:00", "SPINDLE", "id", "handwritten_scan", 0.66, "MEK-001", 6.0, "RECURRED",
     "Spindel getar lagi, bearing depan baru diganti 11 bulan lalu",
     ["ALM-162"], ["NSK-7014CTYNSULP4", "SEAL-SP-0031"],
     "Seal labirin SEAL-SP-0031 tidak diganti pada perbaikan sebelumnya, coolant masuk lagi ke bantalan.",
     ["Bongkar cartridge spindel dan periksa bantalan serta seal", "Ganti set bantalan NSK-7014CTYNSULP4 DAN seal SEAL-SP-0031 bersamaan",
      "Periksa air purge spindel (tekanan 0.3 bar)", "Isi grease sesuai spec dan run-in 30 menit", "Ukur getaran dan catat baseline"],
     "LOTO sebelum membuka spindel.",
     "ALM-162 lagi di VF-2 No.2. Bearing depan rusak lg, seal labirin SEAL-SP-0031 tdk ikut diganti dulu. Ganti bearing + seal bersamaan, cek air purge 0.3 bar. Pelajaran: selalu ganti seal bersama bearing.")


case("CNC-01", "2024-09-16T10:20:00+07:00", "SPINDLE", "en", "typed", None, "MEK-006", 1.5, "RESOLVED",
     "Alarm 108 spindle motor overheat during second shift roughing",
     ["Alarm 108"], ["59-0112"],
     "Spindle cooling fan filter 59-0112 clogged with mist and fine chips; airflow too low for two-shift load.",
     ["Stop the cycle and let the spindle cool 20 minutes with the enclosure door open",
      "Remove and inspect spindle cooling fan filter 59-0112", "Replace filter 59-0112 and clean fan guard",
      "Check spindle lubrication level", "Run spindle warm-up program at 50% speed for 10 minutes",
      "Log motor temperature every 30 minutes for the rest of the shift"],
     "Do not reset Alarm 108 repeatedly; if it repeats within one shift, stop and request service.",
     "VF-2 No.1 Alarm 108 at 21:40, motor winding 96 C. Fan filter 59-0112 fully blocked. Replaced filter, warm-up 10 min at 50%, motor stable 74 C until end of shift.")

case("CNC-02", "2025-03-11T14:50:00+07:00", "SPINDLE", "id", "handwritten_scan", 0.76, "MEK-006", 4.0, "RECURRED",
     "Alarm 108 muncul dua kali dalam satu shift walau filter kipas baru diganti",
     ["Alarm 108"], ["59-0112", "DRV-SP-2207"],
     "Filter kipas bersih; penyebab sebenarnya kipas drive spindel DRV-SP-2207 lemah sehingga drive ikut panas.",
     ["Hentikan siklus dan dinginkan spindel 20 menit", "Periksa filter kipas 59-0112 (bersih = cari penyebab lain)",
      "Ukur arus dan suhu drive spindel di kabinet", "Ganti kipas drive DRV-SP-2207",
      "Buat work order bila Alarm 108 muncul dua kali per shift", "Pemanasan spindel 10 menit pada 50% sebelum produksi"],
     "LOTO sebelum membuka kabinet drive.",
     "Alarm 108 dua kali di VF-2 No.2 shift pagi. Filter 59-0112 baru diganti mgg lalu, masih bersih. Kipas drive DRV-SP-2207 putaran lemah, drive panas 71 C. Kipas diganti. Catatan: kalau filter bersih, cek drive!")

# ── OCR degradation ──────────────────────────────────────────────────────────

_CONFUSIONS = [("rn", "m"), ("m", "rn"), ("l", "1"), ("O", "0"), ("0", "O"), ("S", "5"),
               ("cl", "d"), ("I", "l"), ("e", "c"), ("u", "v")]


def _ocr_noise(text: str, conf: float, rng: random.Random) -> str:
    """Corrupt prose; token-level so a handful of words are damaged, not all."""
    out = []
    for tok in text.split(" "):
        if len(tok) > 3 and rng.random() < (1.0 - conf) * 0.55:
            src, dst = rng.choice(_CONFUSIONS)
            if src in tok:
                tok = tok.replace(src, dst, 1)
        out.append(tok)
    return " ".join(out)


def _corrupt_code(code: str, rng: random.Random) -> str:
    """OCR-style code damage: O/0, I/1 swaps (the canonical key folds these back)."""
    for src, dst in (("0", "O"), ("1", "I"), ("O", "0")):
        if src in code and rng.random() < 0.8:
            return code.replace(src, dst, 1)
    return code


def build() -> dict:
    cases = []
    for n, c in enumerate(sorted(C, key=lambda x: x["date"]), start=1):
        model, controller = MODELS[c["machine"]]
        cid = f"CASE-{n:04d}"
        rng = random.Random(cid)
        entry = {
            "id": cid,
            "entry_type": "CASE",
            "status": "RESOLVED",
            "logged_at": c["date"],
            "machine_id": c["machine"],
            "machine_model": model,
            "controller": controller,
            "category": c["category"],
            "language": c["lang"],
            "source": c["source"],
            "symptom": c["symptom"],
            "error_codes": list(c["ecodes"]),
            "part_codes": list(c["pcodes"]),
            "root_cause": c["cause"],
            "fix_steps": list(c["steps"]),
            "safety_notes": c["safety"],
            "downtime_hours": c["hours"],
            "mechanic_id": c["mech"],
            "outcome": c["outcome"],
            "text": c["text"],
        }
        if c["source"] in ("handwritten_scan", "pdf_scan"):
            conf = c["conf"]
            entry["ocr_confidence"] = conf
            entry["scan_ref"] = f"scans/{c['date'][:7]}/{cid.lower()}.{'jpg' if c['source'] == 'handwritten_scan' else 'pdf'}"
            entry["symptom"] = _ocr_noise(entry["symptom"], conf, rng)
            entry["root_cause"] = _ocr_noise(entry["root_cause"], conf, rng)
            entry["text"] = _ocr_noise(entry["text"], conf, rng)
            entry["fix_steps"] = [_ocr_noise(s, conf, rng) for s in entry["fix_steps"]]
            if conf < 0.8:   # low-confidence scans also damage structured fields
                entry["error_codes"] = [_corrupt_code(x, rng) for x in entry["error_codes"]]
                entry["part_codes"] = [_corrupt_code(x, rng) for x in entry["part_codes"]]
        cases.append(entry)
    return {
        "_meta": {
            "synthetic": True,
            "facility": "PT Karawang Precision Manufacturing",
            "note": ("Synthetic maintenance history for demo use. Alarm numbers and part codes are "
                     "illustrative and do not map to real OEM manuals. Scanned entries carry "
                     "deliberate OCR noise."),
            "generator": "scripts/generate_maintenance_seed.py",
        },
        "cases": cases,
    }


if __name__ == "__main__":
    data = build()
    OUT.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    langs = {}
    for c in data["cases"]:
        langs[c["language"]] = langs.get(c["language"], 0) + 1
    scans = sum(1 for c in data["cases"] if "ocr_confidence" in c)
    print(f"wrote {len(data['cases'])} cases to {OUT}  languages={langs}  scanned={scans}")
