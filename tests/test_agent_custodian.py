"""
Unit tests for app.agent.custodian (Custodian Agent Implementation).
Covers workflow execution, authority integration, idempotency, role enforcement, and failure safety.
"""
from __future__ import annotations

from unittest.mock import MagicMock

from app.agent.contracts import (
    HUMAN_ONLY_ACTIONS,
    Actor,
    HistoricalIncident,
    Incident,
    UpcomingBooking,
)
from app.agent.custodian import (
    SimpleAuditLogger,
    SimpleAuthority,
    SimpleNotifier,
    handle_incident,
)


def _make_sample_incident():
    return Incident(
        id="inc-cust-01",
        resource_id="res-laser-cutter",
        reporter_id="user-student-01",
        incident_type="damage",
        description="Coolant pump warning light triggered during raster job.",
        severity="high",
    )


def test_custodian_agent_normal_permitted_workflow():
    """Verify standard operational incident coordination flow with all services."""
    incident = _make_sample_incident()
    actor = Actor(id="agent-01", name="Custodian Agent", role="agent")
    authority = SimpleAuthority()
    audit_logger = SimpleAuditLogger()
    notifier = SimpleNotifier()

    # Mock services for bookings and history
    booking_service = MagicMock()
    booking_service.get_upcoming_bookings.return_value = [
        UpcomingBooking(
            request_id="b-101",
            resource_id="res-laser-cutter",
            requester_id="u2",
            start_date="2026-10-30",
            end_date="2026-10-31",
        )
    ]
    history_service = MagicMock()
    history_service.get_resource_incidents.return_value = [
        HistoricalIncident(
            incident_id="h-50",
            resource_id="res-laser-cutter",
            incident_type="malfunction",
            description="Chiller flow sensor clogged.",
            created_at="2026-08-10",
        )
    ]

    result = handle_incident(
        incident_id=incident.id,
        actor=actor,
        incident=incident,
        authority=authority,
        booking_service=booking_service,
        history_service=history_service,
        notifier=notifier,
        audit_logger=audit_logger,
        llm_enrich=False,
    )

    assert result.processed is True
    assert result.hold_status == "placed"
    assert result.hold_id is not None
    assert "res-laser-cutter" in authority.active_holds
    assert result.packet_generated is True
    assert result.packet is not None
    assert len(result.packet.upcoming_bookings) == 1
    assert len(result.packet.historical_incidents) == 1
    assert len(result.notifications_sent) == 1
    assert len(result.audit_records) == 2  # Hold placement + packet draft
    assert len(result.errors) == 0


def test_custodian_agent_authority_denial_respected():
    """When authority denies the hold, agent respects denial, audits it, and does not bypass."""
    incident = _make_sample_incident()
    actor = Actor(id="agent-01", name="Custodian Agent", role="agent")
    # Authority configured to deny
    mock_authority = MagicMock()
    from app.agent.contracts import AuthorityResult
    mock_authority.execute.return_value = AuthorityResult(
        success=False,
        action="place_precautionary_hold",
        actor_id=actor.id,
        denial_reason="Policy lock: Lab under administrative inspection moratorium.",
    )
    audit_logger = SimpleAuditLogger()

    result = handle_incident(
        incident_id=incident.id,
        actor=actor,
        incident=incident,
        authority=mock_authority,
        audit_logger=audit_logger,
    )

    assert result.processed is True
    assert result.hold_status == "denied"
    assert any("Authority denied" in err for err in result.errors)
    # Check that denial was recorded in audit log
    assert any(rec["outcome"] == "denied" for rec in audit_logger.records)


def test_custodian_agent_idempotent_duplicate_handling():
    """Processing the same incident repeatedly reuses existing hold without conflicts."""
    incident = _make_sample_incident()
    actor = Actor(id="agent-01", name="Custodian Agent", role="agent")
    authority = SimpleAuthority()

    res1 = handle_incident(incident.id, actor, incident=incident, authority=authority)
    res2 = handle_incident(incident.id, actor, incident=incident, authority=authority)

    assert res1.hold_status == "placed"
    assert res2.hold_status == "already_present"
    assert res1.hold_id == res2.hold_id


