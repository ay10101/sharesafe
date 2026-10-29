# tests/test_trust_api_endpoints.py
"""
Integration tests for P2 API endpoints:
- POST /api/incidents (safety, damage, dispute)
- GET /api/incidents
- POST /api/holds
- POST /api/holds/{id}/clear
- GET /api/notifications
- PATCH /api/notifications/{id}/read
- POST /api/notifications/read-all
"""
import tempfile
from pathlib import Path
from datetime import datetime, timezone, timedelta
import pytest
from starlette.testclient import TestClient

from app.auth.jwt import create_access_token
from app.core.audit import InMemoryAuditStore, SQLiteAuditStore, set_audit_store, verify_chain
from app.db.schema import SCHEMA_SQL
from app.db.session import init_db, get_connection
from fastapi import FastAPI
from app.routers.incidents import router as incidents_router
from app.routers.holds import router as holds_router
from app.routers.notifications import router as notifications_router


@pytest.fixture
def test_client(monkeypatch):
    with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
        db_path = Path(f.name)
    init_db(db_path)
    monkeypatch.setenv("DATABASE_PATH", str(db_path))

    # Seed basic records
    conn = get_connection(db_path)
    try:
        now_iso = datetime.now(timezone.utc).isoformat()
        conn.execute(
            """
            INSERT INTO users (id, name, email, role, kind)
            VALUES
                ('user:maya', 'Maya Lin', 'maya@campus.edu', 'borrower', 'human'),
                ('user:marcus', 'Marcus Vance', 'marcus@campus.edu', 'owner', 'human'),
                ('user:dr_thorne', 'Dr. Aris Thorne', 'thorne@campus.edu', 'inspector', 'human'),
                ('user:sarah', 'Sarah Admin', 'admin@campus.edu', 'admin', 'human'),
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

    set_audit_store(SQLiteAuditStore(db_path=db_path))

    # Test app with P2 routers mounted
    app = FastAPI()
    app.include_router(incidents_router)
    app.include_router(holds_router)
    app.include_router(notifications_router)

    client = TestClient(app)
    try:
        yield client
    finally:
        set_audit_store(InMemoryAuditStore())
        try:
            db_path.unlink(missing_ok=True)
        except Exception:
            pass


def test_safety_incident_api_and_hold_flow(test_client):
    """POST /api/incidents with safety type records incident and places precautionary hold."""
    token = create_access_token(user_id="user:maya", role="borrower", name="Maya Lin")
    headers = {"Authorization": f"Bearer {token}"}

    res = test_client.post(
        "/api/incidents",
        json={
            "type": "safety",
            "description": "Fan fault reported during return: excessive heat and noise.",
            "resource_id": "res-gpu-01",
        },
        headers=headers,
    )
    assert res.status_code == 201
    data = res.json()
    assert data["status"] == "incident_recorded"
    assert data["type"] == "safety"
    assert data["hold"] is not None
    assert data["hold"]["status"] == "hold_placed"

    # Verify hold is visible via GET /api/holds
    holds_res = test_client.get("/api/holds?resource_id=res-gpu-01")
    assert holds_res.status_code == 200
    holds = holds_res.json()
    assert len(holds) == 1
    assert holds[0]["status"] == "active"
    hold_id = holds[0]["id"]

    # Borrower trying to clear hold -> 403 Forbidden
    clear_attempt = test_client.post(
        f"/api/holds/{hold_id}/clear",
        json={"cleared_note": "I think it is ok"},
        headers=headers,
    )
    assert clear_attempt.status_code == 403

    # Inspector clearing hold without note -> 422 Unprocessable Entity
    insp_token = create_access_token(user_id="user:dr_thorne", role="inspector", name="Dr. Aris Thorne")
    insp_headers = {"Authorization": f"Bearer {insp_token}"}
    empty_note_res = test_client.post(
        f"/api/holds/{hold_id}/clear",
        json={"cleared_note": "   "},
        headers=insp_headers,
    )
    assert empty_note_res.status_code == 422

    # Inspector clearing hold with written note -> 200 OK
    valid_clear_res = test_client.post(
        f"/api/holds/{hold_id}/clear",
        json={"cleared_note": "Replaced blower fan bearing, load tested 60min, thermals nominal at 68C."},
        headers=insp_headers,
    )
    assert valid_clear_res.status_code == 200
    assert valid_clear_res.json()["status"] == "hold_cleared"


def test_notifications_lifecycle(test_client):
    """GET /api/notifications and marking notifications as read."""
    token = create_access_token(user_id="user:maya", role="borrower", name="Maya Lin")
    headers = {"Authorization": f"Bearer {token}"}

    # Place hold that notifies Maya
    insp_token = create_access_token(user_id="user:dr_thorne", role="inspector", name="Dr. Aris Thorne")
    test_client.post(
        "/api/holds",
        json={"resource_id": "res-gpu-01", "reason": "Emergency calibration"},
        headers={"Authorization": f"Bearer {insp_token}"},
    )

    # Fetch notifications
    notifs_res = test_client.get("/api/notifications", headers=headers)
    assert notifs_res.status_code == 200

    # Mark as read
    from app.routers.notifications import send_notification
    n = send_notification("user:maya", "reminder", "Equipment due tomorrow")
    read_res = test_client.patch(f"/api/notifications/{n['id']}/read", headers=headers)
    assert read_res.status_code == 200
    assert read_res.json()["read"] == 1


def test_disputes_create_separate_incident_entries(test_client):
    """Disputes append new entries to /api/incidents rather than editing original."""
    token = create_access_token(user_id="user:maya", role="borrower", name="Maya Lin")
    headers = {"Authorization": f"Bearer {token}"}

    # 1. Damage report
    test_client.post(
        "/api/incidents",
        json={"type": "damage", "description": "Scratched lens casing", "resource_id": "res-gpu-01"},
        headers=headers,
    )

    # 2. Borrower dispute
    test_client.post(
        "/api/incidents",
        json={"type": "dispute", "description": "Case scratch was present during pre-checkout", "resource_id": "res-gpu-01"},
        headers=headers,
    )

    # Check both records exist independently
    list_res = test_client.get("/api/incidents?resource_id=res-gpu-01")
    assert list_res.status_code == 200
    items = list_res.json()
    assert len(items) == 2
    types = [i["incident_type"] for i in items]
    assert "damage" in types
    assert "dispute" in types


def test_get_incident_evidence_packet_endpoint(test_client):
    """GET /api/incidents/{id}/packet retrieves compiled EvidencePacket."""
    token = create_access_token(user_id="user:maya", role="borrower", name="Maya Lin")
    headers = {"Authorization": f"Bearer {token}"}

    res = test_client.post(
        "/api/incidents",
        json={"type": "safety", "description": "Thermal cutoff triggered during training run", "resource_id": "res-gpu-01"},
        headers=headers,
    )
    assert res.status_code == 201
    inc_id = res.json()["incident_id"]

    pkt_res = test_client.get(f"/api/incidents/{inc_id}/packet")
    assert pkt_res.status_code == 200
    packet = pkt_res.json()
    assert packet["incident_id"] == inc_id
    assert packet["resource_id"] == "res-gpu-01"
    assert "Thermal cutoff" in packet["reported_description"]
    assert packet["summary"] != ""


def test_incident_reporter_id_integration(test_client):
    """Verify that authenticated reporter_id is correctly recorded and agent hook executes without NameError."""
    token = create_access_token(user_id="user:maya", role="borrower", name="Maya Lin")
    headers = {"Authorization": f"Bearer {token}"}

    res = test_client.post(
        "/api/incidents",
        json={"type": "damage", "description": "Spindle bearing worn and vibrating under load", "resource_id": "res-gpu-01"},
        headers=headers,
    )
    assert res.status_code == 201
    data = res.json()
    inc_id = data["incident_id"]

    # Verify incident record in database has reporter_id = user:maya
    from app.db.session import get_connection
    conn = get_connection()
    try:
        row = conn.execute("SELECT * FROM incidents WHERE id = ?", (inc_id,)).fetchone()
        assert row is not None
        assert row["reporter_id"] == "user:maya"
        assert row["incident_type"] == "damage"
    finally:
        conn.close()


