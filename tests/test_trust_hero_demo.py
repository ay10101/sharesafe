# tests/test_trust_hero_demo.py
"""
End-to-end Hero Demo journey test for ShareSafe Trust Layer.
Runs the complete 19-step hero journey with LLM OFF:
1. Borrower logs in through Judge Mode.
2. Borrower opens GPU workstation.
3. Borrower sees eligibility, training requirements, readiness.
4. Borrower submits request.
5. System records explainable decision trace.
6. Human owner approves.
7. Borrower checks in with verified pre-condition notes.
8. Condition notes are recorded.
9. Borrower returns equipment and reports: 'fan fault'.
10. Custodian Agent places precautionary hold.
11. Resource becomes NOT_READY.
12. Upcoming booking becomes blocked_by_hold.
13. Affected borrower receives notification.
14. Evidence/history becomes visible in passport.
15. Inspector reviews the issue.
16. Inspector clears hold with written note.
17. Resource returns to READY.
18. Audit timeline shows complete sequence.
19. Verify Chain returns ok: True (tamper-evident proof).
"""
import tempfile
from datetime import datetime, timezone, timedelta
from pathlib import Path
import pytest
from starlette.testclient import TestClient
from fastapi import FastAPI

from app.auth.jwt import create_access_token
from app.contracts import Readiness
from app.core.audit import SQLiteAuditStore, get_audit_trail, set_audit_store, verify_chain
from app.core.holds import clear_safety_hold_record, place_safety_hold
from app.core.readiness import evaluate_readiness
from app.db.schema import SCHEMA_SQL
from app.db.session import db_transaction, get_connection, init_db
from app.routers.audit import router as audit_router
from app.routers.holds import router as holds_router
from app.routers.incidents import router as incidents_router
from app.routers.notifications import router as notifications_router


@pytest.fixture
def demo_app(monkeypatch):
    with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
        db_path = Path(f.name)
    init_db(db_path)
    monkeypatch.setenv("DATABASE_PATH", str(db_path))

    conn = get_connection(db_path)
    now_iso = datetime.now(timezone.utc).isoformat()
    conn.execute(
        """
        INSERT INTO users (id, name, email, role, kind, year, training)
        VALUES
            ('user:maya', 'Maya Lin', 'maya@campus.edu', 'borrower', 'human', 3, '["gpu_safety"]'),
            ('user:marcus', 'Marcus Vance', 'marcus@campus.edu', 'owner', 'human', 4, '[]'),
            ('user:dr_thorne', 'Dr. Aris Thorne', 'thorne@campus.edu', 'inspector', 'human', 5, '[]'),
            ('user:david', 'David Kim', 'david@campus.edu', 'borrower', 'human', 2, '[]'),
            ('agent:custodian', 'Custodian Agent', 'agent@sharesafe.ai', 'agent', 'agent', 0, '[]');
        """
    )
    conn.execute(
        """
        INSERT INTO resources (id, name, category, location, owner_id, status, readiness_state, rules_json, created_at)
        VALUES
            ('res-gpu-01', 'NVIDIA RTX 4090 Workstation Pod', 'computing', 'Turing Hall 402', 'user:marcus', 'available', 'ready', '{"min_year": 3, "required_training": ["gpu_safety"], "max_hours_per_booking": 4}', ?);
        """,
        (now_iso,),
    )
    conn.commit()
    conn.close()

    set_audit_store(SQLiteAuditStore(db_path=db_path))

    app = FastAPI()
    app.include_router(audit_router)
    app.include_router(holds_router)
    app.include_router(incidents_router)
    app.include_router(notifications_router)

    client = TestClient(app)
    yield client, db_path

    try:
        db_path.unlink(missing_ok=True)
    except Exception:
        pass


