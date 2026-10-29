"""
Tests for app/adversarial/suite.py
Verifies:
- All 7 scripted adversarial checks run and pass.
- Format matches requirements [{name, passed, detail}].
"""
import pytest
from app.adversarial.suite import run_adversarial_suite, AdversarialTestResult

def test_full_adversarial_suite():
    results = run_adversarial_suite()
    assert len(results) == 7

    names = [r["name"] for r in results]
    assert "prompt_injection_resistance" in names
    assert "role_escalation_guard" in names
    assert "concurrency_race_condition" in names
    assert "proxy_booking_detection" in names
    assert "agent_overreach_boundary" in names
    assert "hold_bypass_prevention" in names
    assert "audit_tamper_detection" in names

    for r in results:
        assert r["passed"] is True, f"Adversarial test {r['name']} failed: {r['detail']}"
        assert len(r["detail"]) > 10

