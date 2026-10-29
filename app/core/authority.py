# app/core/authority.py
"""
ShareSafe Authority Gate & Bounded Autonomy Engine.
Enforces the principle that AI agents may only execute safe-direction or reversible actions.
Human-only actions (rejections, penalties, safety clearance) strictly require proper human roles.
Every attempt—approved or denied—is written to the tamper-evident audit chain.
"""
from __future__ import annotations
from typing import Any, Callable, Dict, List, Optional
from app.contracts import Actor
from app.core.audit import append_audit


# Table-driven authority matrix defining bounded autonomy rules
AUTHORITY: Dict[str, Dict[str, Any]] = {
    "place_precautionary_hold": {
        "agent": True,
        "reversible": True,
        "roles": ["owner", "inspector", "admin", "agent"],
        "description": "Place safety hold to immediately prevent equipment dispatch",
    },
    "send_reminder": {
        "agent": True,
        "reversible": True,
        "roles": ["owner", "inspector", "admin", "agent"],
        "description": "Send notification reminder to borrower or custodian",
    },
    "flag_overdue": {
        "agent": True,
        "reversible": True,
        "roles": ["owner", "inspector", "admin", "agent"],
        "description": "Flag booking as overdue past scheduled end window",
    },
    "draft_incident_packet": {
        "agent": True,
        "reversible": True,
        "roles": ["owner", "inspector", "admin", "agent"],
        "description": "Assemble factual timeline and evidence for human review",
    },
    "propose_decision": {
        "agent": True,
        "reversible": True,
        "roles": ["owner", "inspector", "admin", "agent"],
        "description": "Propose non-binding approval/waitlist recommendation to human owner",
    },
    "approve_exception": {
        "agent": False,
        "reversible": False,
        "roles": ["owner", "admin"],
        "description": "Approve request that violates standard policy",
    },
    "clear_safety_hold": {
        "agent": False,
        "reversible": False,
        "roles": ["inspector", "admin"],
        "description": "Clear safety hold following verified inspection with written notes",
    },
    "apply_penalty": {
        "agent": False,
        "reversible": False,
        "roles": ["admin"],
        "description": "Apply strike or penalty to borrower profile",
    },
    "reject_request": {
        "agent": False,
        "reversible": False,
        "roles": ["owner", "admin"],
        "description": "Reject equipment booking request",
    },
}


# Tool registry for reversible and non-reversible action execution
TOOLS: Dict[str, Callable[[dict], dict]] = {}
UNDO_HANDLERS: Dict[str, Callable[[dict], dict]] = {}

# In-memory journal of executed actions and their reverse checkpoints (keyed by audit_id)
_reversible_journal: Dict[str, Dict[str, Any]] = {}


def register_tool(
    action: str,
    handler: Callable[[dict], dict],
    undo_handler: Optional[Callable[[dict], dict]] = None,
) -> None:
    """Register tool handler and optional undo handler."""
    TOOLS[action] = handler
    if undo_handler:
        UNDO_HANDLERS[action] = undo_handler


# Default mock implementations for standalone execution
def _default_place_precautionary_hold(args: dict) -> dict:
    return {
        "status": "hold_placed",
        "resource_id": args.get("resource_id"),
        "reason": args.get("reason", "Precautionary safety hold"),
        "active": True,
    }


def _default_undo_place_precautionary_hold(checkpoint: dict) -> dict:
    return {
        "status": "hold_reversed",
        "resource_id": checkpoint.get("resource_id"),
        "active": False,
    }


def _default_send_reminder(args: dict) -> dict:
    return {
        "status": "reminder_sent",
        "user_id": args.get("user_id"),
        "message": args.get("message"),
    }


def _default_flag_overdue(args: dict) -> dict:
    return {
        "status": "booking_flagged_overdue",
        "booking_id": args.get("booking_id"),
        "overdue": True,
    }


def _default_undo_flag_overdue(checkpoint: dict) -> dict:
    return {
        "status": "booking_unflagged",
        "booking_id": checkpoint.get("booking_id"),
        "overdue": False,
    }


def _default_draft_incident_packet(args: dict) -> dict:
    return {
        "status": "packet_drafted",
        "incident_id": args.get("incident_id"),
        "summary": args.get("summary", ""),
    }


def _default_propose_decision(args: dict) -> dict:
    return {
        "status": "decision_proposed",
        "request_id": args.get("request_id"),
        "proposal": args.get("proposal", "recommend_approve"),
    }


def _default_clear_safety_hold(args: dict) -> dict:
    return {
        "status": "hold_cleared",
        "resource_id": args.get("resource_id"),
        "note": args.get("note") or args.get("cleared_note"),
    }


def _default_reject_request(args: dict) -> dict:
    return {
        "status": "request_rejected",
        "request_id": args.get("request_id"),
        "reason": args.get("reason", "Rejected by owner"),
    }


def _default_approve_exception(args: dict) -> dict:
    return {
        "status": "exception_approved",
        "request_id": args.get("request_id"),
        "justification": args.get("justification", ""),
    }


def _default_apply_penalty(args: dict) -> dict:
    return {
        "status": "penalty_applied",
        "user_id": args.get("user_id"),
        "penalty": args.get("penalty"),
    }


