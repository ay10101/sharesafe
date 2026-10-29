# tests/test_lifecycle.py
"""
Tests for ShareSafe Booking Lifecycle.
Covers:
- Complete journey (request -> approve -> check-in -> return)
- Rejected journey (request -> reject -> resource reverts)
- Illegal transitions (check-in without approval, double return, missing condition notes)
- Overlap detection and concurrent double-booking race condition
"""
import concurrent.futures
from datetime import datetime, timedelta
from pathlib import Path
import pytest

from app.core.lifecycle import (
    BookingStatus,
    ConditionNoteRequiredError,
    IllegalTransitionError,
    SlotOverlapError,
    create_booking_request,
    transition_booking,
)
from app.db.seed import seed_database
from app.db.session import get_connection


@pytest.fixture
def test_db(tmp_path: Path):
    db_file = tmp_path / "test_sharesafe.db"
    seed_database(db_file)
    return db_file


def test_complete_journey(test_db):
    """
    Test a complete happy path lifecycle:
    request -> approve -> check-in -> return
    """
    start = (datetime.utcnow() + timedelta(days=1)).replace(hour=10, minute=0, second=0).isoformat()
    end = (datetime.utcnow() + timedelta(days=1)).replace(hour=12, minute=0, second=0).isoformat()

    # 1. Request
    booking = create_booking_request(
        resource_id="r1",
        requester_id="u1",
        start_date=start,
        end_date=end,
        message="Need GPU for thesis benchmark",
        purpose="Academic Research",
        training_acknowledged=True,
        db_path=test_db,
    )
    assert booking["status"] == "requested"
    assert booking["decision_trace_json"] is not None
    booking_id = booking["id"]

    # Resource should reflect requested
    conn = get_connection(test_db)
    res = conn.execute("SELECT status FROM resources WHERE id = 'r1'").fetchone()
    assert res[0] == "requested"
    conn.close()

    # 2. Approve
    approved = transition_booking(
        booking_id=booking_id,
        target_status=BookingStatus.APPROVED,
        actor_id="u2",
        owner_reason="Approved for thesis benchmark",
        db_path=test_db,
    )
    assert approved["status"] == "approved"
    assert approved["approved_at"] is not None

    # 3. Check-in (with condition note)
    checked_in = transition_booking(
        booking_id=booking_id,
        target_status=BookingStatus.CHECKED_IN,
        actor_id="u1",
        condition_note="Clean boot, GPU idle at 34C, fans spinning normally.",
        db_path=test_db,
    )
    assert checked_in["status"] == "checked_in"
    assert checked_in["checked_in_at"] is not None

    # Check resource is checked_out
    conn = get_connection(test_db)
    res = conn.execute("SELECT status FROM resources WHERE id = 'r1'").fetchone()
    assert res[0] == "checked_out"

    # Verify initial condition record persisted
    cond_before = conn.execute(
        "SELECT phase, notes, recorded_by FROM conditions WHERE booking_id = ? AND phase = 'before'",
        (booking_id,),
    ).fetchone()
    assert cond_before is not None
    assert "Clean boot" in cond_before[1]
    conn.close()

    # 4. Return (with condition note)
    returned = transition_booking(
        booking_id=booking_id,
        target_status=BookingStatus.RETURNED,
        actor_id="u1",
        condition_note="Run finished safely, workstation wiped, ready for next user.",
        db_path=test_db,
    )
    assert returned["status"] == "returned"
    assert returned["returned_at"] is not None

    # Verify return condition record persisted
    conn = get_connection(test_db)
    cond_after = conn.execute(
        "SELECT phase, notes, recorded_by FROM conditions WHERE booking_id = ? AND phase = 'after'",
        (booking_id,),
    ).fetchone()
    assert cond_after is not None
    assert "Run finished safely" in cond_after[1]

    # Verify resource reverted to available
    res = conn.execute("SELECT status FROM resources WHERE id = 'r1'").fetchone()
    assert res[0] == "available"
    conn.close()


def test_rejected_journey(test_db):
    """
    Test a rejected journey:
    request -> reject -> verify resource is available and not stuck.
    """
    start = (datetime.utcnow() + timedelta(days=2)).replace(hour=14, minute=0, second=0).isoformat()
    end = (datetime.utcnow() + timedelta(days=2)).replace(hour=16, minute=0, second=0).isoformat()

    booking = create_booking_request(
        resource_id="r1",
        requester_id="u1",
        start_date=start,
        end_date=end,
        message="Non-academic gaming benchmark",
        db_path=test_db,
    )
    booking_id = booking["id"]
    assert booking["status"] == "requested"

    # Reject
    rejected = transition_booking(
        booking_id=booking_id,
        target_status=BookingStatus.REJECTED,
        actor_id="u2",
        owner_reason="Non-academic workload not permitted on GPU node.",
        db_path=test_db,
    )
    assert rejected["status"] == "rejected"
    assert rejected["rejected_at"] is not None

    # Check resource is immediately available again
    conn = get_connection(test_db)
    res = conn.execute("SELECT status FROM resources WHERE id = 'r1'").fetchone()
    assert res[0] == "available"
    conn.close()

    # Attempting to check-in to rejected request must fail
    with pytest.raises(IllegalTransitionError):
        transition_booking(
            booking_id=booking_id,
            target_status=BookingStatus.CHECKED_IN,
            actor_id="u1",
            condition_note="Trying to sneak in",
            db_path=test_db,
        )


