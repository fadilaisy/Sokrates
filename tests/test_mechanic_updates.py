"""tests/test_mechanic_updates.py — follow-up messages to the mechanic after the supervisor decides."""

from backend import mechanic_updates as mu

CHANGES = [{"order_id": "WO-1043", "from_machine": "CNC-01", "to_machine": "CNC-02",
            "new_start_time": "09:00 WIB", "new_end_time": "15:00 WIB (Lembur)"},
           {"order_id": "WO-1044", "from_machine": "CNC-01", "to_machine": "CNC-01",
            "new_start_time": "11:00 WIB", "new_end_time": "13:00 WIB"}]


def test_approved_lists_parts_and_moves():
    text = mu.approved("Supervisor Budi", "CNC-01", CHANGES,
                       [{"id": "RSV-1", "part_no": "SEAL-SP-0031", "qty": 1}],
                       [{"id": "PR-7", "part_no": "NSK-7014CTYNSULP4", "qty": 2}], mechanic="Hendra Wijaya")
    assert text.splitlines() == [
        "✅ Rencana CNC-01 disetujui oleh Supervisor Budi",
        "🧰 Diambil dari gudang: SEAL-SP-0031 ×1",
        "🛒 Dipesan: NSK-7014CTYNSULP4 ×2 (PR-7)",
        "🔀 Jadwal produksi diperbarui:",
        "• WO-1043: CNC-01 → CNC-02, 09:00–15:00 (lembur)",
        "• WO-1044: tetap di CNC-01, 11:00–13:00",
        "Hendra, silakan lanjut perbaikan sesuai checklist."]


def test_refused_and_reverted():
    assert mu.refused("CNC-01", "Mesin CNC-02 berstatus DOWN").startswith("❌ Rencana CNC-01 tidak jadi diterapkan: Mesin")
    text = mu.reverted("Supervisor Budi", [{"id": "WO-1044"}, {"id": "WO-1043"}])
    assert "WO-1043, WO-1044 kembali ke jadwal semula" in text
