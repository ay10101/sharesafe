# app/core/audit.py
"""
ShareSafe Tamper-Evident Audit Chain.
Provides cryptographic chaining of all system state transitions,
enforcing server-side accountability and verification.
"""
from __future__ import annotations
import hashlib
import json
import sqlite3
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional
import threading
from contextlib import contextmanager

GENESIS_HASH = "GENESIS"

_thread_local = threading.local()

# Safely track active transaction or open connection on the current thread to prevent SQLite same-thread deadlocks
_thread_local = threading.local()

class _TrackedConnection(sqlite3.Connection):
    def close(self):
        if getattr(_thread_local, "current_conn", None) is self:
            _thread_local.current_conn = None
        if getattr(_thread_local, "active_conn", None) is self:
            _thread_local.active_conn = None
        return super().close()

_orig_connect = sqlite3.connect

def _tracked_connect(*args, **kwargs):
    if "factory" not in kwargs or kwargs["factory"] is sqlite3.Connection:
        kwargs["factory"] = _TrackedConnection
    conn = _orig_connect(*args, **kwargs)
    _thread_local.current_conn = conn
    return conn

sqlite3.connect = _tracked_connect

try:
    import app.db.session as _db_session
    _orig_db_trans = _db_session.db_transaction

    @contextmanager
    def _tracked_db_transaction(db_path: Optional[Path] = None):
        with _orig_db_trans(db_path) as conn:
            _thread_local.active_conn = conn
            try:
                yield conn
            finally:
                _thread_local.active_conn = None

    _db_session.db_transaction = _tracked_db_transaction
except Exception:
    pass


def canonical_json(data: Any) -> str:
    """
    Produce canonical, deterministic JSON representation.
    Keys are strictly sorted with no redundant whitespace.
    """
    return json.dumps(data, sort_keys=True, separators=(",", ":"), default=str)


def compute_hash(prev_hash: str, body: dict) -> str:
    """
    Calculate SHA-256 hash over previous hash and canonical body.
    SHA256(prev_hash + canonical_json(body))
    """
    payload_str = prev_hash + canonical_json(body)
    return hashlib.sha256(payload_str.encode("utf-8")).hexdigest()


class AuditEntry:
    def __init__(
        self,
        entry_id: str,
        actor: str,
        action: str,
        payload: dict,
        timestamp: str,
        prev_hash: str,
        entry_hash: str,
    ):
        self.id = entry_id
        self.actor = actor
        self.actor_id = actor
        self.action = action
        self.payload = payload
        self.timestamp = timestamp
        self.prev_hash = prev_hash
        self.hash = entry_hash

    @property
    def body(self) -> dict:
        return {
            "action": self.action,
            "actor": self.actor,
            "payload": self.payload,
            "timestamp": self.timestamp,
        }

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "actor": self.actor,
            "actor_id": self.actor,
            "action": self.action,
            "payload": self.payload,
            "timestamp": self.timestamp,
            "prev_hash": self.prev_hash,
            "hash": self.hash,
        }


class AuditStore:
    """Abstract base interface for audit storage."""
    def append(
        self, actor_id: str, action: str, payload: dict, conn: Optional[sqlite3.Connection] = None
    ) -> AuditEntry:
        raise NotImplementedError

    def get_all(
        self, resource_id: Optional[str] = None, booking_id: Optional[str] = None
    ) -> List[AuditEntry]:
        raise NotImplementedError

    def verify(self) -> Dict[str, Any]:
        raise NotImplementedError

    def clear(self) -> None:
        raise NotImplementedError

    def tamper(self, index: int, field: str = "payload", new_val: Any = "TAMPERED") -> bool:
        raise NotImplementedError


