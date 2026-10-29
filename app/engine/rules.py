"""
app/engine/rules.py - Pure, deterministic evaluation of individual resource rules.

Features:
- Pure functions, no DB, no network, no LLM.
- Plain-language actionable messages returned for every rule failure.
- Severity levels: BLOCK (prohibits approval), CONDITION (approval allowed with note), INFO.
- Handles:
  * Readiness state (ResourceCtx.readiness)
  * min_year
  * required_training (names missing certifications)
  * max_hours_per_booking
  * min_notice_hours
  * weekend_requires_supervisor
  * max_consecutive_bookings
  * recent_usage_cap_hours_30d
  * requires_inspection_within_days
"""
from __future__ import annotations
from datetime import datetime, timedelta
from typing import List, Optional

from app.contracts import (
    BookingCtx,
    RequestCtx,
    ResourceCtx,
    RuleResult,
    Severity,
    UserCtx,
)

def check_readiness(resource: ResourceCtx) -> RuleResult:
    """Readiness must be 'ready' or 'ready_with_conditions'. If 'not_ready', it is a BLOCK."""
    readiness = getattr(resource, "readiness", "ready")
    if readiness == "not_ready":
        return RuleResult(
            rule_id="readiness",
            passed=False,
            severity=Severity.BLOCK,
            message="Resource is currently marked Not Ready (under maintenance or active safety hold).",
        )
    elif readiness == "ready_with_conditions":
        return RuleResult(
            rule_id="readiness",
            passed=True,
            severity=Severity.CONDITION,
            message="Resource is Ready with conditions. Special handling or supervision required.",
        )
    return RuleResult(
        rule_id="readiness",
        passed=True,
        severity=Severity.INFO,
        message="Resource verified in ready condition.",
    )

def check_min_year(user: UserCtx, min_year: Optional[int]) -> RuleResult:
    if min_year is None:
        return RuleResult("min_year", True, Severity.INFO, "No academic year requirement.")
    if user.year < min_year:
        return RuleResult(
            rule_id="min_year",
            passed=False,
            severity=Severity.BLOCK,
            message=f"Requires minimum academic year {min_year}. Current year: {user.year}.",
        )
    return RuleResult("min_year", True, Severity.INFO, f"Satisfies minimum academic year {min_year}.")

def check_required_training(user: UserCtx, required_training: Optional[List[str]]) -> RuleResult:
    if not required_training:
        return RuleResult("required_training", True, Severity.INFO, "No special training required.")
    
    user_trainings = set(user.training or [])
    missing = [t for t in required_training if t not in user_trainings]
    if missing:
        missing_str = ", ".join(missing)
        return RuleResult(
            rule_id="required_training",
            passed=False,
            severity=Severity.BLOCK,
            message=f"Missing required training certification: {missing_str}. Complete it before requesting.",
        )
    return RuleResult("required_training", True, Severity.INFO, "All required training certifications verified.")

def check_max_hours(request: RequestCtx, max_hours: Optional[float]) -> RuleResult:
    if max_hours is None:
        return RuleResult("max_hours_per_booking", True, Severity.INFO, "No single-booking duration limit.")
    
    duration_hours = (request.end - request.start).total_seconds() / 3600.0
    if duration_hours > max_hours:
        return RuleResult(
            rule_id="max_hours_per_booking",
            passed=False,
            severity=Severity.BLOCK,
            message=f"Requested duration ({duration_hours:.1f}h) exceeds maximum allowed limit of {max_hours:.1f}h per booking.",
        )
    return RuleResult("max_hours_per_booking", True, Severity.INFO, f"Duration ({duration_hours:.1f}h) within {max_hours:.1f}h limit.")

def check_min_notice(request: RequestCtx, min_notice_hours: Optional[float], now: datetime) -> RuleResult:
    if min_notice_hours is None:
        return RuleResult("min_notice_hours", True, Severity.INFO, "No advance notice requirement.")
    
    notice_hours = (request.start - now).total_seconds() / 3600.0
    if notice_hours < min_notice_hours:
        return RuleResult(
            rule_id="min_notice_hours",
            passed=False,
            severity=Severity.BLOCK,
            message=f"Request submitted with {max(0.0, notice_hours):.1f}h notice; requires at least {min_notice_hours:.1f}h advance notice.",
        )
    return RuleResult("min_notice_hours", True, Severity.INFO, f"Advance notice requirement met ({notice_hours:.1f}h notice provided).")

