"""
app/agent/custodian.py - Custodian Agent.

Enforces Bounded Autonomy:
- The Custodian Agent can ONLY take safe-direction or reversible actions.
- Every single action is executed strictly through app.core.authority.execute().
- It NEVER writes directly to the database or calls tools without authority gating.
- It does NOT have code paths to decide fault, apply penalties, reject requests, or clear holds.

Primary duties:
1. on_incident(incident_id):
   - Places precautionary hold via execute(Actor("agent", "custodian"), "place_precautionary_hold", ...)
   - Identifies affected upcoming bookings
   - Matches similar historical incidents
   - Drafts incident evidence packet via execute(..., "draft_incident_packet", ...)
   - Sends alerts to owner/inspector via execute(..., "send_reminder", ...)
2. Autonomous background tasks (deterministic & reversible):
   - flag_overdue(booking_id)
   - send_reminder(booking_id, message)
   - propose_decision(request_id, proposal)
"""
from __future__ import annotations
import logging
from dataclasses import dataclass
from typing import Any, Callable, Dict, List, Optional

from app.contracts import Actor, EvidencePacket
from app.core.authority import execute
from app.agent.packet import build_packet, enrich_packet

logger = logging.getLogger("sharesafe.agent.custodian")

# Custodian agent identity
CUSTODIAN_ACTOR = Actor(id="agent-custodian-01", kind="agent", role="custodian")

@dataclass
class ActionResult:
    action: str
    status: str
    details: Dict[str, Any]

# In-memory incident and booking data accessors (can be plugged into app.store or db)
_INCIDENTS_STORE: Dict[str, dict] = {}
_UPCOMING_BOOKINGS_PROVIDER: Optional[Callable[[str], List[dict]]] = None
_HISTORY_PROVIDER: Optional[Callable[[str], List[dict]]] = None

def register_incident(incident: dict) -> None:
    _INCIDENTS_STORE[incident["id"]] = incident

def get_incident(incident_id: str) -> Optional[dict]:
    return _INCIDENTS_STORE.get(incident_id)

def set_data_accessors(
    upcoming_bookings_fn: Callable[[str], List[dict]],
    history_fn: Callable[[str], List[dict]],
) -> None:
    global _UPCOMING_BOOKINGS_PROVIDER, _HISTORY_PROVIDER
    _UPCOMING_BOOKINGS_PROVIDER = upcoming_bookings_fn
    _HISTORY_PROVIDER = history_fn

def default_get_upcoming_bookings(resource_id: str) -> List[dict]:
    if _UPCOMING_BOOKINGS_PROVIDER:
        return _UPCOMING_BOOKINGS_PROVIDER(resource_id)
    return []

def default_get_history(resource_id: str) -> List[dict]:
    if _HISTORY_PROVIDER:
        return _HISTORY_PROVIDER(resource_id)
    return []

class CustodianAgent:
    """
    Autonomous Custodian Agent operating strictly within bounded authority.
    """

    def __init__(self, actor: Optional[Actor] = None):
        self.actor = actor or CUSTODIAN_ACTOR

    def on_incident(
        self,
        incident_id: str,
        incident_data: Optional[dict] = None,
        upcoming_bookings: Optional[List[dict]] = None,
        history: Optional[List[dict]] = None,
    ) -> List[ActionResult]:
        """
        Triggered when an incident or return condition fault is submitted.
        Steps:
        1. place_precautionary_hold on resource via execute()
        2. Query upcoming bookings & historical incidents
        3. draft_incident_packet via execute()
        4. send_reminder / notify owner & inspector via execute()
        """
        results: List[ActionResult] = []

        incident = incident_data or get_incident(incident_id) or {
            "id": incident_id,
            "resource_id": "res-demo",
            "description": "Equipment fault reported during usage/return.",
            "reported_by": "user-unknown",
        }
        resource_id = incident.get("resource_id", "res-unknown")

        # 1. Place precautionary hold (Reversible, Safe-Direction)
        try:
            hold_res = execute(
                self.actor,
                "place_precautionary_hold",
                {
                    "resource_id": resource_id,
                    "reason": f"Precautionary safety hold: incident {incident_id}",
                    "incident_id": incident_id,
                },
            )
            results.append(ActionResult(action="place_precautionary_hold", status="success", details=hold_res))
        except PermissionError as e:
            logger.error(f"Custodian denied hold: {e}")
            results.append(ActionResult(action="place_precautionary_hold", status="denied", details={"error": str(e)}))
            return results

        # 2. Collect data for packet
        bookings = upcoming_bookings if upcoming_bookings is not None else default_get_upcoming_bookings(resource_id)
        past_history = history if history is not None else default_get_history(resource_id)

        # 3. Build & enrich evidence packet
        packet = build_packet(incident, history=past_history, upcoming_bookings=bookings)
        packet = enrich_packet(packet, incident_data=incident)

        # 4. Draft incident packet (Reversible, Safe-Direction)
        try:
            draft_res = execute(
                self.actor,
                "draft_incident_packet",
                {
                    "incident_id": incident_id,
                    "resource_id": resource_id,
                    "summary": packet.summary,
                    "timeline": packet.timeline,
                    "affected_bookings": packet.affected_bookings,
                    "suggested_next_step": packet.suggested_next_step,
                    "generated_by": packet.generated_by,
                },
            )
            results.append(ActionResult(action="draft_incident_packet", status="success", details=draft_res))
        except PermissionError as e:
            results.append(ActionResult(action="draft_incident_packet", status="denied", details={"error": str(e)}))

        # 5. Send notifications to owner & inspector (Reversible)
        try:
            notify_res = execute(
                self.actor,
                "send_reminder",
                {
                    "target_role": "owner",
                    "resource_id": resource_id,
                    "incident_id": incident_id,
                    "message": f"Precautionary hold active on {resource_id}. Evidence packet ready for review.",
                },
            )
            results.append(ActionResult(action="send_reminder", status="success", details=notify_res))
        except PermissionError as e:
            results.append(ActionResult(action="send_reminder", status="denied", details={"error": str(e)}))

        return results

    def flag_overdue(self, booking_id: str, resource_id: str, hours_overdue: float) -> ActionResult:
        """
        Flag an overdue booking (Deterministic, Safe/Reversible).
        """
        res = execute(
            self.actor,
            "flag_overdue",
            {
                "booking_id": booking_id,
                "resource_id": resource_id,
                "hours_overdue": hours_overdue,
            },
        )
        return ActionResult(action="flag_overdue", status="success", details=res)

    def send_reminder(self, recipient_id: str, message: str) -> ActionResult:
        """
        Send a reminder before return deadline (Deterministic, Safe/Reversible).
        """
        res = execute(
            self.actor,
            "send_reminder",
            {
                "recipient_id": recipient_id,
                "message": message,
            },
        )
        return ActionResult(action="send_reminder", status="success", details=res)

    def propose_decision(self, request_id: str, recommendation: str, reason: str) -> ActionResult:
        """
        Propose a decision on a booking request (Reversible, human has final sign-off).
        """
        res = execute(
            self.actor,
            "propose_decision",
            {
                "request_id": request_id,
                "recommendation": recommendation,
                "reason": reason,
            },
        )
        return ActionResult(action="propose_decision", status="success", details=res)

