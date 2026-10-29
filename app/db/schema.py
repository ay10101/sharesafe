# app/db/schema.py
"""
Database schema for ShareSafe.
Defines tables for:
users, resources, bookings, conditions, incidents, holds, waitlist, notifications, audit.
"""

SCHEMA_SQL = """
PRAGMA foreign_keys = ON;

CREATE TABLE IF NOT EXISTS users (
    id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    email TEXT,
    role TEXT NOT NULL,          -- borrower, owner, inspector, admin, agent
    kind TEXT NOT NULL DEFAULT 'human', -- human, agent
    training TEXT NOT NULL DEFAULT '[]', -- JSON array of strings
    year INTEGER NOT NULL DEFAULT 1,
    team_id TEXT,
    avatar TEXT,
    title TEXT,
    password_hash TEXT
);

CREATE TABLE IF NOT EXISTS resources (
    id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    category TEXT NOT NULL,
    location TEXT NOT NULL,
    description TEXT,
    owner_id TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'available',      -- available, requested, checked_out, not_ready
    readiness_state TEXT NOT NULL DEFAULT 'ready', -- ready, ready_with_conditions, not_ready
    hold_id TEXT,
    rules_json TEXT NOT NULL DEFAULT '{}',
    image_url TEXT,
    training_required INTEGER NOT NULL DEFAULT 0,
    training_description TEXT,
    usage_rules TEXT,
    max_duration_days INTEGER NOT NULL DEFAULT 7,
    bay_tag TEXT,
    item_code TEXT,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS bookings (
    id TEXT PRIMARY KEY,
    resource_id TEXT NOT NULL,
    user_id TEXT NOT NULL,
    owner_id TEXT NOT NULL,
    start TEXT NOT NULL,
    end TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'requested', -- requested, approved, rejected, waitlisted, checked_in, returned, cancelled, overdue, incident_open
    message TEXT,
    purpose TEXT,
    justification TEXT,
    training_acknowledged INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL,
    approved_at TEXT,
    rejected_at TEXT,
    waitlisted_at TEXT,
    checked_in_at TEXT,
    returned_at TEXT,
    cancelled_at TEXT,
    owner_reason TEXT,
    decision_trace_json TEXT,
    returned_late INTEGER NOT NULL DEFAULT 0,
    no_show INTEGER NOT NULL DEFAULT 0,
    team_id TEXT,
    FOREIGN KEY(resource_id) REFERENCES resources(id),
    FOREIGN KEY(user_id) REFERENCES users(id)
);

CREATE TABLE IF NOT EXISTS conditions (
    id TEXT PRIMARY KEY,
    booking_id TEXT NOT NULL,
    phase TEXT NOT NULL, -- 'before' | 'after'
    notes TEXT NOT NULL,
    photo_hash TEXT,
    recorded_by TEXT NOT NULL,
    created_at TEXT NOT NULL,
    FOREIGN KEY(booking_id) REFERENCES bookings(id)
);

CREATE TABLE IF NOT EXISTS incidents (
    id TEXT PRIMARY KEY,
    resource_id TEXT NOT NULL,
    booking_id TEXT,
    reporter_id TEXT NOT NULL,
    description TEXT NOT NULL,
    incident_type TEXT DEFAULT 'fault',
    status TEXT NOT NULL DEFAULT 'open', -- 'open', 'investigating', 'resolved'
    resolution_note TEXT,
    resolved_by TEXT,
    created_at TEXT NOT NULL,
    resolved_at TEXT,
    FOREIGN KEY(resource_id) REFERENCES resources(id)
);

CREATE TABLE IF NOT EXISTS holds (
    id TEXT PRIMARY KEY,
    resource_id TEXT NOT NULL,
    placed_by TEXT NOT NULL,
    reason TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'active', -- 'active', 'cleared'
    cleared_by TEXT,
    cleared_note TEXT,
    created_at TEXT NOT NULL,
    cleared_at TEXT,
    FOREIGN KEY(resource_id) REFERENCES resources(id)
);

CREATE TABLE IF NOT EXISTS waitlist (
    id TEXT PRIMARY KEY,
    resource_id TEXT NOT NULL,
    user_id TEXT NOT NULL,
    slot_start TEXT NOT NULL,
    slot_end TEXT NOT NULL,
    priority_json TEXT,
    created_at TEXT NOT NULL,
    FOREIGN KEY(resource_id) REFERENCES resources(id),
    FOREIGN KEY(user_id) REFERENCES users(id)
);

CREATE TABLE IF NOT EXISTS notifications (
    id TEXT PRIMARY KEY,
    user_id TEXT NOT NULL,
    kind TEXT NOT NULL,
    body TEXT NOT NULL,
    read INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL,
    FOREIGN KEY(user_id) REFERENCES users(id)
);

CREATE TABLE IF NOT EXISTS audit (
    id TEXT PRIMARY KEY,
    prev_hash TEXT NOT NULL,
    body_json TEXT NOT NULL,
    hash TEXT NOT NULL,
    timestamp TEXT NOT NULL,
    actor_id TEXT NOT NULL,
    action TEXT NOT NULL
);
"""

