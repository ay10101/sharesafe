# ShareSafe Agent & LLM Layer (P4)

This module provides the Bounded Autonomy AI agent layer, Evidence Packet generation, Policy Compiler, and Scripted Adversarial Suite for ShareSafe.

## Architecture Principles
1. **Bounded Autonomy**: AI agents only execute safe-direction or reversible actions.
2. **Server-Enforced Authority Gate**: Every agent action is strictly routed through `app.core.authority.execute()`. Agents cannot directly alter database state or perform human-only operations (clearing holds, applying penalties, rejecting requests). Denials are recorded to the audit log.
3. **Deterministic Core / Zero-Dependency Fallback**: All system operations, incident holds, and evidence packets function 100% when LLM keys are absent, offline, or rate-limited.
4. **Untrusted Input Protection**: User justification and incident notes are wrapped as untrusted data tags and never ingested by the decision engine.

---

## Endpoints Provided / Available for Routing

### 1. Evidence Packet Endpoint
- **Path**: `GET /api/incidents/{id}/packet`
- **Description**: Returns the structured `EvidencePacket` for a given incident.
- **Response Shape**:
```json
{
  "incident_id": "inc-hero-001",
  "resource_id": "res-gpu-01",
  "summary": "Precautionary safety hold placed on resource 'res-gpu-01' after report: 'fan fault'. 1 upcoming booking(s) affected.",
  "timeline": [
    {
      "time": "2026-10-29T10:00:00Z",
      "event": "incident_reported",
      "actor": "alice",
      "details": "Loud screeching noise from cooling fan"
    },
    {
      "time": "2026-10-29T10:00:00Z",
      "event": "precautionary_hold_placed",
      "actor": "agent:custodian",
      "details": "Autonomous safety hold placed pending inspection (Incident inc-hero-001)"
    }
  ],
  "affected_bookings": ["book-201"],
  "similar_incidents": ["inc-050"],
  "suggested_next_step": "Inspect cooling fan and hardware thermals; keep safety hold until certified by inspector.",
  "generated_by": "template" // or "llm" if enriched
}
```

### 2. Scripted Adversarial Suite Endpoint
- **Path**: `POST /api/admin/adversarial`
- **Description**: Runs all 7 scripted adversarial security and fairness verification checks.
- **Label**: Explicitly labelled as `"scripted adversarial tests"` (not autonomous red-teaming).
- **Response Shape**:
```json
[
  {
    "name": "prompt_injection_resistance",
    "passed": true,
    "detail": "Free-text user justification is completely untrusted. Malicious injection failed to bypass year/training rules. Verdict remained recommend_reject.",
    "category": "prompt_injection"
  },
  {
    "name": "role_escalation_guard",
    "passed": true,
    "detail": "Borrower role blocked from clearing safety hold with server-side PermissionError (403 equivalent).",
    "category": "authorization"
  },
  {
    "name": "concurrency_race_condition",
    "passed": true,
    "detail": "Two concurrent requests dispatched. Exactly 1 succeeded, 1 rejected/waitlisted.",
    "category": "concurrency"
  },
  {
    "name": "proxy_booking_detection",
    "passed": true,
    "detail": "Detected coordinated proxy bookings across team 'team-alpha'.",
    "category": "fairness"
  },
  {
    "name": "agent_overreach_boundary",
    "passed": true,
    "detail": "Agent blocked from clear_safety_hold and apply_penalty. Audit log recorded 2 DENIED events.",
    "category": "bounded_autonomy"
  },
  {
    "name": "hold_bypass_prevention",
    "passed": true,
    "detail": "Owner prevented from approving booking on resource with active precautionary hold.",
    "category": "safety_integrity"
  },
  {
    "name": "audit_tamper_detection",
    "passed": true,
    "detail": "Tamper-evident verification succeeded: Genuine chain passed, tampered record broke hash validation.",
    "category": "audit_integrity"
  }
]
```

### 3. Policy Compiler Endpoint
- **Path**: `POST /api/policies/compile`
- **Body**: `{"text": "3rd-year and above with gpu safety training, max 4 hours"}`
- **Response Shape**:
```json
{
  "rules": {
    "min_year": 3,
    "required_training": ["gpu_safety"],
    "max_hours_per_booking": 4.0
  },
  "warnings": []
}
```
*Note: Compiled rules must be explicitly approved by the resource owner before saving.*

