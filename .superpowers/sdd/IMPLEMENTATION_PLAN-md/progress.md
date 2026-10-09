# SDD ledger — plan: D:/repos/Sokrates/IMPLEMENTATION_PLAN.md

Task 1: complete (commits 0063ca1..4b5b2a0, Phase 1: correct money and demo story + Phase 2 foundation: skills loader)
- Removed max(…,0) rounding in solver
- Recommend by lowest total_cost_idr
- Added cost_breakdown to scenarios
- Rescaled Class A SLA penalty to Rp 20M/hour (capped at Rp 150M)
- Updated UI to show negative net_savings in red with +/- prefix
- Added CostBreakdown interface and cost_breakdown display
- Created skills/loader.py with SkillRegistry, Hook, SafetyInterlock classes
- Added safe condition parser (no eval()) for hooks.json and safety_interlocks.json
