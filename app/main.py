"""
ShareSafe — FastAPI Application
Campus platform for safely sharing equipment and spaces.
Provides deterministic readiness, bounded agent autonomy, transactional lifecycle state machine,
RBAC enforcement, and audit logs.
"""
from __future__ import annotations
import json
import logging
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

from fastapi import Depends, FastAPI, HTTPException, Request, status
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from app.auth.dependencies import (
    get_current_user,
    require_admin,
    require_borrower,
    require_inspector,
    require_owner,
)
from app.auth.jwt import create_access_token
from app.core.audit import append_audit, get_audit_trail
from app.core.lifecycle import (
    BookingStatus,
    ConditionNoteRequiredError,
    IllegalTransitionError,
    ResourceUnavailableError,
    SlotOverlapError,
    create_booking_request,
    normalize_status,
    transition_booking,
)
from app.db.seed import seed_database
from app.db.session import db_transaction, get_connection, init_db
from app.sharesafe_models import (
    AuthResponse,
    BorrowRequestCreate,
    CheckoutPayload,
    ClearHoldPayload,
    IncidentPayload,
    LoginPayload,
    OwnerActionPayload,
    ResourceCreate,
    ReturnPayload,
)

# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------
logging.basicConfig(
    level=logging.INFO, format="%(asctime)s [%(levelname)s] %(name)s: %(message)s"
)
logger = logging.getLogger("sharesafe")

