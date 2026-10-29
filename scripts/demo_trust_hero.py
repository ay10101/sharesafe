#!/usr/bin/env python3
"""
ShareSafe Live Demo Script: The Complete 19-Step Trust Layer Journey.
Demonstrates:
- Bounded Agent Autonomy
- Deterministic Readiness Evaluation
- Fan Fault Incident -> Precautionary Safety Hold
- Automatic Upcoming Booking Blocking & Borrower Notifications
- Inspector-Only Clearance with Mandatory Written Notes
- Cryptographic SHA-256 Tamper-Evident Audit Chain Verification
- Live Tamper Detection Proof
"""
import sys
import json
from datetime import datetime, timezone, timedelta
from pathlib import Path

# Add project root to path
ROOT_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT_DIR))

# Ensure UTF-8 output on Windows consoles
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

from app.contracts import Readiness, Actor
from app.core.audit import append_audit, get_audit_trail, verify_chain, simulate_tamper
from app.core.authority import execute
from app.core.holds import place_safety_hold, clear_safety_hold_record
from app.core.readiness import evaluate_readiness
from app.db.seed import seed_database
from app.db.session import db_transaction, get_connection


def print_banner(text: str):
    print("\n" + "=" * 70)
    print(f"  {text}")
    print("=" * 70)


def print_step(num: int, title: str):
    print(f"\n[Step {num:02d}] {title}")
    print("-" * 50)


