"""
ShareSafe Agent Contracts & Data Models.

Defines P4 domain models for the Custodian Agent, Evidence Packets,
Authority permissions, and incident-handling workflows.
"""
from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any, Dict, List, Literal, Optional, Protocol, Union
from pydantic import BaseModel, Field

# ---------------------------------------------------------------------------
# Permitted and Restricted Actions
# ---------------------------------------------------------------------------
AGENT_ALLOWED_ACTIONS = frozenset({
    "place_precautionary_hold",
    "send_reminder",
    "flag_overdue",
    "draft_incident_packet",
    "propose_decision",
})

HUMAN_ONLY_ACTIONS = frozenset({
    "approve_exception",
    "clear_safety_hold",
    "apply_penalty",
    "reject_request",
})


# ---------------------------------------------------------------------------
# Actor Model
# ---------------------------------------------------------------------------
class Actor(BaseModel):
    """Represents an authenticated caller in the system."""
    id: str
    name: str
    role: Literal["student", "borrower", "owner", "custodian", "inspector", "system", "agent"]
    permissions: List[str] = Field(default_factory=list)


# ---------------------------------------------------------------------------
# Domain Models: Incidents, Holds, Bookings
# ---------------------------------------------------------------------------
class Incident(BaseModel):
    """Represents an incident reported on a resource."""
    id: str = Field(default_factory=lambda: f"inc-{uuid.uuid4().hex[:8]}")
    resource_id: str
    reporter_id: str
    incident_type: str = "damage"  # e.g., "damage", "delay", "safety_hazard", "other"
    description: str
    severity: str = "medium"       # "low", "medium", "high", "critical"
    created_at: str = Field(default_factory=lambda: datetime.utcnow().isoformat())
    request_id: Optional[str] = None


class PrecautionaryHold(BaseModel):
    """A reversible safety hold placed on equipment pending human review."""
    hold_id: str = Field(default_factory=lambda: f"hold-{uuid.uuid4().hex[:8]}")
    resource_id: str
    incident_id: str
    placed_by: str
    reason: str
    created_at: str = Field(default_factory=lambda: datetime.utcnow().isoformat())
    active: bool = True


class UpcomingBooking(BaseModel):
    """An upcoming approved reservation that may be impacted by an incident."""
    request_id: str
    resource_id: str
    requester_id: str
    requester_name: Optional[str] = None
    start_date: str
    end_date: str
    status: str = "approved"


class HistoricalIncident(BaseModel):
    """A past incident on the same resource for context and pattern analysis."""
    incident_id: str
    resource_id: str
    incident_type: str
    description: str
    severity: str = "medium"
    created_at: str
    resolution: Optional[str] = None


# ---------------------------------------------------------------------------
# Evidence Packet Model
# ---------------------------------------------------------------------------
class EvidencePacket(BaseModel):
    """Structured, immutable-ready evidence packet compiled for human review."""
    packet_id: str = Field(default_factory=lambda: f"pkt-{uuid.uuid4().hex[:8]}")
    incident_id: str
    resource_id: str
    incident_type: str
    severity: str
    reported_description: str
    incident_timestamp: str
    upcoming_bookings: List[UpcomingBooking] = Field(default_factory=list)
    historical_incidents: List[HistoricalIncident] = Field(default_factory=list)
    source_references: Dict[str, Any] = Field(default_factory=dict)
    missing_evidence: List[str] = Field(default_factory=list)
    limitations: List[str] = Field(default_factory=list)
    summary: str = ""
    key_observations: List[str] = Field(default_factory=list)
    questions_for_inspector: List[str] = Field(default_factory=list)
    generated_by: Literal["template", "llm"] = "template"
    generated_at: str = Field(default_factory=lambda: datetime.utcnow().isoformat())
    version: str = "1.0"


# ---------------------------------------------------------------------------
# Authority Result & Protocol
# ---------------------------------------------------------------------------
class AuthorityResult(BaseModel):
    """Result of an action execution through the central authority."""
    success: bool
    action: str
    actor_id: str
    details: Dict[str, Any] = Field(default_factory=dict)
    denial_reason: Optional[str] = None


class AuthorityInterface(Protocol):
    """Protocol for the central authority dispatcher."""
    def execute(self, actor: Actor, action: str, args: Dict[str, Any]) -> AuthorityResult:
        ...


# ---------------------------------------------------------------------------
# Custodian Action Result
# ---------------------------------------------------------------------------
class CustodianActionResult(BaseModel):
    """Structured outcome returned by the Custodian Agent after incident processing."""
    incident_id: str
    processed: bool
    hold_status: Literal["placed", "already_present", "not_required", "denied", "failed"]
    hold_id: Optional[str] = None
    packet_generated: bool
    packet: Optional[EvidencePacket] = None
    notifications_sent: List[str] = Field(default_factory=list)
    audit_records: List[str] = Field(default_factory=list)
    errors: List[str] = Field(default_factory=list)
    summary: str