register_tool("place_precautionary_hold", _default_place_precautionary_hold, _default_undo_place_precautionary_hold)
register_tool("send_reminder", _default_send_reminder)
register_tool("flag_overdue", _default_flag_overdue, _default_undo_flag_overdue)
register_tool("draft_incident_packet", _default_draft_incident_packet)
register_tool("propose_decision", _default_propose_decision)
register_tool("clear_safety_hold", _default_clear_safety_hold)
register_tool("reject_request", _default_reject_request)
register_tool("approve_exception", _default_approve_exception)
register_tool("apply_penalty", _default_apply_penalty)


def execute(actor: Actor, action: str, args: dict) -> dict:
    """
    Execute an action under the server-side Authority Gate.
    Verifies actor kind (agent vs human), role permissions, and mandatory requirements.
    Every call—approved or denied—writes to the tamper-evident audit log.
    If denied, raises PermissionError AFTER logging the denial.
    """
    if action not in AUTHORITY:
        reason = f"Unknown action '{action}' requested under authority gate"
        append_audit(
            actor_id=actor.id,
            action=f"DENIED:{action}",
            payload={"reason": reason, "args": args, "actor_kind": actor.kind, "actor_role": actor.role},
        )
        raise PermissionError(reason)

    spec = AUTHORITY[action]

    # Rule 1: Agent authority check
    if actor.kind == "agent" and not spec.get("agent", False):
        reason = f"Agents are strictly forbidden from executing '{action}'. Human review required."
        append_audit(
            actor_id=actor.id,
            action=f"DENIED:{action}",
            payload={"reason": reason, "args": args, "actor_kind": actor.kind, "actor_role": actor.role},
        )
        raise PermissionError(reason)

    # Rule 2: Role check for humans
    allowed_roles = spec.get("roles", [])
    if actor.role not in allowed_roles:
        reason = f"Role '{actor.role}' is not authorized to execute '{action}'. Allowed roles: {allowed_roles}"
        append_audit(
            actor_id=actor.id,
            action=f"DENIED:{action}",
            payload={"reason": reason, "args": args, "actor_kind": actor.kind, "actor_role": actor.role},
        )
        raise PermissionError(reason)

    # Rule 3: Specific rule for clear_safety_hold (Inspector + non-empty written note)
    if action == "clear_safety_hold":
        note = (args.get("note") or args.get("cleared_note") or "").strip()
        if not note:
            reason = "Clearing a safety hold strictly requires a non-empty written inspection resolution note."
            append_audit(
                actor_id=actor.id,
                action=f"DENIED:{action}",
                payload={"reason": reason, "args": args, "actor_kind": actor.kind, "actor_role": actor.role},
            )
            raise PermissionError(reason)

    # Execution passed authority gate -> invoke tool and write audit log
    handler = TOOLS.get(action)
    if handler:
        result = handler(args)
    else:
        result = {"action": action, "actor": actor.id, "args": args, "status": "executed"}

    # Audit logging for valid execution
    audit_hash = append_audit(
        actor_id=actor.id,
        action=action,
        payload={"args": args, "result": result, "actor_role": actor.role, "actor_kind": actor.kind},
    )

    # Record reversible checkpoint if action is reversible
    if spec.get("reversible", False):
        _reversible_journal[audit_hash] = {
            "action": action,
            "actor": actor,
            "args": args,
            "result": result,
            "audit_hash": audit_hash,
        }

    return {"status": "ok", "action": action, "result": result, "audit_hash": audit_hash}


def undo(actor: Actor, audit_id: str) -> dict:
    """
    Undo a previously executed reversible agent action.
    Restores the previous state and logs the undo event to the audit chain.
    Only reversible actions may be undone.
    """
    record = _reversible_journal.get(audit_id)
    if not record:
        reason = f"No reversible action found matching audit id '{audit_id}'"
        append_audit(
            actor_id=actor.id,
            action="DENIED:undo",
            payload={"reason": reason, "audit_id": audit_id},
        )
        raise ValueError(reason)

    action = record["action"]
    spec = AUTHORITY.get(action, {})
    if not spec.get("reversible", False):
        reason = f"Action '{action}' is marked non-reversible and cannot be undone."
        append_audit(
            actor_id=actor.id,
            action="DENIED:undo",
            payload={"reason": reason, "audit_id": audit_id, "action": action},
        )
        raise PermissionError(reason)

    # Permissions to undo: owner, inspector, admin, or original actor
    if actor.role not in ["owner", "inspector", "admin"] and actor.id != record["actor"].id:
        reason = f"Role '{actor.role}' is not authorized to undo actions performed by '{record['actor'].id}'."
        append_audit(
            actor_id=actor.id,
            action="DENIED:undo",
            payload={"reason": reason, "audit_id": audit_id},
        )
        raise PermissionError(reason)

    # Invoke registered undo handler if present
    undo_handler = UNDO_HANDLERS.get(action)
    if undo_handler:
        restored_state = undo_handler(record["args"])
    else:
        restored_state = {"status": "restored", "original_action": action, "original_args": record["args"]}

    del _reversible_journal[audit_id]

    undo_hash = append_audit(
        actor_id=actor.id,
        action=f"undo_{action}",
        payload={
            "undone_audit_id": audit_id,
            "original_action": action,
            "restored_state": restored_state,
        },
    )

    return {
        "status": "ok",
        "undone_action": action,
        "undone_audit_id": audit_id,
        "restored_state": restored_state,
        "audit_hash": undo_hash,
    }
    if action in _action_handlers:
        return _action_handlers[action](args)
    return {"action": action, "actor": actor.id, "args": args, "status": "executed"}

