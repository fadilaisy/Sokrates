"""Skill Studio draft: structured files built in code, LLM only writes SKILL.md."""

import pytest
from fastapi.testclient import TestClient

import backend.main as m

client = TestClient(m.app)

BODY = {
    "machine_type": "Vertical Machining Center", "failure_mode": "Motor overload",
    "sla_class_a_penalty_per_hour_idr": 20_000_000,
    "sla_class_b_penalty_per_hour_idr": 300_000,
    "sla_class_c_penalty_per_hour_idr": 100_000,
    "overtime_cost_per_hour_idr": 450_000, "changeover_cost_idr": 350_000,
    "safety_thresholds": {"motor_temp_celsius": 90, "spindle_vibration_mm_per_s": 8, "coolant_pressure_bar": 2},
}


@pytest.fixture
def llm(monkeypatch):
    replies = {}

    async def fake(prompt, max_tokens=512, **kw):
        replies["prompt"] = prompt
        return replies["text"]

    monkeypatch.setattr(m, "_call_gemini", fake)
    monkeypatch.setattr(m.aicore, "configured", lambda: True)
    return replies


def _draft():
    r = client.post("/api/skills/draft", json=BODY)
    assert r.status_code == 200
    return r.json()


@pytest.mark.parametrize("text", [
    "[Gemini: tidak ada teks dalam respons (finishReason=MAX_TOKENS)] Silakan tinjau",
    "",
    '{"skill_md": "# SKILL.md\n- truncated',
])
def test_llm_failure_falls_back_without_errors(llm, text):
    llm["text"] = text
    d = _draft()
    assert d["validation_errors"] == []
    assert d["skill_md"].startswith("# SKILL.md") and "Vertical Machining Center" in d["skill_md"]


def test_llm_markdown_used_and_fence_stripped(llm):
    llm["text"] = "```markdown\n# SKILL.md\nIsi dari model\n```"
    d = _draft()
    assert d["skill_md"] == "# SKILL.md\nIsi dari model"
    assert "SAFE-001" in llm["prompt"]


def test_interlocks_tightened_and_pass_lint(llm):
    llm["text"] = "# SKILL.md\nok"
    d = _draft()
    by_id = {r["id"]: r for r in d["interlocks"]}
    assert by_id["SAFE-001"]["condition"] == "motor_temp_celsius > 90"   # user's stricter value
    assert by_id["SAFE-002"]["condition"] == "spindle_vibration_mm_per_s > 8"
    assert all(not r["override_allowed"] for r in d["interlocks"] if r["tier"] == 1)
    assert d["hooks"] and d["sla_penalties"]["order_classes"]["A"]["penalty_per_hour_idr"] == 20_000_000
    lint = client.post("/api/skills/lint", json={k: d[k] for k in ("skill_md", "sla_penalties", "hooks", "interlocks")}).json()
    assert lint["valid"] is True, lint["errors"]
