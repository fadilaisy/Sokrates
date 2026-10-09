# SkillForge: Implementation Plan to Meet the Proposal

> **Branch:** `fix/proposal-gaps` (off `feat/supervisor-cockpit-ui`)
> **Goal:** the 3-minute demo in Proposal §8 runs exactly as written, and every claim a judge can check is true in the code.
> **Out of scope (roadmap):** OCR → RAG, real SAP OData sandbox, Bedrock/AWS migration.

---

## 1. Where we stand

Checked against `SkillForge_Proposal_Condensed_3.pdf` and a live run of the app on `feat/supervisor-cockpit-ui`.

| Proposal goal | Status | Evidence |
|---|---|---|
| Recommendation in < 60 s | ✅ Met | `/api/disrupt` returns 3 scenarios in ~0.3 s (without Gemini) |
| Supervised autonomy (analysis is read-only, write needs approval) | ✅ Met | Disrupt leaves SAP at v1; only `/api/approve` writes |
| Field-level diff at approval | ✅ Met | Approval modal shows order / machine / hours / cost per field |
| Re-validate before writeback, block on drift | ✅ Met | Optimistic lock → HTTP 409, rejection logged |
| Append-only hash-chained audit ledger | ✅ Met | `verify` returns `valid: true` |
| Bahasa Indonesia UI, live Gantt, live telemetry | ✅ Met | Cockpit + `/ws/telemetry` |
| Rupiah-priced scenarios with net saving | ⚠️ Wrong | Net saving rounded up to 0 (`max(…, 0)`); demo data makes "do nothing" the cheapest option |
| Declarative Skills authored by plant staff | ⚠️ Partial | Only `SKILL.md` text is generated; no rules or hooks, no approve-and-save step, Skill Studio not reachable in the new UI |
| Failure drill (second disruption mid-approval) | ⚠️ Faked | 409 only via `?drift` URL flag |
| Every read / decision / approval / write logged | ⚠️ Partial | Only approvals and rejections are logged |
| Skills drive behaviour (progressive disclosure) | ❌ Missing | Solver and telemetry code hardcode values that "mirror" the JSON files; editing a skill changes nothing |
| Three-tier constraint hierarchy (safety > quality > cost) | ❌ Missing | Solver ignores `safety_interlocks.json` |
| Skill linter, versioning | ❌ Missing | None |
| Ledger rollback reference | ❌ Missing | None |
| OCR → RAG for paper SOPs | ❌ Missing | Roadmap |
| SAP OData (PP/PM/MM) via BTP | ❌ Missing | JSON mock, production orders only. Roadmap |
| Bedrock (Claude Sonnet/Haiku) on AWS | ❌ Different | FastAPI + Gemini locally. Disclose in docs |

---

## Phase 1: Correct money and a demo story that holds up (~2–3 h)

**Problem:** `solver/schedule_solver.py` rounds losses up to zero in four places (`max(net_savings, 0)`). It then recommends the scenario with the highest `net_savings_idr`, so scenario A wins only because it comes first in the list. Demo penalties (Rp 3.15M) are smaller than the overtime costs, so the agent recommends doing nothing. That is the opposite of the proposal's "net saving Rp 46M" story.

**Changes**
1. **Solver** (`solver/schedule_solver.py`)
   - Remove the `max(…, 0)` rounding so `net_savings_idr` can be negative.
   - Recommend by lowest total cost (SLA + overtime + changeover), breaking ties by order-class priority.
   - Add `cost_breakdown: {sla, overtime, changeover, freight}` to each scenario.
2. **Demo data** (`erp_adapter/mock_sap_state.json`, `skills/cnc_milling/rules/sla_penalties.json`)
   - Rescale Class A to the proposal's range: about Rp 20M per hour, capped at Rp 150M. Scale Class B and C proportionately.
   - Target outcome for the default What-if: *"Skenario B biaya Rp 14 jt lembur, hindari denda Rp 60 jt, hemat bersih Rp 46 jt."*
3. **UI** (`frontend/src/cockpit/AgentArea.tsx:153-158`)
   - Show negative net savings in red with a minus sign.
   - Add a one-line rationale per scenario in the proposal's format.

**Done when**
- The default What-if recommends B or C with a positive net saving close to the proposal's example.
- No scenario shows a false Rp 0.

---

## Phase 2: Skill files drive the system (~4–5 h, the core innovation)

