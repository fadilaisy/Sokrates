"""
backend/skill_studio.py
───────────────────────
The parts of Skill Studio that work from the maintenance history, kept free of
FastAPI so they can be tested on plain data:

- proposals: resolved cases no skill covers yet, grouped by alarm code → the
  agent proposes a new skill; repairs that disagree with a skill → the agent
  proposes a change; field-trial skills confirmed often enough → promote.
- backtest: replay a skill.json against the resolved cases its trigger catches.
- generated test: a PLC reading built from the skill's own trigger, so any
  skill can be tested, not only the simulator's three scenarios.
"""

from __future__ import annotations

import re
from typing import Any, Callable, Dict, Iterable, List, Optional

from skills.loader import normalize_alarm_code

# A field-trial skill becomes "active" after this many repairs confirm its first cause.
TRIAL_CONFIRMATIONS = 2
# A new skill is proposed once this many resolved cases share an alarm code and no skill catches them.
MIN_CASES_FOR_PROPOSAL = 2
# Reading used for a generated test: past the WARNING line of the shipped skills, below CRITICAL.
GENERATED_VALUES = {
    "motor_temp_celsius": 92.0,
    "spindle_vibration_mm_per_s": 6.5,
    "coolant_pressure_bar": 2.2,
}

_WORD = re.compile(r"[a-z0-9]{4,}")
_STOP = {"with", "after", "from", "that", "this", "yang", "dari", "pada", "untuk", "dengan", "karena",
         "tidak", "atau", "into", "when", "lalu", "saat", "pada", "been", "were", "have"}


def _words(text: Any) -> set:
    return {w for w in _WORD.findall(str(text or "").lower()) if w not in _STOP}


def cause_matches(cause: str, root_cause: str) -> bool:
    """A listed cause and a repair's root cause say the same thing (word overlap, either language)."""
    a, b = _words(cause), _words(root_cause)
    if not a or not b:
        return False
    return len(a & b) / min(len(a), len(b)) >= 0.34


def resolved(cases: Iterable[Dict[str, Any]]) -> List[Dict[str, Any]]:
    return [c for c in cases if c.get("status") == "RESOLVED" and c.get("entry_type", "CASE") == "CASE"]


def catches(spec: Dict[str, Any], case: Dict[str, Any], metrics: List[str]) -> bool:
    """Would this skill's trigger fire for the alarm this case was opened for?"""
    trig = spec["trigger"]
    if trig["machines"] and case.get("machine_id") not in trig["machines"]:
        return False
    codes = {normalize_alarm_code(c) for c in trig["alarm_codes"]}
    case_codes = {normalize_alarm_code(c) for c in case.get("error_codes") or []}
    if codes and case_codes:
        return bool(codes & case_codes)  # an alarm-code skill is judged on its own alarms only
    return bool(set(trig["metrics"]) & set(metrics))


def rank_of(spec: Dict[str, Any], case: Dict[str, Any]) -> Optional[int]:
    """Position (0 = first) of the skill's cause that explains this case, or None."""
    for n, c in enumerate(spec.get("root_causes", [])):
        if case.get("id") in (c.get("sources") or []) or cause_matches(c.get("cause", ""), case.get("root_cause", "")):
            return n
    return None


def backtest(spec: Dict[str, Any], cases: Iterable[Dict[str, Any]],
             case_metrics: Callable[[List[Dict[str, Any]]], List[str]], top: int = 3) -> Dict[str, Any]:
    """Each resolved case the trigger catches: did the skill list its real cause in the top `top`?"""
    rows = []
    for c in resolved(cases):
        if not catches(spec, c, case_metrics([c])):
            continue
        rank = rank_of(spec, c)
        rows.append({"case_id": c["id"], "machine_id": c.get("machine_id"), "root_cause": c.get("root_cause"),
                     "rank": None if rank is None else rank + 1, "hit": rank is not None and rank < top,
                     "cited": any(c["id"] in (x.get("sources") or []) for x in spec.get("root_causes", []))})
    hits = sum(r["hit"] for r in rows)
    return {"total": len(rows), "hits": hits, "rows": rows,
            "ok": not rows or hits * 2 >= len(rows)}


def generated_reading(spec: Dict[str, Any], cases: Iterable[Dict[str, Any]],
                      case_metrics: Callable[[List[Dict[str, Any]]], List[str]],
                      machines: List[str]) -> Optional[Dict[str, Any]]:
    """A PLC reading the skill's trigger should catch: its machine, alarm code and signal."""
    trig = spec["trigger"]
    cited = {s for c in spec.get("root_causes", []) for s in c.get("sources") or []}
    source_cases = [c for c in cases if c.get("id") in cited]
    metric = next(iter(trig["metrics"]), None) or next(iter(case_metrics(source_cases)), None)
    if metric not in GENERATED_VALUES:
        metric = next((m for m in trig["metrics"] if m in GENERATED_VALUES), None) or "spindle_vibration_mm_per_s"
    machine = (next(iter(trig["machines"]), None)
               or next((c.get("machine_id") for c in source_cases if c.get("machine_id") in machines), None)
               or next(iter(machines), None))
    if not machine:
        return None
    code = next(iter(trig["alarm_codes"]), None)
    return {"machine_id": machine, "metric": metric, "value": GENERATED_VALUES[metric], "alarm_code": code,
            "alarm_text": spec.get("name") or code or metric}


def _group_key(case: Dict[str, Any]) -> Optional[str]:
    codes = case.get("error_codes") or []
    return normalize_alarm_code(codes[0]) if codes else None


