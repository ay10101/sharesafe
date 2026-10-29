"""
ShareSafe — Deterministic Evidence Packet Builder.

Compiles reported incident telemetry, resource history, and upcoming reservations
into a structured, immutable-ready evidence packet for human custodian review.
Operates completely deterministically without LLM reliance.
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional, Union

from app.agent.contracts import (
    EvidencePacket,
    HistoricalIncident,
    Incident,
    UpcomingBooking,
)


def _normalize_incident(incident: Union[Incident, Dict[str, Any]]) -> Dict[str, Any]:
    """Extract standard incident fields from an Incident model or dictionary."""
    if isinstance(incident, Incident):
        return incident.model_dump()
    if isinstance(incident, dict):
        return incident
    raise ValueError(f"Unsupported incident input type: {type(incident)}")


def _normalize_history(
    history: Optional[List[Union[HistoricalIncident, Dict[str, Any]]]]
) -> List[HistoricalIncident]:
    """Safely parse and normalize historical incidents."""
    if not history:
        return []
    normalized: List[HistoricalIncident] = []
    for item in history:
        if isinstance(item, HistoricalIncident):
            normalized.append(item)
        elif isinstance(item, dict):
            try:
                normalized.append(
                    HistoricalIncident(
                        incident_id=item.get("incident_id") or item.get("id", "hist-unknown"),
                        resource_id=item.get("resource_id", "res-unknown"),
                        incident_type=item.get("incident_type", "other"),
                        description=item.get("description", "No description provided."),
                        severity=item.get("severity", "medium"),
                        created_at=item.get("created_at", "unknown"),
                        resolution=item.get("resolution"),
                    )
                )
            except Exception:
                continue
    return normalized


def _normalize_bookings(
    bookings: Optional[List[Union[UpcomingBooking, Dict[str, Any]]]]
) -> List[UpcomingBooking]:
    """Safely parse and normalize upcoming bookings."""
    if not bookings:
        return []
    normalized: List[UpcomingBooking] = []
    for item in bookings:
        if isinstance(item, UpcomingBooking):
            normalized.append(item)
        elif isinstance(item, dict):
            try:
                normalized.append(
                    UpcomingBooking(
                        request_id=item.get("request_id") or item.get("id", "req-unknown"),
                        resource_id=item.get("resource_id", "res-unknown"),
                        requester_id=item.get("requester_id", "unknown"),
                        requester_name=item.get("requester_name"),
                        start_date=item.get("start_date", "unknown"),
                        end_date=item.get("end_date", "unknown"),
                        status=item.get("status", "approved"),
                    )
                )
            except Exception:
                continue
    return normalized


def _build_deterministic_summary(
    incident_id: str,
    resource_id: str,
    incident_type: str,
    severity: str,
    reported_desc: str,
    booking_count: int,
    history_count: int,
) -> str:
    """Construct an objective, non-accusatory deterministic summary."""
    lines = [
        f"Incident {incident_id} reported for resource {resource_id} (Category: {incident_type}, Severity: {severity}).",
        f"Reported description: \"{reported_desc}\".",
        f"Context: {booking_count} upcoming reservation(s) may be impacted; {history_count} past incident(s) on record.",
        "Deterministic preliminary assessment: Human inspection required prior to hold release.",
    ]
    return " ".join(lines)


def build_packet(
    incident: Union[Incident, Dict[str, Any]],
    history: Optional[List[Union[HistoricalIncident, Dict[str, Any]]]] = None,
    bookings: Optional[List[Union[UpcomingBooking, Dict[str, Any]]]] = None,
) -> EvidencePacket:
    """
    Build a deterministic evidence packet from reported incident data and context.

    Parameters:
    - incident: Incident model or dictionary containing incident details.
    - history: Optional list of past incidents for the same resource.
    - bookings: Optional list of upcoming reservations for the affected resource.

    Returns:
    - EvidencePacket containing verified metadata, context, and deterministic summaries.
    """
    inc_data = _normalize_incident(incident)

    incident_id = str(inc_data.get("id") or inc_data.get("incident_id") or "inc-unassigned")
    resource_id = str(inc_data.get("resource_id") or "res-unassigned")
    incident_type = str(inc_data.get("incident_type") or "damage")
    severity = str(inc_data.get("severity") or "medium")
    reported_description = str(inc_data.get("description") or inc_data.get("incident_description") or "No description provided.")
    incident_timestamp = str(inc_data.get("created_at") or inc_data.get("timestamp") or "unrecorded")
    reporter_id = str(inc_data.get("reporter_id") or "unknown")

    norm_history = _normalize_history(history)
    norm_bookings = _normalize_bookings(bookings)

    # Compile source references without exposing sensitive credentials
    source_references = {
        "incident_id": incident_id,
        "resource_id": resource_id,
        "reporter_id": reporter_id,
        "request_id": inc_data.get("request_id"),
    }

    # Identify missing evidence objectively
    missing_evidence: List[str] = []
    if not inc_data.get("image_url") and not inc_data.get("evidence_photos"):
        missing_evidence.append("No photographic or sensor telemetry evidence attached to report.")
    if not norm_history:
        missing_evidence.append("No prior incident history on file for this resource.")
    if inc_data.get("condition_on_checkout") is None:
        missing_evidence.append("Pre-checkout baseline condition inspection notes not found.")

    # Formulate explicit uncertainties and limitations
    limitations = [
        "Description is based solely on borrower/user report and has not been physically verified by a custodian.",
        "Packet does not establish borrower fault or liability.",
        "Safety hold recommendations are precautionary and reversible.",
    ]

    # Deterministic factual key observations
    key_observations = [
        f"Resource '{resource_id}' flagged with '{incident_type}' condition.",
        f"{len(norm_bookings)} upcoming booking(s) scheduled for this resource.",
        f"{len(norm_history)} prior incident record(s) cataloged.",
    ]
    if severity in ("high", "critical"):
        key_observations.append("Reported severity indicates immediate safety or equipment degradation concern.")

    # Suggested inspection checklist questions for the human lab manager
    questions_for_inspector = [
        f"Inspect physical condition of resource {resource_id} against initial checkout baseline.",
        "Verify whether functional components, cables, or accessories show signs of wear or improper handling.",
        "Determine if resource can be safely recertified or requires certified technician repair.",
    ]
    if norm_bookings:
        questions_for_inspector.append(
            f"Review {len(norm_bookings)} upcoming booking(s) for possible re-assignment or rescheduling."
        )

    summary = _build_deterministic_summary(
        incident_id=incident_id,
        resource_id=resource_id,
        incident_type=incident_type,
        severity=severity,
        reported_desc=reported_description,
        booking_count=len(norm_bookings),
        history_count=len(norm_history),
    )

    return EvidencePacket(
        incident_id=incident_id,
        resource_id=resource_id,
        incident_type=incident_type,
        severity=severity,
        reported_description=reported_description,
        incident_timestamp=incident_timestamp,
        upcoming_bookings=norm_bookings,
        historical_incidents=norm_history,
        source_references=source_references,
        missing_evidence=missing_evidence,
        limitations=limitations,
        summary=summary,
        key_observations=key_observations,
        questions_for_inspector=questions_for_inspector,
        generated_by="template",
    )
