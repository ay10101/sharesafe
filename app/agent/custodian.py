"""
ShareSafe — Custodian Agent Implementation.

Coordinates deterministic, reversible incident handling:
- Places precautionary safety holds strictly through the central authority.
- Queries upcoming reservations and past incident history.
- Assembles deterministic evidence packets with optional LLM enrichment.
- Dispatches notifications to custodians and resource owners.
- Records all actions to the audit trail.
- Never executes human-only actions (penalties, hold clearance, rejection).

IMPORTANT SECURITY ARCHITECTURE NOTES:
1. Trusted Identity: The Actor model represents identity context passed from the host
   application. While the agent checks actor.role as an internal policy gate, the upstream
   API routers and authentication middleware are strictly responsible for cryptographically
   verifying session credentials / JWT tokens before constructing the Actor instance.
2. Mandatory Authority Injection: State-changing operations strictly require an explicitly
   supplied AuthorityInterface instance. handle_incident() will NEVER silently fall back
   to an ephemeral in-memory authority.
"""
from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional, Protocol, Union

from app.agent.contracts import (
    AGENT_ALLOWED_ACTIONS,
    HUMAN_ONLY_ACTIONS,
    Actor,
    AuthorityInterface,
    AuthorityResult,
    CustodianActionResult,
    EvidencePacket,
    HistoricalIncident,
    Incident,
    UpcomingBooking,
)
from app.agent.enrichment import enrich_packet
from app.agent.packet import build_packet

logger = logging.getLogger("sharesafe.agent.custodian")


# ---------------------------------------------------------------------------
# Test-Only / Mock Service Adapters
# ---------------------------------------------------------------------------
class SimpleAuthority:
    """
    Test-only in-memory authority dispatcher enforcing RBAC and action boundaries.
    Explicitly instantiated and injected in unit and adversarial tests.
    MUST NOT be used as a silent production fallback.
    """
    def __init__(self, active_holds: Optional[Dict[str, str]] = None) -> None:
        self.active_holds: Dict[str, str] = active_holds or {}  # resource_id -> hold_id
        self.action_log: List[Dict[str, Any]] = []

    def execute(self, actor: Actor, action: str, args: Dict[str, Any]) -> AuthorityResult:
        self.action_log.append({"actor_id": actor.id, "action": action, "args": args})

        # Strict Security Rule: Human-only actions cannot be executed by an agent
        if action in HUMAN_ONLY_ACTIONS:
            logger.warning("Authority denied: Action '%s' is restricted to authorized humans only.", action)
            return AuthorityResult(
                success=False,
                action=action,
                actor_id=actor.id,
                denial_reason=f"Action '{action}' is restricted to authorized humans only.",
            )

        if action not in AGENT_ALLOWED_ACTIONS:
            logger.warning("Authority denied: Action '%s' is not an authorized agent action.", action)
            return AuthorityResult(
                success=False,
                action=action,
                actor_id=actor.id,
                denial_reason=f"Action '{action}' is unrecognized or unauthorized.",
            )

        # Actor permission check
        if actor.role in ("student", "borrower") and action == "place_precautionary_hold":
            return AuthorityResult(
                success=False,
                action=action,
                actor_id=actor.id,
                denial_reason="Role 'borrower' lacks privilege to place equipment holds.",
            )

        if action == "place_precautionary_hold":
            resource_id = args.get("resource_id", "")
            incident_id = args.get("incident_id", "")
            if resource_id in self.active_holds:
                return AuthorityResult(
                    success=True,
                    action=action,
                    actor_id=actor.id,
                    details={"hold_id": self.active_holds[resource_id], "already_active": True},
                )
            hold_id = f"hold-{resource_id[:6]}-{incident_id[:6]}"
            self.active_holds[resource_id] = hold_id
            return AuthorityResult(
                success=True,
                action=action,
                actor_id=actor.id,
                details={"hold_id": hold_id, "already_active": False},
            )

        return AuthorityResult(success=True, action=action, actor_id=actor.id, details=args)


class SimpleAuditLogger:
    """In-memory audit logger for agent action verification."""
    def __init__(self) -> None:
        self.records: List[Dict[str, Any]] = []

    def record(self, actor: Actor, action: str, outcome: str, details: Dict[str, Any]) -> str:
        record_id = f"audit-{len(self.records) + 1:04d}"
        entry = {
            "record_id": record_id,
            "actor_id": actor.id,
            "action": action,
            "outcome": outcome,
            "details": details,
        }
        self.records.append(entry)
        return record_id


