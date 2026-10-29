# app/routers/incidents.py
"""
ShareSafe Incidents Router.
Handles incident reporting for damage, safety, delay, and dispute.
Preserves original reports immutably; disputes create separate entries.
Safety and damage incidents trigger agent triage and precautionary safety holds.
"""
from __future__ import annotations
import uuid
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel, Field

from app.auth.dependencies import get_current_user
from app.core.audit import append_audit
from app.core.holds import place_safety_hold
from app.db.session import db_transaction, get_connection

router = APIRouter(prefix="/api/incidents", tags=["incidents"])


class CreateIncidentRequest(BaseModel):
    type: str = Field(..., description="Incident type: damage, safety, delay, dispute")
    description: str = Field(..., description="Factual description of the event or condition")
    booking_id: Optional[str] = Field(None, description="Associated booking reservation ID")
    resource_id: Optional[str] = Field(None, description="Equipment or space resource ID")


@router.post("", status_code=status.HTTP_201_CREATED)
async def report_incident(
    payload: CreateIncidentRequest,
    current_user: Dict[str, Any] = Depends(get_current_user),
):
    """
    Report an incident against a resource or booking.
    - Original reports are never overwritten.
    - Disputes append separate entries.
    - Safety and damage reports immediately trigger custodian agent triage and safety holds.
    """
    inc_type = payload.type.lower().strip()
    allowed_types = {"damage", "delay", "safety", "dispute"}
    if inc_type not in allowed_types:
        raise HTTPException(
            status_code=400,
            detail=f"Invalid incident type '{payload.type}'. Allowed types: {', '.join(sorted(allowed_types))}",
        )

    if not payload.description.strip():
        raise HTTPException(status_code=422, detail="Incident description cannot be empty.")

    now_ts = datetime.now(timezone.utc).isoformat()
    incident_id = f"inc-{uuid.uuid4().hex[:8]}"

    res_id = payload.resource_id
    # If booking_id provided but not resource_id, resolve from booking
    if payload.booking_id and not res_id:
        conn = get_connection()
        try:
            b_row = conn.execute(
                "SELECT resource_id FROM bookings WHERE id = ?", (payload.booking_id,)
            ).fetchone()
            if b_row:
                res_id = b_row["resource_id"]
        finally:
            conn.close()

    if not res_id:
        raise HTTPException(
            status_code=400,
            detail="Resource ID could not be determined. Please provide resource_id or a valid booking_id.",
        )

    # Insert incident record into database
    with db_transaction() as conn:
        conn.execute(
            """
            INSERT INTO incidents (id, resource_id, booking_id, reporter_id, description, incident_type, status, created_at)
            VALUES (?, ?, ?, ?, ?, ?, 'open', ?);
            """,
            (
                incident_id,
                res_id,
                payload.booking_id,
                current_user["id"],
                payload.description,
                inc_type,
                now_ts,
            ),
        )

    # Audit event for incident reporting
    append_audit(
        actor_id=current_user["id"],
        action="report_incident",
        payload={
            "incident_id": incident_id,
            "incident_type": inc_type,
            "resource_id": res_id,
            "booking_id": payload.booking_id,
            "description": payload.description,
        },
    )

    # For safety and damage incidents: trigger custodian hook and auto-place safety hold
    hold_info = None
    if inc_type in ("safety", "damage"):
        # Trigger agent hook (with safe boundary, supporting both on_incident and handle_incident)
        try:
            from app.agent import custodian
            context_data = {"booking_id": payload.booking_id, "description": payload.description}
            if hasattr(custodian, "on_incident"):
                custodian.on_incident(
                    incident_id=incident_id,
                    resource_id=res_id,
                    context=context_data,
                )
            elif hasattr(custodian, "handle_incident"):
                custodian.handle_incident(
                    incident_id=incident_id,
                    resource_id=res_id,
                    context=context_data,
                )
        except Exception:
            # Safe stub boundary: does not break workflow if agent layer is disabled/stubbed
            pass

        # Trigger safety hold placement
        hold_info = place_safety_hold(
            resource_id=res_id,
            placed_by="agent:custodian",
            reason=f"Incident [{inc_type.upper()}]: {payload.description}",
            actor_kind="agent",
            actor_role="agent",
            incident_id=incident_id,
        )

    return {
        "status": "incident_recorded",
        "incident_id": incident_id,
        "type": inc_type,
        "resource_id": res_id,
        "booking_id": payload.booking_id,
        "created_at": now_ts,
        "hold": hold_info,
    }


@router.get("")
async def list_incidents(
    resource_id: Optional[str] = Query(None),
    booking_id: Optional[str] = Query(None),
):
    """List recorded incidents, preserving chronological order and original descriptions."""
    conn = get_connection()
    try:
        query = "SELECT * FROM incidents WHERE 1=1"
        params: List[Any] = []
        if resource_id:
            query += " AND resource_id = ?"
            params.append(resource_id)
        if booking_id:
            query += " AND booking_id = ?"
            params.append(booking_id)
        query += " ORDER BY created_at DESC"
        rows = conn.execute(query, params).fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()


@router.get("/{incident_id}")
async def get_incident(incident_id: str):
    """Retrieve details for a single incident."""
    conn = get_connection()
    try:
        row = conn.execute("SELECT * FROM incidents WHERE id = ?", (incident_id,)).fetchone()
        if not row:
            raise HTTPException(status_code=404, detail=f"Incident '{incident_id}' not found.")
        return dict(row)
    finally:
        conn.close()
