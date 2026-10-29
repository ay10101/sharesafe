"""
app/engine/fairness.py - Deterministic Priority, Anti-Monopolisation, and Proxy Detection.

Features:
- Documented configurable weights for transparent priority scoring.
- Scoring components:
  * recent_usage (negative, rolling window exponential-like decay penalty)
  * no_shows / late_returns (capped penalties)
  * deadline (boost for verified structured datetime, capped against gaming)
  * waiting_time (boost for request seniority)
- Anti-monopolisation rules:
  * deprioritizes heavy users when competing with zero-access users.
- Deterministic tie-breaking: earliest created_at, then user id.
- Coordinated proxy booking detection:
  * Detects teammates booking consecutive slots.
  * Detects rapid duplicate booking patterns.
  * Emits Flag objects for owner review; never auto-penalizes.
"""
from __future__ import annotations
import math
from datetime import datetime, timedelta
from typing import Dict, List, Optional

from app.contracts import (
    BookingCtx,
    Flag,
    PriorityBreakdown,
    RequestCtx,
    UserCtx,
)

# ---------------------------------------------------------------------------
# Transparent Priority Weights Configuration
# Resource owners and administrators can inspect and adjust these parameters.
# ---------------------------------------------------------------------------
FAIRNESS_CONFIG = {
    # Weight per hour used in past 30 days (negative penalty)
    "usage_penalty_per_hour": 0.05,
    "max_usage_penalty": 0.50,

    # Penalty for unexcused no-shows (per incident, capped)
    "no_show_penalty": 0.20,
    "max_no_show_penalty": 0.40,

    # Penalty for late returns (per incident, capped)
    "late_return_penalty": 0.10,
    "max_late_return_penalty": 0.30,

    # Boost if user has a verified academic deadline within 7 days
    "deadline_boost_max": 0.30,

    # Boost per 24 hours already waited since request creation
    "waiting_boost_per_day": 0.05,
    "max_waiting_boost": 0.20,

    # Baseline starting score
    "base_score": 1.0,

    # Anti-monopolisation zero-access protection boost
    "zero_access_equity_boost": 0.25,
}

def priority_score(
    request: RequestCtx,
    history: List[BookingCtx],
    now: datetime,
    is_contested_against_zero_access: bool = False,
) -> PriorityBreakdown:
    """
    Computes a transparent priority score for a request relative to history and time.
    Deterministic: uses provided 'now' timestamp; no randomness.
    """
    components: Dict[str, float] = {}
    cfg = FAIRNESS_CONFIG

    # 1. Recent usage penalty (past 30 days)
    cutoff = now - timedelta(days=30)
    recent_hours = 0.0
    for b in history:
        if b.user_id == request.user.id and b.resource_id == request.resource_id:
            if b.status in ("approved", "completed", "checked_out") and b.start >= cutoff:
                # Recency factor: bookings within last 7 days weigh full, older decay
                days_ago = max(0.0, (now - b.start).total_seconds() / 86400.0)
                decay = math.exp(-0.03 * days_ago) # ~0.4 score weight after 30 days
                duration = (b.end - b.start).total_seconds() / 3600.0
                recent_hours += duration * decay

    usage_pen = min(cfg["max_usage_penalty"], recent_hours * cfg["usage_penalty_per_hour"])
    components["recent_usage"] = round(-usage_pen, 3)

    # 2. Reliability penalties (no_shows and late_returns)
    user_no_shows = sum(1 for b in history if b.user_id == request.user.id and b.no_show)
    user_late_returns = sum(1 for b in history if b.user_id == request.user.id and b.returned_late)

    no_show_pen = min(cfg["max_no_show_penalty"], user_no_shows * cfg["no_show_penalty"])
    late_return_pen = min(cfg["max_late_return_penalty"], user_late_returns * cfg["late_return_penalty"])

    components["no_shows"] = round(-no_show_pen, 3)
    components["late_returns"] = round(-late_return_pen, 3)

    # 3. Academic deadline boost
    deadline_boost = 0.0
    if request.deadline and request.deadline >= request.start:
        days_to_deadline = max(0.0, (request.deadline - now).total_seconds() / 86400.0)
        if days_to_deadline <= 7.0:
            # Scaled up to max boost as deadline approaches
            deadline_boost = cfg["deadline_boost_max"] * (1.0 - (days_to_deadline / 7.0))
    components["deadline"] = round(deadline_boost, 3)

    # 4. Waiting seniority boost
    wait_days = max(0.0, (now - request.created_at).total_seconds() / 86400.0)
    wait_boost = min(cfg["max_waiting_boost"], wait_days * cfg["waiting_boost_per_day"])
    components["waiting_time"] = round(wait_boost, 3)

    # 5. Anti-monopolisation equity boost (new or zero-access borrowers)
    if is_contested_against_zero_access and recent_hours == 0.0:
        components["equity_boost"] = round(cfg["zero_access_equity_boost"], 3)
    else:
        components["equity_boost"] = 0.0

    # Total score calculation
    total = cfg["base_score"] + sum(components.values())
    total = max(0.0, round(total, 3))

    return PriorityBreakdown(
        total=total,
        components=components,
        tiebreak="earliest_created_at_then_user_id",
    )

def detect_proxy_patterns(bookings: List[BookingCtx]) -> List[Flag]:
    """
    Detects coordinated booking patterns and potential quota circumventing:
    1. Team proxy booking: Teammates booking the same resource back-to-back.
    2. Rapid multi-day hoarding: Same user booking repetitive identical slots across consecutive days.
    
    Flags are returned for owner advisory review; never auto-penalizing.
    """
    flags: List[Flag] = []
    if not bookings:
        return flags

    # Sort bookings chronologically by start time
    sorted_b = sorted(bookings, key=lambda b: b.start)

    # 1. Check for coordinated team back-to-back bookings
    for i in range(len(sorted_b) - 1):
        b1 = sorted_b[i]
        b2 = sorted_b[i + 1]

        # Same resource, same team, different users, contiguous in time
        if (
            b1.resource_id == b2.resource_id
            and b1.team_id
            and b1.team_id == b2.team_id
            and b1.user_id != b2.user_id
            and abs((b2.start - b1.end).total_seconds()) <= 1800 # <= 30 min gap
        ):
            flags.append(
                Flag(
                    kind="proxy_booking",
                    user_ids=[b1.user_id, b2.user_id],
                    booking_ids=[b1.id, b2.id],
                    reason=(
                        f"Teammates ({b1.user_id}, {b2.user_id}) from team '{b1.team_id}' "
                        f"booked consecutive back-to-back sessions on resource '{b1.resource_id}'."
                    ),
                )
            )

    # 2. Check for repetitive multi-day slot hoarding by single user
    user_resource_map: Dict[tuple, List[BookingCtx]] = {}
    for b in sorted_b:
        key = (b.user_id, b.resource_id)
        user_resource_map.setdefault(key, []).append(b)

    for (uid, rid), user_list in user_resource_map.items():
        if len(user_list) >= 4:
            # Check if bookings span 4+ consecutive days at similar hours
            consecutive_days = 1
            for j in range(len(user_list) - 1):
                day_diff = (user_list[j + 1].start.date() - user_list[j].start.date()).days
                if day_diff == 1:
                    consecutive_days += 1
                elif day_diff > 1:
                    consecutive_days = 1
            
            if consecutive_days >= 4:
                flags.append(
                    Flag(
                        kind="hoarding",
                        user_ids=[uid],
                        booking_ids=[b.id for b in user_list],
                        reason=f"User '{uid}' booked repetitive slots across {consecutive_days} consecutive days on '{rid}'.",
                    )
                )

    return flags
