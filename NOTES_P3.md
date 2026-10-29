# ShareSafe P3 - AI/ML #1 Development Notes

## Owner: P3 (AI/ML #1: Decision + Fairness Engine)

## 1. What was Built & Verified

### A. Individual Rule Evaluator (`app/engine/rules.py`)
- Evaluates individual rules deterministically with plain-language student-actionable messages.
- Handled rules:
  - `readiness`: `ready` (INFO), `ready_with_conditions` (CONDITION), `not_ready` (BLOCK).
  - `min_year`: Enforces year threshold (BLOCK).
  - `required_training`: Lists exact missing certificates required (BLOCK).
  - `max_hours_per_booking`: Limits single session durations (BLOCK).
  - `min_notice_hours`: Enforces advance booking window (BLOCK).
  - `weekend_requires_supervisor`: Weekend sessions permitted under supervision condition (CONDITION).
  - `recent_usage_cap_hours_30d`: Rolling 30-day usage quota (BLOCK).
  - `max_consecutive_bookings`: Limits back-to-back user/team bookings (BLOCK).
  - `requires_inspection_within_days`: Verifies physical safety checks are current (BLOCK if overdue).

### B. Transparent Priority & Fairness Scorer (`app/engine/fairness.py`)
- Configurable weights dictionary `FAIRNESS_CONFIG` documented at the top of the module.
- Components in `PriorityBreakdown`:
  - `recent_usage`: Exponential decay weighting of hours used in last 30 days.
  - `no_shows`: Capped penalty.
  - `late_returns`: Capped penalty.
  - `deadline`: Structured academic deadline boost within 7 days.
  - `waiting_time`: Request seniority boost per 24 hours waited.
  - `equity_boost`: Anti-monopoly boost for zero-access borrowers.
- Tie-breaking: Earliest `created_at`, then user ID.
- Coordinated proxy & hoarding detection (`detect_proxy_patterns`):
  - Flags same-team back-to-back bookings.
  - Flags 4+ consecutive daily bookings by a single user.

### C. Decision Assembly (`app/engine/decision.py`)
- `evaluate_request(request, resource, user, history, competing, now, last_inspection_date) -> Decision`
- Pure function, deterministic, zero DB/network/LLM dependencies.
- **Untrusted User Input Guard**: `RequestCtx.justification` is never read or referenced.
- Ranks contenders for contested slots, returning `WAITLIST` with `waitlist_position` and `PriorityBreakdown`.
- Produces deterministic plain-language explanations.

### D. Synthetic Simulation & Measurement (`scripts/simulate_fairness.py`)
- **Synthetic Data**: 40 student users, 4 teams, 3 equipment resources, 4 weeks traffic.
- **Seeded RNG (Seed=42)**: Exact 100% reproducibility.
- **Results**:
  - Gini inequality reduced from **0.5965** (FCFS) down to **0.4676** (ShareSafe Engine).
  - Top 10% heavy user concentration dropped from **45.6%** down to **26.5%** (a 19.1 percentage-point drop in concentration).
  - Generated report at `docs/fairness_results.md`.

### E. Advisory Photo Comparison (Stretch: `app/vision/compare.py`)
- Computes SHA-256 hash of each uploaded photo for P2's audit chain.
- Lightweight perceptual block-intensity comparison per angle.
- Outputs score, changed regions, and plain-language advisory note.
- Never auto-penalizes. Discloses lighting/angle limitations.

### F. FastAPI Engine Router (`app/engine/router.py`)
- `POST /api/engine/evaluate`: API endpoint to evaluate requests against rules.
- `GET /api/engine/fairness/summary`: Returns live fairness metrics and weights configuration for dashboards.

---

## 2. Test Suite (`tests/`)
- All 18 tests passing:
  - `tests/test_engine.py`: 13 tests covering all rules, determinism, contested slots, proxy detection, and prompt injection resistance.
  - `tests/test_router.py`: 3 tests covering evaluate and fairness summary endpoints.
  - `tests/test_vision.py`: 2 tests covering photo hash and advisory comparison.
- Shared fixtures established in `tests/fixtures/engine_fixtures.py`.

---

## 3. Disclosures for Submission PDF
- **AI/LLM Usage**: **None**. All decisions and priority scores in the decision engine are 100% deterministic rule algorithms.
- **Fairness Metrics**: Explicitly labelled as **"synthetic data, simulation"** with fixed seed (42). No claims of measured real-world adoption.
- **Vision Comparison**: Explicitly labelled as **"advisory flag for human review"** (not damage detection).