# ---------------------------------------------------------------------------
# App initialization
# ---------------------------------------------------------------------------
app = FastAPI(
    title="ShareSafe — Campus Resource Sharing",
    description="Safely share equipment and spaces across campus with bounded agent autonomy.",
    version="2.0.0",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

BASE_DIR = Path(__file__).resolve().parent.parent
STATIC_DIR = BASE_DIR / "static"
STATIC_DIR.mkdir(parents=True, exist_ok=True)

# Ensure DB initialized on startup
init_db()


# ---------------------------------------------------------------------------
# Helper functions
# ---------------------------------------------------------------------------
def _enrich_booking_dict(b: dict, conn) -> dict:
    res = conn.execute("SELECT * FROM resources WHERE id = ?", (b["resource_id"],)).fetchone()
    requester = conn.execute("SELECT * FROM users WHERE id = ?", (b["user_id"],)).fetchone()
    owner = conn.execute("SELECT * FROM users WHERE id = ?", (b["owner_id"],)).fetchone()

    res_d = dict(res) if res else {}
    req_u = dict(requester) if requester else {}
    own_u = dict(owner) if owner else {}

    # Parse decision trace if present
    trace = None
    if b.get("decision_trace_json"):
        try:
            trace = json.loads(b["decision_trace_json"])
        except Exception:
            pass

    # Fetch conditions associated with booking
    conds = conn.execute(
        "SELECT phase, notes, created_at FROM conditions WHERE booking_id = ? ORDER BY created_at ASC",
        (b["id"],),
    ).fetchall()
    cond_list = [dict(c) for c in conds]

    status_str = b.get("status", "requested")
    # For backwards UI compatibility, map 'requested' -> 'pending' if needed, or keep status
    legacy_status = "pending" if status_str == "requested" else "checked_out" if status_str == "checked_in" else status_str

    return {
        **b,
        "requester_id": b["user_id"],
        "start_date": b["start"],
        "end_date": b["end"],
        "status": status_str,
        "legacy_status": legacy_status,
        "resource_name": res_d.get("name", "Unknown Resource"),
        "resource_location": res_d.get("location", ""),
        "resource_category": res_d.get("category", ""),
        "resource_image": res_d.get("image_url"),
        "bay_tag": res_d.get("bay_tag", ""),
        "item_code": res_d.get("item_code", ""),
        "readiness_state": res_d.get("readiness_state", "ready"),
        "requester_name": req_u.get("name", "Unknown User"),
        "requester_title": req_u.get("title", ""),
        "owner_name": own_u.get("name", "Unknown Owner"),
        "decision_trace": trace,
        "conditions": cond_list,
    }


# ---------------------------------------------------------------------------
# Authentication & Role Switching
# ---------------------------------------------------------------------------
@app.get("/api/users")
async def list_users():
    """Return all registered users (for demo switcher and judge inspection)."""
    conn = get_connection()
    try:
        rows = conn.execute("SELECT id, name, email, role, kind, training, year, team_id, avatar, title FROM users").fetchall()
        users = []
        for r in rows:
            u = dict(r)
            try:
                u["training"] = json.loads(u["training"])
            except Exception:
                u["training"] = []
            users.append(u)
        return users
    finally:
        conn.close()


@app.post("/api/auth/login", response_model=AuthResponse)
async def login(payload: LoginPayload):
    """Authenticate user with user_id or email, returning signed access token."""
    conn = get_connection()
    try:
        user_id = payload.user_id or payload.email
        row = conn.execute("SELECT * FROM users WHERE id = ? OR email = ?", (user_id, user_id)).fetchone()
        if not row:
            raise HTTPException(status_code=401, detail="Invalid user credentials.")
        user = dict(row)
        token = create_access_token(user["id"], user["role"], user["name"])
        return AuthResponse(token=token, user=user)
    finally:
        conn.close()


@app.post("/api/auth/demo-login/{user_id}", response_model=AuthResponse)
async def demo_login(user_id: str):
    """One-click demo login for Judge mode and persona switching."""
    conn = get_connection()
    try:
        row = conn.execute("SELECT * FROM users WHERE id = ?", (user_id,)).fetchone()
        if not row:
            raise HTTPException(status_code=404, detail=f"Demo user '{user_id}' not found.")
        user = dict(row)
        token = create_access_token(user["id"], user["role"], user["name"])
        return AuthResponse(token=token, user=user)
    finally:
        conn.close()


@app.get("/api/auth/me")
async def get_me(user: Dict = Depends(get_current_user)):
    """Return current authenticated user profile."""
    return user


# ---------------------------------------------------------------------------
# Resources  —  GET /api/resources  &  POST /api/resources
# ---------------------------------------------------------------------------
@app.get("/api/resources")
async def list_resources(
    status: Optional[str] = None,
    category: Optional[str] = None,
    readiness: Optional[str] = None,
):
    """List resources with optional status, category, or readiness filters."""
    conn = get_connection()
    try:
        query = "SELECT * FROM resources WHERE 1=1"
        params = []
        if status:
            query += " AND status = ?"
            params.append(status)
        if category:
            query += " AND category = ?"
            params.append(category)
        if readiness:
            query += " AND readiness_state = ?"
            params.append(readiness)

        rows = conn.execute(query, params).fetchall()
        results = []
        for r in rows:
            res = dict(r)
            try:
                res["rules"] = json.loads(res.get("rules_json") or "{}")
            except Exception:
                res["rules"] = {}
            results.append(res)
        return results
    finally:
        conn.close()


@app.post("/api/resources", status_code=201)
async def create_resource(
    payload: ResourceCreate,
    current_user: Dict = Depends(require_owner),
):
    """List a new resource (Owner or Admin role required)."""
    owner_id = payload.owner_id or current_user["id"]
    now_iso = datetime.utcnow().isoformat()
    res_id = f"r-{datetime.utcnow().strftime('%M%S')}"

    with db_transaction() as conn:
        conn.execute(
            """
            INSERT INTO resources (
                id, name, category, location, description, owner_id,
                status, readiness_state, rules_json, image_url,
                training_required, training_description, usage_rules,
                max_duration_days, bay_tag, item_code, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, 'available', 'ready', ?, ?, ?, ?, ?, ?, ?, ?, ?);
            """,
            (
                res_id,
                payload.name,
                payload.category,
                payload.location,
                payload.description,
                owner_id,
                payload.rules_json or "{}",
                payload.image_url,
                1 if payload.training_required else 0,
                payload.training_description,
                payload.usage_rules,
                payload.max_duration_days,
                payload.bay_tag,
                payload.item_code,
                now_iso,
            ),
        )
        append_audit(current_user["id"], "create_resource", {"resource_id": res_id, "name": payload.name})
        row = conn.execute("SELECT * FROM resources WHERE id = ?", (res_id,)).fetchone()
        return dict(row)


@app.get("/api/resources/{resource_id}")
async def get_resource(resource_id: str):
    """Get single resource passport details."""
    conn = get_connection()
    try:
        row = conn.execute("SELECT * FROM resources WHERE id = ?", (resource_id,)).fetchone()
        if not row:
            raise HTTPException(status_code=404, detail=f"Resource '{resource_id}' not found.")
        res = dict(row)
        try:
            res["rules"] = json.loads(res.get("rules_json") or "{}")
        except Exception:
            res["rules"] = {}
        # Fetch any active holds
        holds = conn.execute(
            "SELECT * FROM holds WHERE resource_id = ? ORDER BY created_at DESC", (resource_id,)
        ).fetchall()
        res["holds"] = [dict(h) for h in holds]
        return res
    finally:
        conn.close()


@app.get("/api/resources/{resource_id}/history")
async def resource_history(resource_id: str):
    """Return chronological booking and condition history for resource passport."""
    conn = get_connection()
    try:
        rows = conn.execute(
            "SELECT * FROM bookings WHERE resource_id = ? ORDER BY created_at DESC",
            (resource_id,),
        ).fetchall()
        return [_enrich_booking_dict(dict(r), conn) for r in rows]
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# Booking Requests  —  POST /api/requests  &  GET /api/requests
# ---------------------------------------------------------------------------
@app.post("/api/requests", status_code=201)
async def create_request(
    payload: BorrowRequestCreate,
    current_user: Dict = Depends(get_current_user),
):
    """
    Borrower submits a request for a resource.
    Guaranteed atomic overlap check, decision engine trace, and audit recording.
    """
    requester_id = payload.requester_id or current_user["id"]
    try:
        booking = create_booking_request(
            resource_id=payload.resource_id,
            requester_id=requester_id,
            start_date=payload.start_date,
            end_date=payload.end_date,
            message=payload.message,
            purpose=payload.purpose,
            training_acknowledged=payload.training_acknowledged,
            justification=payload.justification or payload.message or "",
        )
        conn = get_connection()
        try:
            return _enrich_booking_dict(booking, conn)
        finally:
            conn.close()
    except ResourceUnavailableError as e:
        raise HTTPException(status_code=409, detail=str(e))
    except SlotOverlapError as e:
        raise HTTPException(status_code=409, detail=str(e))
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))


