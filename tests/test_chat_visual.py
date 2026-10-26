"""tests/test_chat_visual.py — optional HTML visual in chat answers: extract, sanitize, ground."""

import asyncio

import backend.main as m
from backend import chat_visual as cv

CONTEXT = "WO-2025-001 di CNC-01, denda SLA Rp 20,000,000/jam; NSK-7014CTYNSULP4 stok 0, lead time 2 hari"

VISUAL = """Order WO-2025-001 paling mahal.

```html-visual
<div style="font:14px sans-serif"><svg width="200" height="40" onload="alert(1)">
<rect width="180" height="16" fill="url(#g)"/><text x="4" y="30">Rp 20.000.000/jam</text></svg>
<script>fetch('https://evil.example')</script><img src="https://evil.example/x.png">
<a href="javascript:alert(1)">x</a></div>
```"""


def test_extract_strips_block_and_dangerous_parts():
    text, html = cv.extract(VISUAL, CONTEXT)
    assert text == "Order WO-2025-001 paling mahal."
    assert html and "<svg" in html and "20.000.000" in html
    for bad in ("<script", "onload", "evil.example", "javascript:", "url("):
        assert bad not in html


def test_reply_without_visual_is_untouched():
    assert cv.extract("Semua mesin normal.", CONTEXT) == ("Semua mesin normal.", None)


def test_visual_with_made_up_numbers_is_dropped():
    reply = "Teks.\n```html-visual\n<div>Downtime 999 jam, biaya Rp 123.456</div>\n```"
    assert cv.extract(reply, CONTEXT) == ("Teks.", None)


def test_oversized_visual_is_dropped():
    big = "<div>" + "20 " * (cv.MAX_VISUAL_BYTES // 2) + "</div>"
    assert cv.extract(f"Teks.\n```html-visual\n{big}\n```", CONTEXT) == ("Teks.", None)


def test_svg_local_gradient_reference_survives():
    html = cv.sanitize_visual('<svg><rect fill="red" xlink:href="#g"/><use href="#g"/></svg>')
    assert 'href="#g"' in html


def test_chat_endpoint_returns_visual_and_prompts_for_it(monkeypatch):
    captured = {}

    async def fake(prompt, max_tokens=512, **kw):
        captured["prompt"] = prompt
        return ("WO-2025-001 di CNC-01 punya denda tertinggi.\n```html-visual\n"
                "<table><tr><td>WO-2025-001</td><td>20,000,000</td></tr></table>\n```")

    monkeypatch.setattr(m, "_call_llm", fake)
    monkeypatch.setattr(m, "_chat_sources", lambda message, state: [])
    out = asyncio.run(m.post_chat(m.ChatRequest(message="bandingkan denda SLA tiap order")))
    assert out.reply == "WO-2025-001 di CNC-01 punya denda tertinggi."
    assert out.visual and "<table>" in out.visual
    assert "html-visual" in captured["prompt"] and "Stok suku cadang" in captured["prompt"]


def test_machine_id_alone_does_not_ground_a_visual():
    ctx = "CNC-01 suhu 78 C, CNC-02 suhu 65 C"
    made_up = "<div><b>CNC-01</b> 91 · 47 · 33</div>"
    assert not cv.grounded(made_up, ctx)
    assert cv.grounded("<div><b>CNC-01</b> 78 · <b>CNC-02</b> 65</div>", ctx)
