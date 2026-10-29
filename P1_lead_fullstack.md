# ShareSafe: shared context (identical in all four briefs)

## What we're building
Hackathon: 48-hour build, agentic AI theme. Problem statement: *Unlock Underused Equipment and Spaces* (trusted platform to share underused spaces/equipment with eligibility checks, approvals, check-in/out, condition records, incident handling, fair allocation, auditable history).
Repo (already partly built, we build ON TOP of it): https://github.com/ay10101/sharesafe
Existing stack: FastAPI, Pydantic v2, JSON-file store (being moved to SQLite), vanilla JS SPA, Tailwind. Do NOT rewrite the stack.

Judging (100): Problem understanding 15, Core solution 20, **Working end-to-end product 20**, Technical depth/reliability 15, Trust/safety/accountability 10, Fair allocation/accessibility 10, Originality 10.
Rules that matter: important access/safety/penalty decisions must not depend entirely on an unexplained automated system; owners stay in control; judges must complete a full journey with no help; undisclosed pre-built work or AI use can disqualify us.

## Product thesis
ShareSafe is a **readiness-and-accountability system for shared equipment**, not a booking calendar.
1. **Resource Passport:** every resource has a verifiable history. Free on the calendar != ready and safe. Readiness = Ready / Ready with conditions / Not ready, always with explicit reasons.
2. **Bounded Autonomy:** AI agents may only take actions that are safe-direction or reversible. Anything that restricts or penalises a person goes to a human with an evidence packet. Enforced server-side; denied attempts are logged.
Pitch: "Our agents can only make things safer or do things you can undo. Anything that affects a person needs a human, and we can prove it."

