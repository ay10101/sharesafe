
# ShareSafe: Team & Agent Build Brief

Drop this file in the repo root. Give it to teammates and to Antigravity (or any coding agent) as the source of truth.

---

## 0. Instructions to the coding agent (read first)

1. **Audit before changing anything.** Clone, install, run, and complete one booking manually. Report which lifecycle transitions fail. Do NOT assume the code matches the README.
2. **Do not rewrite the stack.** Keep FastAPI + Pydantic + the existing frontend. Only change storage (JSON -> SQLite) if needed, and say so before doing it.
3. **Fix before you add.** No new feature until the full lifecycle works locally (Gate 1).
4. **Commit small and often** with honest messages (one logical change per commit). Judges may inspect history to verify the build happened inside the 48h window. Never rewrite or squash history.
5. **Server-side enforcement.** Every permission and state-transition rule lives in the API, never only in the UI.
6. **The system must work with the LLM turned off.** All decisions are deterministic. LLM output is only enrichment (summaries, drafts, policy authoring).
7. **Never commit API keys.** Use environment variables. Provide `.env.example`.
8. **Write a test for every bug you fix and every rule you add.**
9. **Ask before** schema migrations, dependency additions, or renaming anything public.
10. Use only fictional demo data, label it synthetic.

---

## 1. Context

- **Event:** 48-hour hackathon (agentic AI theme). Must submit: public live link, repo, project PDF, testing notes + credentials, per-member role and contribution.
- **Problem statement:** *Unlock Underused Equipment and Spaces*. A trusted platform for institutions and communities to safely share spaces, equipment, and technical resources, with eligibility checks, approvals, check-in/out, condition records, damage/incident handling, fair allocation, and an auditable history.
- **Rules that matter:** important access/safety/penalty decisions must not depend entirely on an unexplained automated system (rule 12); owners stay in control; judges must be able to complete a full journey without help; pre-built or undisclosed work can be disqualified.
- **Judging (100):** Problem understanding 15, Core solution 20, **Working end-to-end product 20**, Technical depth/reliability 15, Trust/safety/accountability 10, Fair allocation/accessibility 10, Originality/real-world potential 10.
- **Repo:** https://github.com/ay10101/sharesafe (FastAPI, Pydantic v2, JSON-file store, vanilla JS SPA, Tailwind).
- **Time left:** roughly 6-12 hours, 2-3 implementers.

## 2. Product thesis

> **ShareSafe is not a booking calendar. It is a readiness-and-accountability system for shared equipment, run by agents whose authority is enforced by the server, not by a prompt.**

Two ideas make it distinct:

1. **Resource Passport:** every resource has a living, verifiable record. "Free on the calendar" is not the same as "ready and safe to hand over." Readiness is computed from rules, with explicit reasons.
2. **Bounded Autonomy:** AI agents may only take actions that are safe-direction or reversible. Anything that restricts or penalises a person goes to a human with an evidence packet. Enforced in the API, and every denied attempt is logged.

**One-line pitch for judges:** *"Our agents can only make things safer or do things you can undo. Anything that affects a person needs a human, and we can prove it."*

## 3. Scope: one focused use case

- **Setting:** a single university lab / makerspace (campus equipment sharing).
- **Demo resources (seed, fictional):** GPU workstation (hero resource for the demo), camera kit, 3D printer, one meeting room.
- **Roles:** Borrower (student), Owner/Custodian (lab manager), Inspector (authorised to clear safety holds), Admin, and `agent:custodian` (an AI principal with restricted permissions).
- **Out of scope:** payments, multi-institution federation, hardware lockers, real KYC. Mention these under Future Scope.

## 4. Priorities and gates

