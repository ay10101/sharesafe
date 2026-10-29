"""
app/engine/decision.py - Deterministic Request Evaluation and Decision Assembly.

Function:
  evaluate_request(request, resource, user, history, competing, now, last_inspection_date) -> Decision

Execution Flow:
1. Pure function: deterministic output for identical inputs.
2. User justification free text is NEVER accessed or read (prompt injection safe).
3. Evaluates all configured rules from resource.rules and readiness.
4. If any BLOCK rule fails -> RECOMMEND_REJECT with detailed plain-language RuleResults.
5. If slot is contested by competing requests:
   - Evaluates rules for all contenders.
   - Computes transparent PriorityBreakdown for eligible contenders.
   - Ranks contenders deterministically (priority total desc, created_at asc, user_id asc).
   - If current request ranks first -> RECOMMEND_APPROVE.
   - If current request does not rank first -> WAITLIST with waitlist_position and PriorityBreakdown.
6. If slot is uncontested and rules pass -> RECOMMEND_APPROVE (noting any CONDITION severities).
7. If inputs are contradictory or invalid -> NEEDS_HUMAN.
8. Produces deterministic plain-language explanation summary (100% offline, zero LLM).
"""
from __future__ import annotations
from datetime import datetime
from typing import List, Optional

from app.contracts import (
    BookingCtx,
    Decision,
    PriorityBreakdown,
    RequestCtx,
    ResourceCtx,
    RuleResult,
    Severity,
    UserCtx,
    Verdict,
)
from app.engine.rules import (
    check_consecutive_bookings,
    check_inspection_requirement,
    check_max_hours,
    check_min_notice,
    check_min_year,
    check_readiness,
    check_recent_usage_cap,
    check_required_training,
    check_weekend_supervisor,
)
from app.engine.fairness import priority_score

def _build_plain_language_explanation(
    verdict: Verdict,
    rule_results: List[RuleResult],
    priority: Optional[PriorityBreakdown],
    waitlist_position: Optional[int],
) -> str:
    """Builds a clear, deterministic explanation for the borrower and owner."""
    blocks = [r.message for r in rule_results if r.severity == Severity.BLOCK and not r.passed]
    conditions = [r.message for r in rule_results if r.severity == Severity.CONDITION]

    if verdict == Verdict.RECOMMEND_REJECT:
        reasons_text = " ".join(blocks)
        return f"Request cannot be approved due to policy constraints: {reasons_text}"

    if verdict == Verdict.WAITLIST:
        score_detail = f"Priority score: {priority.total:.2f}" if priority else ""
        return (
            f"Eligible for resource, but the requested slot is currently contested. "
            f"Placed at waitlist position #{waitlist_position}. {score_detail}."
        )

    if verdict == Verdict.RECOMMEND_APPROVE:
        if conditions:
            cond_text = " ".join(conditions)
            return f"Recommended for approval with conditions: {cond_text}"
        return "All eligibility checks passed. Recommended for approval."

    return "Request requires manual human administrator review due to conflicting schedule or unusual parameters."