def test_illegal_transitions_and_guards(test_db):
    """
    Verify server refuses all illegal transitions:
    - check-in without approval
    - return without check-in
    - double return
    - missing condition notes
    """
    start = (datetime.utcnow() + timedelta(days=3)).replace(hour=9, minute=0, second=0).isoformat()
    end = (datetime.utcnow() + timedelta(days=3)).replace(hour=11, minute=0, second=0).isoformat()

    booking = create_booking_request(
        resource_id="r2",
        requester_id="u1",
        start_date=start,
        end_date=end,
        message="Film shoot",
        db_path=test_db,
    )
    booking_id = booking["id"]

    # 1. Check-in directly from requested (without approval) -> Illegal
    with pytest.raises(IllegalTransitionError):
        transition_booking(
            booking_id=booking_id,
            target_status=BookingStatus.CHECKED_IN,
            actor_id="u1",
            condition_note="Skipping approval",
            db_path=test_db,
        )

    # 2. Return directly from requested -> Illegal
    with pytest.raises(IllegalTransitionError):
        transition_booking(
            booking_id=booking_id,
            target_status=BookingStatus.RETURNED,
            actor_id="u1",
            condition_note="Returning before even borrowing",
            db_path=test_db,
        )

    # Now approve it
    transition_booking(
        booking_id=booking_id,
        target_status=BookingStatus.APPROVED,
        actor_id="u2",
        db_path=test_db,
    )

    # 3. Return from approved (without check-in) -> Illegal
    with pytest.raises(IllegalTransitionError):
        transition_booking(
            booking_id=booking_id,
            target_status=BookingStatus.RETURNED,
            actor_id="u1",
            condition_note="Returning without checkout",
            db_path=test_db,
        )

    # 4. Check-in without condition note -> ConditionNoteRequiredError
    with pytest.raises(ConditionNoteRequiredError):
        transition_booking(
            booking_id=booking_id,
            target_status=BookingStatus.CHECKED_IN,
            actor_id="u1",
            condition_note="",
            db_path=test_db,
        )

    # Valid check-in
    transition_booking(
        booking_id=booking_id,
        target_status=BookingStatus.CHECKED_IN,
        actor_id="u1",
        condition_note="Lenses pristine, batteries full.",
        db_path=test_db,
    )

    # 5. Return without condition note -> ConditionNoteRequiredError
    with pytest.raises(ConditionNoteRequiredError):
        transition_booking(
            booking_id=booking_id,
            target_status=BookingStatus.RETURNED,
            actor_id="u1",
            condition_note="   ",
            db_path=test_db,
        )

    # Valid return
    transition_booking(
        booking_id=booking_id,
        target_status=BookingStatus.RETURNED,
        actor_id="u1",
        condition_note="Lenses capped, all accessories returned.",
        db_path=test_db,
    )

    # 6. Double return -> Illegal
    with pytest.raises(IllegalTransitionError):
        transition_booking(
            booking_id=booking_id,
            target_status=BookingStatus.RETURNED,
            actor_id="u1",
            condition_note="Returning second time",
            db_path=test_db,
        )


def test_concurrent_double_booking_race(test_db):
    """
    Two simultaneous requests for the exact same slot.
    Under atomic DB transaction, exactly ONE must succeed, and the other must fail with SlotOverlapError.
    """
    start = (datetime.utcnow() + timedelta(days=5)).replace(hour=14, minute=0, second=0).isoformat()
    end = (datetime.utcnow() + timedelta(days=5)).replace(hour=18, minute=0, second=0).isoformat()

    results = []
    errors = []

    def try_book(user_id: str):
        try:
            res = create_booking_request(
                resource_id="r1",
                requester_id=user_id,
                start_date=start,
                end_date=end,
                message=f"Concurrent booking by {user_id}",
                db_path=test_db,
            )
            results.append(res)
        except Exception as e:
            errors.append(e)

    # Launch two concurrent attempts for the exact same slot
    with concurrent.futures.ThreadPoolExecutor(max_workers=2) as executor:
        f1 = executor.submit(try_book, "u1")
        f2 = executor.submit(try_book, "u5")
        f1.result()
        f2.result()

    # Exactly one succeeded
    assert len(results) == 1, f"Expected exactly 1 booking to win, got {len(results)}"
    # Exactly one failed with SlotOverlapError
    assert len(errors) == 1, f"Expected exactly 1 error, got {len(errors)}"
    assert isinstance(errors[0], SlotOverlapError)