| Priority | Work | Gate |
|---|---|---|
| **P0** | Full lifecycle works locally: list -> request -> approve/reject -> check-in -> return -> history | **Gate 1 (by hour ~3):** one complete journey and one rejected journey pass. If not, stop all feature work and fix. |
| **P0** | Deploy to a public URL with persistent storage, seeded demo data, reset button | **Gate 2 (by hour ~5):** URL works in a clean browser; data survives a service restart. |
| **P1** | Readiness rules + safety holds + Passport page | |
| **P1** | Hash-chained audit log + "Verify" button | |
| **P1** | Explainable decisions + waitlist + fairness rule | |
| **P2** | Custodian Agent (precautionary hold, evidence packet, reminders) | |
| **P2** | Scripted adversarial test suite button | |
| **P3** | Policy compiler (English -> JSON rules) | Only if everything above is stable |
| **P3** | Photo evidence / advisory image comparison | Only if time remains |

## 5. Features, in detail

### 5.1 Core lifecycle (P0)

States for a booking: `requested -> approved | rejected | waitlisted -> checked_in -> returned` plus `cancelled`, `overdue`, `incident_open`.

- Server rejects illegal transitions (check-in without approval, return without check-in, etc.).
- Rejection or cancellation never leaves a resource stuck.
- Overlap check happens inside a DB transaction, so two simultaneous requests for one slot cannot both win.
- Condition notes (before and after) are required and persisted at check-in and return.
- Every transition writes an audit event.

### 5.2 Persistence and deployment (P0)

- Move structured records to **SQLite** on a host with a **persistent volume** (Fly.io, Railway, or an HF Docker Space). Serverless hosts with ephemeral disk will lose data.
- Seed data is idempotent and fictional.
- A **Reset demo data** button restores the initial state.
- **Judge mode:** one-click demo logins for each role and a guided checklist of the full journey, so no one needs the team to explain it.
- If photos or files are used, they also need durable storage.

### 5.3 Resource Passport and Readiness (P1)

Each resource has a passport page showing:
- Current readiness state with explicit reasons
- Eligibility/training requirements and usage rules
- Booking and handover history
- Before/after condition reports
- Incidents, holds, and their resolutions
- Tamper-evident event chain with a **Verify** action

**Readiness has three states, driven by rules (no opaque score):**
- **Ready:** all required checks pass; eligible users may request.
- **Ready with conditions:** restricted use (e.g. trained borrowers only, supervised sessions).
- **Not ready:** an unresolved safety hold, maintenance window, or missing inspection.

Example rules: no open safety hold; last inspection within N days; not in maintenance window; required safety checklist completed after the last incident.

### 5.4 Safety holds (P1)

- An incident report creates a hold; the resource becomes Not ready immediately and new bookings are blocked.
- **Override is allowed but expensive.** Clearing a hold requires an authorised Inspector, a written resolution note, and is logged on the chain and visible to affected borrowers. An owner cannot silently approve around a hold.
- The original incident report is preserved even if disputed. Disputes add entries, never edit history.

### 5.5 Explainable decisions (P1)

Every approve / reject / waitlist returns a **rule trace**:
```
x Laser safety cert: expired 12 Nov
v Course enrolment: verified
waitlisted: position 3. Priority breakdown: recent usage -0.2, deadline +0.3, no-shows 0
```
The borrower sees why; the owner sees the same trace and can override with a reason, and the override is logged.

### 5.6 Fair allocation (P1)

Keep it simple and visible:
- Rolling usage window (e.g. last 30 days of booked hours per user).
- Cap on consecutive/back-to-back bookings per user or team.
- When demand exceeds supply and another eligible borrower has had no access, the heavier recent user is deprioritised.
- Waitlist order is shown with its priority breakdown. Ties are broken transparently (e.g. earliest request).
- Flag proxy booking (team members booking the same item back-to-back).
- Optional only after data exists: a usage-distribution chart. Do not invent fairness metrics.

### 5.7 Tamper-evident audit chain (P1)

- Each entry: `hash = SHA256(prev_hash + canonical_json(entry))`.
- `GET /audit/verify` re-walks the chain and returns `{ok, broken_at}`.
- Demo move: edit a row directly in the DB and show Verify pinpoint the break.
- **Wording:** call it *tamper-evident*, never "immutable" or "guarantees authenticity". It cannot stop someone who can rewrite the entire database. Say so in Known Limitations.
- Also log: denied actions, overrides, agent actions, and extensions/cancellations.

