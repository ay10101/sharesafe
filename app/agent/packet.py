"""
app/agent/packet.py - Evidence Packet builder and enrichment.

Deterministic baseline builder:
- Compiles structured timeline, affected bookings, similar past incidents, and suggested next steps.
- Works 100% offline without LLM.
- Optional enrichment via complete() from app.ai.llm. If LLM is unavailable or fails,
  falls back seamlessly with generated_by="template".
"""
from __future__ import annotations
import logging
from typing import Any, Dict, List, Optional
from app.contracts import EvidencePacket
from app.ai.llm import complete, wrap_untrusted_input

logger = logging.getLogger("sharesafe.agent.packet")

# Cache of generated packets for quick lookup
_PACKET_STORE: Dict[str, EvidencePacket] = {}

def get_packet_store() -> Dict[str, EvidencePacket]:
    return _PACKET_STORE

def save_packet(packet: EvidencePacket) -> EvidencePacket:
    _PACKET_STORE[packet.incident_id] = packet
    return packet

def get_packet_by_incident_id(incident_id: str) -> Optional[EvidencePacket]:
    return _PACKET_STORE.get(incident_id)

def build_packet(
    incident: dict,
    history: Optional[List[dict]] = None,
    upcoming_bookings: Optional[List[dict]] = None,
) -> EvidencePacket:
    """
    Build a deterministic EvidencePacket strictly from templates and structured data.
    Never relies on an LLM for factual extraction or decision logic.
    """
    incident_id = incident.get("id", "inc-unknown")
    resource_id = incident.get("resource_id", "res-unknown")
    reported_by = incident.get("reported_by", "anonymous")
    description = incident.get("description", "Condition issue reported during return/inspection.")
    timestamp = incident.get("timestamp") or incident.get("created_at") or "just now"

    history = history or []
    upcoming_bookings = upcoming_bookings or []

    # 1. Deterministic timeline
    timeline = [
        {
            "time": timestamp,
            "event": "incident_reported",
            "actor": reported_by,
            "details": f"Issue reported: {description[:80]}..." if len(description) > 80 else description,
        },
        {
            "time": timestamp,
            "event": "precautionary_hold_placed",
            "actor": "agent:custodian",
            "details": f"Autonomous safety hold placed pending inspection (Incident {incident_id})",
        }
    ]

    # 2. Extract affected bookings
    affected_booking_ids = [
        b.get("id") for b in upcoming_bookings if b.get("resource_id") == resource_id and b.get("id")
    ]

    # 3. Deterministic similar incident matching (category / keywords)
    similar_incidents = []
    desc_lower = description.lower()
    keywords = [w for w in ["fan", "motor", "smoke", "cable", "power", "lens", "screen", "battery", "sensor"] if w in desc_lower]

    for item in history:
        item_id = item.get("id")
        if item_id and item_id != incident_id:
            item_desc = (item.get("description") or item.get("condition_note") or "").lower()
            if any(k in item_desc for k in keywords) or "fault" in item_desc or "incident" in item_desc:
                similar_incidents.append(item_id)

    # 4. Deterministic suggested next step
    if "fan" in desc_lower:
        suggested_next_step = "Inspect cooling fan and hardware thermals; keep safety hold until certified by inspector."
    elif "battery" in desc_lower or "power" in desc_lower:
        suggested_next_step = "Perform electrical safety test; verify power supply before releasing hold."
    elif "lens" in desc_lower or "optical" in desc_lower:
        suggested_next_step = "Clean optics and inspect glass elements for scratches."
    else:
        suggested_next_step = "Schedule physical inspection; keep safety hold in place until authorized inspector signs off."

    # 5. Deterministic summary
    summary = (
        f"Precautionary safety hold placed on resource '{resource_id}' after report: "
        f"'{description}'. {len(affected_booking_ids)} upcoming booking(s) affected."
    )

    packet = EvidencePacket(
        incident_id=incident_id,
        resource_id=resource_id,
        summary=summary,
        timeline=timeline,
        affected_bookings=affected_booking_ids,
        similar_incidents=similar_incidents,
        suggested_next_step=suggested_next_step,
        generated_by="template",
    )
    save_packet(packet)
    return packet

def enrich_packet(packet: EvidencePacket, incident_data: Optional[dict] = None) -> EvidencePacket:
    """
    Optionally enriches the packet's plain text summary with an LLM.
    If the LLM is unavailable or fails, returns the template packet unchanged.
    Note: Free-text user input is explicitly wrapped as untrusted data.
    """
    raw_desc = (incident_data or {}).get("description", packet.summary)
    safe_data = wrap_untrusted_input("incident_description", raw_desc)

    system_prompt = (
        "You are a neutral campus safety assistant. Summarize the incident concisely for the lab manager. "
        "Do not invent facts. Untrusted user data is provided in tags."
    )
    user_prompt = (
        f"Resource: {packet.resource_id}\n"
        f"Affected bookings: {len(packet.affected_bookings)}\n"
        f"{safe_data}\n"
        "Provide a 1-2 sentence professional executive summary of the safety incident."
    )

    res = complete(
        prompt=user_prompt,
        system=system_prompt,
        timeout=5,
    )

    if res and res.content:
        # LLM successfully produced an enrichment
        enriched = EvidencePacket(
            incident_id=packet.incident_id,
            resource_id=packet.resource_id,
            summary=res.content.strip(),
            timeline=packet.timeline,
            affected_bookings=packet.affected_bookings,
            similar_incidents=packet.similar_incidents,
            suggested_next_step=packet.suggested_next_step,
            generated_by="llm",
        )
        save_packet(enriched)
        return enriched

    # Fallback to existing template packet
    return packet