@app.get("/api/requests")
async def list_requests(
    owner_id: Optional[str] = None,
    requester_id: Optional[str] = None,
    resource_id: Optional[str] = None,
    status: Optional[str] = None,
):
    """List bookings with optional filters, sorted newest first."""
    conn = get_connection()
    try:
        query = "SELECT * FROM bookings WHERE 1=1"
        params = []
        if owner_id:
            query += " AND owner_id = ?"
            params.append(owner_id)
        if requester_id:
            query += " AND user_id = ?"
            params.append(requester_id)
        if resource_id:
            query += " AND resource_id = ?"
            params.append(resource_id)
        if status:
            if status == "pending":
                query += " AND status IN ('requested', 'pending')"
            elif status == "active":
                query += " AND status IN ('approved', 'checked_in', 'checked_out')"
            else:
                query += " AND status = ?"
                params.append(status)

        query += " ORDER BY created_at DESC"
        rows = conn.execute(query, params).fetchall()
        return [_enrich_booking_dict(dict(r), conn) for r in rows]
    finally:
        conn.close()


@app.get("/api/requests/{request_id}")
async def get_request(request_id: str):
    conn = get_connection()
    try:
        row = conn.execute("SELECT * FROM bookings WHERE id = ?", (request_id,)).fetchone()
        if not row:
            raise HTTPException(status_code=404, detail=f"Request '{request_id}' not found.")
        return _enrich_booking_dict(dict(row), conn)
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# State Transitions: Approve, Reject, Checkout/Checkin, Return, Cancel
# ---------------------------------------------------------------------------
@app.patch("/api/requests/{request_id}/approve")
async def approve_request(
    request_id: str,
    payload: OwnerActionPayload = OwnerActionPayload(),
    current_user: Dict = Depends(require_owner),
):
    """Owner approves pending request."""
    try:
        updated = transition_booking(
            booking_id=request_id,
            target_status=BookingStatus.APPROVED,
            actor_id=current_user["id"],
            owner_reason=payload.owner_reason,
        )
        conn = get_connection()
        try:
            return _enrich_booking_dict(updated, conn)
        finally:
            conn.close()
    except IllegalTransitionError as e:
        raise HTTPException(status_code=409, detail=str(e))
    except ResourceUnavailableError as e:
        raise HTTPException(status_code=409, detail=str(e))
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))


