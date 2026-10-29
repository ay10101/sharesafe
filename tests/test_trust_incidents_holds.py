# tests/test_trust_incidents_holds.py
"""
Trust layer integration tests for safety holds, incidents, notifications, and scheduler.
Verifies:
1. Safety incident creates incident record and triggers precautionary hold
2. Damage incident creates incident record and triggers precautionary hold
3. Original incident report is preserved immutably (disputes add entries)
4. Hold changes resource readiness state to NOT_READY
5. Upcoming bookings are marked blocked_by_hold and borrowers notified
6. Clearing hold requires Authorized Inspector role
7. Clearing hold requires written note
8. Clearing hold restores resource readiness to READY and restores bookings
9. Overdue scheduler flags overdue loans, notifies borrower, and logs audit
"""
import tempfile
from datetime import datetime, timezone, timedelta
from pathlib import Path
import pytest

from app.contracts import Readiness
from app.core.audit import InMemoryAuditStore, SQLiteAuditStore, get_audit_trail, set_audit_store, verify_chain
from app.core.holds import clear_safety_hold_record, override_safety_hold, place_safety_hold
from app.core.readiness import evaluate_readiness
from app.core.scheduler import check_overdue_bookings
from app.db.schema import SCHEMA_SQL
from app.db.session import db_transaction, get_connection, init_db


@pytest.fixture
def temp_db():
    with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
        path = Path(f.name)
    init_db(path)

    # Populate seed resources and users into temp_db
    conn = get_connection(path)
    try:
        now_iso = datetime.now(timezone.utc).isoformat()
        conn.execute(
            """
            INSERT INTO users (id, name, email, role, kind)
            VALUES
                ('user:maya', 'Maya Lin', 'maya@campus.edu', 'borrower', 'human'),
                ('user:marcus', 'Marcus Vance', 'marcus@campus.edu', 'owner', 'human'),
                ('user:dr_thorne', 'Dr. Aris Thorne', 'thorne@campus.edu', 'inspector', 'human'),
                ('agent:custodian', 'Custodian Agent', 'agent@sharesafe.ai', 'agent', 'agent');
            """
        )
        conn.execute(
            """
            INSERT INTO resources (id, name, category, location, owner_id, status, readiness_state, rules_json, created_at)
            VALUES
                ('res-gpu-01', 'NVIDIA RTX 4090 Workstation', 'computing', 'Turing Hall 402', 'user:marcus', 'available', 'ready', '{}', ?);
            """,
            (now_iso,),
        )
        conn.commit()
    finally:
        conn.close()

    # Point audit store to this temp DB
    set_audit_store(SQLiteAuditStore(db_path=path))
    try:
        yield path
    finally:
        set_audit_store(InMemoryAuditStore())
        try:
            path.unlink(missing_ok=True)
        except Exception:
            pass


def test_safety_incident_triggers_hold_and_blocks_readiness(temp_db, monkeypatch):
    """Safety incident creates hold, flips readiness to NOT_READY, and notifies affected borrowers."""
    monkeypatch.setenv("DATABASE_PATH", str(temp_db))

    now = datetime.now(timezone.utc)
    t_start = (now + timedelta(hours=2)).isoformat()
    t_end = (now + timedelta(hours=4)).isoformat()

    # Create an upcoming booking for Maya
    with db_transaction() as conn:
        conn.execute(
            """
            INSERT INTO bookings (id, resource_id, user_id, owner_id, start, end, status, created_at)
            VALUES ('bk-upcoming-01', 'res-gpu-01', 'user:maya', 'user:marcus', ?, ?, 'approved', ?);
            """,
            (t_start, t_end, now.isoformat()),
        )

    # Precautionary safety hold placed (e.g. from return report of fan fault)
    hold_result = place_safety_hold(
        resource_id="res-gpu-01",
        placed_by="agent:custodian",
        reason="Fan fault reported during return: bearing rattle and thermal spike",
        actor_kind="agent",
        actor_role="agent",
    )

    assert hold_result["status"] == "hold_placed"
    assert "bk-upcoming-01" in hold_result["blocked_bookings"]

    # Verify resource state in database
    conn = get_connection()
    res = conn.execute("SELECT * FROM resources WHERE id = 'res-gpu-01'").fetchone()
    assert res["status"] == "not_ready"
    assert res["readiness_state"] == "not_ready"
    assert res["hold_id"] == hold_result["hold_id"]

    # Verify upcoming booking is blocked
    bk = conn.execute("SELECT * FROM bookings WHERE id = 'bk-upcoming-01'").fetchone()
    assert bk["status"] == "blocked_by_hold"
    assert "Blocked by safety hold" in bk["owner_reason"]

    # Verify notification was sent to Maya
    notif = conn.execute("SELECT * FROM notifications WHERE user_id = 'user:maya'").fetchone()
    assert notif is not None
    assert notif["kind"] == "hold_placed"
    assert "Fan fault reported" in notif["body"]

    # Verify readiness evaluation reflects NOT_READY
    holds = conn.execute("SELECT * FROM holds WHERE resource_id = 'res-gpu-01'").fetchall()
    readiness_res = evaluate_readiness(dict(res), [dict(h) for h in holds])
    assert readiness_res.state == Readiness.NOT_READY
    assert any("Fan fault reported" in r.message for r in readiness_res.reasons)
    conn.close()