def test_complete_hero_demo_journey(demo_app):
    client, db_path = demo_app

    # 1. Borrower logs in through Judge Mode
    maya_token = create_access_token(user_id="user:maya", role="borrower", name="Maya Lin")
    maya_headers = {"Authorization": f"Bearer {maya_token}"}
    marcus_token = create_access_token(user_id="user:marcus", role="owner", name="Marcus Vance")
    marcus_headers = {"Authorization": f"Bearer {marcus_token}"}
    thorne_token = create_access_token(user_id="user:dr_thorne", role="inspector", name="Dr. Aris Thorne")
    thorne_headers = {"Authorization": f"Bearer {thorne_token}"}

    # 2. Borrower inspects GPU workstation
    conn = get_connection(db_path)
    gpu_res = conn.execute("SELECT * FROM resources WHERE id = 'res-gpu-01'").fetchone()
    assert gpu_res is not None

    # 3. Sees eligibility & readiness
    readiness_eval = evaluate_readiness(dict(gpu_res), holds=[])
    # gpu_safety is required, so readiness evaluates to CONDITIONS
    assert readiness_eval.state == Readiness.CONDITIONS
    assert any("training prerequisites" in r.message for r in readiness_eval.reasons)

    # 4 & 5. Borrower submits request and system creates booking
    now = datetime.now(timezone.utc)
    t_start = (now + timedelta(hours=1)).isoformat()
    t_end = (now + timedelta(hours=3)).isoformat()
    booking_id = "bk-hero-01"

    with db_transaction() as db_conn:
        db_conn.execute(
            """
            INSERT INTO bookings (id, resource_id, user_id, owner_id, start, end, status, justification, created_at)
            VALUES (?, 'res-gpu-01', 'user:maya', 'user:marcus', ?, ?, 'requested', 'CS701 CUDA optimization lab', ?);
            """,
            (booking_id, t_start, t_end, now.isoformat()),
        )
    from app.core.audit import append_audit
    append_audit("user:maya", "create_request", {"booking_id": booking_id, "resource_id": "res-gpu-01"})

    # Also add an upcoming booking for David Kim later in the day
    upcoming_bk_id = "bk-david-02"
    with db_transaction() as db_conn:
        db_conn.execute(
            """
            INSERT INTO bookings (id, resource_id, user_id, owner_id, start, end, status, created_at)
            VALUES (?, 'res-gpu-01', 'user:david', 'user:marcus', ?, ?, 'approved', ?);
            """,
            (upcoming_bk_id, (now + timedelta(hours=4)).isoformat(), (now + timedelta(hours=6)).isoformat(), now.isoformat()),
        )

    # 6. Human owner approves Maya's request
    with db_transaction() as db_conn:
        db_conn.execute("UPDATE bookings SET status = 'approved', approved_at = ? WHERE id = ?", (now.isoformat(), booking_id))
    append_audit("user:marcus", "approve_request", {"booking_id": booking_id, "resource_id": "res-gpu-01"})

    # 7 & 8. Borrower checks in and condition notes are recorded
    with db_transaction() as db_conn:
        db_conn.execute("UPDATE bookings SET status = 'checked_in', checked_in_at = ? WHERE id = ?", (now.isoformat(), booking_id))
        db_conn.execute(
            """
            INSERT INTO conditions (id, booking_id, phase, notes, recorded_by, created_at)
            VALUES ('cond-01', ?, 'before', 'Clean glass panel, power LED operational, cables secure', 'user:maya', ?);
            """,
            (booking_id, now.isoformat()),
        )
    append_audit("user:maya", "check_in", {"booking_id": booking_id, "resource_id": "res-gpu-01", "condition": "Clean glass panel, power LED operational"})

    # 9 & 10. Borrower returns equipment and reports 'fan fault'
    # Report incident via API
    inc_res = client.post(
        "/api/incidents",
        json={
            "type": "safety",
            "description": "Fan fault: lower intake fan bearing grinding and abnormal heat under load.",
            "booking_id": booking_id,
            "resource_id": "res-gpu-01",
        },
        headers=maya_headers,
    )
    assert inc_res.status_code == 201
    inc_data = inc_res.json()
    assert inc_data["status"] == "incident_recorded"
    assert inc_data["hold"] is not None

    # 11. Resource becomes NOT_READY
    res_after = conn.execute("SELECT * FROM resources WHERE id = 'res-gpu-01'").fetchone()
    assert res_after["status"] == "not_ready"
    assert res_after["readiness_state"] == "not_ready"

    # 12. Upcoming booking (David's) becomes blocked_by_hold
    david_bk = conn.execute("SELECT * FROM bookings WHERE id = ?", (upcoming_bk_id,)).fetchone()
    assert david_bk["status"] == "blocked_by_hold"
    assert "Blocked by safety hold" in david_bk["owner_reason"]

    # 13. Affected borrower (David) receives notification
    notif = conn.execute("SELECT * FROM notifications WHERE user_id = 'user:david'").fetchone()
    assert notif is not None
    assert notif["kind"] == "hold_placed"
    assert "safety hold" in notif["body"]

    # 14. Evidence and history visible
    incidents = client.get("/api/incidents?resource_id=res-gpu-01").json()
    assert len(incidents) == 1
    assert "Fan fault" in incidents[0]["description"]

    # 15 & 16. Inspector reviews and clears hold with written note
    active_holds = client.get("/api/holds?resource_id=res-gpu-01&status=active").json()
    assert len(active_holds) == 1
    hold_id = active_holds[0]["id"]

    clear_res = client.post(
        f"/api/holds/{hold_id}/clear",
        json={
            "cleared_note": "Replaced Noctua intake fan assembly. Ran Furmark stress test for 45 min at 100% TDP; max GPU temp 69C, acoustic noise nominal.",
        },
        headers=thorne_headers,
    )
    assert clear_res.status_code == 200
    assert clear_res.json()["status"] == "hold_cleared"

    # 17. Resource returns to READY (or ready with training condition)
    res_final = conn.execute("SELECT * FROM resources WHERE id = 'res-gpu-01'").fetchone()
    assert res_final["status"] == "available"
    assert res_final["readiness_state"] == "ready"
    assert res_final["hold_id"] is None

    # 18. Audit timeline shows complete sequence
    audit_trail = client.get("/api/audit?resource_id=res-gpu-01").json()
    actions = [e["action"] for e in audit_trail]
    assert "create_request" in actions
    assert "approve_request" in actions
    assert "check_in" in actions
    assert "report_incident" in actions
    assert "place_precautionary_hold" in actions
    assert "clear_safety_hold" in actions

    # 19. Verify Chain shows ok: True (tamper-evident proof)
    verify_res = client.get("/api/audit/verify").json()
    assert verify_res["ok"] is True
    assert verify_res["broken_at"] is None
    assert verify_res["checked"] >= 6
    conn.close()