def evaluate_request(
    request: RequestCtx,
    resource: ResourceCtx,
    user: Optional[UserCtx] = None,
    history: Optional[List[BookingCtx]] = None,
    competing: Optional[List[RequestCtx]] = None,
    now: Optional[datetime] = None,
    last_inspection_date: Optional[datetime] = None,
) -> Decision:
    """
    Evaluates a borrow request deterministically.
    'now' is passed as an explicit parameter to ensure 100% reproducibility in tests and simulations.
    """
    if now is None:
        # Fallback to request created_at if now is omitted
        now = request.created_at

    history = history or []
    user = user or request.user
    competing = competing or []
    rules = resource.rules or {}

    # Basic sanity validation
    if request.start >= request.end:
        return Decision(
            verdict=Verdict.NEEDS_HUMAN,
            rule_results=[RuleResult("time_window", False, Severity.BLOCK, "Start time must be before end time.")],
            explanation="Invalid booking window: start time must precede end time.",
        )

    # 1. Run all individual rules (Readiness + Schema rules)
    results: List[RuleResult] = []

    # Readiness
    results.append(check_readiness(resource))
    # Min Year
    results.append(check_min_year(user, rules.get("min_year")))
    # Required Training
    results.append(check_required_training(user, rules.get("required_training")))
    # Max Hours
    results.append(check_max_hours(request, rules.get("max_hours_per_booking")))
    # Min Notice
    results.append(check_min_notice(request, rules.get("min_notice_hours"), now))
    # Weekend Supervisor
    results.append(check_weekend_supervisor(request, rules.get("weekend_requires_supervisor")))
    # Recent Usage Cap
    results.append(check_recent_usage_cap(user, history, request, rules.get("recent_usage_cap_hours_30d"), now))
    # Consecutive Bookings
    results.append(check_consecutive_bookings(user, history, request, rules.get("max_consecutive_bookings")))
    # Inspection Days
    results.append(check_inspection_requirement(resource, rules.get("requires_inspection_within_days"), last_inspection_date, now))

    # Check for blocking violations
    blocking_failures = [r for r in results if r.severity == Severity.BLOCK and not r.passed]
    if blocking_failures:
        explanation = _build_plain_language_explanation(Verdict.RECOMMEND_REJECT, results, None, None)
        return Decision(
            verdict=Verdict.RECOMMEND_REJECT,
            rule_results=results,
            explanation=explanation,
        )

    # 2. Check for slot competition
    # Overlapping active bookings in history
    slot_occupied = any(
        b.resource_id == request.resource_id
        and b.status in ("approved", "checked_out")
        and not (request.end <= b.start or request.start >= b.end)
        for b in history
    )

    # Overlapping competing requests
    overlapping_competing = [
        c for c in competing
        if c.resource_id == request.resource_id
        and c.user.id != request.user.id
        and not (request.end <= c.start or request.start >= c.end)
    ]

    # Calculate this request's priority score
    user_has_zero_recent = not any(
        b.user_id == user.id and b.resource_id == request.resource_id and b.status in ("approved", "completed", "checked_out")
        for b in history
    )
    my_priority = priority_score(request, history, now, is_contested_against_zero_access=user_has_zero_recent)

    if slot_occupied or overlapping_competing:
        # Contested slot -> Rank all contenders
        all_candidates: List[tuple[RequestCtx, PriorityBreakdown]] = [(request, my_priority)]

        for comp_req in overlapping_competing:
            comp_zero_access = not any(
                b.user_id == comp_req.user.id and b.resource_id == comp_req.resource_id and b.status in ("approved", "completed", "checked_out")
                for b in history
            )
            comp_prio = priority_score(comp_req, history, now, is_contested_against_zero_access=comp_zero_access)
            all_candidates.append((comp_req, comp_prio))

        # Sort order:
        # 1. Priority score (descending)
        # 2. Earliest created_at (ascending)
        # 3. User ID alphabetical (ascending, tie-break)
        all_candidates.sort(
            key=lambda item: (-item[1].total, item[0].created_at, item[0].user.id)
        )

        ranked_requests = [item[0] for item in all_candidates]
        my_index = ranked_requests.index(request)

        # If slot was already locked by an approved/checked-out booking, all pending requests waitlist
        if slot_occupied:
            waitlist_pos = my_index + 1
            verdict = Verdict.WAITLIST
        else:
            if my_index == 0:
                verdict = Verdict.RECOMMEND_APPROVE
                waitlist_pos = None
            else:
                verdict = Verdict.WAITLIST
                waitlist_pos = my_index + 1

        explanation = _build_plain_language_explanation(verdict, results, my_priority, waitlist_pos)
        return Decision(
            verdict=verdict,
            rule_results=results,
            priority=my_priority,
            waitlist_position=waitlist_pos,
            explanation=explanation,
        )

    # 3. Uncontested slot & all checks passed -> RECOMMEND_APPROVE
    explanation = _build_plain_language_explanation(Verdict.RECOMMEND_APPROVE, results, my_priority, None)
    return Decision(
        verdict=Verdict.RECOMMEND_APPROVE,
        rule_results=results,
        priority=my_priority,
        explanation=explanation,
    )
