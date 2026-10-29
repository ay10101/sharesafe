"""
ShareSafe Adversarial Testing Package.
Exports scripted adversarial scenarios A through H.
"""
from app.adversarial.scenarios import (
    ScenarioResult,
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

__all__ = [
    "ScenarioResult",
    "run_all_adversarial_scenarios",
    "run_scenario_a_prompt_injection",
    "run_scenario_b_role_escalation",
    "run_scenario_c_agent_overreach",
    "run_scenario_d_hold_bypass",
    "run_scenario_e_duplicate_processing",
    "run_scenario_f_proxy_booking",
    "run_scenario_g_audit_tampering",
    "run_scenario_h_llm_outage_and_malice",
]
