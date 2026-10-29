# NOTES: P2 — Trust Layer (Full-Stack Developer #2)

## Role & Ownership
- **Role:** P2 — Trust Layer & Bounded Autonomy
- **Branch:** `p2/trust-layer`
- **Files Owned & Created:**
  - `app/core/audit.py` (Tamper-evident SHA-256 hash chain)
  - `app/core/authority.py` (Table-driven authority matrix & bounded autonomy gate)
  - `app/core/readiness.py` (Deterministic readiness evaluation with plain-language reasons)
  - `app/core/holds.py` (Safety holds service, borrower notifications, blocking logic)
  - `app/core/scheduler.py` (Idempotent overdue checker and notification worker)
  - `app/routers/incidents.py` (`POST /api/incidents` for damage, safety, delay, dispute)
  - `app/routers/holds.py` (`GET /api/holds`, `POST /api/holds`, `/clear`, `/override`)
  - `app/routers/notifications.py` (`GET /api/notifications`, mark-read endpoints)
  - `app/routers/audit.py` (`GET /api/audit/verify`, `GET /api/audit`, `POST /api/audit/tamper`)
  - `static/passport.html` & `static/passport.js` (Resource Trust Passport SPA)
  - `tests/test_trust_audit.py` (18 integrity tests)
  - `tests/test_trust_authority.py` (9 authority tests)
  - `tests/test_trust_readiness.py` (5 readiness tests)
  - `tests/test_trust_incidents_holds.py` (4 hold/incident/overdue tests)
  - `tests/test_trust_api_endpoints.py` (3 API integration tests)
  - `tests/test_trust_hero_demo.py` (1 complete 19-step hero demo journey test)

---

## 1. What Was Implemented

### Task 1 — Tamper-Evident Audit Chain (`app/core/audit.py`)
- Cryptographic hash chaining: `H = SHA256(prev_hash + canonical_json(body))`.
- Genesis anchor: `prev_hash = "GENESIS"`.
- Canonical JSON using `json.dumps(body, sort_keys=True, separators=(",", ":"))`.
- `append_audit(actor_id, action, payload) -> str`: Appends entry, calculates hash, and records UTC ISO timestamp.
- `verify_chain() -> {"ok": bool, "broken_at": int | None, "checked": int}`:
  - Detects modified body, modified hash, deleted middle entry, and broken previous-hash relationships.
  - Returns the exact 0-indexed position of any tampering.
- Abstract storage architecture:
  - `InMemoryAuditStore`: for isolated test fixtures.
  - `SQLiteAuditStore`: persists entries directly to the `audit` database table.
  - Transparent active connection tracking on the executing thread prevents same-thread SQLite locks during atomic transactions.
- Filtered query support: `get_audit_trail(resource_id=..., booking_id=...)`.
- Live demonstration simulation: `simulate_tamper(index, field, value)` allows judges to see tamper detection live.
- Terminology: Consistently uses **"tamper-evident"**, never "immutable".

### Task 2 — Authority Gate & Bounded Autonomy (`app/core/authority.py`)
- Enforces the core product principle: *Agents can only make things safer or do reversible actions. Actions that restrict or penalize a human require a human.*
- Table-driven matrix `AUTHORITY`:
  - Reversible/Agent allowed: `place_precautionary_hold`, `send_reminder`, `flag_overdue`, `draft_incident_packet`, `propose_decision`.
  - Human-only (strictly forbidden to agents): `approve_exception`, `clear_safety_hold`, `apply_penalty`, `reject_request`.
- Server-side role enforcement:
  - `clear_safety_hold` strictly requires an authorized Inspector or Admin AND a non-empty written resolution note.
  - Borrowers are denied from clearing holds.
- Server-side audit logging of all attempts:
  - Approved executions log the event and payload.
  - Denied attempts log an audit event prefixed with `DENIED:<action>` (e.g. `DENIED:clear_safety_hold`) **before** raising `PermissionError`.
- Reversible action tool registry and undo engine:
  - Reversible actions maintain an execution checkpoint keyed by audit hash.
  - `undo(actor, audit_id)` restores previous state and writes an `undo_<action>` audit event.
  - Non-reversible actions cannot be undone.

### Task 3 — Resource Readiness Engine (`app/core/readiness.py`)
- Evaluates equipment operational status deterministically into:
  - `READY`: nominal condition, no blocks or conditions.
  - `READY_WITH_CONDITIONS`: non-blocking requirements (e.g. weekend supervision, required training prerequisites, restricted materials).
  - `NOT_READY`: blocking safety failures (active safety hold, scheduled maintenance window, overdue periodic inspection).
- Plain-language explanations: Returns explicit `RuleResult` objects with human-readable messages (e.g. `Open safety hold: Fan fault reported during return`). Never just a raw boolean.

### Task 4 & 5 — Safety Holds & Incidents (`app/core/holds.py`, `app/routers/incidents.py`, `app/routers/holds.py`)
- Incidents: `POST /api/incidents` supporting `damage`, `safety`, `delay`, and `dispute`.
- Original reports are never overwritten. Disputes create distinct follow-up entries.
- For safety and damage reports:
  - Calls custodian agent hook `app.agent.custodian.on_incident(incident_id)` with safe try/except fallback.
  - Triggers automated placement of precautionary safety hold.
  - Flips resource status and readiness to `not_ready`.
  - Automatically identifies all upcoming bookings on the resource, transitions them to `blocked_by_hold`, and notifies borrowers.
  - Logs audit events for every transition.
- Emergency override process: Named inspector, written reason, audit entry, and borrower notification.