class InMemoryAuditStore(AuditStore):
    """In-memory audit store for fast unit testing and isolated verification."""
    def __init__(self):
        self.entries: List[AuditEntry] = []

    def append(
        self, actor_id: str, action: str, payload: dict, conn: Optional[sqlite3.Connection] = None
    ) -> AuditEntry:
        prev_hash = self.entries[-1].hash if self.entries else GENESIS_HASH
        ts = datetime.now(timezone.utc).isoformat()
        body = {
            "action": action,
            "actor": actor_id,
            "payload": payload,
            "timestamp": ts,
        }
        h = compute_hash(prev_hash, body)
        entry_id = f"aud-{len(self.entries) + 1:04d}"
        entry = AuditEntry(
            entry_id=entry_id,
            actor=actor_id,
            action=action,
            payload=payload,
            timestamp=ts,
            prev_hash=prev_hash,
            entry_hash=h,
        )
        self.entries.append(entry)
        return entry

    def get_all(
        self, resource_id: Optional[str] = None, booking_id: Optional[str] = None
    ) -> List[AuditEntry]:
        results = []
        for e in self.entries:
            p = e.payload or {}
            res_val = p.get("resource_id") or p.get("args", {}).get("resource_id")
            book_val = p.get("booking_id") or p.get("args", {}).get("booking_id")
            if resource_id and res_val != resource_id:
                continue
            if booking_id and book_val != booking_id:
                continue
            results.append(e)
        return results

    def verify(self) -> Dict[str, Any]:
        checked = len(self.entries)
        if checked == 0:
            return {"ok": True, "broken_at": None, "checked": 0}

        expected_prev = GENESIS_HASH
        for idx, entry in enumerate(self.entries):
            # Check 1: prev_hash linkage
            if entry.prev_hash != expected_prev:
                return {"ok": False, "broken_at": idx, "checked": idx}

            # Check 2: recompute hash from prev_hash + canonical_json(body)
            computed = compute_hash(entry.prev_hash, entry.body)
            if entry.hash != computed:
                return {"ok": False, "broken_at": idx, "checked": idx}

            expected_prev = entry.hash

        return {"ok": True, "broken_at": None, "checked": checked}

    def clear(self) -> None:
        self.entries.clear()

    def tamper(self, index: int, field: str = "payload", new_val: Any = "TAMPERED") -> bool:
        if 0 <= index < len(self.entries):
            entry = self.entries[index]
            if field == "payload":
                entry.payload = {"tampered": new_val}
            elif field == "hash":
                entry.hash = str(new_val)
            elif field == "prev_hash":
                entry.prev_hash = str(new_val)
            elif field == "actor":
                entry.actor = str(new_val)
                entry.actor_id = str(new_val)
            elif field == "action":
                entry.action = str(new_val)
            return True
        return False


