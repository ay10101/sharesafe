"""
Scripted Adversarial Test Suite for ShareSafe.
Executes scenarios A through H ensuring deterministic safeguards against
prompt injection, role escalation, agent overreach, hold bypass, and tampering.
"""
from __future__ import annotations

from app.adversarial.scenarios import (
    run_all_adversarial_scenarios,
    run_scenario_a_prompt_injection,
    run_scenario_b_role_escalation,
    run_scenario_c_agent_overreach,
    run_scenario_d_hold_bypass,
    run_scenario_e_duplicate_processing,
    run_scenario_f_proxy_booking,
    run_scenario_g_audit_tampering,
    run_scenario_h_llm_outage_and_malice,
)


def test_scenario_a_prompt_injection():
    """Scenario A: Verify prompt injection in incident text is neutralized."""
    result = run_scenario_a_prompt_injection()
    assert result.passed is True
    assert result.details["hold_active"] is True


def test_scenario_b_role_escalation():
    """Scenario B: Verify borrower role cannot escalate or trigger custodian actions."""
    result = run_scenario_b_role_escalation()
    assert result.passed is True
    assert result.details["hold_denied"] is True
    assert result.details["direct_exec_denied"] is True


def test_scenario_c_agent_overreach():
    """Scenario C: Verify agent cannot execute human-only actions."""
    result = run_scenario_c_agent_overreach()
    assert result.passed is True
    # All 4 human-only actions must be denied
    for action, denied in result.details.items():
        assert denied is True, f"Action {action} was not properly denied"


def test_scenario_d_hold_bypass():
    """Scenario D: Verify safety hold blocks access and cannot be bypassed."""
    result = run_scenario_d_hold_bypass()
    assert result.passed is True
    assert result.details["hold_active"] is True
    assert result.details["operation_permitted"] is False


def test_scenario_e_duplicate_processing():
    """Scenario E: Verify concurrent/duplicate incident processing is idempotent."""
    result = run_scenario_e_duplicate_processing()
    assert result.passed is True
    assert result.details["call1_status"] == "placed"
    assert result.details["call2_status"] == "already_present"


def test_scenario_f_proxy_booking():
    """Scenario F: Verify free-text proxy claims cannot substitute for verified identity."""
    result = run_scenario_f_proxy_booking()
    assert result.passed is True
    assert result.details["authorized"] is False


def test_scenario_g_audit_tampering():
    """Scenario G: Verify audit log tampering is detected."""
    result = run_scenario_g_audit_tampering()
    assert result.passed is True
    assert result.details["tamper_detected"] is True


def test_scenario_h_llm_outage_and_malice():
    """Scenario H: Verify LLM outage or malicious text safely defaults to template."""
    result = run_scenario_h_llm_outage_and_malice()
    assert result.passed is True
    assert result.details["generated_by"] == "template"
    assert result.details["hold_active"] is True


def test_all_adversarial_suite_runner():
    """Verify master suite runner executes all 8 scenarios with 100% pass rate."""
    results = run_all_adversarial_scenarios()
    assert len(results) == 8
    for r in results:
        assert r.passed is True, f"Scenario {r.scenario_id} ({r.scenario_name}) failed: {r.summary}"
