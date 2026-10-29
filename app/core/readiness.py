# app/core/readiness.py
"""
ShareSafe Resource Readiness Engine.
Evaluates equipment readiness deterministically using explicit rule checks.
Computes whether an asset is Ready, Ready with Conditions, or Not Ready,
always generating plain-language RuleResult explanations.
"""
from __future__ import annotations
import json
from datetime import datetime, timezone, timedelta
from typing import Any, Dict, List, Optional, Union

from app.contracts import Readiness, ReadinessResult, ResourceCtx, RuleResult, Severity


def _parse_datetime(dt_val: Union[str, datetime, None]) -> Optional[datetime]:
    if dt_val is None:
        return None
    if isinstance(dt_val, datetime):
        if dt_val.tzinfo is None:
            return dt_val.replace(tzinfo=timezone.utc)
        return dt_val
    try:
        # Handles ISO strings (with or without 'Z')
        s = dt_val.replace("Z", "+00:00")
        dt = datetime.fromisoformat(s)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt
    except Exception:
        return None


def evaluate_readiness(
    resource: Union[Dict[str, Any], ResourceCtx],
    holds: List[Dict[str, Any]],
    inspections: Optional[List[Dict[str, Any]]] = None,
    maintenance_windows: Optional[List[Dict[str, Any]]] = None,
    now: Optional[datetime] = None,
) -> ReadinessResult:
    """
    Evaluate the readiness of a resource given active holds, maintenance windows,
    and inspection records.

    States:
    - NOT_READY: Blocking failure (open safety hold, maintenance window, overdue inspection).
    - CONDITIONS: Non-blocking safety conditions (training required, supervision, weekend rules).
    - READY: All safety and operational checks passed.
    """
    if now is None:
        now = datetime.now(timezone.utc)
    elif now.tzinfo is None:
        now = now.replace(tzinfo=timezone.utc)

    # Extract rules dictionary
    if isinstance(resource, ResourceCtx):
        rules = resource.rules or {}
        resource_id = resource.id
    else:
        rules_data = resource.get("rules") or resource.get("rules_json") or {}
        if isinstance(rules_data, str):
            try:
                rules = json.loads(rules_data)
            except Exception:
                rules = {}
        else:
            rules = rules_data or {}
        resource_id = resource.get("id", "unknown")

    reasons: List[RuleResult] = []
    blocking_failures: List[RuleResult] = []
    conditions: List[RuleResult] = []

    # -------------------------------------------------------------------------
    # 1. Safety Holds Check (Blocking)
    # -------------------------------------------------------------------------
    active_holds = [
        h for h in holds
        if h.get("status") in ("active", "open")
    ]
    if active_holds:
        for h in active_holds:
            reason_text = h.get("reason", "Safety hold active")
            placed_by = h.get("placed_by", "custodian")
            rr = RuleResult(
                rule_id="no_open_holds",
                passed=False,
                severity=Severity.BLOCK,
                message=f"Open safety hold: {reason_text} (placed by {placed_by})",
            )
            blocking_failures.append(rr)
            reasons.append(rr)
    else:
        reasons.append(
            RuleResult(
                rule_id="no_open_holds",
                passed=True,
                severity=Severity.INFO,
                message="No active safety holds.",
            )
        )

    # -------------------------------------------------------------------------
    # 2. Maintenance Window Check (Blocking)
    # -------------------------------------------------------------------------
    in_maintenance = False
    if maintenance_windows:
        for win in maintenance_windows:
            w_start = _parse_datetime(win.get("start"))
            w_end = _parse_datetime(win.get("end"))
            if w_start and w_end and (w_start <= now <= w_end):
                in_maintenance = True
                end_str = w_end.strftime("%Y-%m-%d %H:%M UTC")
                reason_desc = win.get("reason", "Scheduled maintenance")
                rr = RuleResult(
                    rule_id="maintenance_window",
                    passed=False,
                    severity=Severity.BLOCK,
                    message=f"Under scheduled maintenance until {end_str}: {reason_desc}",
                )
                blocking_failures.append(rr)
                reasons.append(rr)
                break

    if not in_maintenance:
        reasons.append(
            RuleResult(
                rule_id="maintenance_window",
                passed=True,
                severity=Severity.INFO,
                message="No active maintenance window.",
            )
        )

    # -------------------------------------------------------------------------
    # 3. Required Inspection Schedule Check (Blocking)
    # -------------------------------------------------------------------------
    max_inspection_days = rules.get("requires_inspection_within_days")
    if max_inspection_days is not None and max_inspection_days > 0:
        latest_inspection_date: Optional[datetime] = None
        if inspections:
            for insp in inspections:
                insp_dt = _parse_datetime(insp.get("created_at") or insp.get("date"))
                if insp_dt and (latest_inspection_date is None or insp_dt > latest_inspection_date):
                    latest_inspection_date = insp_dt

        if latest_inspection_date is None:
            rr = RuleResult(
                rule_id="inspection_current",
                passed=False,
                severity=Severity.BLOCK,
                message=f"Required safety inspection overdue: No inspection records found (max interval {max_inspection_days} days)",
            )
            blocking_failures.append(rr)
            reasons.append(rr)
        else:
            days_since = (now - latest_inspection_date).total_seconds() / 86400.0
            if days_since > max_inspection_days:
                rr = RuleResult(
                    rule_id="inspection_current",
                    passed=False,
                    severity=Severity.BLOCK,
                    message=f"Required safety inspection overdue: Last inspected {int(days_since)} days ago (limit {max_inspection_days} days)",
                )
                blocking_failures.append(rr)
                reasons.append(rr)
            else:
                reasons.append(
                    RuleResult(
                        rule_id="inspection_current",
                        passed=True,
                        severity=Severity.INFO,
                        message=f"Inspection is current ({int(days_since)} days ago, valid for {max_inspection_days} days)",
                    )
                )

    # -------------------------------------------------------------------------
    # 4. Non-blocking Operational Conditions
    # -------------------------------------------------------------------------
    # 4a. Weekend supervision requirement
    if rules.get("weekend_requires_supervisor"):
        # Python weekday: Monday=0, Saturday=5, Sunday=6
        if now.weekday() >= 5:
            cr = RuleResult(
                rule_id="weekend_supervisor",
                passed=True,
                severity=Severity.CONDITION,
                message="Weekend reservation requires designated lab supervisor present during operation.",
            )
            conditions.append(cr)
            reasons.append(cr)

    # 4b. Required safety training prerequisites
    req_training = rules.get("required_training") or []
    if req_training:
        cr = RuleResult(
            rule_id="training_prerequisite",
            passed=True,
            severity=Severity.CONDITION,
            message=f"Borrower must complete certified training prerequisites: {', '.join(req_training)}",
        )
        conditions.append(cr)
        reasons.append(cr)

    # 4c. Mandatory continuous supervision flag
    if rules.get("supervision_required") or rules.get("supervised_only"):
        cr = RuleResult(
            rule_id="supervision_required",
            passed=True,
            severity=Severity.CONDITION,
            message="Continuous staff supervision required throughout equipment operation.",
        )
        conditions.append(cr)
        reasons.append(cr)

    # 4d. Restricted use or consumable requirements
    if rules.get("restricted_materials"):
        cr = RuleResult(
            rule_id="restricted_materials",
            passed=True,
            severity=Severity.CONDITION,
            message="Only approved lab filament/materials allowed; third-party consumables strictly prohibited.",
        )
        conditions.append(cr)
        reasons.append(cr)

    # -------------------------------------------------------------------------
    # Determine Final State
    # -------------------------------------------------------------------------
    if blocking_failures:
        state = Readiness.NOT_READY
    elif conditions:
        state = Readiness.CONDITIONS
    else:
        state = Readiness.READY

    return ReadinessResult(state=state, reasons=reasons)