def new_skill_proposals(cases: Iterable[Dict[str, Any]], specs: Dict[str, Dict[str, Any]],
                        case_metrics: Callable[[List[Dict[str, Any]]], List[str]],
                        dismissed: Iterable[str] = ()) -> List[Dict[str, Any]]:
    """Resolved cases sharing an alarm code that no installed skill catches → one proposal per code."""
    groups: Dict[str, List[Dict[str, Any]]] = {}
    for c in resolved(cases):
        key = _group_key(c)
        if not key:
            continue
        if any(catches(s, c, []) for s in specs.values()):
            continue
        groups.setdefault(key, []).append(c)
    skip = set(dismissed)
    out = []
    for key, group in sorted(groups.items(), key=lambda kv: (-len(kv[1]), kv[0])):
        if len(group) < MIN_CASES_FOR_PROPOSAL or f"new:{key}" in skip:
            continue
        group.sort(key=lambda c: c["id"])
        # Show the spelling without OCR slips (ALM-162, not ALM-I62).
        spellings = [c["error_codes"][0] for c in group]
        clean = next((x for x in spellings if re.sub(r"[^A-Z0-9]", "", x.upper()) == key), spellings[0])
        out.append({"id": f"new:{key}", "kind": "new", "alarm_code": clean,
                    "case_ids": [c["id"] for c in group], "machines": sorted({c.get("machine_id") for c in group}),
                    "metrics": case_metrics(group),
                    "summary": f"{len(group)} kasus selesai dengan kode {clean} "
                               f"belum punya skill: {', '.join(c['id'] for c in group)}."})
    return out


def feedback_for(case: Dict[str, Any], skill_id: str, spec: Dict[str, Any]) -> Dict[str, Any]:
    """What a resolved repair says about the skill that handled its alarm."""
    rank = rank_of(spec, case)
    return {"skill_id": skill_id, "skill_version": spec.get("version"), "case_id": case["id"],
            "machine_id": case.get("machine_id"), "root_cause": case.get("root_cause"),
            "rank": None if rank is None else rank + 1,
            "verdict": "confirmed" if rank == 0 else ("other_listed" if rank is not None else "new_cause")}


def change_proposals(feedback: Iterable[Dict[str, Any]], specs: Dict[str, Dict[str, Any]],
                     dismissed: Iterable[str] = ()) -> List[Dict[str, Any]]:
    """Learning loop: a repair whose cause the skill missed or ranked low → reorder or add; a trial confirmed → promote."""
    skip = set(dismissed)
    out: List[Dict[str, Any]] = []
    confirmed: Dict[str, List[str]] = {}
    for f in feedback:
        spec = specs.get(f["skill_id"])
        if spec is None or f.get("skill_version") != spec.get("version"):
            continue  # feedback on an older version was already folded in, or the skill is gone
        if f["verdict"] == "confirmed":
            confirmed.setdefault(f["skill_id"], []).append(f["case_id"])
            continue
        pid = f"change:{f['skill_id']}:{f['case_id']}"
        if pid in skip:
            continue
        if f["verdict"] == "new_cause":
            text = f"{f['case_id']} ditutup dengan penyebab yang belum ada di skill: {f['root_cause']}"
        else:
            text = f"{f['case_id']} ditutup dengan penyebab no. {f['rank']}; agent mengusulkan menaikkannya."
        out.append({"id": pid, "kind": "change", "skill_id": f["skill_id"], "name": spec.get("name"),
                    "case_id": f["case_id"], "verdict": f["verdict"], "root_cause": f["root_cause"],
                    "rank": f["rank"], "version": spec.get("version"), "next_version": bump(spec.get("version")),
                    "summary": text})
    for sid, case_ids in confirmed.items():
        spec = specs[sid]
        pid = f"promote:{sid}:{spec.get('version')}"
        if spec.get("status") == "trial" and len(case_ids) >= TRIAL_CONFIRMATIONS and pid not in skip:
            out.append({"id": pid, "kind": "promote", "skill_id": sid, "name": spec.get("name"),
                        "case_ids": case_ids, "version": spec.get("version"),
                        "summary": f"{len(case_ids)} perbaikan membuktikan penyebab no. 1. "
                                   "Agent mengusulkan status Aktif."})
    return out


def trial_progress(feedback: Iterable[Dict[str, Any]], skill_id: str, version: Any) -> Dict[str, Any]:
    ids = [f["case_id"] for f in feedback
           if f["skill_id"] == skill_id and f.get("skill_version") == version and f["verdict"] == "confirmed"]
    return {"confirmed": len(ids), "needed": TRIAL_CONFIRMATIONS, "case_ids": ids}


def apply_change(spec: Dict[str, Any], proposal: Dict[str, Any]) -> Dict[str, Any]:
    """The skill.json the change proposal leads to: the repair's cause moved (or added) to no. 1, version bumped."""
    new = {**spec, "root_causes": [dict(c) for c in spec.get("root_causes", [])],
           "version": proposal["next_version"]}
    causes = new["root_causes"]
    if proposal["verdict"] == "other_listed" and proposal.get("rank"):
        hit = causes.pop(proposal["rank"] - 1)
        hit["sources"] = list(dict.fromkeys([*hit.get("sources", []), proposal["case_id"]]))
        causes.insert(0, hit)
    else:
        causes.insert(0, {"cause": proposal["root_cause"], "evidence": "Dari perbaikan " + proposal["case_id"],
                          "sources": [proposal["case_id"]]})
    return new


def bump(version: Any) -> str:
    parts = str(version or "1.0.0").split(".")
    try:
        major, minor = int(parts[0]), int(parts[1]) if len(parts) > 1 else 0
    except ValueError:
        return "1.1.0"
    return f"{major}.{minor + 1}.0"