class SimpleNotifier:
    """In-memory notification dispatcher."""
    def __init__(self) -> None:
        self.sent: List[Dict[str, Any]] = []

    def notify(self, recipient_id: str, event: str, message: str, metadata: Dict[str, Any]) -> str:
        notice_id = f"notif-{len(self.sent) + 1:04d}"
        self.sent.append({
            "notice_id": notice_id,
            "recipient_id": recipient_id,
            "event": event,
            "message": message,
            "metadata": metadata,
        })
        return notice_id


# ---------------------------------------------------------------------------
# Custodian Agent Entry Point
# ---------------------------------------------------------------------------
def handle_incident(
    incident_id: str,
    actor: Actor,
    *,
    incident: Optional[Union[Incident, Dict[str, Any]]] = None,
    authority: Optional[AuthorityInterface] = None,
    booking_service: Optional[Any] = None,
    history_service: Optional[Any] = None,
    notifier: Optional[Any] = None,
    audit_logger: Optional[Any] = None,
    llm_enrich: bool = False,
) -> CustodianActionResult:
    """
    Execute the Custodian Agent incident coordination workflow.

    Parameters:
    - incident_id: Identifier of the reported incident.
    - actor: Authenticated caller / agent context.
    - incident: Optional pre-loaded Incident object or dictionary.
    - authority: Authority dispatcher for state-changing operations.
    - booking_service: Provider for upcoming reservations query.
    - history_service: Provider for past equipment incident history query.
    - notifier: Service to dispatch notices to custodians/owners.
    - audit_logger: Service to record auditable action transactions.
    - llm_enrich: If True, attempts bounded LLM summary enrichment.

    Returns:
    - CustodianActionResult describing the workflow execution and evidence packet.
    """
    logger.info("Custodian Agent initiated for incident '%s' by actor '%s' (%s)", incident_id, actor.id, actor.role)
    errors: List[str] = []
    audit_records: List[str] = []
    notifications_sent: List[str] = []

    auditor = audit_logger or SimpleAuditLogger()
    notif = notifier or SimpleNotifier()

    # 1. Mandatory Authority Check: Never silently fall back to an in-memory authority.
    if authority is None or not hasattr(authority, "execute"):
        logger.error("Custodian Agent execution halted: authority dependency is required.")
        rec_id = auditor.record(
            actor=actor,
            action="handle_incident",
            outcome="failed",
            details={"incident_id": incident_id, "reason": "Missing or invalid authority dependency."},
        )
        return CustodianActionResult(
            incident_id=incident_id,
            processed=False,
            hold_status="failed",
            hold_id=None,
            packet_generated=False,
            packet=None,
            notifications_sent=[],
            audit_records=[rec_id],
            errors=["Authority dependency is required and must implement execute(actor, action, args)."],
            summary="Execution failed: Missing or invalid authority dependency.",
        )

    # 2. Validate caller role: borrower cannot directly act as Custodian Agent
    if actor.role in ("student", "borrower"):
        logger.warning("Custodian Agent denied: actor '%s' has unauthorized role '%s'.", actor.id, actor.role)
        rec_id = auditor.record(
            actor=actor,
            action="handle_incident",
            outcome="denied",
            details={"incident_id": incident_id, "reason": "Unauthorized borrower role escalation attempt."},
        )
        return CustodianActionResult(
            incident_id=incident_id,
            processed=False,
            hold_status="denied",
            hold_id=None,
            packet_generated=False,
            packet=None,
            notifications_sent=[],
            audit_records=[rec_id],
            errors=["Unauthorized role: Borrower cannot invoke Custodian Agent operations."],
            summary="Execution denied: Caller does not possess custodian or agent privileges.",
        )

    # 3. Resolve incident record
    if incident is None:
        logger.error("Incident '%s' data not supplied.", incident_id)
        return CustodianActionResult(
            incident_id=incident_id,
            processed=False,
            hold_status="failed",
            hold_id=None,
            packet_generated=False,
            packet=None,
            notifications_sent=[],
            audit_records=[],
            errors=[f"Incident record '{incident_id}' not found."],
            summary="Incident resolution failed: record not found.",
        )

    # Normalize incident data
    if isinstance(incident, Incident):
        inc_data = incident.model_dump()
    elif isinstance(incident, dict):
        inc_data = incident
    else:
        errors.append("Invalid incident payload type.")
        return CustodianActionResult(
            incident_id=incident_id,
            processed=False,
            hold_status="failed",
            hold_id=None,
            packet_generated=False,
            packet=None,
            notifications_sent=[],
            audit_records=[],
            errors=errors,
            summary="Invalid incident format.",
        )

    resource_id = inc_data.get("resource_id", "unknown-resource")
    incident_type = inc_data.get("incident_type", "damage")
    severity = inc_data.get("severity", "medium")

    # 4. Precautionary Hold Decision (Policy-driven)
    # Policy: Holds are required for damage, safety hazards, high/critical severity, or default equipment caution
    hold_required = incident_type in ("damage", "safety_hazard", "malfunction") or severity in ("medium", "high", "critical")
    hold_status = "not_required"
    hold_id: Optional[str] = None

    if hold_required:
        hold_args = {
            "resource_id": resource_id,
            "incident_id": incident_id,
            "reason": f"Precautionary hold for {incident_type} (Severity: {severity}): {inc_data.get('description', '')[:80]}",
        }
        hold_result: AuthorityResult = authority.execute(actor, "place_precautionary_hold", hold_args)
        if hold_result.success:
            hold_id = hold_result.details.get("hold_id")
            if hold_result.details.get("already_active"):
                hold_status = "already_present"
            else:
                hold_status = "placed"
            audit_id = auditor.record(
                actor=actor,
                action="place_precautionary_hold",
                outcome="success",
                details={"resource_id": resource_id, "hold_id": hold_id, "status": hold_status},
            )
            audit_records.append(audit_id)
        else:
            hold_status = "denied"
            errors.append(f"Authority denied safety hold: {hold_result.denial_reason}")
            audit_id = auditor.record(
                actor=actor,
                action="place_precautionary_hold",
                outcome="denied",
                details={"resource_id": resource_id, "reason": hold_result.denial_reason},
            )
            audit_records.append(audit_id)

    # 4. Retrieve context: upcoming bookings
    upcoming_bookings: List[UpcomingBooking] = []
    if booking_service and hasattr(booking_service, "get_upcoming_bookings"):
        try:
            upcoming_bookings = booking_service.get_upcoming_bookings(resource_id)
        except Exception as e:
            errors.append(f"Failed to query upcoming bookings: {e}")

    # 5. Retrieve context: historical incidents
    historical_incidents: List[HistoricalIncident] = []
    if history_service and hasattr(history_service, "get_resource_incidents"):
        try:
            historical_incidents = history_service.get_resource_incidents(resource_id)
        except Exception as e:
            errors.append(f"Failed to query incident history: {e}")

    # 6. Build deterministic evidence packet
    packet: EvidencePacket = build_packet(
        incident=inc_data,
        history=historical_incidents,
        bookings=upcoming_bookings,
    )

    # 7. Optional LLM enrichment (Never a single point of failure)
    if llm_enrich:
        try:
            packet = enrich_packet(packet)
        except Exception as e:
            logger.warning("LLM enrichment failed gracefully: %s; template retained.", e)

    # 8. Dispatch notifications to owner and custodian
    owner_id = inc_data.get("owner_id") or "custodian-general"
    try:
        notice_id = notif.notify(
            recipient_id=owner_id,
            event="safety_hold_alert",
            message=f"Precautionary hold ({hold_status}) placed on resource '{resource_id}' for review.",
            metadata={"incident_id": incident_id, "packet_id": packet.packet_id, "hold_id": hold_id},
        )
        notifications_sent.append(notice_id)
    except Exception as e:
        errors.append(f"Notification dispatch failed: {e}")

    # 9. Record final packet generation to audit log
    audit_pkt_id = auditor.record(
        actor=actor,
        action="draft_incident_packet",
        outcome="success",
        details={"packet_id": packet.packet_id, "incident_id": incident_id, "generated_by": packet.generated_by},
    )
    audit_records.append(audit_pkt_id)

    summary_text = (
        f"Incident {incident_id} processed. Hold status: {hold_status}. "
        f"Evidence packet {packet.packet_id} compiled (summary source: {packet.generated_by}). "
        f"{len(notifications_sent)} notice(s) sent."
    )

    return CustodianActionResult(
        incident_id=incident_id,
        processed=True,
        hold_status=hold_status,
        hold_id=hold_id,
        packet_generated=True,
        packet=packet,
        notifications_sent=notifications_sent,
        audit_records=audit_records,
        errors=errors,
        summary=summary_text,
    )
