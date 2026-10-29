# app/core/holds.py
"""
ShareSafe Safety Holds Management.
Enforces hold creation, inspector-only clearance with written notes,
borrower notifications, and updating affected upcoming bookings.
"""
from __future__ import annotations
import uuid
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from app.contracts import Actor
from app.core.audit import append_audit
from app.core.authority import execute
from app.db.session import db_transaction, get_connection


def place_safety_hold(
    resource_id: str,
    placed_by: str,
    reason: str,
    actor_kind: str = "human",
    actor_role: str = "owner",
    incident_id: Optional[str] = None,
) -> Dict[str, Any]:
    """
    Place a safety hold on a resource.
    - If placed by agent, verifies through authority gate.
    - Updates resource state to 'not_ready'.
    - Marks upcoming bookings on the resource as 'blocked_by_hold'.
    - Dispatches notifications to affected borrowers.
    - Appends entry to tamper-evident audit log.
    """
    now_ts = datetime.now(timezone.utc).isoformat()
    hold_id = f"hold-{uuid.uuid4().hex[:8]}"

    # Enforce through authority gate
    actor = Actor(id=placed_by, kind=actor_kind, role=actor_role)
    execute(
        actor=actor,
        action="place_precautionary_hold",
        args={"resource_id": resource_id, "reason": reason, "incident_id": incident_id},
    )

    with db_transaction() as conn:
        # 1. Insert into holds table
        conn.execute(
            """
            INSERT INTO holds (id, resource_id, placed_by, reason, status, created_at)
            VALUES (?, ?, ?, ?, 'active', ?);
            """,
            (hold_id, resource_id, placed_by, reason, now_ts),
        )

        # 2. Update resource readiness and status
        conn.execute(
            """
            UPDATE resources
            SET status = 'not_ready', readiness_state = 'not_ready', hold_id = ?
            WHERE id = ?;
            """,
            (hold_id, resource_id),
        )

        # 3. Find upcoming bookings on this resource that are requested or approved
        cur = conn.cursor()
        cur.execute(
            """
            SELECT id, user_id, start, end FROM bookings
            WHERE resource_id = ?
              AND status IN ('requested', 'approved', 'waitlisted')
              AND end >= ?;
            """,
            (resource_id, now_ts),
        )
        affected_bookings = cur.fetchall()

        # 4. Mark upcoming bookings as blocked_by_hold and notify borrowers
        blocked_ids = []
        for b in affected_bookings:
            b_id = b["id"]
            user_id = b["user_id"]
            blocked_ids.append(b_id)

            conn.execute(
                "UPDATE bookings SET status = 'blocked_by_hold', owner_reason = ? WHERE id = ?",
                (f"Blocked by safety hold: {reason}", b_id),
            )

            # Insert notification
            notif_id = f"notif-{uuid.uuid4().hex[:8]}"
            notif_body = (
                f"Your reservation ({b_id}) has been temporarily blocked due to a safety hold "
                f"placed on the equipment: {reason}"
            )
            conn.execute(
                """
                INSERT INTO notifications (id, user_id, kind, body, read, created_at)
                VALUES (?, ?, 'hold_placed', ?, 0, ?);
                """,
                (notif_id, user_id, notif_body, now_ts),
            )

            # Audit event for each blocked booking (using transaction connection)
            append_audit(
                actor_id=placed_by,
                action="booking_blocked_by_hold",
                payload={"booking_id": b_id, "resource_id": resource_id, "hold_id": hold_id, "reason": reason},
                conn=conn,
            )

    return {
        "status": "hold_placed",
        "hold_id": hold_id,
        "resource_id": resource_id,
        "placed_by": placed_by,
        "reason": reason,
        "blocked_bookings": blocked_ids,
        "created_at": now_ts,
    }


