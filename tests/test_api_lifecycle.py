# tests/test_api_lifecycle.py
"""
End-to-end API integration tests for ShareSafe.
Verifies the complete lifecycle, RBAC permissions, and demo reset over the FastAPI HTTP endpoints.
"""
from datetime import datetime, timedelta
from starlette.testclient import TestClient
from app.main import app
from app.db.seed import seed_database


def test_api_full_journey_and_rbac(tmp_path, monkeypatch):
    test_db = tmp_path / "test_api.db"
    seed_database(test_db)
    monkeypatch.setenv("DATABASE_PATH", str(test_db))

    client = TestClient(app)

    # 1. Health check
    res_health = client.get("/health")
    assert res_health.status_code == 200
    assert res_health.json()["storage"] == "sqlite"

    # 2. Demo logins
    res_login_borrower = client.post("/api/auth/demo-login/u1")
    assert res_login_borrower.status_code == 200
    token_borrower = res_login_borrower.json()["token"]

    res_login_owner = client.post("/api/auth/demo-login/u2")
    assert res_login_owner.status_code == 200
    token_owner = res_login_owner.json()["token"]

    borrower_headers = {"Authorization": f"Bearer {token_borrower}"}
    owner_headers = {"Authorization": f"Bearer {token_owner}"}

    # 3. Create request by borrower
    start = (datetime.utcnow() + timedelta(days=1)).replace(hour=10, minute=0, second=0).isoformat()
    end = (datetime.utcnow() + timedelta(days=1)).replace(hour=12, minute=0, second=0).isoformat()

    req_payload = {
        "resource_id": "r1",
        "start_date": start,
        "end_date": end,
        "message": "API lifecycle integration test",
        "purpose": "Automated testing",
        "training_acknowledged": True,
    }
    res_req = client.post("/api/requests", json=req_payload, headers=borrower_headers)
    assert res_req.status_code == 201, res_req.text
    booking = res_req.json()
    booking_id = booking["id"]
    assert booking["status"] == "requested"
    assert booking["decision_trace"] is not None

    # 4. Overlap rejection: another request for same slot gets 409
    res_conflict = client.post("/api/requests", json=req_payload, headers=borrower_headers)
    assert res_conflict.status_code == 409
    assert "overlaps" in res_conflict.json()["detail"].lower()

    # 5. RBAC: Borrower attempting to approve their own request gets 403 Forbidden!
    res_bad_approve = client.patch(f"/api/requests/{booking_id}/approve", headers=borrower_headers)
    assert res_bad_approve.status_code == 403

    # 6. Owner approves -> 200 OK
    res_approve = client.patch(
        f"/api/requests/{booking_id}/approve",
        json={"owner_reason": "Approved by lab manager"},
        headers=owner_headers,
    )
    assert res_approve.status_code == 200
    assert res_approve.json()["status"] == "approved"

    # 7. Check out: missing condition note fails or default note succeeds
    res_checkout = client.patch(
        f"/api/requests/{booking_id}/checkout",
        json={"condition_note": "Rig verified and operational"},
        headers=borrower_headers,
    )
    assert res_checkout.status_code == 200
    assert res_checkout.json()["status"] == "checked_in"

    # 8. Return with condition note
    res_return = client.patch(
        f"/api/requests/{booking_id}/return",
        json={"condition_note": "Clean shutdown, no anomalies"},
        headers=borrower_headers,
    )
    assert res_return.status_code == 200
    assert res_return.json()["status"] == "returned"

    # 9. Double return gets 409 Conflict
    res_double_return = client.patch(
        f"/api/requests/{booking_id}/return",
        json={"condition_note": "Trying double return"},
        headers=borrower_headers,
    )
    assert res_double_return.status_code == 409

    # 10. History verification
    res_hist = client.get("/api/resources/r1/history")
    assert res_hist.status_code == 200
    history = res_hist.json()
    assert any(h["id"] == booking_id and h["status"] == "returned" for h in history)

    # 11. Reset demo works
    res_reset = client.post("/api/admin/reset-demo")
    assert res_reset.status_code == 200



