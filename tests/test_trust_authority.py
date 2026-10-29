# tests/test_trust_authority.py
"""
Tests for ShareSafe Authority Gate and Bounded Autonomy.
Verifies all 9 mandatory requirements:
1. Agent can place precautionary hold
2. Agent cannot clear safety hold
3. Agent cannot reject request
4. Borrower cannot clear hold
5. Inspector without note cannot clear hold
6. Inspector with note can clear hold
7. Denied attempt creates DENIED: audit event
8. Undo restores previous state
9. Non-reversible action cannot be undone
"""
import pytest
from app.contracts import Actor
from app.core.audit import InMemoryAuditStore, get_audit_trail, set_audit_store
from app.core.authority import execute, undo, _reversible_journal


@pytest.fixture(autouse=True)
def setup_audit_store():
    store = InMemoryAuditStore()
    set_audit_store(store)
    _reversible_journal.clear()
    yield store
    store.clear()
    _reversible_journal.clear()


def test_1_agent_can_place_precautionary_hold():
    """1. Agent is authorized to place a precautionary safety hold (reversible action)."""
    agent_actor = Actor(id="agent:custodian", kind="agent", role="agent")
    res = execute(
        actor=agent_actor,
        action="place_precautionary_hold",
        args={"resource_id": "res-gpu-01", "reason": "Fan fault anomaly detected"},
    )
    assert res["status"] == "ok"
    assert res["action"] == "place_precautionary_hold"
    assert res["result"]["active"] is True

    trail = get_audit_trail()
    assert len(trail) == 1
    assert trail[0]["action"] == "place_precautionary_hold"
    assert trail[0]["actor"] == "agent:custodian"


def test_2_agent_cannot_clear_safety_hold():
    """2. Agent is strictly denied from clearing a safety hold (human-only)."""
    agent_actor = Actor(id="agent:custodian", kind="agent", role="agent")
    with pytest.raises(PermissionError) as exc_info:
        execute(
            actor=agent_actor,
            action="clear_safety_hold",
            args={"resource_id": "res-gpu-01", "note": "All good"},
        )
    assert "strictly forbidden" in str(exc_info.value)

    trail = get_audit_trail()
    assert len(trail) == 1
    assert trail[0]["action"] == "DENIED:clear_safety_hold"
    assert trail[0]["actor"] == "agent:custodian"


def test_3_agent_cannot_reject_request():
    """3. Agent cannot reject a request; rejections strictly require human owners."""
    agent_actor = Actor(id="agent:custodian", kind="agent", role="agent")
    with pytest.raises(PermissionError) as exc_info:
        execute(
            actor=agent_actor,
            action="reject_request",
            args={"request_id": "req-123", "reason": "Over capacity"},
        )
    assert "strictly forbidden" in str(exc_info.value)

    trail = get_audit_trail()
    assert len(trail) == 1
    assert trail[0]["action"] == "DENIED:reject_request"


def test_4_borrower_cannot_clear_hold():
    """4. Human borrower cannot clear holds; role is unauthorized."""
    borrower_actor = Actor(id="user:maya", kind="human", role="borrower")
    with pytest.raises(PermissionError) as exc_info:
        execute(
            actor=borrower_actor,
            action="clear_safety_hold",
            args={"resource_id": "res-gpu-01", "note": "I fixed it myself"},
        )
    assert "not authorized" in str(exc_info.value)

    trail = get_audit_trail()
    assert len(trail) == 1
    assert trail[0]["action"] == "DENIED:clear_safety_hold"
    assert trail[0]["actor"] == "user:maya"


def test_5_inspector_without_note_cannot_clear_hold():
    """5. Inspector attempting to clear hold without written note is denied."""
    inspector_actor = Actor(id="user:dr_thorne", kind="human", role="inspector")
    with pytest.raises(PermissionError) as exc_info:
        execute(
            actor=inspector_actor,
            action="clear_safety_hold",
            args={"resource_id": "res-gpu-01", "note": ""},
        )
    assert "strictly requires a non-empty written" in str(exc_info.value)

    trail = get_audit_trail()
    assert len(trail) == 1
    assert trail[0]["action"] == "DENIED:clear_safety_hold"


def test_6_inspector_with_note_can_clear_hold():
    """6. Authorized inspector with a verified written note successfully clears hold."""
    inspector_actor = Actor(id="user:dr_thorne", kind="human", role="inspector")
    res = execute(
        actor=inspector_actor,
        action="clear_safety_hold",
        args={
            "resource_id": "res-gpu-01",
            "note": "Replaced blower fan assembly, tested under full GPU load, thermal profile nominal.",
        },
    )
    assert res["status"] == "ok"
    assert res["action"] == "clear_safety_hold"

    trail = get_audit_trail()
    assert len(trail) == 1
    assert trail[0]["action"] == "clear_safety_hold"
    assert trail[0]["actor"] == "user:dr_thorne"


def test_7_denied_attempt_creates_denied_audit_event():
    """7. Any denied action writes an audit event with prefix DENIED:<action> BEFORE error."""
    owner_actor = Actor(id="user:marcus", kind="human", role="owner")
    # Owners cannot apply penalties (admin only)
    with pytest.raises(PermissionError):
        execute(
            actor=owner_actor,
            action="apply_penalty",
            args={"user_id": "user:maya", "penalty": "strike_1"},
        )

    trail = get_audit_trail()
    assert len(trail) == 1
    assert trail[0]["action"] == "DENIED:apply_penalty"
    assert trail[0]["actor"] == "user:marcus"
    assert "reason" in trail[0]["payload"]


def test_8_undo_restores_previous_state():
    """8. Undo of a reversible agent action restores prior state and writes audit log."""
    agent_actor = Actor(id="agent:custodian", kind="agent", role="agent")
    exec_res = execute(
        actor=agent_actor,
        action="place_precautionary_hold",
        args={"resource_id": "res-gpu-01", "reason": "Precautionary"},
    )
    audit_hash = exec_res["audit_hash"]

    # Owner performs undo
    owner_actor = Actor(id="user:marcus", kind="human", role="owner")
    undo_res = undo(actor=owner_actor, audit_id=audit_hash)

    assert undo_res["status"] == "ok"
    assert undo_res["undone_action"] == "place_precautionary_hold"
    assert undo_res["restored_state"]["active"] is False

    trail = get_audit_trail()
    assert len(trail) == 2
    assert trail[0]["action"] == "place_precautionary_hold"
    assert trail[1]["action"] == "undo_place_precautionary_hold"
    assert trail[1]["actor"] == "user:marcus"


def test_9_non_reversible_action_cannot_be_undone():
    """9. Non-reversible actions cannot be undone."""
    inspector_actor = Actor(id="user:dr_thorne", kind="human", role="inspector")
    exec_res = execute(
        actor=inspector_actor,
        action="clear_safety_hold",
        args={"resource_id": "res-gpu-01", "note": "Inspected and resolved."},
    )
    audit_hash = exec_res["audit_hash"]

    with pytest.raises(ValueError) as exc_info:
        undo(actor=inspector_actor, audit_id=audit_hash)
    assert "No reversible action found" in str(exc_info.value)
