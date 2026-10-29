# app/core/authority.py
"""
Stub / implementation of Authority Gate (owned by P2).
Enforces server-side authority matrix for agents and humans.
"""
from __future__ import annotations
import logging
from typing import Any, Callable, Dict, Optional
from app.contracts import Actor

logger = logging.getLogger("sharesafe.authority")

# In-memory audit log for fallback / testing when audit module is stubbed
AUDIT_LOG: list[dict] = []

# Authority matrix definition
# (Action, Agent-Allowed?, Reversible?)
ACTION_PERMISSIONS = {
    "place_precautionary_hold": {"agent_allowed": True, "reversible": True},
    "send_reminder": {"agent_allowed": True, "reversible": True},
    "flag_overdue": {"agent_allowed": True, "reversible": True},
    "draft_incident_packet": {"agent_allowed": True, "reversible": True},
    "propose_decision": {"agent_allowed": True, "reversible": True},
    "approve_exception": {"agent_allowed": False, "reversible": False},
    "clear_safety_hold": {"agent_allowed": False, "reversible": False},
    "apply_penalty": {"agent_allowed": False, "reversible": False},
    "reject_request": {"agent_allowed": False, "reversible": False},
}

# Optional custom handler hooks registered by apps/subsystems
_action_handlers: Dict[str, Callable[[Actor, dict], dict]] = {}

def register_handler(action: str, handler: Callable[[Actor, dict], dict]):
    _action_handlers[action] = handler

def append_audit(actor_id: str, action: str, payload: dict) -> str:
    """Append entry to audit log and return mock hash."""
    import hashlib, time, json
    entry = {
        "actor_id": actor_id,
        "action": action,
        "payload": payload,
        "timestamp": time.time(),
    }
    raw = json.dumps(entry, sort_keys=True, default=str)
    entry_hash = hashlib.sha256(raw.encode()).hexdigest()
    entry["hash"] = entry_hash
    AUDIT_LOG.append(entry)
    return entry_hash

def execute(actor: Actor, action: str, args: dict) -> dict:
    """
    Authority Gate:
    raises PermissionError if an agent attempts a human-only action;
    the denial is recorded in the audit chain.
    """
    perm = ACTION_PERMISSIONS.get(action)
    if perm is None:
        logger.warning(f"Unknown action {action} attempted by {actor}")
        # Default policy: block unknown actions for agents
        if actor.kind == "agent":
            append_audit(actor.id, f"DENIED:{action}", {"reason": "unknown_action", "args": args})
            raise PermissionError(f"Agent {actor.id} denied execution of unknown action: {action}")

    if actor.kind == "agent" and perm and not perm["agent_allowed"]:
        denial_payload = {
            "actor": {"id": actor.id, "kind": actor.kind, "role": actor.role},
            "attempted_action": action,
            "args": args,
            "reason": "Agent not permitted to perform human-only action",
        }
        append_audit(actor.id, f"DENIED:{action}", denial_payload)
        logger.warning(f"DENIED: Agent {actor.id} attempted human-only action {action}")
        raise PermissionError(f"Action '{action}' is human-only and cannot be executed by agent '{actor.id}'.")

    # Record action in audit log
    append_audit(actor.id, f"EXECUTE:{action}", {"args": args})

    # Execute custom handler if registered, otherwise return recorded execution
    if action in _action_handlers:
        return _action_handlers[action](actor, args)

    return {
        "status": "success",
        "action": action,
        "actor_id": actor.id,
        "args": args,
    }

