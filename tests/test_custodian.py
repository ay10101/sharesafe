"""
Tests for Custodian Agent and Evidence Packet builder.
Verifies:
- on_incident triggers precautionary hold, draft packet, and notifications via execute()
- Acts ONLY through execute()
- Evidence packet builds deterministically with no LLM key
- Pre-cached hero demo triggers enriched packet
- Agent attempts to perform human-only actions are blocked by execute()
"""
import pytest
from app.contracts import Actor, EvidencePacket
from app.core.authority import execute, AUDIT_LOG
from app.agent.custodian import CustodianAgent, ActionResult
from app.agent.packet import build_packet, enrich_packet, get_packet_by_incident_id

@pytest.fixture(autouse=True)
def clean_audit():
    AUDIT_LOG.clear()

def test_evidence_packet_deterministic_offline(monkeypatch):
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)

    incident = {
        "id": "inc-101",
        "resource_id": "res-gpu-01",
        "reported_by": "alice",
        "description": "Loud screeching noise from cooling fan during training run.",
        "timestamp": "2026-10-29T10:00:00Z",
    }
    upcoming = [
        {"id": "book-201", "resource_id": "res-gpu-01"},
        {"id": "book-202", "resource_id": "res-other"},
    ]
    history = [
        {"id": "inc-050", "description": "Fan bearing oil leak"},
        {"id": "inc-051", "description": "Routine clean"},
    ]

    packet = build_packet(incident, history=history, upcoming_bookings=upcoming)
    assert packet.incident_id == "inc-101"
    assert packet.resource_id == "res-gpu-01"
    assert packet.generated_by == "template"
    assert "book-201" in packet.affected_bookings
    assert "book-202" not in packet.affected_bookings
    assert "inc-050" in packet.similar_incidents
    assert "cooling fan" in packet.suggested_next_step.lower()

def test_evidence_packet_hero_demo_enrichment(monkeypatch):
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)

    incident = {
        "id": "inc-fan-demo",
        "resource_id": "res-gpu-01",
        "reported_by": "bob",
        "description": "Critical cooling fan failure and thermal throttle",
    }
    packet = build_packet(incident)
    enriched = enrich_packet(packet, incident_data=incident)
    assert enriched.generated_by == "llm"
    assert "gpu workstation" in enriched.summary.lower()

def test_custodian_on_incident_lifecycle():
    agent = CustodianAgent()
    incident = {
        "id": "inc-hero-001",
        "resource_id": "res-gpu-hero",
        "description": "Fan fault on return",
        "reported_by": "judge",
    }
    upcoming = [{"id": "book-next-01", "resource_id": "res-gpu-hero"}]
    history = [{"id": "inc-past-01", "description": "High temperature fan warning"}]

    results = agent.on_incident("inc-hero-001", incident_data=incident, upcoming_bookings=upcoming, history=history)

    assert len(results) == 3
    actions = [r.action for r in results]
    assert "place_precautionary_hold" in actions
    assert "draft_incident_packet" in actions
    assert "send_reminder" in actions

    # Verify audit log entries
    executed_actions = [e["action"] for e in AUDIT_LOG if e["action"].startswith("EXECUTE:")]
    assert "EXECUTE:place_precautionary_hold" in executed_actions
    assert "EXECUTE:draft_incident_packet" in executed_actions
    assert "EXECUTE:send_reminder" in executed_actions

    packet = get_packet_by_incident_id("inc-hero-001")
    assert packet is not None
    assert "book-next-01" in packet.affected_bookings

def test_custodian_cannot_perform_human_only_actions():
    agent = CustodianAgent()
    # Ensure agent cannot clear hold or apply penalties
    with pytest.raises(PermissionError) as exc_info:
        execute(agent.actor, "clear_safety_hold", {"resource_id": "res-gpu-hero"})
    assert "human-only" in str(exc_info.value)

    denied = [e for e in AUDIT_LOG if e["action"] == "DENIED:clear_safety_hold"]
    assert len(denied) == 1