@app.patch("/api/requests/{request_id}/reject")
async def reject_request(
    request_id: str,
    payload: OwnerActionPayload = OwnerActionPayload(),
    current_user: Dict = Depends(require_owner),
):
    """Owner rejects request; resource cleanly reverts to available."""
    try:
        updated = transition_booking(
            booking_id=request_id,
            target_status=BookingStatus.REJECTED,
            actor_id=current_user["id"],
            owner_reason=payload.owner_reason,
        )
        conn = get_connection()
        try:
            return _enrich_booking_dict(updated, conn)
        finally:
            conn.close()
    except IllegalTransitionError as e:
        raise HTTPException(status_code=409, detail=str(e))
    except ResourceUnavailableError as e:
        raise HTTPException(status_code=409, detail=str(e))
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))


@app.patch("/api/requests/{request_id}/checkout")
@app.patch("/api/requests/{request_id}/checkin")
async def checkout_request(
    request_id: str,
    payload: CheckoutPayload = CheckoutPayload(),
    current_user: Dict = Depends(require_borrower),
):
    """Borrower checks out equipment. Mandatory initial condition note."""
    note = payload.get_note() or "Initial condition inspected: clean, calibrated, undamaged."
    try:
        updated = transition_booking(
            booking_id=request_id,
            target_status=BookingStatus.CHECKED_IN,
            actor_id=current_user["id"],
            condition_note=note,
        )
        conn = get_connection()
        try:
            return _enrich_booking_dict(updated, conn)
        finally:
            conn.close()
    except IllegalTransitionError as e:
        raise HTTPException(status_code=409, detail=str(e))
    except ResourceUnavailableError as e:
        raise HTTPException(status_code=409, detail=str(e))
    except ConditionNoteRequiredError as e:
        raise HTTPException(status_code=422, detail=str(e))
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))


@app.patch("/api/requests/{request_id}/return")
async def return_request(
    request_id: str,
    payload: ReturnPayload = ReturnPayload(),
    current_user: Dict = Depends(require_borrower),
):
    """
    Borrower returns equipment. Mandatory condition report.
    If fault or damage is indicated, an incident and precautionary hold are created.
    """
    note = payload.get_note() or "Returned in verified good operating condition."
    try:
        updated = transition_booking(
            booking_id=request_id,
            target_status=BookingStatus.RETURNED,
            actor_id=current_user["id"],
            condition_note=note,
            incident_type=payload.incident_type,
            incident_description=payload.incident_description,
        )
        conn = get_connection()
        try:
            return _enrich_booking_dict(updated, conn)
        finally:
            conn.close()
    except IllegalTransitionError as e:
        raise HTTPException(status_code=409, detail=str(e))
    except ResourceUnavailableError as e:
        raise HTTPException(status_code=409, detail=str(e))
    except ConditionNoteRequiredError as e:
        raise HTTPException(status_code=422, detail=str(e))
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))


@app.patch("/api/requests/{request_id}/cancel")
async def cancel_request(
    request_id: str,
    current_user: Dict = Depends(get_current_user),
):
    """Cancel booking before checkout."""
    try:
        updated = transition_booking(
            booking_id=request_id,
            target_status=BookingStatus.CANCELLED,
            actor_id=current_user["id"],
        )
        conn = get_connection()
        try:
            return _enrich_booking_dict(updated, conn)
        finally:
            conn.close()
    except IllegalTransitionError as e:
        raise HTTPException(status_code=409, detail=str(e))
    except ResourceUnavailableError as e:
        raise HTTPException(status_code=409, detail=str(e))
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))