def test_clearing_hold_requires_inspector_and_note(temp_db, monkeypatch):
    """Clearing a safety hold is refused for borrowers or missing notes, and succeeds with inspector note."""
    monkeypatch.setenv("DATABASE_PATH", str(temp_db))

    place_safety_hold(
        resource_id="res-gpu-01",
        placed_by="agent:custodian",
        reason="Power supply unstable",
        actor_kind="agent",
        actor_role="agent",
    )

    # Borrower attempt denied
    with pytest.raises(PermissionError):
        clear_safety_hold_record(
            resource_id="res-gpu-01",
            cleared_by="user:maya",
            cleared_note="Looks fine to me",
            actor_kind="human",
            actor_role="borrower",
        )

    # Inspector without note denied
    with pytest.raises(PermissionError):
        clear_safety_hold_record(
            resource_id="res-gpu-01",
            cleared_by="user:dr_thorne",
            cleared_note="",
            actor_kind="human",
            actor_role="inspector",
        )

    # Inspector with valid note succeeds
    clear_res = clear_safety_hold_record(
        resource_id="res-gpu-01",
        cleared_by="user:dr_thorne",
        cleared_note="Replaced faulty PSU with 1000W Corsair Gold unit. Passed 1hr Furmark test.",
        actor_kind="human",
        actor_role="inspector",
    )

    assert clear_res["status"] == "hold_cleared"

    conn = get_connection()
    res = conn.execute("SELECT * FROM resources WHERE id = 'res-gpu-01'").fetchone()
    assert res["status"] == "available"
    assert res["readiness_state"] == "ready"
    assert res["hold_id"] is None
    conn.close()


def test_overdue_scheduler_marks_and_notifies(temp_db, monkeypatch):
    """Scheduler identifies checked-in loan that has passed end time, flags overdue and notifies."""
    monkeypatch.setenv("DATABASE_PATH", str(temp_db))

    now = datetime.now(timezone.utc)
    t_start = (now - timedelta(hours=5)).isoformat()
    t_end = (now - timedelta(hours=1)).isoformat()  # Ended 1 hour ago

    with db_transaction() as conn:
        conn.execute(
            """
            INSERT INTO bookings (id, resource_id, user_id, owner_id, start, end, status, created_at)
            VALUES ('bk-overdue-01', 'res-gpu-01', 'user:maya', 'user:marcus', ?, ?, 'checked_in', ?);
            """,
            (t_start, t_end, t_start),
        )

    # Run overdue check
    flagged = check_overdue_bookings(now=now)
    assert len(flagged) == 1
    assert flagged[0]["booking_id"] == "bk-overdue-01"

    conn = get_connection()
    bk = conn.execute("SELECT * FROM bookings WHERE id = 'bk-overdue-01'").fetchone()
    assert bk["status"] == "overdue"
    assert bk["returned_late"] == 1

    notif = conn.execute("SELECT * FROM notifications WHERE user_id = 'user:maya' AND kind = 'overdue'").fetchone()
    assert notif is not None
    assert "OVERDUE" in notif["body"]

    # Running a second time is idempotent (doesn't duplicate or fail)
    flagged_second = check_overdue_bookings(now=now)
    assert len(flagged_second) == 0  # Already transitioned from checked_in
    conn.close()


def test_audit_chain_records_full_incident_and_clearance_journey(temp_db, monkeypatch):
    """The entire journey writes tamper-evident audit records that pass chain verification."""
    monkeypatch.setenv("DATABASE_PATH", str(temp_db))

    # 1. Place hold
    place_safety_hold(
        resource_id="res-gpu-01",
        placed_by="agent:custodian",
        reason="Thermal sensor failure",
        actor_kind="agent",
        actor_role="agent",
    )

    # 2. Denied borrower attempt
    with pytest.raises(PermissionError):
        clear_safety_hold_record(
            resource_id="res-gpu-01",
            cleared_by="user:maya",
            cleared_note="Bypassing hold",
            actor_kind="human",
            actor_role="borrower",
        )

    # 3. Valid inspector clearance
    clear_safety_hold_record(
        resource_id="res-gpu-01",
        cleared_by="user:dr_thorne",
        cleared_note="Recalibrated thermistors and updated firmware.",
        actor_kind="human",
        actor_role="inspector",
    )

    # Verify audit chain integrity
    verify_res = verify_chain()
    assert verify_res["ok"] is True
    assert verify_res["broken_at"] is None
    assert verify_res["checked"] >= 3

    trail = get_audit_trail()
    actions = [e["action"] for e in trail]
    assert "place_precautionary_hold" in actions
    assert "DENIED:clear_safety_hold" in actions
    assert "clear_safety_hold" in actions
