"""
backend/mechanic_updates.py
───────────────────────────
Follow-up Telegram messages for the mechanic once the supervisor decides on the
plan: approved (what moved, which parts), refused, or reverted. Built only from
the data SAP actually received, never from model text.
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional


def _clock(label: Any) -> str:
    """Solver labels look like "09:00 WIB" or "15:00 WIB (Lembur)"; keep the clock time."""
    return str(label or "?").split(" ")[0]


def _moves(changes: List[Dict[str, Any]]) -> List[str]:
    lines = []
    for c in changes:
        where = (f"{c.get('from_machine')} → {c.get('to_machine')}"
                 if c.get("to_machine") != c.get("from_machine") else f"tetap di {c.get('to_machine')}")
        lembur = " (lembur)" if "Lembur" in str(c.get("new_end_time")) else ""
        lines.append(f"• {c.get('order_id')}: {where}, "
                     f"{_clock(c.get('new_start_time'))}–{_clock(c.get('new_end_time'))}{lembur}")
    return lines


def approved(approved_by: str, machine_id: Optional[str], changes: List[Dict[str, Any]],
             reserved: List[Dict[str, Any]] = (), ordered: List[Dict[str, Any]] = (),
             mechanic: Optional[str] = None) -> str:
    """`reserved` / `ordered` are SAP reservation and purchase-requisition rows (part_no, qty, id)."""
    lines = [f"✅ Rencana {machine_id or ''} disetujui oleh {approved_by}".replace("  ", " ")]
    if reserved:
        lines.append("🧰 Diambil dari gudang: " + ", ".join(f"{r['part_no']} ×{r.get('qty', 1)}" for r in reserved))
    if ordered:
        lines.append("🛒 Dipesan: " + ", ".join(f"{p['part_no']} ×{p.get('qty', 1)} ({p['id']})" for p in ordered))
    if changes:
        lines.append("🔀 Jadwal produksi diperbarui:")
        lines += _moves(changes)
    who = (mechanic or "").split()[0] if mechanic else "Tim"
    lines.append(f"{who}, silakan lanjut perbaikan sesuai checklist.")
    return "\n".join(lines)


def refused(machine_id: Optional[str], reason: str) -> str:
    return (f"❌ Rencana {machine_id or ''} tidak jadi diterapkan: {reason}\n".replace("  ", " ")
            + "Jadwal dan suku cadang belum berubah. Tunggu instruksi supervisor.")


def reverted(approved_by: str, changes: List[Dict[str, Any]]) -> str:
    orders = ", ".join(sorted({str(c.get("id") or c.get("order_id")) for c in changes})) or "-"
    return (f"↩️ Persetujuan sebelumnya dibatalkan oleh {approved_by}.\n"
            f"Order {orders} kembali ke jadwal semula. Tunggu instruksi supervisor sebelum lanjut.")