def test_checkout_and_approval_blocked_by_active_safety_hold(tmp_path, monkeypatch):
    """
    Regression test:
    1. Active safety hold must block owner approval.
    2. Active safety hold placed after approval must block checkout route with 409 Conflict.
    """
    test_db = tmp_path / "test_hold_security.db"
    seed_database(test_db)
    monkeypatch.setenv("DATABASE_PATH", str(test_db))

    client = TestClient(app)

    # Logins
    token_borrower = client.post("/api/auth/demo-login/u1").json()["token"]
    token_owner = client.post("/api/auth/demo-login/u2").json()["token"]
    token_inspector = client.post("/api/auth/demo-login/u3").json()["token"]

    borrower_headers = {"Authorization": f"Bearer {token_borrower}"}
    owner_headers = {"Authorization": f"Bearer {token_owner}"}
    inspector_headers = {"Authorization": f"Bearer {token_inspector}"}

    # 1. Borrower requests booking
    start = (datetime.utcnow() + timedelta(days=2)).replace(hour=10, minute=0, second=0).isoformat()
    end = (datetime.utcnow() + timedelta(days=2)).replace(hour=12, minute=0, second=0).isoformat()
    req_res = client.post(
        "/api/requests",
        json={"resource_id": "r1", "start_date": start, "end_date": end, "message": "Research run"},
        headers=borrower_headers,
    )
    assert req_res.status_code == 201
    booking_id = req_res.json()["id"]

    # 2. Place an active safety hold directly into DB (simulating Custodian Agent hold)
    from app.db.session import get_connection
    conn = get_connection(test_db)
    now_ts = datetime.utcnow().isoformat()
    conn.execute(
        "INSERT INTO holds (id, resource_id, placed_by, reason, status, created_at) VALUES (?, ?, ?, ?, 'active', ?)",
        ("hold-test-01", "r1", "agent:custodian", "Anomalous temperature reading", now_ts),
    )
    conn.execute("UPDATE resources SET readiness_state = 'not_ready', status = 'not_ready', hold_id = 'hold-test-01' WHERE id = 'r1'")
    conn.commit()
    conn.close()

    # 3. Owner attempts to approve while hold is open -> BLOCKED (409 Conflict)
    res_approve_blocked = client.patch(f"/api/requests/{booking_id}/approve", headers=owner_headers)
    assert res_approve_blocked.status_code == 409
    assert "safety hold" in res_approve_blocked.json()["detail"].lower() or "not ready" in res_approve_blocked.json()["detail"].lower()

    # 4. Inspector clears the hold with written note
    res_clear = client.post(
        "/api/resources/r1/clear-hold",
        json={"cleared_note": "Thermal sensors inspected, fans cleaned and recalibrated. Certified safe."},
        headers=inspector_headers,
    )
    assert res_clear.status_code == 200

    # 5. Now Owner can approve -> 200 OK
    res_approve = client.patch(f"/api/requests/{booking_id}/approve", headers=owner_headers)
    assert res_approve.status_code == 200
    assert res_approve.json()["status"] == "approved"

    # 6. Now a NEW safety hold is placed (e.g. fan fault reported by prior borrower)
    conn = get_connection(test_db)
    conn.execute(
        "INSERT INTO holds (id, resource_id, placed_by, reason, status, created_at) VALUES (?, ?, ?, ?, 'active', ?)",
        ("hold-test-02", "r1", "agent:custodian", "Chassis fan failure detected", now_ts),
    )
    conn.execute("UPDATE resources SET readiness_state = 'not_ready', status = 'not_ready', hold_id = 'hold-test-02' WHERE id = 'r1'")
    conn.commit()
    conn.close()

    # 7. Borrower tries to check out equipment via PATCH /checkout -> BLOCKED (409 Conflict)!
    res_checkout_blocked = client.patch(
        f"/api/requests/{booking_id}/checkout",
        json={"condition_note": "Attempting pickup during hold"},
        headers=borrower_headers,
    )
    assert res_checkout_blocked.status_code == 409
    assert "safety hold" in res_checkout_blocked.json()["detail"].lower() or "not ready" in res_checkout_blocked.json()["detail"].lower()
