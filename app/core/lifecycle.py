# app/core/lifecycle.py
"""
ShareSafe Booking Lifecycle State Machine.
Defines valid states, transition guards, transactional overlap checks,
condition note persistence, and audit logging.
"""
from __future__ import annotations
import json
import uuid
from datetime import datetime
from enum import Enum
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from app.contracts import (
    BookingCtx,
    Decision,
    RequestCtx,
    ResourceCtx,
    UserCtx,
    Verdict,
)
from app.core.audit import append_audit
from app.db.session import db_transaction, get_connection
from app.engine.decision import evaluate_request


class BookingStatus(str, Enum):
    REQUESTED = "requested"
    APPROVED = "approved"
    REJECTED = "rejected"
    WAITLISTED = "waitlisted"
    CHECKED_IN = "checked_in"
    RETURNED = "returned"
    CANCELLED = "cancelled"
    OVERDUE = "overdue"
    INCIDENT_OPEN = "incident_open"


# Canonical mapping for aliases used in legacy frontends/stores
STATUS_ALIASES = {
    "pending": BookingStatus.REQUESTED,
    "checked_out": BookingStatus.CHECKED_IN,
}

def normalize_status(status_str: str) -> BookingStatus:
    s = status_str.lower().strip()
    if s in STATUS_ALIASES:
        return STATUS_ALIASES[s]
    return BookingStatus(s)


ALLOWED_TRANSITIONS: Dict[BookingStatus, set[BookingStatus]] = {
    BookingStatus.REQUESTED: {
        BookingStatus.APPROVED,
        BookingStatus.REJECTED,
        BookingStatus.WAITLISTED,
        BookingStatus.CANCELLED,
    },
    BookingStatus.APPROVED: {
        BookingStatus.CHECKED_IN,
        BookingStatus.CANCELLED,
    },
    BookingStatus.WAITLISTED: {
        BookingStatus.APPROVED,
        BookingStatus.CANCELLED,
    },
    BookingStatus.CHECKED_IN: {
        BookingStatus.RETURNED,
        BookingStatus.OVERDUE,
        BookingStatus.INCIDENT_OPEN,
    },
    BookingStatus.OVERDUE: {
        BookingStatus.RETURNED,
        BookingStatus.INCIDENT_OPEN,
    },
    BookingStatus.INCIDENT_OPEN: {
        BookingStatus.RETURNED,
        BookingStatus.CANCELLED,
    },
    BookingStatus.RETURNED: set(),
    BookingStatus.REJECTED: set(),
    BookingStatus.CANCELLED: set(),
}


class LifecycleError(Exception):
    """Base exception for lifecycle state violations."""
    pass


class IllegalTransitionError(LifecycleError):
    """Raised when an invalid state transition is attempted."""
    pass


class SlotOverlapError(LifecycleError):
    """Raised when another booking already reserves the requested time slot."""
    pass


class ConditionNoteRequiredError(LifecycleError):
    """Raised when condition notes are missing at check-in or return."""
    pass


class ResourceUnavailableError(LifecycleError):
    """Raised when a resource has a safety hold or is in maintenance."""
    pass


def _now() -> str:
    return datetime.utcnow().isoformat()


def check_slot_overlap(
    conn,
    resource_id: str,
    start_iso: str,
    end_iso: str,
    exclude_booking_id: Optional[str] = None,
) -> bool:
    """
    Check if [start_iso, end_iso) overlaps with any active booking for resource_id.
    Active statuses: requested, approved, checked_in (and aliases).
    Returns True if an overlap exists.
    """
    query = """
    SELECT id, start, end, status FROM bookings
    WHERE resource_id = ?
      AND status IN ('requested', 'pending', 'approved', 'checked_in', 'checked_out')
      AND start < ?
      AND end > ?
    """
    params = [resource_id, end_iso, start_iso]
    if exclude_booking_id:
        query += " AND id != ?"
        params.append(exclude_booking_id)

    cur = conn.execute(query, params)
    rows = cur.fetchall()
    return len(rows) > 0