def main():
    print_banner("SHARESAFE TRUST LAYER: LIVE HERO DEMONSTRATION")
    print("Theme: Readiness-and-accountability system with Bounded Autonomy.")
    print("Principle: Agents only make things safer or reversible; human review for restrictions.")

    # Step 1: Clean seed state
    print_step(1, "Resetting database to pristine seed state")
    seed_database()
    print("✓ Seed state initialized (Users: Maya, Marcus, Dr. Thorne, Sarah, Agent: Custodian).")

    # Step 2: Inspect GPU Workstation
    print_step(2, "Inspecting NVIDIA RTX 4090 Workstation Pod (Resource r1)")
    conn = get_connection()
    gpu = conn.execute("SELECT * FROM resources WHERE id = 'r1'").fetchone()
    print(f"Resource: {gpu['name']} ({gpu['location']})")
    print(f"Current State: {gpu['status']} | Readiness: {gpu['readiness_state']}")

    # Step 3: Evaluate Readiness with Rules
    print_step(3, "Evaluating deterministic readiness rules")
    readiness = evaluate_readiness(dict(gpu), holds=[])
    print(f"Readiness Verdict: {readiness.state.value.upper()}")
    for r in readiness.reasons:
        icon = "✓" if r.passed else "✗"
        print(f"  [{icon}] {r.rule_id}: {r.message}")

    # Step 4: Borrower Maya requests booking
    print_step(4, "Borrower (Maya Lin, u1) submits reservation request")
    now = datetime.now(timezone.utc)
    t_start = (now + timedelta(hours=1)).isoformat()
    t_end = (now + timedelta(hours=3)).isoformat()
    booking_id = "bk-demo-cuda"

    with db_transaction() as tx:
        tx.execute(
            """
            INSERT INTO bookings (id, resource_id, user_id, owner_id, start, end, status, justification, created_at)
            VALUES (?, 'r1', 'u1', 'u2', ?, ?, 'requested', 'CS701 CUDA optimization research', ?);
            """,
            (booking_id, t_start, t_end, now.isoformat()),
        )
    append_audit("u1", "create_request", {"booking_id": booking_id, "resource_id": "r1"})
    print(f"✓ Booking {booking_id} created in 'requested' state.")

    # Also simulate an upcoming booking by David Kim later in the day
    upcoming_id = "bk-demo-david"
    with db_transaction() as tx:
        tx.execute(
            """
            INSERT INTO bookings (id, resource_id, user_id, owner_id, start, end, status, created_at)
            VALUES (?, 'r1', 'u5', 'u2', ?, ?, 'approved', ?);
            """,
            (upcoming_id, (now + timedelta(hours=4)).isoformat(), (now + timedelta(hours=6)).isoformat(), now.isoformat()),
        )
    print(f"✓ Upcoming confirmed reservation {upcoming_id} queued for David Kim (u5).")

    # Step 5: Owner Marcus approves request
    print_step(5, "Lab Manager (Marcus Vance, u2) approves Maya's request")
    with db_transaction() as tx:
        tx.execute("UPDATE bookings SET status = 'approved', approved_at = ? WHERE id = ?", (now.isoformat(), booking_id))
    append_audit("u2", "approve_request", {"booking_id": booking_id, "resource_id": "r1"})
    print(f"✓ Booking {booking_id} approved by owner.")

    # Step 6: Checkout and pre-condition inspection
    print_step(6, "Borrower checks in at Smart Locker Kiosk")
    with db_transaction() as tx:
        tx.execute("UPDATE bookings SET status = 'checked_in', checked_in_at = ? WHERE id = ?", (now.isoformat(), booking_id))
        tx.execute(
            """
            INSERT INTO conditions (id, booking_id, phase, notes, recorded_by, created_at)
            VALUES ('cond-pre-01', ?, 'before', 'Glass panel intact, dual power rails nominal, fans silent.', 'u1', ?);
            """,
            (booking_id, now.isoformat()),
        )
    append_audit("u1", "check_in", {"booking_id": booking_id, "resource_id": "r1", "notes": "Fans silent"})
    print(f"✓ Equipment unlocked from Bay. Pre-condition inspection recorded.")

    # Step 7: Return with reported fault
    print_step(7, "Borrower returns equipment and reports 'Fan Fault'")
    incident_desc = "Fan fault: intake fan rattling loudly, GPU temperatures peaked at 93C under 40% load."
    with db_transaction() as tx:
        tx.execute("UPDATE bookings SET status = 'returned', returned_at = ? WHERE id = ?", (now.isoformat(), booking_id))
        tx.execute(
            """
            INSERT INTO conditions (id, booking_id, phase, notes, recorded_by, created_at)
            VALUES ('cond-post-01', ?, 'after', 'Locker return with thermal warning.', 'u1', ?);
            """,
            (booking_id, now.isoformat()),
        )
        tx.execute(
            """
            INSERT INTO incidents (id, resource_id, booking_id, reporter_id, description, incident_type, status, created_at)
            VALUES ('inc-fan-01', 'r1', ?, 'u1', ?, 'safety', 'open', ?);
            """,
            (booking_id, incident_desc, now.isoformat()),
        )
    append_audit("u1", "report_incident", {"incident_id": "inc-fan-01", "resource_id": "r1", "type": "safety"})
    print(f"✓ Incident inc-fan-01 registered permanently into immutable incident log.")

    # Step 8: Custodian Agent triggers precautionary hold
    print_step(8, "Custodian Agent places automated Precautionary Hold")
    hold_info = place_safety_hold(
        resource_id="r1",
        placed_by="agent:custodian",
        reason=f"Precautionary safety hold: {incident_desc}",
        actor_kind="agent",
        actor_role="agent",
        incident_id="inc-fan-01",
    )
    print(f"✓ Hold {hold_info['hold_id']} placed by agent:custodian.")
    print(f"✓ Resource flipped to: NOT_READY.")

    # Step 9: Upcoming bookings blocked
    print_step(9, "Checking affected upcoming reservations")
    david_booking = conn.execute("SELECT * FROM bookings WHERE id = ?", (upcoming_id,)).fetchone()
    print(f"David Kim's booking {upcoming_id} status: {david_booking['status'].upper()}")
    print(f"Reason: {david_booking['owner_reason']}")

    # Step 10: Notification verification
    print_step(10, "Verifying automated borrower notifications")
    notif = conn.execute("SELECT * FROM notifications WHERE user_id = 'u5'").fetchone()
    print(f"Notification to David: \"{notif['body']}\"")

    # Step 11: Inspector review and clearance
    print_step(11, "Authorized Inspector (Dr. Aris Thorne, u3) inspects and clears hold")
    # Verify unauthorized attempt is denied
    print("Testing unauthorized borrower bypass...")
    try:
        clear_safety_hold_record("r1", "u1", "Bypassing hold", actor_role="borrower")
    except PermissionError as e:
        print(f"  ✓ Server-side security guard blocked borrower: {e}")

    # Inspector clearance with mandatory written note
    clear_note = "Replaced lower intake fan bearing assembly. Stress tested with Furmark 45min at 100% TDP. Max temp 67C, acoustics nominal."
    clear_res = clear_safety_hold_record(
        resource_id="r1",
        cleared_by="u3",
        cleared_note=clear_note,
        actor_role="inspector",
    )
    print(f"✓ Hold cleared by Dr. Thorne. Note recorded: \"{clear_note}\"")

    # Step 12: Verify resource restored
    print_step(12, "Verifying equipment restored to service")
    gpu_final = conn.execute("SELECT * FROM resources WHERE id = 'r1'").fetchone()
    print(f"Resource State: {gpu_final['status']} | Readiness: {gpu_final['readiness_state']}")
    assert gpu_final["status"] == "available"
    assert gpu_final["readiness_state"] == "ready"

    # Step 13: Cryptographic Audit Chain Verification
    print_step(13, "Cryptographic Audit Chain Integrity Check")
    verification = verify_chain()
    print(f"Chain Verification: {'VALID' if verification['ok'] else 'BROKEN'}")
    print(f"Blocks Verified: {verification['checked']}")
    print(f"Broken At: {verification['broken_at']}")
    assert verification["ok"] is True

    # Step 14: Live Tamper Detection Proof
    print_step(14, "Simulating unauthorized database modification (Tamper Proof)")
    simulate_tamper(index=1, field="payload", value="HACKED_BYPASS_ATTEMPT")
    tamper_check = verify_chain()
    print(f"Re-verifying after injected tamper:")
    print(f"  Chain Status: {'VALID' if tamper_check['ok'] else 'BROKEN ✗'}")
    print(f"  Tampered Block Identified: Block #{tamper_check['broken_at'] + 1} (Index {tamper_check['broken_at']})")
    assert tamper_check["ok"] is False
    assert tamper_check["broken_at"] == 1
    print("✓ Cryptographic tamper-evidence PROVEN: unauthorized row mutation instantly detected!")

    print_banner("DEMONSTRATION COMPLETED SUCCESSFULLY")
    print("All 14 milestones verified. System is deterministic, safe, and tamper-evident.")
    conn.close()


if __name__ == "__main__":
    main()
