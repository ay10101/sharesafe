# tests/test_trust_audit.py
"""
Unit and integrity tests for the tamper-evident audit chain.
Verifies all 9 core requirements:
1. Empty chain verification
2. Single valid entry
3. Multiple valid entries
4. Modified payload detected
5. Modified hash detected
6. Deleted middle entry detected
7. Wrong previous hash detected
8. Exact broken entry index returned
9. Denied actions logged
"""
import tempfile
from pathlib import Path
import pytest

from app.core.audit import (
    GENESIS_HASH,
    InMemoryAuditStore,
    SQLiteAuditStore,
    append_audit,
    get_audit_trail,
    set_audit_store,
    verify_chain,
)
from app.db.schema import SCHEMA_SQL
from app.db.session import init_db


@pytest.fixture(params=["in_memory", "sqlite"])
def audit_store(request):
    """Fixture parameterized to run tests against both in-memory and SQLite stores."""
    if request.param == "in_memory":
        store = InMemoryAuditStore()
        set_audit_store(store)
        yield store
        store.clear()
    else:
        with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
            temp_db = Path(f.name)
        init_db(temp_db)
        store = SQLiteAuditStore(db_path=temp_db)
        set_audit_store(store)
        yield store
        temp_db.unlink(missing_ok=True)


def test_1_empty_chain(audit_store):
    """1. Empty chain reports ok: True, broken_at: None, checked: 0."""
    result = verify_chain()
    assert result == {"ok": True, "broken_at": None, "checked": 0}


def test_2_single_valid_entry(audit_store):
    """2. Single entry with GENESIS previous hash verifies successfully."""
    h = append_audit("user:maya", "create_request", {"resource_id": "res-gpu-01"})
    assert isinstance(h, str)
    assert len(h) == 64

    trail = get_audit_trail()
    assert len(trail) == 1
    assert trail[0]["prev_hash"] == GENESIS_HASH
    assert trail[0]["actor"] == "user:maya"
    assert trail[0]["action"] == "create_request"

    result = verify_chain()
    assert result == {"ok": True, "broken_at": None, "checked": 1}


def test_3_multiple_valid_entries(audit_store):
    """3. Multiple valid entries maintain cryptographic chain and verify cleanly."""
    append_audit("user:maya", "create_request", {"resource_id": "res-gpu-01"})
    append_audit("user:marcus", "approve_request", {"booking_id": "bk-1"})
    append_audit("user:maya", "check_in", {"condition": "good"})
    append_audit("user:maya", "return", {"condition": "good"})

    trail = get_audit_trail()
    assert len(trail) == 4

    # Verify each link points to previous
    assert trail[0]["prev_hash"] == GENESIS_HASH
    assert trail[1]["prev_hash"] == trail[0]["hash"]
    assert trail[2]["prev_hash"] == trail[1]["hash"]
    assert trail[3]["prev_hash"] == trail[2]["hash"]

    result = verify_chain()
    assert result == {"ok": True, "broken_at": None, "checked": 4}


def test_4_modified_payload_detected(audit_store):
    """4. Modifying the payload in any middle row is detected by verification."""
    append_audit("user:maya", "create_request", {"resource_id": "res-gpu-01"})
    append_audit("user:marcus", "approve_request", {"booking_id": "bk-1"})
    append_audit("user:maya", "check_in", {"condition": "pristine"})

    # Tamper middle entry (index 1) payload
    audit_store.tamper(index=1, field="payload", new_val={"tampered_amount": 9999})

    result = verify_chain()
    assert result["ok"] is False
    assert result["broken_at"] == 1


def test_5_modified_hash_detected(audit_store):
    """5. Altering an entry's hash is detected immediately."""
    append_audit("user:maya", "create_request", {"resource_id": "res-gpu-01"})
    append_audit("user:marcus", "approve_request", {"booking_id": "bk-1"})

    # Tamper hash of index 0
    audit_store.tamper(index=0, field="hash", new_val="0" * 64)

    result = verify_chain()
    assert result["ok"] is False
    assert result["broken_at"] == 0


def test_6_deleted_middle_entry_detected(audit_store):
    """6. Deleting a middle entry breaks the prev_hash linkage at that exact position."""
    append_audit("user:maya", "create_request", {"resource_id": "res-gpu-01"})
    append_audit("user:marcus", "approve_request", {"booking_id": "bk-1"})
    append_audit("user:maya", "check_in", {"condition": "ok"})

    if isinstance(audit_store, InMemoryAuditStore):
        del audit_store.entries[1]
    else:
        conn = audit_store._get_connection()
        # Delete second inserted row
        cur = conn.cursor()
        cur.execute("SELECT rowid FROM audit ORDER BY rowid ASC")
        rows = cur.fetchall()
        del_rowid = rows[1]["rowid"]
        conn.execute("DELETE FROM audit WHERE rowid = ?", (del_rowid,))
        conn.commit()
        conn.close()

    result = verify_chain()
    assert result["ok"] is False
    # The entry that used to be index 2 is now index 1, and its prev_hash points to deleted entry
    assert result["broken_at"] == 1


def test_7_wrong_previous_hash_detected(audit_store):
    """7. An entry with an arbitrary or incorrect prev_hash is detected."""
    append_audit("user:maya", "create_request", {"resource_id": "res-gpu-01"})
    append_audit("user:marcus", "approve_request", {"booking_id": "bk-1"})

    audit_store.tamper(index=1, field="prev_hash", new_val="bad_prev_hash_value")

    result = verify_chain()
    assert result["ok"] is False
    assert result["broken_at"] == 1


def test_8_exact_broken_index_returned(audit_store):
    """8. When chain is broken at index 3 of 5, broken_at returns 3."""
    for i in range(5):
        append_audit(f"user:{i}", f"action_{i}", {"step": i})

    audit_store.tamper(index=3, field="payload", new_val="corrupted")

    result = verify_chain()
    assert result["ok"] is False
    assert result["broken_at"] == 3
    assert result["checked"] == 3


def test_9_denied_actions_are_logged(audit_store):
    """9. Denied actions are logged with DENIED:<action> prefix."""
    append_audit(
        actor_id="agent:custodian",
        action="DENIED:clear_safety_hold",
        payload={"reason": "Agent cannot clear safety holds", "resource_id": "res-gpu-01"},
    )

    trail = get_audit_trail()
    assert len(trail) == 1
    assert trail[0]["action"] == "DENIED:clear_safety_hold"
    assert trail[0]["actor"] == "agent:custodian"

    result = verify_chain()
    assert result["ok"] is True