## Team and ownership (file ownership prevents merge conflicts)
| Person | Role | Owns (only they edit these; others open a PR or ask) |
|---|---|---|
| P1 | Lead full-stack, owns main repo, merges everything | app/main.py, app/db/*, app/auth/*, app/core/lifecycle.py, app/contracts.py, deploy files, seed/reset, static/ app shell + Judge mode, tests/test_lifecycle* |
| P2 | Full-stack #2: trust layer | app/core/audit.py, app/core/authority.py, app/core/readiness.py, app/routers/incidents.py, holds, notifications, scheduler, static/passport.*, tests/test_trust* |
| P3 | AI/ML #1: decision + fairness engine | app/engine/*, scripts/simulate_fairness.py, app/vision/* (stretch), tests/test_engine* |
| P4 | AI/ML #2: agent + LLM layer + adversarial | app/agent/*, app/ai/*, app/adversarial/*, tests/test_agent*, tests/test_adversarial* |

Git rules: each person works on a feature branch (`p2/audit-chain`, `p3/engine`, ...), small commits with honest messages, PR into main, P1 merges. NEVER squash or rewrite history (judges may inspect it to verify the work happened in the 48h window). Each person's contribution must be visible in their own commits (certificates list personal contribution).

## Hard rules for every agent
1. Audit existing code before changing it; don't assume README matches reality.
2. Server-side enforcement for every permission/state rule; UI is never the only guard.
3. **Everything must work with the LLM turned off.** LLM output is enrichment only (summaries, drafts, policy authoring). Never in a decision path.
4. Never commit API keys; use env vars + `.env.example`.
5. Free-text user input (justification, incident description) is UNTRUSTED. It is never used by the decision engine and never allowed to trigger an action. If shown to an LLM it is wrapped as quoted data and the output is display-only.
6. Write a test for every rule and every bug fix. Run the tests before every PR.
7. Fictional demo data only; label synthetic data as synthetic.
8. Honest claims: "tamper-evident" (not immutable), "advisory flag for human review" (not damage detection), "scripted adversarial tests" (not autonomous red-teaming). No invented accuracy/fairness numbers; only numbers we measure, labelled with their data source.
9. Keep a short `NOTES_<yourname>.md` of what you built and what's unfinished (feeds the submission PDF).
10. Ask before adding dependencies or changing shared contracts.

## SHARED CONTRACTS (P1 commits `app/contracts.py` in the first 30 minutes; nobody changes it without telling everyone)
Everyone codes against this from minute 0, using fixtures and mocks until real integration at Gate 1.

```python
# app/contracts.py
from __future__ import annotations
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Optional

# ---------- decisions (produced by app/engine, P3) ----------
class Verdict(str, Enum):
    RECOMMEND_APPROVE = "recommend_approve"
    RECOMMEND_REJECT = "recommend_reject"
    WAITLIST = "waitlist"
    NEEDS_HUMAN = "needs_human"

class Severity(str, Enum):
    BLOCK = "block"; CONDITION = "condition"; INFO = "info"

@dataclass
class RuleResult:
    rule_id: str
    passed: bool
    severity: Severity
    message: str                      # plain language, shown to the borrower

@dataclass
class PriorityBreakdown:
    total: float
    components: dict[str, float]      # {"recent_usage": -0.2, "deadline": 0.3, "no_shows": 0.0}
    tiebreak: str                     # "earliest_request"

@dataclass
class Decision:
    verdict: Verdict
    rule_results: list[RuleResult]
    priority: Optional[PriorityBreakdown] = None
    waitlist_position: Optional[int] = None
    explanation: str = ""             # deterministic plain-language summary

@dataclass
class UserCtx:
    id: str; role: str; year: int; training: list[str]; team_id: Optional[str] = None

@dataclass
class ResourceCtx:
    id: str; category: str; rules: dict; readiness: str   # "ready" | "ready_with_conditions" | "not_ready"

@dataclass
class BookingCtx:
    id: str; resource_id: str; user_id: str; team_id: Optional[str]
    start: datetime; end: datetime; status: str; created_at: datetime
    returned_late: bool = False; no_show: bool = False

@dataclass
class RequestCtx:
    user: UserCtx; resource_id: str; start: datetime; end: datetime; created_at: datetime
    deadline: Optional[datetime] = None
    justification: str = ""           # UNTRUSTED free text. Engine must NEVER read it.

@dataclass
class Flag:
    kind: str                         # "proxy_booking" | "hoarding" | ...
    user_ids: list[str]; booking_ids: list[str]; reason: str

# ---------- readiness (app/core/readiness.py, P2) ----------
class Readiness(str, Enum):
    READY = "ready"; CONDITIONS = "ready_with_conditions"; NOT_READY = "not_ready"

@dataclass
class ReadinessResult:
    state: Readiness
    reasons: list[RuleResult]

# ---------- authority gate (app/core/authority.py, P2) ----------
@dataclass
class Actor:
    id: str; kind: str; role: str     # kind: "human" | "agent"

# execute(actor: Actor, action: str, args: dict) -> dict
#   raises PermissionError if an agent attempts a human-only action; the denial is written to the audit chain.
# append_audit(actor_id: str, action: str, payload: dict) -> str   # returns hash   (app/core/audit.py, P2)

# ---------- evidence packet (app/agent, P4) ----------
@dataclass
class EvidencePacket:
    incident_id: str; resource_id: str; summary: str
    timeline: list[dict]; affected_bookings: list[str]; similar_incidents: list[str]
    suggested_next_step: str
    generated_by: str                 # "template" | "llm"

# ---------- policy compiler (app/agent/policy.py, P4) ----------
@dataclass
class CompiledPolicy:
    rules: dict                       # must validate against RESOURCE_RULES_SCHEMA below
    warnings: list[str] = field(default_factory=list)
```

### Resource rules schema (the ONE format both the engine and the policy compiler use)
Stored as JSON in `resources.rules`. All keys optional.
```json
{
  "min_year": 3,
  "required_training": ["gpu_safety"],
  "max_hours_per_booking": 4,
  "min_notice_hours": 2,
  "weekend_requires_supervisor": true,
  "max_consecutive_bookings": 2,
  "recent_usage_cap_hours_30d": 20,
  "requires_inspection_within_days": 30
}
```

### Authority matrix (enforced in `execute()`, P2)
| Action | Agent alone? | Reversible? |
|---|---|---|
| place_precautionary_hold | Yes | Yes |
| send_reminder | Yes | Yes |
| flag_overdue | Yes | Yes |
| draft_incident_packet | Yes | Yes |
| propose_decision | Yes (proposal only) | Yes |
| approve_exception | NO (human) | n/a |
| clear_safety_hold | NO (Inspector + written note) | n/a |
| apply_penalty | NO (human) | n/a |
| reject_request | NO (human) | n/a |

## Hero demo (what the judge sees; every role contributes to it)
1. Borrower logs in via Judge mode, finds the GPU workstation, sees eligibility rules and readiness.
2. Submits request, gets an explainable rule trace; owner approves (human).
3. Check-in with condition notes; return reporting a **fan fault**.
4. **Custodian Agent** instantly places a precautionary hold; resource flips to Not ready; next pending booking shows why in plain words; evidence packet appears for the owner.
5. Inspector confirms the hold, later clears it with a note; override logged and visible to affected borrowers.
6. Passport page: full history, readiness reasons, **Verify** shows chain intact (optional live tamper demo).
7. **Run adversarial suite**: all green.

## Timeline and gates (adjust to hours left)
| Hours | Focus |
|---|---|
| 0-0.5 | P1 commits contracts.py + stubs. Everyone branches. |
| 0-3 | P1 audits and fixes lifecycle. P2/P3/P4 build their modules standalone against contracts with fixtures and unit tests. |
| **Gate 1 (~h3)** | Full lifecycle works locally (one complete + one rejected journey). If not, STOP all feature merges until fixed. |
| 3-5 | P1 deploys (SQLite + persistent volume, seed, reset, Judge mode). **Gate 2 (~h5):** public URL works, data survives restart. P2/P3/P4 integrate their modules into the app via small PRs. |
| 5-9 | Hero demo wiring end to end. |
| 9-10 | Adversarial suite, regression on the deployed app, fix bugs. |
| Final | Submission PDF, README, disclosures, final deployed re-test. |
Cut features before cutting testing. A working deployed core beats clever features on localhost.

## Definition of done for any task
Code merged to main via PR, tests pass, works on the deployed URL (not just localhost), works with LLM off, audit event written for any state change, short note in your NOTES file.

---
# YOUR BRIEF: P1, Lead full-stack (owner of the main repo)

You are the integrator. The project lives or dies on whether a judge can complete the full journey on a public URL. Everything else is secondary.

## Your mission
Make the lifecycle work, deploy it with persistent storage, and integrate the other three people's modules without breaking main. You are also the merge gatekeeper.

## Step 0 (first 30 minutes, blocks everyone)
1. Clone the repo, run it, complete one booking manually, list exactly which transitions fail.
2. Create `app/contracts.py` from the SHARED CONTRACTS section above, verbatim. Commit and push to main immediately.
3. Add stub modules so others can import before real code exists:
   - `app/core/audit.py: append_audit()` writes to an in-memory list for now (P2 replaces it)
   - `app/core/authority.py: execute()` calls the tool directly for now (P2 replaces it)
   - `app/engine/decision.py: evaluate_request()` returns `Decision(Verdict.NEEDS_HUMAN, [])` (P3 replaces it)
   - `app/agent/custodian.py: on_incident()` no-op (P4 replaces it)
4. Tell the team on chat: "contracts + stubs are on main."

## Your deliverables, in order

### A. Stabilise the lifecycle (hours 0-3, Gate 1)
- Booking states: `requested -> approved | rejected | waitlisted -> checked_in -> returned`, plus `cancelled`, `overdue`, `incident_open`.
- Implement a single state machine in `app/core/lifecycle.py`. The server refuses illegal transitions (check-in without approval, return without check-in, double return, etc.).
- Fix any payload mismatches between frontend and API (known risk: return payload).
- Rejection and cancellation must never leave a resource stuck.
- Condition notes required at check-in and return; persisted.
- Overlap check inside one DB transaction so two simultaneous requests can't both win the slot.
- Call `append_audit(...)` on every transition (stub now, real later).
- Call `evaluate_request(...)` from the booking-request endpoint and store `Decision` JSON on the booking as `decision_trace` (stub returns NEEDS_HUMAN until P3 lands; owner approval stays human).
- Tests: `tests/test_lifecycle.py` covering one complete journey, one rejected journey, each illegal transition, and the double-booking race.
**Gate 1:** both journeys pass locally. Announce it. Until then, don't merge feature PRs from others.

### B. Real auth and RBAC
- Replace the profile-switcher-as-trust with real login (session/JWT) and server-side role checks on every endpoint. Roles: borrower, owner, inspector, admin, plus the agent principal `agent:custodian`.
- If any demo shortcut stays, label it "demo-only" in the UI and the PDF. Do not claim production-grade auth.

### C. Storage and deployment (hours 3-5, Gate 2)
- Move structured records to SQLite (SQLAlchemy or plain sqlite3; keep it simple). Define tables for users, resources, bookings, conditions, incidents, holds, waitlist, notifications, audit (see data model in the plan; P2 owns audit/holds/incidents table definitions, coordinate).
- Host with a **persistent volume** (Fly.io, Railway, or an HF Docker Space). NOT serverless/ephemeral disk. Add Dockerfile, env config, `.env.example`.
- Idempotent seed script with fictional data: GPU workstation (hero), camera kit, 3D printer, meeting room; users for each role; a couple of past bookings so fairness history exists.
- `POST /api/admin/reset-demo` + a visible Reset button.
- **Judge mode:** landing page with one-click login per role and a guided checklist of the full journey so a stranger needs no explanation.
**Gate 2:** public URL works in a clean browser; restart the service and verify bookings/history survive.

### D. Integration (hours 5-9)
Review and merge PRs from P2/P3/P4 in this order: P2 audit + authority + readiness, then P3 engine, then P4 agent. After each merge, rerun the lifecycle tests and the deployed smoke test. Wire:
- booking request -> readiness check (P2) -> decision engine (P3) -> stored trace -> owner confirms
- return with fault -> incident creation (P2 router) -> agent `on_incident` (P4) -> hold + packet
- frontend: rule-trace display on request result, waitlist position + priority breakdown, owner dashboard with Confirm/Release/Escalate and Undo for agent actions (coordinate UI components with P2's passport page).

### E. Submission support (final hours)
Release checklist run against the deployed URL, README, per-member contribution statements, disclosure of templates/libraries/AI tools. Keep the commit history clean and honest.

## Your acceptance tests
- A stranger completes the hero demo on the public URL with no help.
- Restart: data persists. Reset: restores seed state.
- Borrower calling an owner-only endpoint gets 403.
- Two simultaneous requests for one slot: exactly one wins.
- App fully works with the LLM key absent.

## Prompt to give your Antigravity agent
"Read this brief. First audit the repo: run it, complete one booking, and report which lifecycle steps fail before changing code. Then commit `app/contracts.py` and the stub modules exactly as specified, and work through sections A to C in order. Keep the FastAPI + vanilla JS stack, commit small, add tests for every fix. Do not start section D until Gate 1 passes. Ask me before adding dependencies or changing contracts."