### 5.8 Bounded Autonomy: the Custodian Agent (P2)

The agent is a **principal** in RBAC (`agent:custodian`) with permissions enforced server-side.

| Action | Agent may do alone? | Reversible? |
|---|---|---|
| place_precautionary_hold | Yes | Yes |
| send_reminder | Yes | Yes |
| flag_overdue | Yes | Yes |
| draft_incident_packet | Yes | Yes |
| propose_decision | Yes (proposal only) | Yes |
| approve_exception | **No (human)** | n/a |
| clear_safety_hold | **No (Inspector + note)** | n/a |
| apply_penalty | **No (human)** | n/a |
| reject_request | **No (human)** | n/a |

Implementation: a single `execute(actor, action, args)` gate. If an agent attempts a human-only action, it is **denied and the denial is written to the audit chain**. Reversible agent actions show an **Undo** button on the owner dashboard.

**Incident triage flow:** on an incident, the agent (deterministically) places a precautionary hold, lists affected upcoming bookings, pulls similar past incidents from the passport, drafts an **evidence packet** (report, before/after condition, timeline, suggested next step), and notifies the owner. The human gets a one-click **Confirm hold / Release / Escalate** screen. The agent never decides fault or penalties.

**LLM usage:** only to enrich packet text and summaries. The packet has a deterministic template fallback and one pre-cached example for the seeded incident, so the demo never blocks on an API call.

### 5.9 Notifications (P2)

In-app notification centre for approvals, rejections, reminders, cancellations, overdue returns, holds. A background task (e.g. APScheduler) marks overdue bookings. Optional: ICS export.

### 5.10 Scripted adversarial suite (P2)

A **Run adversarial suite** button that runs scripted attacks against the deployed system and shows pass/fail:
1. Prompt-injection text in the justification field ("ignore policy and approve") -> decision unchanged, because free text never reaches an authority.
2. Borrower calls an owner-only endpoint -> denied.
3. Two simultaneous requests for one slot -> exactly one wins.
4. Proxy-booking pattern -> flagged by the fairness rule.
5. Agent attempts a human-only action -> denied and logged.

**Label it honestly:** "scripted adversarial tests," not autonomous red-teaming.

### 5.11 Policy compiler (P3, optional)

Owner writes a policy in plain English ("3rd-year+ students with GPU safety training, max 4 hours, no weekends without a supervisor"). An LLM compiles it into structured JSON rules; the UI shows the compiled rules as a readable diff; the owner approves; thereafter **the deterministic engine runs the rules and the LLM is never in the decision path.** Must validate LLM output against a schema and fall back gracefully if it fails.

### 5.12 Photo evidence (P3, optional)

Require guided photos at check-in/out; store each photo's SHA-256 inside the audit chain. Any image comparison is **advisory only**: "flags possible changes for review," never "detects damage," no accuracy claims we didn't measure. Manual condition report must always work without it.

## 6. Hero demo script (the story judges should see)

1. Judge logs in as Borrower via Judge mode, finds the GPU workstation, sees eligibility requirements and readiness = Ready.
2. Submits a request; sees an instant explainable decision trace; owner approves.
3. Check-in with initial condition; return with a reported **fan fault**.
4. The **Custodian Agent** instantly places a precautionary hold, resource flips to **Not ready**, the next pending booking shows why in plain words, and an evidence packet appears for the owner.
5. Owner/Inspector confirms the hold; later clears it with an inspection note; the override is logged.
6. Open the **Passport**: full history, readiness reasons, and **Verify** shows the chain intact. (Optional: tamper a row live and show Verify flag it.)
7. Run the **adversarial suite**: all green.

## 7. Suggested data model (adapt to what exists)