def create_booking_request(
    resource_id: str,
    requester_id: str,
    start_date: str,
    end_date: str,
    message: Optional[str] = None,
    purpose: Optional[str] = None,
    training_acknowledged: bool = False,
    justification: str = "",
    db_path: Optional[Path] = None,
) -> Dict[str, Any]:
    """
    Submit a new booking request.
    Executes in an atomic transaction:
    1. Validates resource readiness
    2. Enforces overlap check
    3. Runs decision engine evaluate_request()
    4. Persists booking with decision_trace_json
    5. Appends audit log
    """
    with db_transaction(db_path) as conn:
        # 1. Fetch resource
        res_row = conn.execute("SELECT * FROM resources WHERE id = ?", (resource_id,)).fetchone()
        if not res_row:
            raise ValueError(f"Resource '{resource_id}' does not exist.")
        
        resource = dict(res_row)
        if resource.get("readiness_state") == "not_ready":
            raise ResourceUnavailableError(
                f"Resource '{resource['name']}' is Not Ready (active hold or maintenance). New requests blocked."
            )

        # 2. Fetch requester user
        user_row = conn.execute("SELECT * FROM users WHERE id = ?", (requester_id,)).fetchone()
        if not user_row:
            raise ValueError(f"User '{requester_id}' does not exist.")
        user = dict(user_row)

        # 3. Check for overlap in this transaction
        overlap = check_slot_overlap(conn, resource_id, start_date, end_date)
        if overlap:
            raise SlotOverlapError(
                f"Requested time slot ({start_date} to {end_date}) overlaps with an existing booking."
            )

        # 4. Fetch past history for decision engine context
        hist_rows = conn.execute(
            "SELECT * FROM bookings WHERE resource_id = ? ORDER BY created_at DESC LIMIT 50",
            (resource_id,),
        ).fetchall()
        
        user_training = []
        try:
            user_training = json.loads(user.get("training") or "[]")
        except Exception:
            pass

        user_ctx = UserCtx(
            id=user["id"],
            role=user["role"],
            year=user.get("year", 1),
            training=user_training,
            team_id=user.get("team_id"),
        )
        res_rules = {}
        try:
            res_rules = json.loads(resource.get("rules_json") or "{}")
        except Exception:
            pass

        res_ctx = ResourceCtx(
            id=resource["id"],
            category=resource["category"],
            rules=res_rules,
            readiness=resource.get("readiness_state", "ready"),
        )
        try:
            parsed_start = datetime.fromisoformat(start_date.replace("Z", "+00:00"))
            parsed_end = datetime.fromisoformat(end_date.replace("Z", "+00:00"))
        except Exception:
            parsed_start = datetime.utcnow()
            parsed_end = datetime.utcnow()

        req_ctx = RequestCtx(
            user=user_ctx,
            resource_id=resource_id,
            start=parsed_start,
            end=parsed_end,
            created_at=datetime.utcnow(),
            justification=justification or message or "",
        )

        decision: Decision = evaluate_request(req_ctx, res_ctx)
        decision_dict = {
            "verdict": decision.verdict.value if hasattr(decision.verdict, "value") else str(decision.verdict),
            "rule_results": [
                {
                    "rule_id": r.rule_id,
                    "passed": r.passed,
                    "severity": r.severity.value if hasattr(r.severity, "value") else str(r.severity),
                    "message": r.message,
                }
                for r in decision.rule_results
            ],
            "priority": {
                "total": decision.priority.total,
                "components": decision.priority.components,
                "tiebreak": decision.priority.tiebreak,
            }
            if decision.priority
            else None,
            "waitlist_position": decision.waitlist_position,
            "explanation": decision.explanation,
        }

        # 5. Insert booking
        booking_id = f"req-{uuid.uuid4().hex[:8]}"
        initial_status = BookingStatus.WAITLISTED.value if decision.verdict == Verdict.WAITLIST else BookingStatus.REQUESTED.value
        now_ts = _now()

        conn.execute(
            """
            INSERT INTO bookings (
                id, resource_id, user_id, owner_id, start, end, status,
                message, purpose, justification, training_acknowledged,
                created_at, decision_trace_json, team_id
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?);
            """,
            (
                booking_id,
                resource_id,
                requester_id,
                resource["owner_id"],
                start_date,
                end_date,
                initial_status,
                message,
                purpose,
                justification or message or "",
                1 if training_acknowledged else 0,
                now_ts,
                json.dumps(decision_dict),
                user.get("team_id"),
            ),
        )

        # Update resource status if currently available
        if resource.get("status") == "available":
            conn.execute("UPDATE resources SET status = 'requested' WHERE id = ?", (resource_id,))

        append_audit(
            actor_id=requester_id,
            action="create_request",
            payload={
                "booking_id": booking_id,
                "resource_id": resource_id,
                "start": start_date,
                "end": end_date,
                "status": initial_status,
                "verdict": decision_dict["verdict"],
            },
        )

        # Return enriched dict
        row = conn.execute("SELECT * FROM bookings WHERE id = ?", (booking_id,)).fetchone()
        return dict(row)


