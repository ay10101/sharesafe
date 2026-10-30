# app/db/session.py
"""
Database connection and transaction management for SQLite.
Provides atomic transactions (BEGIN IMMEDIATE) to guarantee race-free booking operations.
"""
from __future__ import annotations
import os
import sqlite3
from contextlib import contextmanager
from pathlib import Path
from typing import Generator, Optional
from app.db.schema import SCHEMA_SQL

DEFAULT_DB_PATH = Path(__file__).resolve().parent.parent.parent / "data" / "sharesafe.db"

def get_db_path() -> Path:
    env_path = os.getenv("DATABASE_PATH")
    if env_path:
        p = Path(env_path)
    elif os.getenv("VERCEL"):
        p = Path("/tmp/sharesafe.db")
    else:
        p = DEFAULT_DB_PATH
    try:
        p.parent.mkdir(parents=True, exist_ok=True)
    except Exception:
        p = Path("/tmp/sharesafe.db")
        p.parent.mkdir(parents=True, exist_ok=True)
    return p

def get_connection(db_path: Optional[Path] = None) -> sqlite3.Connection:
    path = db_path or get_db_path()
    conn = sqlite3.connect(str(path), timeout=30.0, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON;")
    conn.execute("PRAGMA journal_mode = WAL;")
    return conn

def init_db(db_path: Optional[Path] = None) -> None:
    path = db_path or get_db_path()
    conn = get_connection(path)
    try:
        conn.executescript(SCHEMA_SQL)
        conn.commit()
    finally:
        conn.close()

@contextmanager
def db_transaction(db_path: Optional[Path] = None) -> Generator[sqlite3.Connection, None, None]:
    """
    Context manager that acquires an exclusive transaction lock (BEGIN IMMEDIATE).
    Guarantees that no two concurrent requests can interleave overlap checks and booking insertions.
    """
    conn = get_connection(db_path)
    try:
        conn.execute("BEGIN IMMEDIATE;")
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()

@contextmanager
def db_connection(db_path: Optional[Path] = None) -> Generator[sqlite3.Connection, None, None]:
    """
    Simple read-only or autocommit connection context manager.
    """
    conn = get_connection(db_path)
    try:
        yield conn
    finally:
        conn.close()