### Task 6 — Notifications (`app/routers/notifications.py`)
- `GET /api/notifications` retrieves notifications chronologically for the active user.
- Supports marking notifications as read individually (`PATCH /api/notifications/{id}/read`) or in bulk (`POST /api/notifications/read-all`).
- Events handled: hold placed, hold cleared, loan overdue, reminder.

### Task 7 — Overdue Scheduler (`app/core/scheduler.py`)
- Scans for checked-in equipment loans past their scheduled return window.
- Idempotently marks loans as `overdue`, sets `returned_late = 1`, dispatches borrower notifications, and logs `flag_overdue` audit events.
- Background monitoring daemon with graceful start/stop.
- Manual trigger available via function/endpoint for hackathon judging demo.

### Task 8 — Resource Trust Passport UI (`static/passport.html`, `static/passport.js`)
- Standalone SPA matching existing Tailwind and typography design tokens.
- Displays comprehensive resource trust passport:
  1. Header with live status, bay tag, item code, and big Readiness Badge (`READY`, `READY WITH CONDITIONS`, `NOT READY`).
  2. List of plain-language readiness rule results.
  3. Eligibility and access policies (min academic year, required safety training, max hours, notice, weekend policy).
  4. Active and cleared safety holds with inspector notes.
  5. Incident and dispute log with permanent original reports.
  6. Usage and handover history with pre/post condition inspection notes.
  7. Cryptographic Tamper-Evident Audit Chain block timeline.
  8. **Verify Chain** button calling `/api/audit/verify` with instant visual green confirmation (`✓ Audit chain verified`) or red pinpointing (`✗ Audit chain broken at entry #`).
  9. **Simulate Tamper** button (demo helper) allowing judges to observe live detection of data modification.
  10. Judge Mode persona switcher (Maya, Marcus, Dr. Thorne, Sarah Admin).

---

## 2. Test Verification

All 45 tests across the entire repository pass with zero errors:
```
tests/test_api_lifecycle.py::test_api_full_journey_and_rbac PASSED       [  2%]
tests/test_lifecycle.py::test_complete_journey PASSED                    [  4%]
tests/test_lifecycle.py::test_rejected_journey PASSED                    [  6%]
tests/test_lifecycle.py::test_illegal_transitions_and_guards PASSED      [  8%]
tests/test_lifecycle.py::test_concurrent_double_booking_race PASSED      [ 11%]
tests/test_trust_api_endpoints.py::test_safety_incident_api_and_hold_flow PASSED [ 13%]
tests/test_trust_api_endpoints.py::test_notifications_lifecycle PASSED   [ 15%]
tests/test_trust_api_endpoints.py::test_disputes_create_separate_incident_entries PASSED [ 17%]
tests/test_trust_audit.py (18 test cases parameterized in-memory & sqlite) ALL PASSED [ 57%]
tests/test_trust_authority.py (9 test cases covering authority matrix) ALL PASSED   [ 77%]
tests/test_trust_hero_demo.py::test_complete_hero_demo_journey PASSED    [ 80%]
tests/test_trust_incidents_holds.py (4 test cases) ALL PASSED           [ 88%]
tests/test_trust_readiness.py (5 test cases) ALL PASSED                 [100%]
======================= 45 passed in 1.42s =======================
```

---

## 3. What is Integrated
- All P2 core modules and routers operate cleanly against SQLite schema and contracts.
- `append_audit` is integrated with `lifecycle.py` and records transitions seamlessly without locking.
- Hero Demo 19-step journey executes end-to-end completely with LLM OFF.

---

## 4. What Remains Unfinished / Next Integration Steps
- P1 needs to register the P2 routers in `app/main.py` (see Section 6 below).
- P1 needs to link the Passport page from resource cards in `static/index.html`.

---

## 5. Assumptions
- SQLite is configured in WAL mode with Foreign Keys enabled.
- For reversible agent actions, reverse checkpoints are stored in the authority journal during process lifetime; persistent journal can be backed by a table in the future if required across restarts.
- Demonstration tamper button is provided for live hackathon presentation and should be restricted to administrators or judge mode.

---

## 6. Integration Points Needed from P1 / P3 / P4

### For P1 (`app/main.py`):
P1 owns `app/main.py`. The following changes should be merged by P1 to mount the P2 trust layer routes:
```python
# In app/main.py:
from app.routers.incidents import router as incidents_router
from app.routers.holds import router as holds_router
from app.routers.notifications import router as notifications_router
from app.routers.audit import router as audit_router
from app.core.scheduler import start_overdue_scheduler, check_overdue_bookings

app.include_router(incidents_router)
app.include_router(holds_router)
app.include_router(notifications_router)
app.include_router(audit_router)

@app.get("/passport")
async def serve_passport():
    return FileResponse(STATIC_DIR / "passport.html")

@app.post("/api/admin/check-overdue")
async def admin_check_overdue():
    return check_overdue_bookings()

@app.on_event("startup")
async def on_startup():
    start_overdue_scheduler(interval_seconds=60)
```

### For P3 (Decision Engine):
- P3 should read `resource.readiness` from `ResourceCtx`.
- When `readiness == "not_ready"`, requests should be blocked or rejected with the explicit reason provided by `evaluate_readiness`.

### For P4 (Agent / LLM Layer):
- Custodian Agent should call `app.core.authority.execute(actor, action, args)` for all tool invocations.
- Custodian Agent should implement `on_incident(incident_id, resource_id, context)` to assemble evidence packets when safety or damage incidents occur.
- Free-text descriptions remain untrusted data for display and evidence only; never inputs to state transitions.
