"""
Tests for the proactive maintenance flow (knowledge/) and the exact-code additions to rag/.

    python3 -m pytest tests/test_maintenance.py -q

Offline and isolated: the agent gets its own SQLite Store (not the process-wide one that
tests/test_rag.py seeds), and no LLM is called.
"""

from __future__ import annotations

import asyncio

import pytest

from knowledge.maintenance_ledger import MaintenanceLedger
from knowledge.proactive_agent import ProactiveAgent
from rag import metadata
from rag.store import Store


@pytest.fixture(scope="module")
def agent(tmp_path_factory):
    d = tmp_path_factory.mktemp("maint")
    a = ProactiveAgent(MaintenanceLedger(d / "m.jsonl"), store=Store(d / "rag.sqlite3"))
    counts = a.sync()
    assert counts["failed"] == 0 and counts["indexed"] + counts["unchanged"] > 0
    return a


def exact_ids(res):
    return [c["case_id"] for c in res["cases"] if c["exact_match"]]


# ── rag/metadata additions ──────────────────────────────────────────────────

def test_ocr_tolerant_fault_codes():
    assert metadata.normalize_fault_code("SVO401") == "SV0401"
    assert metadata.normalize_fault_code("ALM-I62") == "ALM162"
    assert metadata.normalize_fault_code("SOL1234") == "SOL1234"      # unknown prefix: not folded
    assert metadata.extract_fault_codes("muncul SVO401 lalu SV040I") == ["SV0401"]


def test_controller_codes_and_part_numbers_are_kept_apart():
    assert metadata.extract_fault_codes("ATC-2031, CELOS-2305, ATC-2O31") == ["ATC2031", "CELOS2305"]
    # identifiers and parts are not faults
    assert metadata.extract_fault_codes("CNC-0042 CASE-0004 SOP-PRD-014 HSK-A63-SP-0042") == []
    parts = metadata.extract_part_numbers("HSK-A63-SP-0042, NSK-7014CTYNSULP4, CBL-FB-X-0412, WO-MT-221, ATC-2031")
    assert parts == ["HSK-A63-SP-0042", "NSK-7014CTYNSULP4", "CBL-FB-X-0412"]
    assert metadata.part_key("CAM-ATC-O150") == metadata.part_key("CAM-ATC-0150")


# ── seed data ───────────────────────────────────────────────────────────────

def test_seed_is_hash_chained_bilingual_and_has_scans(agent):
    assert agent.ledger.verify()["valid"]
    cases = agent.ledger.cases()
    assert {"en", "id", "mixed"} <= {c["language"] for c in cases}
    assert sum(1 for c in cases if c.get("ocr_confidence")) >= 5


# ── exact-code retrieval through rag ────────────────────────────────────────

@pytest.mark.parametrize("query,expected", [
    ("SV0401", "CASE-0002"),
    ("SVO401", "CASE-0002"),               # OCR-damaged query
    ("HSK-A63-SP-0042", "CASE-0016"),
    ("NSK-7O14CTYNSULP4", "CASE-0001"),    # OCR-damaged part
    ("alarm 970", "CASE-0009"),
    ("ATC-2031", "CASE-0007"),             # case 0007 was scanned as "ATC-2O31"
])
def test_exact_code_lookup(agent, query, expected):
    res = agent.search(query, cases_only=True)
    assert not res["abstained"]
    assert expected in exact_ids(res)
    assert res["cases"][0]["exact_match"]  # exact hits rank first


def test_metadata_filter_and_cross_language(agent):
    res = agent.search("spindel bunyi kasar getaran", machine_id="CNC-01", cases_only=True)
    ids = [c["case_id"] for c in res["cases"]]
    assert "CASE-0001" in ids                                   # English case found from Bahasa
    assert all(c["case"]["machine_model"] == "Haas VF-2" for c in res["cases"])  # machine scope filter
    assert any(f.startswith("machine") for f in res["filters"])


def test_irrelevant_query_abstains(agent):
    assert agent.search("zzqx plorb wibble", cases_only=True)["cases"] == []


# ── the proactive flow ──────────────────────────────────────────────────────