def clear_safety_hold_record(
    resource_id: str,
    cleared_by: str,
    cleared_note: str,
    actor_kind: str = "human",
    actor_role: str = "inspector",
    hold_id: Optional[str] = None,
) -> Dict[str, Any]:
    """
    Clear an active safety hold on a resource.
    - Strictly enforced: requires Inspector (or Admin) role and non-empty note.
    - Sets hold status to 'cleared'.
    - Restores resource readiness to 'ready' (or evaluates conditions).
    - Writes audit log and notifies affected borrowers.
    """
    now_ts = datetime.now(timezone.utc).isoformat()

    # Authority gate enforcement
    actor = Actor(id=cleared_by, kind=actor_kind, role=actor_role)
    execute(
        actor=actor,
        action="clear_safety_hold",
        args={"resource_id": resource_id, "note": cleared_note, "hold_id": hold_id},
    )

    with db_transaction() as conn:
        if hold_id:
            hold = conn.execute(
                "SELECT * FROM holds WHERE id = ? AND status = 'active'", (hold_id,)
            ).fetchone()
        else:
            hold = conn.execute(
                "SELECT * FROM holds WHERE resource_id = ? AND status = 'active' ORDER BY created_at DESC LIMIT 1",
                (resource_id,),
            ).fetchone()

        if not hold:
            raise ValueError(f"No active safety hold found for resource '{resource_id}'.")

        target_hold_id = hold["id"]

        # Update hold
        conn.execute(
            """
            UPDATE holds
            SET status = 'cleared', cleared_by = ?, cleared_note = ?, cleared_at = ?
            WHERE id = ?;
            """,
            (cleared_by, cleared_note, now_ts, target_hold_id),
        )

        # Check if any other active holds remain for this resource
        remaining_holds = conn.execute(
            "SELECT COUNT(*) FROM holds WHERE resource_id = ? AND status = 'active'", (resource_id,)
        ).fetchone()[0]

        if remaining_holds == 0:
            conn.execute(
                """
                UPDATE resources
                SET status = 'available', readiness_state = 'ready', hold_id = NULL
                WHERE id = ?;
                """,
                (resource_id,),
            )

        # Notify borrowers whose bookings were blocked that hold is cleared
        cur = conn.cursor()
        cur.execute(
            "SELECT id, user_id FROM bookings WHERE resource_id = ? AND status = 'blocked_by_hold'",
            (resource_id,),
        )
        blocked_bookings = cur.fetchall()
        for b in blocked_bookings:
            conn.execute(
                "UPDATE bookings SET status = 'requested', owner_reason = 'Safety hold cleared; re-submitted for approval' WHERE id = ?",
                (b["id"],),
            )
            notif_id = f"notif-{uuid.uuid4().hex[:8]}"
            notif_body = (
                f"Safety hold on equipment has been inspected and cleared by {cleared_by}: {cleared_note}. "
                f"Your reservation ({b['id']}) is restored to review queue."
            )
            conn.execute(
                """
                INSERT INTO notifications (id, user_id, kind, body, read, created_at)
                VALUES (?, ?, 'hold_cleared', ?, 0, ?);
                """,
                (notif_id, b["user_id"], notif_body, now_ts),
            )

        append_audit(
            actor_id=cleared_by,
            action="clear_safety_hold",
            payload={
                "resource_id": resource_id,
                "hold_id": target_hold_id,
                "cleared_note": cleared_note,
                "cleared_by": cleared_by,
            },
            conn=conn,
        )

        return {
            "status": "hold_cleared",
            "hold_id": target_hold_id,
            "resource_id": resource_id,
            "cleared_by": cleared_by,
            "cleared_note": cleared_note,
            "cleared_at": now_ts,
        }


def override_safety_hold(
    resource_id: str,
    inspector_id: str,
    override_reason: str,
    actor_role: str = "inspector",
) -> Dict[str, Any]:
    """
    Emergency override of safety hold through defined human authority process.
    - Requires named inspector, written reason, audit entry, and visibility to affected borrowers.
    """
    if not override_reason.strip():
        raise ValueError("Safety hold override strictly requires a written reason.")
    if actor_role not in ("inspector", "admin"):
        raise PermissionError("Only Authorized Inspectors or Admins can perform safety hold overrides.")

    append_audit(
        actor_id=inspector_id,
        action="override_safety_hold",
        payload={
            "resource_id": resource_id,
            "inspector_id": inspector_id,
            "override_reason": override_reason,
        },
    )

    return clear_safety_hold_record(
        resource_id=resource_id,
        cleared_by=inspector_id,
        cleared_note=f"[OVERRIDE by {inspector_id}]: {override_reason}",
        actor_kind="human",
        actor_role=actor_role,
    )