def transition_booking(
    booking_id: str,
    target_status: BookingStatus,
    actor_id: str,
    condition_note: Optional[str] = None,
    owner_reason: Optional[str] = None,
    incident_type: Optional[str] = None,
    incident_description: Optional[str] = None,
    db_path: Optional[Path] = None,
) -> Dict[str, Any]:
    """
    Execute a validated state transition on a booking.
    Enforces allowed transitions, condition notes, and audit logs.
    """
    with db_transaction(db_path) as conn:
        b_row = conn.execute("SELECT * FROM bookings WHERE id = ?", (booking_id,)).fetchone()
        if not b_row:
            raise ValueError(f"Booking '{booking_id}' not found.")
        booking = dict(b_row)

        current_status = normalize_status(booking["status"])
        allowed = ALLOWED_TRANSITIONS.get(current_status, set())

        if target_status not in allowed:
            raise IllegalTransitionError(
                f"Illegal transition: Cannot move booking '{booking_id}' from '{current_status.value}' to '{target_status.value}'."
            )

        now_ts = _now()
        updates: Dict[str, Any] = {"status": target_status.value}

        # Handle specific target states
        if target_status == BookingStatus.APPROVED:
            # An owner cannot silently approve around an active safety hold
            res_row = conn.execute(
                "SELECT name, status, readiness_state FROM resources WHERE id = ?",
                (booking["resource_id"],)
            ).fetchone()
            if res_row and (res_row[1] == "not_ready" or res_row[2] == "not_ready"):
                raise ResourceUnavailableError(
                    f"Cannot approve: Resource '{res_row[0]}' is currently Not Ready. Clear safety holds before approval."
                )
            hold = conn.execute(
                "SELECT id, reason FROM holds WHERE resource_id = ? AND status = 'active' LIMIT 1",
                (booking["resource_id"],)
            ).fetchone()
            if hold:
                raise ResourceUnavailableError(
                    f"Cannot approve: Resource is under active safety hold '{hold[0]}': {hold[1]}. Must be cleared by Inspector first."
                )

            updates["approved_at"] = now_ts
            if owner_reason:
                updates["owner_reason"] = owner_reason
            conn.execute("UPDATE resources SET status = 'approved' WHERE id = ?", (booking["resource_id"],))

        elif target_status == BookingStatus.REJECTED:
            updates["rejected_at"] = now_ts
            if owner_reason:
                updates["owner_reason"] = owner_reason
            # Resource reverts to available if no other active booking holds it
            _revert_resource_if_unoccupied(conn, booking["resource_id"], exclude_booking_id=booking_id)

        elif target_status == BookingStatus.CANCELLED:
            updates["cancelled_at"] = now_ts
            _revert_resource_if_unoccupied(conn, booking["resource_id"], exclude_booking_id=booking_id)

        elif target_status == BookingStatus.CHECKED_IN:
            # Active safety hold check: cannot dispatch equipment if under hold or not_ready
            res_row = conn.execute(
                "SELECT name, status, readiness_state FROM resources WHERE id = ?",
                (booking["resource_id"],)
            ).fetchone()
            if res_row and (res_row[1] == "not_ready" or res_row[2] == "not_ready"):
                raise ResourceUnavailableError(
                    f"Cannot check out: Resource '{res_row[0]}' is currently Not Ready. Equipment cannot be dispatched."
                )
            hold = conn.execute(
                "SELECT id, reason FROM holds WHERE resource_id = ? AND status = 'active' LIMIT 1",
                (booking["resource_id"],)
            ).fetchone()
            if hold:
                raise ResourceUnavailableError(
                    f"Cannot check out: Resource is under active safety hold '{hold[0]}': {hold[1]}. Equipment locked until cleared."
                )

            # Condition note is MANDATORY at check-in
            note = (condition_note or "").strip()
            if not note:
                raise ConditionNoteRequiredError("Condition note is required at check-in inspection.")
            updates["checked_in_at"] = now_ts
            cond_id = f"c-{uuid.uuid4().hex[:8]}"
            conn.execute(
                """
                INSERT INTO conditions (id, booking_id, phase, notes, recorded_by, created_at)
                VALUES (?, ?, 'before', ?, ?, ?);
                """,
                (cond_id, booking_id, note, actor_id, now_ts),
            )
            conn.execute("UPDATE resources SET status = 'checked_out' WHERE id = ?", (booking["resource_id"],))

        elif target_status == BookingStatus.RETURNED:
            # Condition note is MANDATORY at return
            note = (condition_note or "").strip()
            if not note:
                raise ConditionNoteRequiredError("Condition note is required at return inspection.")
            updates["returned_at"] = now_ts
            cond_id = f"c-{uuid.uuid4().hex[:8]}"
            conn.execute(
                """
                INSERT INTO conditions (id, booking_id, phase, notes, recorded_by, created_at)
                VALUES (?, ?, 'after', ?, ?, ?);
                """,
                (cond_id, booking_id, note, actor_id, now_ts),
            )

            # If incident/fault is reported on return
            if incident_type or incident_description:
                inc_id = f"inc-{uuid.uuid4().hex[:8]}"
                desc = incident_description or note
                conn.execute(
                    """
                    INSERT INTO incidents (id, resource_id, booking_id, reporter_id, description, incident_type, status, created_at)
                    VALUES (?, ?, ?, ?, ?, ?, 'open', ?);
                    """,
                    (inc_id, booking["resource_id"], booking_id, actor_id, desc, incident_type or "fault", now_ts),
                )
                # Auto-place precautionary hold and invoke custodian agent
                hold_id = f"hold-{uuid.uuid4().hex[:8]}"
                conn.execute(
                    """
                    INSERT INTO holds (id, resource_id, placed_by, reason, status, created_at)
                    VALUES (?, ?, ?, ?, 'active', ?);
                    """,
                    (hold_id, booking["resource_id"], "agent:custodian", f"Precautionary hold on {incident_type or 'fault'}: {desc}", now_ts),
                )
                conn.execute(
                    "UPDATE resources SET status = 'not_ready', readiness_state = 'not_ready', hold_id = ? WHERE id = ?",
                    (hold_id, booking["resource_id"]),
                )
                try:
                    from app.agent.custodian import on_incident
                    on_incident(inc_id, booking["resource_id"], {"booking_id": booking_id, "note": note})
                except Exception:
                    pass
            else:
                _revert_resource_if_unoccupied(conn, booking["resource_id"], exclude_booking_id=booking_id)

        # Apply updates to bookings table
        set_clauses = [f"{k} = ?" for k in updates.keys()]
        values = list(updates.values()) + [booking_id]
        conn.execute(f"UPDATE bookings SET {', '.join(set_clauses)} WHERE id = ?", values)

        # Audit event
        append_audit(
            actor_id=actor_id,
            action=f"transition_{target_status.value}",
            payload={"booking_id": booking_id, "from": current_status.value, "to": target_status.value, "updates": updates},
        )

        row = conn.execute("SELECT * FROM bookings WHERE id = ?", (booking_id,)).fetchone()
        return dict(row)


