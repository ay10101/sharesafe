# tests/test_trust_readiness.py
"""
Unit tests for the ShareSafe Resource Readiness Engine.
Verifies all 5 essential requirements:
1. Open safety hold -> NOT_READY with plain-language reason
2. Active maintenance window -> NOT_READY with plain-language reason
3. Overdue required inspection -> NOT_READY with plain-language reason
4. Restricted operational rules -> CONDITIONS with plain-language reasons
5. No blocking holds or conditions -> READY
"""
from datetime import datetime, timezone, timedelta
import pytest
from app.contracts import Readiness, Severity
from app.core.readiness import evaluate_readiness


def test_1_open_safety_hold_blocks():
    """1. An active or open safety hold flips state to NOT_READY with clear explanation."""
    resource = {"id": "res-gpu-01", "name": "GPU Workstation", "rules": {}}
    holds = [
        {
            "id": "hold-01",
            "resource_id": "res-gpu-01",
            "placed_by": "agent:custodian",
            "reason": "Fan fault reported during return inspection",
            "status": "active",
        }
    ]

    result = evaluate_readiness(resource=resource, holds=holds)
    assert result.state == Readiness.NOT_READY

    blocking = [r for r in result.reasons if r.severity == Severity.BLOCK]
    assert len(blocking) == 1
    assert "Open safety hold: Fan fault reported during return inspection" in blocking[0].message
    assert blocking[0].passed is False


def test_2_maintenance_window_blocks():
    """2. Resource inside scheduled maintenance window flips to NOT_READY."""
    now = datetime(2026, 10, 29, 14, 0, tzinfo=timezone.utc)
    resource = {"id": "res-printer-01", "name": "Bambu 3D Printer", "rules": {}}
    maintenance = [
        {
            "start": (now - timedelta(hours=1)).isoformat(),
            "end": (now + timedelta(hours=3)).isoformat(),
            "reason": "Hotend recalibration and firmware update",
        }
    ]

    result = evaluate_readiness(resource=resource, holds=[], maintenance_windows=maintenance, now=now)
    assert result.state == Readiness.NOT_READY

    blocking = [r for r in result.reasons if r.severity == Severity.BLOCK]
    assert len(blocking) == 1
    assert "Under scheduled maintenance" in blocking[0].message
    assert "Hotend recalibration" in blocking[0].message


def test_3_overdue_inspection_blocks():
    """3. When inspection interval is exceeded, resource becomes NOT_READY."""
    now = datetime(2026, 10, 29, 12, 0, tzinfo=timezone.utc)
    resource = {
        "id": "res-camera-01",
        "name": "Sony FX3 Rig",
        "rules": {"requires_inspection_within_days": 30},
    }
    # Last inspected 45 days ago
    inspections = [
        {"created_at": (now - timedelta(days=45)).isoformat(), "result": "pass"}
    ]

    result = evaluate_readiness(resource=resource, holds=[], inspections=inspections, now=now)
    assert result.state == Readiness.NOT_READY

    blocking = [r for r in result.reasons if r.severity == Severity.BLOCK]
    assert len(blocking) == 1
    assert "Required safety inspection overdue" in blocking[0].message
    assert "45 days ago" in blocking[0].message


def test_4_restricted_use_sets_conditions():
    """4. Non-blocking restrictions (training, supervision) yield READY_WITH_CONDITIONS."""
    # Weekend test (e.g. Saturday)
    saturday_noon = datetime(2026, 10, 31, 12, 0, tzinfo=timezone.utc)
    resource = {
        "id": "res-gpu-01",
        "name": "GPU Workstation",
        "rules": {
            "required_training": ["gpu_safety", "ethics_compliance"],
            "weekend_requires_supervisor": True,
        },
    }

    result = evaluate_readiness(resource=resource, holds=[], now=saturday_noon)
    assert result.state == Readiness.CONDITIONS

    condition_reasons = [r for r in result.reasons if r.severity == Severity.CONDITION]
    assert len(condition_reasons) == 2
    rule_ids = {r.rule_id for r in condition_reasons}
    assert "weekend_supervisor" in rule_ids
    assert "training_prerequisite" in rule_ids


def test_5_nominal_resource_is_ready():
    """5. When all checks pass and no constraints apply, state is READY."""
    wednesday_noon = datetime(2026, 10, 28, 12, 0, tzinfo=timezone.utc)
    resource = {
        "id": "res-meeting-01",
        "name": "Maker Space Meeting Pod",
        "rules": {"requires_inspection_within_days": 90},
    }
    inspections = [
        {"created_at": (wednesday_noon - timedelta(days=10)).isoformat(), "result": "pass"}
    ]

    result = evaluate_readiness(resource=resource, holds=[], inspections=inspections, now=wednesday_noon)
    assert result.state == Readiness.READY
    assert all(r.passed for r in result.reasons if r.severity == Severity.BLOCK)
