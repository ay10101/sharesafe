# ShareSafe P4 - AI/ML #2 Development Notes

## Owner: P4 (AI/ML #2)
**Brief**: Custodian Agent, LLM layer, Evidence Packet, Policy Compiler, Adversarial Suite.

---

## 1. What was Built & Delivered

### A. LLM Wrapper (`app/ai/llm.py`)
- Resilient `complete(prompt, system=None, pydantic_model=None, json_schema=None, timeout=8) -> LLMResult | None`.
- **Zero failure propagation**: Returns `None` if keys are missing, invalid, or during timeouts/rate-limits.
- **Untrusted User Input Guard**: `wrap_untrusted_input()` wraps untrusted user strings in clear data delimiters preventing prompt injection.
- **Caching**: SHA-256 prompt hashing cache + pre-cached responses for hero demo (fan fault incident and GPU safety policy).
- **Audit Logging**: `LLM_CALL_LOG` records model, provider, cache hit, and timestamp without sensitive keys or prompts.

### B. Custodian Agent (`app/agent/custodian.py`)
- **Bounded Autonomy**: Acts exclusively through `app.core.authority.execute()`. No direct database writes or unauthorized actions.
- `on_incident(incident_id)`:
  1. Executes `place_precautionary_hold` on affected resource.
  2. Identifies upcoming bookings affected.
  3. Finds similar historical incidents via deterministic keyword matching.
  4. Executes `draft_incident_packet` with baseline timeline & suggested actions.
  5. Executes `send_reminder` alerting owner and inspector.
- Additional reversible agent routines: `flag_overdue()`, `send_reminder()`, `propose_decision()`.
- **Zero human-overreach**: Has no code paths to clear holds, apply penalties, or reject requests.

### C. Evidence Packet Builder (`app/agent/packet.py`)
- Deterministic template baseline (`build_packet`) compiles timeline, affected bookings, and safety next steps.
- Optional LLM summary enrichment (`enrich_packet`) with fallback to `generated_by="template"` if offline.
- Serves `GET /api/incidents/{id}/packet`.

### D. Scripted Adversarial Suite (`app/adversarial/suite.py`)
- 7 scripted attacks against system boundaries:
  1. `prompt_injection_resistance`: Malicious justification cannot alter verdict.
  2. `role_escalation_guard`: Borrower blocked from inspector endpoints (403).
  3. `concurrency_race_condition`: Simultaneous requests guarantee exactly one succeeds.
  4. `proxy_booking_detection`: Coordinated back-to-back bookings flagged.
  5. `agent_overreach_boundary`: Agent attempts to clear holds / penalize are blocked with `PermissionError` and logged as `DENIED:`.
  6. `hold_bypass_prevention`: Approval blocked on resources with active holds.
  7. `audit_tamper_detection`: Hash chain verifies integrity; detects altered rows.
- Serves `POST /api/admin/adversarial`.

### E. Policy Compiler (`app/agent/policy.py`)
- Compiles English policy ("3rd-year and above with gpu safety training, max 4 hours") to validated JSON schema (`app.engine.schema.ResourceRules`).
- Offline regex-based fallback compiler ensures functionality without LLM.
- Generates structured warnings for ambiguous terms or injection attempts.
- Serves `POST /api/policies/compile`.

---

## 2. Testing & Verification
- Unit & integration tests in `tests/`:
  - `tests/test_llm.py` (4 tests)
  - `tests/test_custodian.py` (4 tests)
  - `tests/test_adversarial.py` (1 test running all 7 scenarios)
  - `tests/test_policy.py` (5 tests)
  - `tests/test_router.py` (3 tests)
- **17 tests passing cleanly**.

---

## 3. Disclosures for Submission PDF
- **Where AI/LLMs are used**:
  - Evidence packet summary enrichment (`generated_by="llm"`).
  - Natural language policy drafting into `ResourceRules` format.
- **Where AI/LLMs are NEVER used**:
  - All access, eligibility, safety, holds, and penalty decisions.
  - Decision engine operates 100% deterministically.
- **Models / Providers**:
  - Google Gemini (`gemini-1.5-flash`) via `GEMINI_API_KEY`, OpenAI fallback (`gpt-4o-mini`) via `OPENAI_API_KEY`.
- **Fallback Architecture**:
  - Deterministic template-based packet generation.
  - Deterministic rule-based policy compiler.
  - Full application functionality is maintained with LLMs disabled.
- **Testing Methodology**:
  - Labelled strictly as **"scripted adversarial tests"** (not autonomous red-teaming).
