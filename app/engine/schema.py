"""
app/engine/schema.py - ResourceRules schema shared between Engine (P3) and Policy Compiler (P4).

Stored as JSON in resources.rules. All keys optional.
"""
from __future__ import annotations
from typing import List, Optional
from pydantic import BaseModel, ConfigDict, Field

class ResourceRules(BaseModel):
    model_config = ConfigDict(extra="ignore")

    min_year: Optional[int] = Field(None, ge=1, le=8, description="Minimum academic year of borrower")
    required_training: Optional[List[str]] = Field(default_factory=list, description="List of mandatory certifications")
    max_hours_per_booking: Optional[float] = Field(None, gt=0, le=168, description="Max allowed duration per booking in hours")
    min_notice_hours: Optional[float] = Field(None, ge=0, description="Minimum hours of advance notice required")
    weekend_requires_supervisor: Optional[bool] = Field(None, description="Whether weekend bookings require supervisor sign-off")
    max_consecutive_bookings: Optional[int] = Field(None, ge=1, description="Max consecutive bookings permitted by a user/team")
    recent_usage_cap_hours_30d: Optional[float] = Field(None, ge=0, description="Max booking hours allowed in a rolling 30-day window")
    requires_inspection_within_days: Optional[int] = Field(None, ge=1, description="Days allowed between physical safety inspections")