class SQLiteAuditStore(AuditStore):
    """SQLite-backed audit store connected to the ShareSafe relational database."""
    def __init__(self, db_path: Optional[Path] = None):
        self.db_path = db_path

    def _get_connection(self) -> sqlite3.Connection:
        if self.db_path:
            conn = sqlite3.connect(str(self.db_path), timeout=30.0, check_same_thread=False)
            conn.row_factory = sqlite3.Row
            return conn
        from app.db.session import get_connection
        return get_connection()

    def append(
        self, actor_id: str, action: str, payload: dict, conn: Optional[sqlite3.Connection] = None
    ) -> AuditEntry:
        should_close = False
        if conn is None:
            active_tx_conn = getattr(_thread_local, "active_conn", None) or getattr(_thread_local, "current_conn", None)
            if active_tx_conn is not None:
                conn = active_tx_conn
            else:
                conn = self._get_connection()
                should_close = True
        try:
            cur = conn.cursor()
            cur.execute("SELECT hash FROM audit ORDER BY rowid DESC LIMIT 1")
            last_row = cur.fetchone()
            prev_hash = last_row["hash"] if last_row else GENESIS_HASH

            ts = datetime.now(timezone.utc).isoformat()
            body = {
                "action": action,
                "actor": actor_id,
                "payload": payload,
                "timestamp": ts,
            }
            h = compute_hash(prev_hash, body)
            entry_id = f"aud-{uuid.uuid4().hex[:8]}"
            body_json = canonical_json(body)

            cur.execute(
                """
                INSERT INTO audit (id, prev_hash, body_json, hash, timestamp, actor_id, action)
                VALUES (?, ?, ?, ?, ?, ?, ?);
                """,
                (entry_id, prev_hash, body_json, h, ts, actor_id, action),
            )
            if should_close:
                conn.commit()
            return AuditEntry(
                entry_id=entry_id,
                actor=actor_id,
                action=action,
                payload=payload,
                timestamp=ts,
                prev_hash=prev_hash,
                entry_hash=h,
            )
        finally:
            if should_close:
                conn.close()

    def get_all(
        self, resource_id: Optional[str] = None, booking_id: Optional[str] = None
    ) -> List[AuditEntry]:
        conn = self._get_connection()
        try:
            cur = conn.cursor()
            cur.execute("SELECT * FROM audit ORDER BY rowid ASC")
            rows = cur.fetchall()
            results = []
            for r in rows:
                try:
                    body = json.loads(r["body_json"])
                    payload = body.get("payload", {})
                except Exception:
                    payload = {}
                res_val = payload.get("resource_id") or payload.get("args", {}).get("resource_id")
                book_val = payload.get("booking_id") or payload.get("args", {}).get("booking_id")
                if resource_id and res_val != resource_id:
                    continue
                if booking_id and book_val != booking_id:
                    continue
                results.append(
                    AuditEntry(
                        entry_id=r["id"],
                        actor=r["actor_id"],
                        action=r["action"],
                        payload=payload,
                        timestamp=r["timestamp"],
                        prev_hash=r["prev_hash"],
                        entry_hash=r["hash"],
                    )
                )
            return results
        finally:
            conn.close()

    def verify(self) -> Dict[str, Any]:
        conn = self._get_connection()
        try:
            cur = conn.cursor()
            cur.execute("SELECT rowid, id, prev_hash, body_json, hash FROM audit ORDER BY rowid ASC")
            rows = cur.fetchall()
            checked = len(rows)
            if checked == 0:
                return {"ok": True, "broken_at": None, "checked": 0}

            expected_prev = GENESIS_HASH
            for idx, r in enumerate(rows):
                if r["prev_hash"] != expected_prev:
                    return {"ok": False, "broken_at": idx, "checked": idx}

                try:
                    body = json.loads(r["body_json"])
                except Exception:
                    return {"ok": False, "broken_at": idx, "checked": idx}

                computed = compute_hash(r["prev_hash"], body)
                if r["hash"] != computed:
                    return {"ok": False, "broken_at": idx, "checked": idx}

                expected_prev = r["hash"]

            return {"ok": True, "broken_at": None, "checked": checked}
        finally:
            conn.close()

    def clear(self) -> None:
        conn = self._get_connection()
        try:
            conn.execute("DELETE FROM audit;")
            conn.commit()
        finally:
            conn.close()

    def tamper(self, index: int, field: str = "payload", new_val: Any = "TAMPERED") -> bool:
        conn = self._get_connection()
        try:
            cur = conn.cursor()
            cur.execute("SELECT rowid, id, body_json FROM audit ORDER BY rowid ASC")
            rows = cur.fetchall()
            if 0 <= index < len(rows):
                target_row = rows[index]
                rowid = target_row["rowid"]
                if field == "payload":
                    body = json.loads(target_row["body_json"])
                    body["payload"] = {"tampered": new_val}
                    cur.execute(
                        "UPDATE audit SET body_json = ? WHERE rowid = ?",
                        (canonical_json(body), rowid),
                    )
                elif field == "hash":
                    cur.execute("UPDATE audit SET hash = ? WHERE rowid = ?", (str(new_val), rowid))
                elif field == "prev_hash":
                    cur.execute(
                        "UPDATE audit SET prev_hash = ? WHERE rowid = ?", (str(new_val), rowid)
                    )
                conn.commit()
                return True
            return False
        finally:
            conn.close()


# Default store: SQLiteAuditStore
_active_store: AuditStore = SQLiteAuditStore()


def set_audit_store(store: AuditStore) -> None:
    """Set the active audit store (e.g. for switching to InMemory in isolated tests)."""
    global _active_store
    _active_store = store


def get_audit_store() -> AuditStore:
    return _active_store


def append_audit(
    actor_id: str, action: str, payload: dict, conn: Optional[sqlite3.Connection] = None
) -> str:
    """
    Append an entry to the tamper-evident audit log and return the entry hash.
    Signature matches shared contract: append_audit(actor_id, action, payload) -> str
    """
    entry = _active_store.append(actor_id, action, payload, conn=conn)
    return entry.hash


def verify_chain() -> Dict[str, Any]:
    """
    Cryptographically verify the integrity of the audit chain.
    Returns: {"ok": bool, "broken_at": int | None, "checked": int}
    """
    return _active_store.verify()


def get_audit_trail(
    resource_id: Optional[str] = None, booking_id: Optional[str] = None
) -> List[Dict[str, Any]]:
    """Return chronological audit trail as dictionaries."""
    entries = _active_store.get_all(resource_id=resource_id, booking_id=booking_id)
    return [e.to_dict() for e in entries]


def simulate_tamper(index: int = 0, field: str = "payload", value: str = "TAMPERED") -> bool:
    """Admin demo helper to simulate a tampered audit record."""
    return _active_store.tamper(index=index, field=field, new_val=value)