# ---------------------------------------------------------------------------
# Holds & Incident Management (P2/P4 Integration)
# ---------------------------------------------------------------------------
@app.post("/api/resources/{resource_id}/clear-hold")
async def clear_safety_hold(
    resource_id: str,
    payload: ClearHoldPayload,
    current_user: Dict = Depends(require_inspector),
):
    """
    Clear a safety hold on a resource.
    Requires Authorized Inspector or Admin role + mandatory written resolution note.
    Logged to audit trail and visible to affected borrowers.
    """
    now_ts = datetime.utcnow().isoformat()
    with db_transaction() as conn:
        hold = conn.execute(
            "SELECT * FROM holds WHERE resource_id = ? AND status = 'active' ORDER BY created_at DESC LIMIT 1",
            (resource_id,),
        ).fetchone()
        if not hold:
            raise HTTPException(status_code=404, detail="No active safety hold found for this resource.")

        conn.execute(
            """
            UPDATE holds SET status = 'cleared', cleared_by = ?, cleared_note = ?, cleared_at = ?
            WHERE id = ?;
            """,
            (current_user["id"], payload.cleared_note, now_ts, hold["id"]),
        )
        conn.execute(
            "UPDATE resources SET status = 'available', readiness_state = 'ready', hold_id = NULL WHERE id = ?",
            (resource_id,),
        )
        append_audit(
            actor_id=current_user["id"],
            action="clear_safety_hold",
            payload={"resource_id": resource_id, "hold_id": hold["id"], "note": payload.cleared_note},
        )
        return {"status": "hold_cleared", "resource_id": resource_id, "cleared_by": current_user["id"]}


# ---------------------------------------------------------------------------
# Audit Trail
# ---------------------------------------------------------------------------
@app.get("/api/audit")
async def get_audit():
    """Return tamper-evident audit chain."""
    return get_audit_trail()


# ---------------------------------------------------------------------------
# Demo Reset & Admin
# ---------------------------------------------------------------------------
@app.post("/api/admin/reset-demo")
async def reset_demo():
    """Reset database to initial seed data for clean demonstration runs."""
    seed_database()
    return {"status": "ok", "message": "ShareSafe demo data successfully reset to clean state."}


@app.get("/api/adversarial/run")
async def run_adversarial_suite():
    """Execute scripted adversarial suite scenarios A through H."""
    from app.adversarial.scenarios import run_all_adversarial_scenarios
    results = run_all_adversarial_scenarios()
    return {
        "total": len(results),
        "passed": sum(1 for r in results if r.passed),
        "scenarios": [r.model_dump() for r in results],
    }


# ---------------------------------------------------------------------------
# Routers Registration (P2 & P4 Modules)
# ---------------------------------------------------------------------------
from app.routers.incidents import router as incidents_router
from app.routers.holds import router as holds_router
from app.routers.notifications import router as notifications_router

app.include_router(incidents_router)
app.include_router(holds_router)
app.include_router(notifications_router)


# ---------------------------------------------------------------------------
# Static files & Root
# ---------------------------------------------------------------------------
app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")


@app.get("/")
async def serve_index():
    index = STATIC_DIR / "index.html"
    if index.exists():
        return FileResponse(index)
    return JSONResponse({"status": "ShareSafe API online. Visit /docs for the API explorer."})


@app.get("/health")
async def health():
    conn = get_connection()
    try:
        res_count = conn.execute("SELECT COUNT(*) FROM resources").fetchone()[0]
        req_count = conn.execute("SELECT COUNT(*) FROM bookings").fetchone()[0]
        user_count = conn.execute("SELECT COUNT(*) FROM users").fetchone()[0]
        return {
            "status": "ok",
            "storage": "sqlite",
            "resources": res_count,
            "requests": req_count,
            "users": user_count,
            "timestamp": datetime.utcnow().isoformat(),
        }
    finally:
        conn.close()