**Problem:** the solver constants and `_classify_telemetry` in `backend/main.py` are hardcoded copies of the skill files. `hooks.json` is never read.

**Changes**
1. **`skills/loader.py`: `SkillRegistry`**
   - At startup, index skill metadata only: name, machine types, hook IDs.
   - Load the full `rules/*.json` only when a disruption or hook needs that skill (progressive disclosure).
   - Record each load so the UI and ledger can show "skills loaded: cnc_milling / sla_penalties, safety_interlocks".
2. **Solver reads rules:** `ScheduleSolver.solve(state, disruption, rules)` takes penalty per hour, caps, overtime and changeover costs from `sla_penalties.json`.
3. **Constraint hierarchy in CP-SAT**
   - **Tier 1, safety (hard rule):** never assign to a machine in MAINTENANCE or FAULT, or under an active interlock.
   - **Tier 2, quality (hard rule):** a machine must be able to run that product. Add `capabilities` to each work center in the mock data.
   - **Tier 3, cost:** the existing objective.
   - A scenario that can't be built under these rules is returned as "tidak layak" (not feasible), never generated with a violation.
4. **Hook evaluator** (replaces `_classify_telemetry`)
   - Parse the `condition` strings from `hooks.json` and `safety_interlocks.json` (`metric > value`) with a safe parser, never `eval`.
   - Respect `debounce_seconds`.
   - Fixes the current `>=` vs `>` mismatch with the rules.
   - Emit `hook_fired` over the WebSocket. A breakdown-type hook automatically starts the disruption analysis, which is the proposal §4 flow.
5. **UI:** add a "Skill dimuat" (skill loaded) step to the agent area's detection steps, listing the files loaded.

**Done when**
- Editing `penalty_per_hour_idr` in the JSON changes scenario costs on the next run.
- Lowering the motor-temperature hook to 70 °C makes it fire on the next reading.
- A test proves the solver never assigns work to a machine in maintenance.

---

## Phase 3: Skill Studio, where plant staff write skills (~4–5 h)

**Problem:** `/api/skills/generate` only returns `SKILL.md` text. There are no rules or hooks, no approve-and-save step and no linter, and the new cockpit has no way to open it.

**Backend**
1. `POST /api/skills/draft`
   - Takes a guided interview (machine, failure modes, SLA classes, safety limits, in Bahasa Indonesia).
   - Gemini returns `SKILL.md`, `rules/*.json` and `hooks.json` as JSON, checked against a schema.
   - Falls back to a template when there is no Gemini key, so the demo can't break.
2. `POST /api/skills/lint`, the tier-hierarchy linter
   - Rejects a draft that raises a Tier 1 threshold, sets `override_allowed: true`, or removes a Tier 1 rule from the base skill.
   - A skill may **add** constraints, never **relax** a higher tier.
   - Errors are in Bahasa Indonesia.
3. `POST /api/skills/approve`
   - Writes the files to `skills/<id>/` with `skill_version`, keeping the previous version as `.v{n}`.
   - Reloads the registry and adds a ledger entry.

**Frontend**
1. Skill Studio overlay, opened from the sidebar in place of "Tab 1": interview → draft (tabs for SKILL.md / Rules / Hooks) → linter result → **"Setujui & aktifkan skill"** (approve and activate).
2. Delete the old `SkillStudio.tsx`, `LatticeLoader`, `ShapeWaves`, the old `Gantt` and `App.css`, plus the unused packages `gsap`, `ogl`, `vgpu`, `@fontsource-variable/geist` and `cn`.

**Done when**
- On stage, someone writes "jika getaran > 4 mm/s, beri peringatan" (warn if vibration exceeds 4 mm/s), approves it, and the next telemetry reading fires it, all in under 2 minutes.
- A draft that tries to raise SAFE-001 to 110 °C is blocked with a clear message.

---

## Phase 4: A real failure drill (~2 h)

**Problem:** the 409 path only appears when the `?drift` flag fakes a stale version.

**Changes**
1. `POST /api/disrupt/inject`
   - Applies a real second disruption to SAP state (for example, CNC-03 goes into FAULT) and bumps `sap_version`.
   - Logged to the ledger as `EXTERNAL_CHANGE`.
