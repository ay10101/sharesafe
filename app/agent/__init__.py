"""
ShareSafe Agent Package.
Exports Custodian Agent entry points, packet builder, enrichment, and domain contracts.
"""
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
    PrecautionaryHold,
    UpcomingBooking,
)
from app.agent.custodian import (
    SimpleAuditLogger,
    SimpleAuthority,
    SimpleNotifier,
    handle_incident,
)
from app.agent.enrichment import PacketEnrichment, enrich_packet
from app.agent.packet import build_packet

__all__ = [
    "AGENT_ALLOWED_ACTIONS",
    "HUMAN_ONLY_ACTIONS",
    "Actor",
    "AuthorityInterface",
    "AuthorityResult",
    "CustodianActionResult",
    "EvidencePacket",
    "HistoricalIncident",
    "Incident",
    "PrecautionaryHold",
    "UpcomingBooking",
    "SimpleAuditLogger",
    "SimpleAuthority",
    "SimpleNotifier",
    "handle_incident",
    "PacketEnrichment",
    "enrich_packet",
    "build_packet",
]
# app/agent/__init__.py