def test_custodian_agent_unauthorized_borrower_role_rejected():
    """Borrower role cannot trigger Custodian Agent operations."""
    incident = _make_sample_incident()
    borrower = Actor(id="student-eve", name="Eve", role="borrower")
    authority = SimpleAuthority()
    audit_logger = SimpleAuditLogger()

    result = handle_incident(
        incident_id=incident.id,
        actor=borrower,
        incident=incident,
        authority=authority,
        audit_logger=audit_logger,
    )

    assert result.processed is False
    assert result.hold_status == "denied"
    assert "res-laser-cutter" not in authority.active_holds
    assert any(rec["outcome"] == "denied" for rec in audit_logger.records)


def test_custodian_agent_missing_incident_safely_handled():
    """If incident is None / missing, handle_incident safely reports failure without crashing."""
    actor = Actor(id="agent-01", name="Custodian Agent", role="agent")
    authority = SimpleAuthority()
    result = handle_incident("non-existent-id", actor, incident=None, authority=authority)

    assert result.processed is False
    assert result.hold_status == "failed"
    assert any("not found" in err.lower() for err in result.errors)


def test_custodian_agent_notification_failure_resilient():
    """If notifier fails, agent captures error and completes remaining workflow safely."""
    incident = _make_sample_incident()
    actor = Actor(id="agent-01", name="Custodian Agent", role="agent")
    authority = SimpleAuthority()
    mock_notifier = MagicMock()
    mock_notifier.notify.side_effect = RuntimeError("SMTP server unreachable")

    result = handle_incident(
        incident.id,
        actor,
        incident=incident,
        authority=authority,
        notifier=mock_notifier,
    )

    assert result.processed is True
    assert result.hold_status == "placed"
    assert result.packet_generated is True
    assert any("Notification dispatch failed" in err for err in result.errors)


def test_human_only_actions_never_authorized_for_agent():
    """Authority strictly rejects all human-only actions when invoked by an agent actor."""
    agent_actor = Actor(id="agent-01", name="Custodian Agent", role="agent")
    authority = SimpleAuthority()

    for action in HUMAN_ONLY_ACTIONS:
        res = authority.execute(agent_actor, action, {"target": "user1"})
        assert res.success is False
        assert "human" in (res.denial_reason or "").lower()


# ---------------------------------------------------------------------------
# Regression Tests: Safe Authority Enforcement & No Silent In-Memory Fallback
# ---------------------------------------------------------------------------
def test_omitting_authority_fails_safely_without_silent_in_memory_hold():
    """Omitting the authority dependency must fail closed and cannot create a silent in-memory hold."""
    incident = _make_sample_incident()
    actor = Actor(id="agent-01", name="Custodian Agent", role="agent")

    # Call handle_incident without authority (authority=None)
    result = handle_incident(
        incident_id=incident.id,
        actor=actor,
        incident=incident,
        authority=None,
    )

    assert result.processed is False
    assert result.hold_status == "failed"
    assert result.hold_id is None
    assert result.packet_generated is False
    assert any("authority dependency is required" in err.lower() for err in result.errors)


def test_invalid_authority_dependency_fails_safely():
    """Supplying an invalid authority (e.g. non-callable, missing execute) fails closed."""
    incident = _make_sample_incident()
    actor = Actor(id="agent-01", name="Custodian Agent", role="agent")

    for invalid_auth in ["invalid_string", 42, object(), None]:
        result = handle_incident(
            incident_id=incident.id,
            actor=actor,
            incident=incident,
            authority=invalid_auth,
        )
        assert result.processed is False
        assert result.hold_status == "failed"
        assert result.hold_id is None
        assert any("authority dependency is required" in err.lower() for err in result.errors)