def test_flow_cites_only_ledger_cases(agent):
    out = asyncio.run(agent.handle_new_entry({"machine_id": "CNC-03",
                                              "symptom": "Alarm SVO401 di sumbu X, axis tidak servo-on"}))
    entry, flow = out["entry"], out["flow"]
    pb = flow["playbook"]
    known = {c["id"] for c in agent.ledger.cases()} - {entry["id"]}
    assert pb["confidence"] == "high" and pb["steps"]
    for step in pb["steps"]:
        assert step["source_case_ids"] and set(step["source_case_ids"]) <= known
    for hit in flow["retrieval"]["hits"]:
        cit = hit["citation"]
        assert cit["ledger_hash"] == agent.ledger.get_case(cit["case_id"])["sha256_hash"]
        assert cit["rag_citation"].endswith("#p1")
    assert any("CASE-0014" in w["source_case_ids"] for w in pb["warnings"])   # recurred precedent
    assert flow["mechanic"]["id"] == "MEK-002"
    assert agent.ledger.verify()["valid"]


def test_flow_without_precedent_does_not_fabricate(agent):
    out = asyncio.run(agent.handle_new_entry({"machine_id": "CNC-05", "symptom": "zzqx plorb wibble"}))
    assert out["flow"]["playbook"]["steps"] == []
    assert out["flow"]["playbook"]["confidence"] == "none"


def test_llm_message_citing_unknown_case_is_rejected(agent):
    async def bad_llm(prompt):
        return "Cek kabel feedback [CASE-9999] dan [CASE-0002]."
    a = ProactiveAgent(agent.ledger, llm=bad_llm, store=agent.store)
    out = asyncio.run(a.handle_new_entry({"machine_id": "CNC-03", "symptom": "Alarm SV0401 X axis"}))
    assert out["flow"]["message_source"] == "template"


def test_resolved_case_becomes_searchable(agent):
    out = asyncio.run(agent.handle_new_entry({"machine_id": "CNC-04", "symptom": "Pompa coolant bunyi aneh"}))
    cid = out["entry"]["id"]
    res = asyncio.run(agent.resolve(cid, {"root_cause": "Bearing pompa aus, part PMP-BRG-0777",
                                          "fix_steps": ["Ganti bearing pompa PMP-BRG-0777"]}))
    assert res["rag"]["status"] == "indexed"
    assert cid in exact_ids(agent.search("PMP-BRG-0777", cases_only=True))


def test_chat_messages_are_short_alert_then_checklist():
    from knowledge.proactive_agent import ProactiveAgent
    entry = {"id": "CASE-0099", "machine_id": "CNC-01", "machine_model": "Haas VF-2", "error_codes": ["ALM-162"],
             "symptom": "Spindle overload"}
    pb = {"steps": [{"n": i, "text": f"step {i}", "source_case_ids": ["CASE-0001"]} for i in range(1, 8)],
          "parts": [{"code": "SEAL-SP-0031"}], "safety": [{"text": "LOTO dulu."}],
          "warnings": [{"text": "Perbaikan ini pernah berulang (2024-12-27): seal tidak diganti."}],
          "est_downtime_hours": 6.0, "confidence": "high", "supporting_cases": ["CASE-0001", "CASE-0026"]}
    alert, steps = ProactiveAgent.chat_messages(entry, {"name": "Hendra Wijaya"}, pb, [])
    assert alert == "🚨 CNC-01 (Haas VF-2) · ALM-162\nSpindle overload\nHendra, tolong ke CNC-01 sekarang. Estimasi ~6 jam."
    assert steps.splitlines() == [
        "📋 Langkah (CASE-0099):", "1. step 1", "2. step 2", "3. step 3", "4. step 4", "5. step 5",
        "+2 langkah lagi di playbook", "🧰 Bawa: SEAL-SP-0031", "⚠️ Pernah berulang: seal tidak diganti.",
        "🦺 LOTO dulu.", "Ref: CASE-0001, CASE-0026 · keyakinan tinggi"]
    assert "[CASE" not in steps                                   # tags only on the Ref line


def test_chat_messages_without_precedent():
    from knowledge.proactive_agent import ProactiveAgent
    entry = {"id": "CASE-0099", "machine_id": "CNC-02", "symptom": "Bunyi aneh"}
    pb = {"steps": [], "est_downtime_hours": None}
    alert, steps = ProactiveAgent.chat_messages(entry, {}, pb, [])
    assert alert.endswith("Tim, tolong ke CNC-02 sekarang.") and "eskalasi" in steps