2. **Approval window, demo mode only:** a "Suntik gangguan kedua" button (inject second disruption).
   - Confirming then hits a real 409.
   - The UI re-runs `/api/disrupt` against the new state automatically.
   - It shows new scenarios with the banner "Dihitung ulang terhadap SAP v2" (recalculated against SAP v2).
   - The chat stays as an alternative path.
3. Remove the `?drift` hack.

**Done when**
- Inject → approve → 409 → new scenarios appear without the user doing anything.
- The ledger shows the external change, the rejected approval and the later approval, and the hash chain still verifies.

---

## Phase 5: Complete audit trail and real approver (~1.5 h)

1. **New ledger events:** `DISRUPTION_DETECTED`, `SKILLS_LOADED`, `SCENARIOS_GENERATED` (scenario costs and hashes) and `HOOK_FIRED`. Each approval references the scenario set it chose from (`decision_ref`).
2. **Rollback reference**
   - Each approved writeback stores the field values before the change.
   - `POST /api/ledger/{id}/revert` applies the reverse change through the same version check and approval step.
3. **Approver**
   - Supervisor picker in the top bar, using names from the SAP state, remembered in the browser.
   - Remove the hardcoded `APPROVER` (`frontend/src/cockpit/store.tsx:21`).
   - The backend rejects an empty `approved_by`.
4. **Ledger drawer:** filters by event type.

---

## Phase 6: Tests, cleanup, docs (~2–3 h)

1. **`tests/` with pytest and FastAPI's built-in test client** (no live server needed)
   - Solver: negative savings, the cheapest scenario is recommended, the Tier 1 rule holds, costs come from the JSON.
   - Hooks: condition parsing and debounce.
   - Linter: blocks a Tier 1 relaxation, allows a stricter rule.
   - API: disrupt → approve → ledger verify; inject → 409 with `receipt_id`; revert.
   - Delete `test_dummy.py` and the old `test_integration.py`, and add `pytest` and `httpx` to `requirements.txt`.
2. **GitHub Actions:** `npm ci && npm run build && npm run lint` plus `pytest`.
3. **Lint:** fix the two warnings in `frontend/src/cockpit/store.tsx`.
4. **UI tidy-up**
   - Add a "Data contoh" (sample data) label to the downtime and OEE panels.
   - Replace the dead Privacy/Terms links.
   - Calculate OEE availability from real disruption and maintenance hours if time allows.
5. **Docs**
   - Update `HANDOVER.md` and `DESIGN.md`: cockpit, locally bundled fonts, the skill pipeline.
   - Add a "Proposal → implementation" table that states the stack differences plainly.

---

## Schedule

| Day | Phases | Why this order |
|---|---|---|
| 1 | 1, 2 | Correct numbers, and skill-driven behaviour underpins everything after it |
| 2 | 3, 4 | The two most visible pieces on stage |
| 3 | 5, 6, rehearsal | Polish and tests; record the backup demo (Proposal §8) |

## Verification after every phase

- `npm run build`, `npm run lint`, `pytest`
- Headless-browser run: disruption → approve → ledger verify (plus the inject drill from Phase 4 on), with screenshots.

## Demo script this plan delivers (Proposal §8)

1. **Telemetry spike** on CNC-02 → a hook fires → *"Skill dimuat: cnc_milling"*.
2. **Three Rupiah-priced scenarios** in under 60 s; recommendation B or C with a positive net saving.
3. **Approval window** with the field-level diff → approve → SAP v1 → v2 → ledger entry.
4. **Failure drill:** inject a second disruption → approve → 409 blocked → automatic re-solve.
5. **Skill Studio:** a supervisor writes a new vibration rule in Bahasa Indonesia → linter passes → it fires live. An unsafe rule is shown being blocked.
6. **Ledger verify:** the whole chain is intact.

## Risks

| Risk | Mitigation |
|---|---|
| Gemini returns malformed skill JSON | Schema check, one retry, template fallback |
| Rescaled penalties look made up | Kept in `sla_penalties.json`, sourced to the proposal's Rp 60–150M range |
| Phase 3 runs long | Ship the linter and approve-and-save first; the interview can start as a single text box |
| No Gemini key at the venue | Every Gemini call already has a working fallback; keep it that way |

## Open decisions

- [ ] Demo date
- [ ] Approve rescaling the SLA penalties (Phase 1.2)
- [ ] Supervisor names for the approver picker (default: operator names in the mock SAP state)
