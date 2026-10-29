"""
Unit tests for app.agent.packet (Deterministic Evidence Packet Builder).
Covers complete inputs, missing data, source preservation, determinism, and safety.
"""
from __future__ import annotations

import copy
from app.agent.contracts import HistoricalIncident, Incident, UpcomingBooking
from app.agent.packet import build_packet


def test_build_packet_complete_input():
    """Verify packet generation when all fields, history, and bookings are present."""
    incident = Incident(
        id="inc-1001",
        resource_id="res-bambu-01",
        reporter_id="user-maya",
        incident_type="damage",
        description="Extruder jammed with carbon fiber filament.",
        severity="high",
        created_at="2026-10-29T10:00:00",
        request_id="req-501",
    )
    bookings = [
        UpcomingBooking(
            request_id="req-502",
            resource_id="res-bambu-01",
            requester_id="user-alex",
            requester_name="Alex Kim",
            start_date="2026-10-30T09:00",
            end_date="2026-10-30T17:00",
        ),
        UpcomingBooking(
            request_id="req-503",
            resource_id="res-bambu-01",
            requester_id="user-jordan",
            requester_name="Jordan Lee",
            start_date="2026-10-31T09:00",
            end_date="2026-10-31T17:00",
        ),
    ]
    history = [
        HistoricalIncident(
            incident_id="hist-990",
            resource_id="res-bambu-01",
            incident_type="damage",
            description="Nozzle temperature sensor error.",
            severity="medium",
            created_at="2026-09-15T14:00:00",
            resolution="Thermistor replaced by lab staff.",
        )
    ]

    packet = build_packet(incident, history=history, bookings=bookings)

    assert packet.incident_id == "inc-1001"
    assert packet.resource_id == "res-bambu-01"
    assert packet.incident_type == "damage"
    assert packet.severity == "high"
    assert packet.reported_description == "Extruder jammed with carbon fiber filament."
    assert packet.generated_by == "template"
    assert len(packet.upcoming_bookings) == 2
    assert len(packet.historical_incidents) == 1
    assert packet.source_references["incident_id"] == "inc-1001"
    assert packet.source_references["request_id"] == "req-501"
    assert "Human inspection required" in packet.summary


def test_build_packet_missing_optional_data():
    """Verify packet generation when history, bookings, and images are absent."""
    incident_dict = {
        "id": "inc-minimal",
        "resource_id": "res-generic",
        "description": "Found unplugged on desk.",
    }

    packet = build_packet(incident_dict, history=None, bookings=None)

    assert packet.incident_id == "inc-minimal"
    assert packet.resource_id == "res-generic"
    assert packet.reported_description == "Found unplugged on desk."
    assert packet.upcoming_bookings == []
    assert packet.historical_incidents == []
    assert packet.generated_by == "template"
    # Verify missing evidence is objectively recorded
    assert any("photographic" in item.lower() for item in packet.missing_evidence)
    assert any("prior incident history" in item.lower() for item in packet.missing_evidence)


def test_build_packet_determinism():
    """Equivalent inputs must produce identical substantive packet contents."""
    incident = {
        "id": "inc-det-01",
        "resource_id": "res-audio-rig",
        "incident_type": "delay",
        "severity": "low",
        "description": "Late return by 30 minutes due to transit.",
        "created_at": "2026-10-29T11:00:00",
    }
    history = [
        {"id": "h1", "resource_id": "res-audio-rig", "incident_type": "delay", "created_at": "2026-10-01"}
    ]

    p1 = build_packet(incident, history=copy.deepcopy(history))
    p2 = build_packet(copy.deepcopy(incident), history=copy.deepcopy(history))

    assert p1.summary == p2.summary
    assert p1.key_observations == p2.key_observations
    assert p1.questions_for_inspector == p2.questions_for_inspector
    assert p1.missing_evidence == p2.missing_evidence
    assert p1.limitations == p2.limitations


def test_build_packet_source_preservation_and_no_fabrication():
    """Verify reported text and identifiers are never rewritten, fabricated, or altered."""
    user_report = "The lens has a circular scratch on the front optic element. Filter was missing."
    incident = Incident(
        id="inc-exact-id-77",
        resource_id="res-sony-fx3",
        reporter_id="user-borrower-44",
        description=user_report,
    )

    packet = build_packet(incident)

    # Reported description must be verbatim
    assert packet.reported_description == user_report
    assert packet.source_references["incident_id"] == "inc-exact-id-77"
    assert packet.source_references["reporter_id"] == "user-borrower-44"

    # Limitations must explicitly state lack of physical verification
    assert any("physically verified" in lim.lower() for lim in packet.limitations)
    assert any("does not establish borrower fault" in lim.lower() for lim in packet.limitations)
