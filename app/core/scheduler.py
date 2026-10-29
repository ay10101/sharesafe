# app/core/scheduler.py
"""
ShareSafe Overdue & Reminder Scheduler.
Detects overdue active equipment loans, transitions status to 'overdue',
dispatches borrower reminders, logs audit events, and ensures idempotency.
Can run as a background daemon or be manually triggered via admin endpoint for live hackathon demos.
"""
from __future__ import annotations
import threading
import time
import uuid
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from app.contracts import Actor
from app.core.audit import append_audit
from app.core.authority import execute
from app.db.session import db_transaction, get_connection


def check_overdue_bookings(now: Optional[datetime] = None) -> List[Dict[str, Any]]:
    """
    Scan for checked-in equipment loans that have passed their scheduled end time.
    Idempotent: Already flagged bookings are not duplicated.
    Transitions status to 'overdue', flags returned_late, sends notification,
    and records an audit event.
    """
    if now is None:
        now = datetime.now(timezone.utc)
    elif now.tzinfo is None:
        now = now.replace(tzinfo=timezone.utc)

    now_iso = now.isoformat()
    flagged: List[Dict[str, Any]] = []

    system_actor = Actor(id="agent:custodian", kind="agent", role="agent")

    with db_transaction() as conn:
        # Find bookings that are checked_in but past end time
        cur = conn.cursor()
        cur.execute(
            """
            SELECT b.id, b.resource_id, b.user_id, b.start, b.end, r.name as resource_name
            FROM bookings b
            JOIN resources r ON b.resource_id = r.id
            WHERE b.status = 'checked_in'
              AND b.end < ?;
            """,
            (now_iso,),
        )
        overdue_rows = cur.fetchall()

        for row in overdue_rows:
            b_id = row["id"]
            user_id = row["user_id"]
            res_id = row["resource_id"]
            res_name = row["resource_name"]

            # Update status to overdue and set returned_late = 1
            conn.execute(
                """
                UPDATE bookings
                SET status = 'overdue', returned_late = 1
                WHERE id = ?;
                """,
                (b_id,),
            )

            # Idempotently check if notification already sent for this overdue loan
            existing_notif = conn.execute(
                """
                SELECT id FROM notifications
                WHERE user_id = ? AND kind = 'overdue' AND body LIKE ?;
                """,
                (user_id, f"%{b_id}%"),
            ).fetchone()

            if not existing_notif:
                notif_id = f"notif-{uuid.uuid4().hex[:8]}"
                notif_body = (
                    f"Action Required: Your reservation for {res_name} ({b_id}) ended at {row['end']} "
                    f"and is now OVERDUE. Please return the equipment to the dispatch locker immediately."
                )
                conn.execute(
                    """
                    INSERT INTO notifications (id, user_id, kind, body, read, created_at)
                    VALUES (?, ?, 'overdue', ?, 0, ?);
                    """,
                    (notif_id, user_id, notif_body, now_iso),
                )

            flagged.append(
                {
                    "booking_id": b_id,
                    "user_id": user_id,
                    "resource_id": res_id,
                    "resource_name": res_name,
                    "end_time": row["end"],
                }
            )

    # Log authority executions & audit events outside the SQLite transaction lock
    for item in flagged:
        execute(
            actor=system_actor,
            action="flag_overdue",
            args={
                "booking_id": item["booking_id"],
                "user_id": item["user_id"],
                "resource_id": item["resource_id"],
            },
        )

    return flagged


_scheduler_running = False
_scheduler_thread: Optional[threading.Thread] = None


def _background_loop(interval_seconds: int = 60):
    global _scheduler_running
    while _scheduler_running:
        try:
            check_overdue_bookings()
        except Exception:
            pass
        time.sleep(interval_seconds)


def start_overdue_scheduler(interval_seconds: int = 60) -> None:
    """Start the lightweight background overdue monitoring thread."""
    global _scheduler_running, _scheduler_thread
    if not _scheduler_running:
        _scheduler_running = True
        _scheduler_thread = threading.Thread(
            target=_background_loop, args=(interval_seconds,), daemon=True
        )
        _scheduler_thread.start()


def stop_overdue_scheduler() -> None:
    """Stop the background monitoring thread."""
    global _scheduler_running
    _scheduler_running = False
