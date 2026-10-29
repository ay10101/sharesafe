"""
tests/fixtures/engine_fixtures.py - Reusable fixtures for P3 tests and P4 adversarial suite.
"""
from datetime import datetime, timezone, timedelta
from app.contracts import (
    BookingCtx,
    RequestCtx,
    ResourceCtx,
    UserCtx,
)

BASE_TIME = datetime(2026, 10, 29, 10, 0, tzinfo=timezone.utc)

def make_user(
    id: str = "u-qualified",
    role: str = "borrower",
    year: int = 3,
    training: list[str] = None,
    team_id: str = None,
) -> UserCtx:
    return UserCtx(
        id=id,
        role=role,
        year=year,
        training=training if training is not None else ["gpu_safety"],
        team_id=team_id,
    )

def make_resource(
    id: str = "res-gpu-01",
    category: str = "lab-equipment",
    readiness: str = "ready",
    rules: dict = None,
) -> ResourceCtx:
    default_rules = {
        "min_year": 3,
        "required_training": ["gpu_safety"],
        "max_hours_per_booking": 4.0,
        "min_notice_hours": 2.0,
        "weekend_requires_supervisor": True,
        "max_consecutive_bookings": 2,
        "recent_usage_cap_hours_30d": 20.0,
        "requires_inspection_within_days": 30,
    }
    return ResourceCtx(
        id=id,
        category=category,
        readiness=readiness,
        rules=rules if rules is not None else default_rules,
    )

def make_request(
    user: UserCtx = None,
    resource_id: str = "res-gpu-01",
    hours_from_base: float = 4.0,
    duration_hours: float = 2.0,
    created_offset_hours: float = 0.0,
    deadline_offset_days: float = None,
    justification: str = "Standard academic research project",
) -> RequestCtx:
    u = user or make_user()
    req_start = BASE_TIME + timedelta(hours=hours_from_base)
    req_end = req_start + timedelta(hours=duration_hours)
    req_created = BASE_TIME + timedelta(hours=created_offset_hours)
    deadline = req_start + timedelta(days=deadline_offset_days) if deadline_offset_days else None

    return RequestCtx(
        user=u,
        resource_id=resource_id,
        start=req_start,
        end=req_end,
        created_at=req_created,
        deadline=deadline,
        justification=justification,
    )

def make_booking(
    id: str = "b1",
    resource_id: str = "res-gpu-01",
    user_id: str = "u-qualified",
    team_id: str = None,
    start: datetime = None,
    end: datetime = None,
    status: str = "completed",
    returned_late: bool = False,
    no_show: bool = False,
) -> BookingCtx:
    b_start = start or (BASE_TIME - timedelta(days=2))
    b_end = end or (b_start + timedelta(hours=2))
    return BookingCtx(
        id=id,
        resource_id=resource_id,
        user_id=user_id,
        team_id=team_id,
        start=b_start,
        end=b_end,
        status=status,
        created_at=b_start - timedelta(days=1),
        returned_late=returned_late,
        no_show=no_show,
    )
