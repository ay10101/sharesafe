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

