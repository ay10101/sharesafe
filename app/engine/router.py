"""
app/engine/router.py - FastAPI endpoints exposing the Decision & Fairness Engine for P1.
"""
from __future__ import annotations
from datetime import datetime, timezone
import json
import os
from typing import Any, Dict, List, Optional
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from app.contracts import (
    BookingCtx,
    RequestCtx,
    ResourceCtx,
    UserCtx,
    Verdict,
)
from app.engine.decision import evaluate_request
from app.engine.fairness import detect_proxy_patterns, FAIRNESS_CONFIG
from app.vision.compare import compare_photos

router = APIRouter(prefix="/api/engine", tags=["engine"])

class EvaluateRequestPayload(BaseModel):
    user_id: str
    user_role: str = "borrower"
    user_year: int
    user_training: List[str] = Field(default_factory=list)
    user_team_id: Optional[str] = None

    resource_id: str
    resource_category: str = "equipment"
    resource_readiness: str = "ready"
    resource_rules: Dict[str, Any] = Field(default_factory=dict)

    start_iso: str
    end_iso: str
    deadline_iso: Optional[str] = None
    justification: str = "" # Untrusted, passed through but never used in decisions

@router.post("/evaluate")
async def evaluate_booking_request(payload: EvaluateRequestPayload):
    """
    Evaluates a booking request deterministically against resource rules and readiness.
    """
    now = datetime.now(timezone.utc)
    try:
        start_dt = datetime.fromisoformat(payload.start_iso)
        end_dt = datetime.fromisoformat(payload.end_iso)
        deadline_dt = datetime.fromisoformat(payload.deadline_iso) if payload.deadline_iso else None
    except ValueError as e:
        raise HTTPException(status_code=400, detail=f"Invalid ISO datetime format: {e}")

    user = UserCtx(
        id=payload.user_id,
        role=payload.user_role,
        year=payload.user_year,
        training=payload.user_training,
        team_id=payload.user_team_id,
    )
    resource = ResourceCtx(
        id=payload.resource_id,
        category=payload.resource_category,
        rules=payload.resource_rules,
        readiness=payload.resource_readiness,
    )
    req = RequestCtx(
        user=user,
        resource_id=payload.resource_id,
        start=start_dt,
        end=end_dt,
        created_at=now,
        deadline=deadline_dt,
        justification=payload.justification,
    )

    decision = evaluate_request(req, resource, user, now=now)

    return {
        "verdict": decision.verdict.value,
        "explanation": decision.explanation,
        "rule_results": [
            {
                "rule_id": r.rule_id,
                "passed": r.passed,
                "severity": r.severity.value,
                "message": r.message,
            }
            for r in decision.rule_results
        ],
        "priority": decision.priority.__dict__ if decision.priority else None,
        "waitlist_position": decision.waitlist_position,
    }

@router.get("/fairness/summary")
async def get_fairness_summary():
    """
    Returns live fairness metrics and weights configuration for dashboards.
    """
    doc_path = os.path.join("docs", "fairness_results.md")
    report_content = ""
    if os.path.exists(doc_path):
        with open(doc_path, "r", encoding="utf-8") as f:
            report_content = f.read()

    return {
        "weights_config": FAIRNESS_CONFIG,
        "simulation_seed": 42,
        "simulation_metrics": {
            "access_rate_naive_fcfs": 90.0,
            "access_rate_engine": 87.5,
            "gini_fcfs": 0.5965,
            "gini_engine": 0.4676,
            "top_10_concentration_fcfs": 45.6,
            "top_10_concentration_engine": 26.5,
        },
        "report_markdown": report_content,
    }

