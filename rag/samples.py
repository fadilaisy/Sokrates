"""
rag/samples.py
──────────────
Demo corpus for the hackathon: a handful of FICTIONAL plant documents for
PT Karawang Precision Manufacturing (the same shop floor as the SAP mock),
plus a golden evaluation set that points at them.

    python -m rag.samples build   # write PDFs/PNG to rag/samples_out/
    python -m rag.samples seed    # build + ingest into the RAG store
    python -m rag.eval            # run the golden set against the store

The documents exercise every pipeline path: digital PDFs with headings and
tables, an Indonesian SOP with two versions (supersede), a safety procedure,
a maintenance log, a machine-specific manual for another controller (filtered
out for Haas incidents), and a scanned image page (OCR path).

All content is invented for demo purposes — not real OEM documentation.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Dict, List

try:
    import pymupdf as fitz  # type: ignore
except Exception:  # pragma: no cover
    import fitz  # type: ignore

OUT = Path(__file__).resolve().parent / "samples_out"

CSS = """
body { font-family: sans-serif; font-size: 10.5pt; line-height: 1.35; }
h1 { font-size: 18pt; font-weight: bold; margin: 0 0 6pt 0; }
h2 { font-size: 13.5pt; font-weight: bold; margin: 10pt 0 4pt 0; }
h3 { font-size: 11.5pt; font-weight: bold; margin: 8pt 0 3pt 0; }
p  { margin: 0 0 5pt 0; }
table { border-collapse: collapse; margin: 4pt 0 8pt 0; }
td, th { border: 1px solid #333; padding: 3pt 5pt; font-size: 9.5pt; }
th { font-weight: bold; }
.meta { font-size: 9pt; color: #444; }
"""

# Each document: filename → list of page HTML bodies
DOCS: Dict[str, List[str]] = {}

DOCS["Haas_VF-2_Operator_Manual_Spindle_Alarms.pdf"] = [
    """
<h1>Haas VF-2 Operator Manual — Spindle and Alarm Reference</h1>
<p class="meta">Demo excerpt (fictional) · Machine model: Haas VF-2 · Controller: Haas NGC · Effective date: 2026-03-01</p>
<h2>1. Spindle Thermal Protection</h2>
<p>The VF-2 spindle motor is monitored by a thermal sensor in the motor winding. When the motor winding temperature
exceeds 95 °C the control raises Alarm 108 (Spindle Motor Overheat) and performs a controlled spindle stop.
The machine cannot restart the spindle until the winding temperature falls below 80 °C.</p>
<h3>1.1 Common causes of spindle overheat</h3>
<p>Typical causes are a clogged spindle cooling fan filter, insufficient spindle lubrication, sustained cutting
above 90 percent of rated load, and a failing spindle drive. Check the cooling fan filter first; it is the most
frequent cause on machines that run two shifts.</p>
<h3>1.2 Recovery procedure after Alarm 108</h3>
<p>Let the spindle cool with the enclosure door open for at least 20 minutes. Clean or replace the cooling fan
filter (Part No. 59-0112). Verify spindle lubrication level, then run the spindle warm-up program at 50 percent of
rated speed for 10 minutes before returning to production. If Alarm 108 repeats within one shift, stop the machine
and request service; do not reset the alarm repeatedly.</p>
""",
    """
<h2>2. Alarm Reference Table</h2>
<table>
<tr><th>Alarm</th><th>Description</th><th>Operator action</th></tr>
<tr><td>Alarm 108</td><td>Spindle motor overheat</td><td>Cool down, clean fan filter 59-0112, warm-up cycle</td></tr>
<tr><td>Alarm 135</td><td>Spindle drive fault</td><td>Stop machine, request service, do not reset more than once</td></tr>
<tr><td>Alarm 161</td><td>Axis drive overload</td><td>Reduce feed rate, check way lubrication</td></tr>
<tr><td>Alarm 970</td><td>Coolant pressure low</td><td>Check coolant level and filter, minimum 2 bar</td></tr>
</table>
<h2>3. Spindle Bearing Maintenance</h2>
<p>Spindle bearings require lubrication every 500 operating hours. Use only the specified spindle grease
(Part No. 93-1000). Over-greasing raises bearing temperature and can trigger Alarm 108 during the first hour
after service.</p>
""",
]

SOP_V1 = [
    """
<h1>SOP Penanganan Overheat Spindle Mesin CNC</h1>
<p class="meta">Nomor dokumen: SOP-PRD-014 · Revisi 1 · Berlaku: 15 Januari 2026 · Area: Line CNC Karawang</p>
<h2>1. Tujuan</h2>
<p>Prosedur ini mengatur langkah operator dan teknisi ketika mesin CNC (Haas VF-2, Mazak Variaxis, Fanuc Robodrill)
mengalami overheat spindle, agar kerusakan bearing dan keterlambatan order dapat dicegah.</p>
<h2>2. Langkah Penanganan</h2>
<p>Jika suhu motor spindle melebihi 90 °C, operator wajib menurunkan feed rate menjadi 70 persen dan memberi tahu
supervisor shift. Jika suhu melebihi 95 °C atau muncul Alarm 108, hentikan siklus pemesinan dan biarkan spindle
dingin minimal 15 menit.</p>
<p>Teknisi memeriksa filter kipas pendingin spindle dan level pelumasan sebelum mesin dijalankan kembali.</p>
""",
]

SOP_V2 = [
    """
<h1>SOP Penanganan Overheat Spindle Mesin CNC</h1>
<p class="meta">Nomor dokumen: SOP-PRD-014 · Revisi 2 · Berlaku: 1 Agustus 2026 · Area: Line CNC Karawang</p>
<h2>1. Tujuan</h2>
<p>Prosedur ini mengatur langkah operator dan teknisi ketika mesin CNC (Haas VF-2, Mazak Variaxis, Fanuc Robodrill)
mengalami overheat spindle, agar kerusakan bearing dan keterlambatan order dapat dicegah.</p>
<h2>2. Langkah Penanganan</h2>
<p>Jika suhu motor spindle melebihi 85 °C, operator wajib menurunkan feed rate menjadi 60 persen dan memberi tahu
supervisor shift melalui kokpit SkillForge. Jika suhu melebihi 95 °C atau muncul Alarm 108, hentikan siklus
pemesinan segera dan biarkan spindle dingin minimal 20 menit dengan pintu enclosure terbuka.</p>
<p>Teknisi memeriksa filter kipas pendingin spindle (kode part 59-0112) dan level pelumasan. Mesin hanya boleh
dijalankan kembali setelah program pemanasan spindle 10 menit pada 50 persen kecepatan.</p>
<h2>3. Eskalasi</h2>
<p>Jika Alarm 108 muncul dua kali dalam satu shift, supervisor wajib membuat work order perbaikan dan
memindahkan order prioritas A ke mesin lain sesuai rekomendasi solver. Keputusan pemindahan order tetap
memerlukan persetujuan supervisor.</p>
""",
]

DOCS["SOP_Overheat_Spindle_CNC_rev1.pdf"] = SOP_V1
DOCS["SOP_Overheat_Spindle_CNC_rev2.pdf"] = SOP_V2

DOCS["Prosedur_Keselamatan_LOTO_CNC.pdf"] = [
    """
<h1>Prosedur Keselamatan Lockout/Tagout (LOTO) Mesin CNC</h1>
<p class="meta">Dokumen K3 · Berlaku: 10 Februari 2026 · Berlaku untuk semua mesin CNC di PT Karawang Precision Manufacturing</p>
<h2>1. Kapan LOTO Wajib Dilakukan</h2>
<p>LOTO wajib dilakukan sebelum teknisi membuka panel listrik, mengganti bearing spindle, atau membersihkan area
chip conveyor. Bahaya utama adalah sengatan listrik dan spindle yang berputar tiba-tiba.</p>
<h2>2. Langkah LOTO</h2>
<p>Tekan tombol E-Stop, matikan main breaker, pasang gembok pribadi dan tag bertanda nama teknisi, lalu lakukan
uji coba start untuk memastikan energi sudah nol. Gunakan APD: sarung tangan, kacamata pengaman, dan sepatu safety.</p>
<h2>3. Aturan Keselamatan yang Tidak Boleh Dilanggar</h2>
<p>Aturan SAFE-001 (suhu motor di atas 95 °C) dan SAFE-002 (getaran spindle di atas 8 mm/s) mewajibkan emergency
stop. Peringatan: mesin tidak boleh dijalankan kembali sebelum penyebab ditemukan dan supervisor memberi izin
tertulis. Aturan keselamatan ini tidak dapat di-override oleh solver maupun AI.</p>
""",
]

DOCS["Log_Perawatan_CNC-02_Q3_2026.pdf"] = [
    """
<h1>Log Perawatan CNC-02 (Haas VF-2 No. 2) — Kuartal 3 2026</h1>
<p class="meta">Catatan perawatan · Mesin: CNC-02 · Teknisi: Rudi Hartono</p>
<table>
<tr><th>Tanggal</th><th>Kejadian</th><th>Tindakan</th><th>Downtime</th></tr>
<tr><td>2026-07-08</td><td>Alarm 108 overheat spindle</td><td>Filter kipas 59-0112 diganti, pemanasan spindle</td><td>1,5 jam</td></tr>
<tr><td>2026-08-19</td><td>Getaran spindle 6,2 mm/s</td><td>Bearing dilumasi ulang dengan grease 93-1000</td><td>2 jam</td></tr>
<tr><td>2026-09-23</td><td>Alarm 108 berulang dua kali</td><td>Work order WO-MT-221 dibuat, drive spindle dicek</td><td>4 jam</td></tr>
</table>
<h2>Catatan Teknisi</h2>
<p>Overheat spindle pada CNC-02 berulang setiap kali mesin menjalankan order kelas A dengan beban di atas
90 persen. Disarankan mengganti filter kipas setiap 2 minggu dan memantau suhu motor saat shift malam.</p>
""",
]

DOCS["Fanuc_Robodrill_Servo_Alarm_Guide.pdf"] = [
    """
<h1>Fanuc Robodrill Servo Alarm Guide</h1>
<p class="meta">Demo excerpt (fictional) · Machine model: Fanuc Robodrill α-D21MiA · Controller: Fanuc 0i-MD</p>
<h2>1. SV0401 Servo V-Ready Off</h2>
<p>SV0401 indicates the servo amplifier ready signal dropped. Check the emergency stop circuit and the
amplifier power supply. Spindle overheat on the Robodrill is reported as SP1241, not as a numbered Haas alarm.</p>
<h2>2. SP1241 Spindle Motor Overheat</h2>
<p>Stop the cycle, let the spindle motor cool for 30 minutes and verify the spindle cooling fan. Replace the fan
unit if the alarm returns within 2 hours of operation.</p>
""",
]

# Scanned page: rendered to PNG so ingestion must OCR it.
SCAN_HTML = """
<h1>INSTRUKSI KERJA COOLANT CNC</h1>
<p>Berlaku: 5 Mei 2026</p>
<h2>1. Pemeriksaan Tekanan Coolant</h2>
<p>Periksa tekanan coolant setiap awal shift. Tekanan minimum adalah 2 bar. Jika tekanan di bawah 2 bar,
hentikan siklus pemesinan dan bersihkan filter coolant. Alarm 970 menunjukkan tekanan coolant rendah.</p>
<h2>2. Penggantian Coolant</h2>
<p>Ganti coolant setiap 3 bulan atau jika konsentrasi di bawah 5 persen. Gunakan sarung tangan saat menangani coolant.</p>
"""
SCAN_NAME = "Instruksi_Kerja_Coolant_scan.png"


def _render_pdf(pages: List[str]) -> bytes:
    doc = fitz.open()
    for body in pages:
        page = doc.new_page(width=595, height=842)  # A4
        page.insert_htmlbox(fitz.Rect(48, 48, 547, 800), body, css=CSS)
    data = doc.tobytes()
    doc.close()
    return data


def build(out: Path = OUT) -> List[Path]:
    out.mkdir(parents=True, exist_ok=True)
    paths = []
    for name, pages in DOCS.items():
        p = out / name
        p.write_bytes(_render_pdf(pages))
        paths.append(p)
    # Scan: render a PDF page to a 200-dpi PNG with no text layer.
    tmp = fitz.open(stream=_render_pdf([SCAN_HTML]), filetype="pdf")
    pix = tmp[0].get_pixmap(dpi=200)
    sp = out / SCAN_NAME
    pix.save(str(sp))
    tmp.close()
    paths.append(sp)
    return paths


# Order matters: rev1 before rev2 so rev2 supersedes rev1.
SEED_ORDER = [
    "Haas_VF-2_Operator_Manual_Spindle_Alarms.pdf",
    "SOP_Overheat_Spindle_CNC_rev1.pdf",
    "SOP_Overheat_Spindle_CNC_rev2.pdf",
    "Prosedur_Keselamatan_LOTO_CNC.pdf",
    "Log_Perawatan_CNC-02_Q3_2026.pdf",
    "Fanuc_Robodrill_Servo_Alarm_Guide.pdf",
    SCAN_NAME,
]

# Golden set: expected sources are identified by filename + page + a phrase that
# must appear in the chunk, so they survive re-ingestion (doc_ids change with hashes).
GOLDEN: List[dict] = [
    {"id": "g1", "incident": {"machine_id": "CNC-02", "fault_code": "Alarm 108", "disruption_type": "OVERHEAT"},
     "query": "langkah pemulihan setelah alarm overheat spindle",
     "expected": [{"file": "SOP_Overheat_Spindle_CNC_rev2.pdf", "page": 1, "contains": "20 menit"},
                  {"file": "Haas_VF-2_Operator_Manual_Spindle_Alarms.pdf", "page": 1, "contains": "Recovery procedure"}]},
    {"id": "g2", "incident": {"machine_id": "CNC-02", "disruption_type": "OVERHEAT"},
     "query": "berapa batas suhu untuk menurunkan feed rate",
     "expected": [{"file": "SOP_Overheat_Spindle_CNC_rev2.pdf", "page": 1, "contains": "85"}]},
    {"id": "g3", "incident": {"machine_id": "CNC-01"},
     "query": "spindle bearing grease part number",
     "expected": [{"file": "Haas_VF-2_Operator_Manual_Spindle_Alarms.pdf", "page": 2, "contains": "93-1000"}]},
    {"id": "g4", "incident": {"machine_id": "CNC-04", "disruption_type": "MAINTENANCE"},
     "query": "langkah lockout tagout sebelum ganti bearing",
     "expected": [{"file": "Prosedur_Keselamatan_LOTO_CNC.pdf", "page": 1, "contains": "gembok"}]},
    {"id": "g5", "incident": {"machine_id": "CNC-02"},
     "query": "riwayat alarm 108 berulang pada CNC-02",
     "expected": [{"file": "Log_Perawatan_CNC-02_Q3_2026.pdf", "page": 1, "contains": "WO-MT-221"}]},
    {"id": "g6", "incident": {"machine_id": "CNC-03", "fault_code": "SV0401"},
     "query": "servo ready signal dropped",
     "expected": [{"file": "Fanuc_Robodrill_Servo_Alarm_Guide.pdf", "page": 1, "contains": "SV0401"}]},
    {"id": "g7", "incident": {"machine_id": "CNC-01", "fault_code": "Alarm 970"},
     "query": "tekanan coolant minimum",
     "expected": [{"file": SCAN_NAME, "page": 1, "contains": "coolant"},
                  {"file": "Haas_VF-2_Operator_Manual_Spindle_Alarms.pdf", "page": 2, "contains": "Alarm 970"}]},
    {"id": "g8", "incident": {"machine_id": "CNC-02"},
     "query": "jadwal kalibrasi printer label gudang",
     "expected": [], "expect_abstain": True},
]


def seed(out: Path = OUT, use_llm: bool = False) -> List[dict]:
    from .ingest import ingest_file
    build(out)
    results = []
    for name in SEED_ORDER:
        r = ingest_file(out / name, role="admin", use_llm=use_llm)
        r["file"] = name
        results.append(r)
    return results


def write_golden(path: Path) -> None:
    path.write_text(json.dumps(GOLDEN, indent=2, ensure_ascii=False), encoding="utf-8")


if __name__ == "__main__":
    cmd = sys.argv[1] if len(sys.argv) > 1 else "build"
    if cmd == "build":
        for p in build():
            print("wrote", p)
    elif cmd == "seed":
        for r in seed():
            print(json.dumps({k: r.get(k) for k in ("file", "doc_id", "status", "chunks", "doc_type",
                                                     "ocr_pages", "duplicate", "error")}, ensure_ascii=False))
    else:
        print("usage: python -m rag.samples [build|seed]")