def test_explicitly_injected_test_authority_succeeds():
    """Explicitly injected SimpleAuthority successfully supports isolated unit testing."""
    incident = _make_sample_incident()
    actor = Actor(id="agent-01", name="Custodian Agent", role="agent")
    test_auth = SimpleAuthority()

    result = handle_incident(
        incident_id=incident.id,
        actor=actor,
        incident=incident,
        authority=test_auth,
    )

    assert result.processed is True
    assert result.hold_status == "placed"
    assert result.hold_id is not None
    assert "res-laser-cutter" in test_auth.active_holds


def test_system_authority_adapter_integration():
    """SystemAuthorityAdapter bridges to central authority and respects role limits."""
    from app.agent.custodian import SystemAuthorityAdapter
    adapter = SystemAuthorityAdapter()
    agent_actor = Actor(id="agent:custodian", name="Custodian Agent", role="agent")

    # Allowed agent action
    res_allowed = adapter.execute(agent_actor, "place_precautionary_hold", {"resource_id": "r1", "reason": "Test hold"})
    assert res_allowed.success is True
    assert res_allowed.action == "place_precautionary_hold"

    # Human-only action must be denied through adapter
    res_denied = adapter.execute(agent_actor, "clear_safety_hold", {"resource_id": "r1", "note": "Trying bypass"})
    assert res_denied.success is False
    assert "strictly forbidden" in res_denied.denial_reason.lower() or "denied" in res_denied.denial_reason.lower()


def test_on_incident_hook():
    """on_incident hook coordinates evidence packet and authority hold."""
    from app.agent.custodian import on_incident
    result = on_incident(
        incident_id="inc-hook-01",
        resource_id="res-laser-cutter",
        context={"type": "safety", "description": "High temperature alert on laser tube"},
    )
    assert result.processed is True
    assert result.hold_status == "placed"
    assert result.packet_generated is True
    assert result.packet is not None
    assert result.packet.resource_id == "res-laser-cutter"


def test_historical_incident_query_column_alignment(tmp_path, monkeypatch):
    """Verify that querying historical incidents from SQLite correctly maps resolution_note to resolution."""
    test_db = tmp_path / "test_hist_query.db"
    from app.db.session import init_db, get_connection
    init_db(test_db)
    monkeypatch.setenv("DATABASE_PATH", str(test_db))

    conn = get_connection(test_db)
    try:
        conn.execute(
            """
            INSERT INTO users (id, name, email, role, kind)
            VALUES ('u-test-1', 'Test User', 'test@campus.edu', 'borrower', 'human');
            """
        )
        conn.execute(
            """
            INSERT INTO resources (id, name, category, location, owner_id, status, readiness_state, rules_json, created_at)
            VALUES ('res-drill', 'CNC Drill', 'machining', 'Bay 4', 'u-test-1', 'available', 'ready', '{}', '2026-10-01');
            """
        )
        # Prior incident with resolution_note column
        conn.execute(
            """
            INSERT INTO incidents (id, resource_id, booking_id, reporter_id, description, incident_type, status, resolution_note, created_at)
            VALUES ('inc-past-01', 'res-drill', NULL, 'u-test-1', 'Collet slipped during milling', 'damage', 'resolved', 'Replaced collet with hardened steel', '2026-10-15');
            """
        )
        conn.commit()
    finally:
        conn.close()

    actor = Actor(id="agent:custodian", name="Custodian Agent", role="agent")
    test_auth = SimpleAuthority()

    current_inc = {
        "id": "inc-curr-02",
        "resource_id": "res-drill",
        "incident_type": "damage",
        "description": "Collet loose again",
        "severity": "medium",
    }

    result = handle_incident(
        incident_id="inc-curr-02",
        actor=actor,
        incident=current_inc,
        authority=test_auth,
        history_service=None,  # triggers SQLite query fallback
    )

    assert result.processed is True
    assert result.packet is not None
    assert len(result.packet.historical_incidents) == 1
    hist = result.packet.historical_incidents[0]
    assert hist.incident_id == "inc-past-01"
    assert hist.resolution == "Replaced collet with hardened steel"