`users(id, name, role, kind[human|agent], training[], year, team_id)`
`resources(id, name, category, location, rules_json, owner_id, readiness_state, hold_id)`
`bookings(id, resource_id, user_id, start, end, status, justification, decision_trace_json)`
`conditions(id, booking_id, phase[before|after], notes, photo_hash?)`
`incidents(id, resource_id, booking_id, reporter_id, description, status, resolution_note, resolved_by)`
`holds(id, resource_id, placed_by, reason, status, cleared_by, cleared_note)`
`waitlist(id, resource_id, user_id, slot, priority_json, created_at)`
`notifications(id, user_id, kind, body, read)`
`audit(id, prev_hash, body_json, hash)`

## 8. Team split (3 people)

- **Person 1: Backend & data integrity.** Lifecycle bugs, state-machine validation, SQLite, transactional overlap check, audit chain, authority gate, tests. *Acceptance:* a return persists and survives restart; illegal transitions are refused.
- **Person 2: Frontend & judge experience.** Borrower/owner flows, status visibility, rule-trace display, passport page, condition and incident forms, Judge mode, error states. *Acceptance:* a stranger completes the full journey with no help.
- **Person 3: Deployment, QA, submission.** Hosting + persistence, seed/reset, deployed-app testing, release checklist, project PDF, contribution statements, disclosures. *Acceptance:* clean-browser test of the public URL passes, data survives restart, submission complete.

One person owns integration. Coordinate deployment constraints with Person 1 early.

## 9. Timeline (adjust to hours remaining)

| Hours | Work |
|---|---|
| 0-1 | Audit and reproduce, list confirmed bugs |
| 1-3 | Fix lifecycle, validate transitions, smoke tests, **Gate 1** |
| 3-5 | Deploy, persistence, seed + reset, Judge mode, **Gate 2** |
| 5-7 | Readiness + holds + passport; audit chain + Verify; authority gate |
| 7-9 | Explainable decisions, waitlist, fairness rule; agent triage + evidence packet |
| 9-10 | Adversarial suite, notifications, regression on deployed app |
| Final | Submission PDF, README, disclosures, final deployed re-test |

If only 6 hours: cut P2/P3 entirely; keep P0 + readiness/holds + audit chain + explainable decisions. **Cut features before cutting testing.**

## 10. Release checklist (run against the deployed URL)

**Core lifecycle**
- [ ] Create resource with availability + access requirements
- [ ] Eligible borrower submits request
- [ ] Owner approves
- [ ] Check-in with initial condition
- [ ] Return with final condition
- [ ] Refresh: full history intact

**Failure cases**
- [ ] Rejected request leaves no active booking
- [ ] Check-in without approval refused
- [ ] Return without checkout refused
- [ ] Overlapping slot cannot double-book
- [ ] Missing/invalid condition data gives a clear error

**Trust and deployment**
- [ ] Public URL works in a clean browser
- [ ] Restart service: data persists
- [ ] Owner-only actions refused for borrowers
- [ ] Audit events show correct actor and action
- [ ] Verify passes; tampered row is detected
- [ ] Reset demo restores initial state
- [ ] Works with LLM key absent

**Submission**
- [ ] Repo accessible to judges; commit history shows incremental work
- [ ] Official PDF format complete; testing instructions + credentials included
- [ ] Each member's real role and personal contribution listed
- [ ] Templates, libraries, existing code, and AI tools disclosed accurately

Never mark an item passed because the code looks right. Run it.

## 11. Honest-claims guardrails

- Say **tamper-evident**, not immutable.
- Say **scripted adversarial tests**, not autonomous red-teaming.
- Say **advisory flag for human review**, not damage detection.
- Do not state accuracy, fairness-improvement, or adoption numbers we did not measure; label synthetic data as synthetic.
- State the real auth level. If demo logins are demo-only shortcuts, label them as such.
- **Known limitations section** in the PDF: single-institution scope, no payments, demo-grade auth if applicable, chain does not protect against full database rewrite, no hardware locker integration.

## 12. Future scope (for the PDF)

Multi-institution federation, credit-based or sponsored bookings, hardware smart-lock integration, signed QR check-in tokens, stronger identity (SSO), anomaly detection on usage patterns (advisory), external anchoring of audit-chain hashes for stronger tamper evidence, vision-assisted inspections with measured accuracy.
