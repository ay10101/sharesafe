# app/routers/holds.py
"""
ShareSafe Holds Router.
Provides endpoints to view, place, clear, and override safety holds.
"""
from __future__ import annotations
from typing import Any, Dict, List, Optional
from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel, Field

from app.auth.dependencies import get_current_user, require_inspector, require_owner
from app.core.holds import clear_safety_hold_record, override_safety_hold, place_safety_hold
from app.db.session import get_connection

router = APIRouter(prefix="/api/holds", tags=["holds"])


class CreateHoldRequest(BaseModel):
    resource_id: str
    reason: str


class ClearHoldRequest(BaseModel):
    cleared_note: str = Field(..., description="Mandatory written resolution inspection note")


class OverrideHoldRequest(BaseModel):
    override_reason: str = Field(..., description="Mandatory written justification for emergency override")


@router.get("")
async def list_holds(
    resource_id: Optional[str] = Query(None),
    status: Optional[str] = Query(None),
):
    """List holds filtered by resource or status."""
    conn = get_connection()
    try:
        q = "SELECT * FROM holds WHERE 1=1"
        params: List[Any] = []
        if resource_id:
            q += " AND resource_id = ?"
            params.append(resource_id)
        if status:
            q += " AND status = ?"
            params.append(status)
        q += " ORDER BY created_at DESC"
        rows = conn.execute(q, params).fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()


@router.post("", status_code=status.HTTP_201_CREATED)
async def create_hold(
    payload: CreateHoldRequest,
    current_user: Dict[str, Any] = Depends(get_current_user),
):
    """Place a safety hold on a resource."""
    actor_kind = current_user.get("kind", "human")
    actor_role = current_user.get("role", "owner")
    try:
        res = place_safety_hold(
            resource_id=payload.resource_id,
            placed_by=current_user["id"],
            reason=payload.reason,
            actor_kind=actor_kind,
            actor_role=actor_role,
        )
        return res
    except PermissionError as e:
        raise HTTPException(status_code=403, detail=str(e))


@router.post("/{hold_id}/clear")
async def clear_hold(
    hold_id: str,
    payload: ClearHoldRequest,
    current_user: Dict[str, Any] = Depends(require_inspector),
):
    """Clear an active safety hold (requires Inspector role + non-empty note)."""
    if not payload.cleared_note.strip():
        raise HTTPException(status_code=422, detail="Inspection resolution note cannot be empty.")

    conn = get_connection()
    try:
        hold = conn.execute("SELECT * FROM holds WHERE id = ?", (hold_id,)).fetchone()
        if not hold:
            raise HTTPException(status_code=404, detail=f"Hold '{hold_id}' not found.")
        resource_id = hold["resource_id"]
    finally:
        conn.close()

    try:
        res = clear_safety_hold_record(
            resource_id=resource_id,
            cleared_by=current_user["id"],
            cleared_note=payload.cleared_note,
            actor_kind=current_user.get("kind", "human"),
            actor_role=current_user.get("role", "inspector"),
            hold_id=hold_id,
        )
        return res
    except PermissionError as e:
        raise HTTPException(status_code=403, detail=str(e))
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.post("/{hold_id}/override")
async def override_hold(
    hold_id: str,
    payload: OverrideHoldRequest,
    current_user: Dict[str, Any] = Depends(require_inspector),
):
    """Override a safety hold through the defined human authority process."""
    conn = get_connection()
    try:
        hold = conn.execute("SELECT * FROM holds WHERE id = ?", (hold_id,)).fetchone()
        if not hold:
            raise HTTPException(status_code=404, detail=f"Hold '{hold_id}' not found.")
        resource_id = hold["resource_id"]
    finally:
        conn.close()

    try:
        res = override_safety_hold(
            resource_id=resource_id,
            inspector_id=current_user["id"],
            override_reason=payload.override_reason,
            actor_role=current_user.get("role", "inspector"),
        )
        return res
    except PermissionError as e:
        raise HTTPException(status_code=403, detail=str(e))
    except ValueError as e:
        raise HTTPException(status_code=422, detail=str(e))
