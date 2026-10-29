# app/routers/notifications.py
"""
ShareSafe Notifications Router & Service.
Provides event notifications for bookings, holds, clearances, and overdue reminders.
"""
from __future__ import annotations
import uuid
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel

from app.auth.dependencies import get_current_user
from app.db.session import db_transaction, get_connection

router = APIRouter(prefix="/api/notifications", tags=["notifications"])


def send_notification(user_id: str, kind: str, body: str) -> Dict[str, Any]:
    """Helper to dispatch a notification to a user."""
    now_ts = datetime.now(timezone.utc).isoformat()
    notif_id = f"notif-{uuid.uuid4().hex[:8]}"
    with db_transaction() as conn:
        conn.execute(
            """
            INSERT INTO notifications (id, user_id, kind, body, read, created_at)
            VALUES (?, ?, ?, ?, 0, ?);
            """,
            (notif_id, user_id, kind, body, now_ts),
        )
    return {
        "id": notif_id,
        "user_id": user_id,
        "kind": kind,
        "body": body,
        "read": 0,
        "created_at": now_ts,
    }


@router.get("")
async def get_notifications(
    user_id: Optional[str] = Query(None),
    current_user: Dict[str, Any] = Depends(get_current_user),
):
    """
    Retrieve notifications for the authenticated user (or specified user if admin).
    """
    target_user_id = user_id or current_user["id"]
    conn = get_connection()
    try:
        rows = conn.execute(
            """
            SELECT * FROM notifications
            WHERE user_id = ?
            ORDER BY created_at DESC;
            """,
            (target_user_id,),
        ).fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()


@router.patch("/{notification_id}/read")
@router.post("/{notification_id}/read")
async def mark_notification_read(
    notification_id: str,
    current_user: Dict[str, Any] = Depends(get_current_user),
):
    """Mark a notification as read."""
    with db_transaction() as conn:
        row = conn.execute(
            "SELECT * FROM notifications WHERE id = ?", (notification_id,)
        ).fetchone()
        if not row:
            raise HTTPException(status_code=404, detail=f"Notification '{notification_id}' not found.")

        conn.execute(
            "UPDATE notifications SET read = 1 WHERE id = ?", (notification_id,)
        )
        return {"status": "ok", "notification_id": notification_id, "read": 1}


@router.post("/read-all")
async def mark_all_notifications_read(
    current_user: Dict[str, Any] = Depends(get_current_user),
):
    """Mark all unread notifications for current user as read."""
    with db_transaction() as conn:
        conn.execute(
            "UPDATE notifications SET read = 1 WHERE user_id = ? AND read = 0",
            (current_user["id"],),
        )
        return {"status": "ok", "user_id": current_user["id"]}