def check_weekend_supervisor(request: RequestCtx, weekend_requires_supervisor: Optional[bool]) -> RuleResult:
    if not weekend_requires_supervisor:
        return RuleResult("weekend_requires_supervisor", True, Severity.INFO, "Weekend sessions do not require a supervisor.")
    
    # 5 is Saturday, 6 is Sunday
    is_weekend = (request.start.weekday() in (5, 6) or request.end.weekday() in (5, 6))
    if is_weekend:
        return RuleResult(
            rule_id="weekend_requires_supervisor",
            passed=True, # Allowed to proceed as a CONDITION
            severity=Severity.CONDITION,
            message="Weekend booking requires designated faculty/lab supervisor presence.",
        )
    return RuleResult("weekend_requires_supervisor", True, Severity.INFO, "Weekday booking; no weekend supervisor required.")

def check_recent_usage_cap(
    user: UserCtx,
    history: List[BookingCtx],
    request: RequestCtx,
    cap_hours_30d: Optional[float],
    now: datetime,
) -> RuleResult:
    if cap_hours_30d is None:
        return RuleResult("recent_usage_cap_hours_30d", True, Severity.INFO, "No rolling 30-day usage cap.")
    
    cutoff = now - timedelta(days=30)
    user_past_hours = 0.0
    for b in history:
        if b.user_id == user.id and b.resource_id == request.resource_id:
            if b.status in ("approved", "completed", "checked_out") and b.start >= cutoff:
                user_past_hours += (b.end - b.start).total_seconds() / 3600.0

    req_hours = (request.end - request.start).total_seconds() / 3600.0
    if (user_past_hours + req_hours) > cap_hours_30d:
        return RuleResult(
            rule_id="recent_usage_cap_hours_30d",
            passed=False,
            severity=Severity.BLOCK,
            message=f"Usage cap exceeded: {user_past_hours:.1f}h used in last 30d + {req_hours:.1f}h requested exceeds {cap_hours_30d:.1f}h limit.",
        )
    return RuleResult("recent_usage_cap_hours_30d", True, Severity.INFO, f"30-day usage ({user_past_hours:.1f}h) within {cap_hours_30d:.1f}h cap.")

def check_consecutive_bookings(
    user: UserCtx,
    history: List[BookingCtx],
    request: RequestCtx,
    max_consecutive: Optional[int],
) -> RuleResult:
    if max_consecutive is None:
        return RuleResult("max_consecutive_bookings", True, Severity.INFO, "No consecutive booking limit.")
    
    # Sort bookings on same resource chronologically
    user_bookings = [
        b for b in history
        if (b.user_id == user.id or (user.team_id and b.team_id == user.team_id))
        and b.resource_id == request.resource_id
        and b.status in ("approved", "completed", "checked_out")
    ]
    user_bookings.sort(key=lambda b: b.start)

    # Count consecutive backward from request.start
    consecutive_count = 0
    curr_start = request.start
    for b in reversed(user_bookings):
        # Within 1 hour gap counts as consecutive
        if abs((curr_start - b.end).total_seconds()) <= 3600:
            consecutive_count += 1
            curr_start = b.start
        elif b.end < curr_start:
            break

    if consecutive_count >= max_consecutive:
        return RuleResult(
            rule_id="max_consecutive_bookings",
            passed=False,
            severity=Severity.BLOCK,
            message=f"Reached limit of {max_consecutive} consecutive bookings for user/team on this resource.",
        )
    return RuleResult("max_consecutive_bookings", True, Severity.INFO, f"Consecutive bookings ({consecutive_count}) within limit of {max_consecutive}.")

def check_inspection_requirement(
    resource: ResourceCtx,
    requires_days: Optional[int],
    last_inspection_date: Optional[datetime],
    now: datetime,
) -> RuleResult:
    if requires_days is None:
        return RuleResult("requires_inspection_within_days", True, Severity.INFO, "No routine inspection schedule enforced.")
    
    if last_inspection_date is None:
        # If inspection date unknown or missing, pass as condition
        return RuleResult(
            rule_id="requires_inspection_within_days",
            passed=True,
            severity=Severity.CONDITION,
            message="No verified inspection date on record; safety check recommended before use.",
        )

    days_since = (now - last_inspection_date).total_seconds() / 86400.0
    if days_since > requires_days:
        return RuleResult(
            rule_id="requires_inspection_within_days",
            passed=False,
            severity=Severity.BLOCK,
            message=f"Safety inspection overdue ({int(days_since)} days since last check; max allowed is {requires_days} days).",
        )
    return RuleResult("requires_inspection_within_days", True, Severity.INFO, f"Safety inspection up to date ({int(days_since)}/{requires_days} days).")