def _revert_resource_if_unoccupied(conn, resource_id: str, exclude_booking_id: Optional[str] = None) -> None:
    """
    Checks if there are any remaining active bookings or safety holds.
    If none, flips the resource status back to 'available'.
    """
    # Check if active hold exists
    hold = conn.execute(
        "SELECT id FROM holds WHERE resource_id = ? AND status = 'active'",
        (resource_id,),
    ).fetchone()
    if hold:
        conn.execute("UPDATE resources SET status = 'not_ready', readiness_state = 'not_ready' WHERE id = ?", (resource_id,))
        return

    # Check for active bookings
    active = conn.execute(
        """
        SELECT status FROM bookings
        WHERE resource_id = ?
          AND id != ?
          AND status IN ('checked_in', 'checked_out', 'approved', 'requested', 'pending')
        ORDER BY created_at DESC LIMIT 1
        """,
        (resource_id, exclude_booking_id or ""),
    ).fetchone()

    if active:
        stat = normalize_status(active[0])
        res_stat = "checked_out" if stat == BookingStatus.CHECKED_IN else "requested"
        conn.execute("UPDATE resources SET status = ? WHERE id = ?", (res_stat, resource_id))
    else:
        conn.execute("UPDATE resources SET status = 'available' WHERE id = ?", (resource_id,))

