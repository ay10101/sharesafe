# app/routers/audit.py
"""
ShareSafe Audit Verification & History Router.
Provides cryptographic verification endpoints and chronological audit query access.
"""
from __future__ import annotations
from typing import Any, Dict, List, Optional
from fastapi import APIRouter, HTTPException, Query, status
from pydantic import BaseModel

from app.core.audit import get_audit_trail, simulate_tamper, verify_chain

router = APIRouter(prefix="/api/audit", tags=["audit"])


class TamperSimulationRequest(BaseModel):
    index: int = 0
    field: str = "payload"
    value: str = "TAMPERED_DEMO_RECORD"


@router.get("/verify")
async def verify_audit_chain():
    """
    Cryptographically verify the entire SHA-256 tamper-evident audit chain.
    Returns: {"ok": bool, "broken_at": int | None, "checked": int}
    """
    return verify_chain()


@router.get("")
async def query_audit_trail(
    resource_id: Optional[str] = Query(None),
    booking_id: Optional[str] = Query(None),
):
    """
    Retrieve chronological audit trail entries, optionally filtered by resource or booking.
    """
    return get_audit_trail(resource_id=resource_id, booking_id=booking_id)


@router.post("/tamper")
async def simulate_tamper_endpoint(payload: TamperSimulationRequest):
    """
    Demo Helper: Intentionally mutate an audit entry to demonstrate tamper detection in live judging.
    """
    success = simulate_tamper(index=payload.index, field=payload.field, value=payload.value)
    if not success:
        raise HTTPException(
            status_code=400,
            detail=f"Unable to tamper record at index {payload.index}. Index out of range or invalid.",
        )
    return {
        "status": "tampered",
        "index": payload.index,
        "message": "Audit record altered. Run /api/audit/verify to observe cryptographic failure detection.",
    }
