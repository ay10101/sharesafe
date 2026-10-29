"""
Unit tests for app.agent.enrichment (Optional LLM Evidence Packet Enrichment).
Covers valid enrichment, deterministic fallback, prompt injection safeguards, and action claim rejections.
"""
from __future__ import annotations

import json
from unittest.mock import patch

from app.agent.contracts import Incident
from app.agent.enrichment import PacketEnrichment, enrich_packet
from app.agent.packet import build_packet
from app.ai.llm import LLMResult


def _make_sample_packet():
    incident = Incident(
        id="inc-test-01",
        resource_id="res-lens-50mm",
        reporter_id="user-bob",
        incident_type="damage",
        description="Focus ring feels gritty and sticks around 3m mark.",
        severity="medium",
    )
    return build_packet(incident)


@patch("app.agent.enrichment.complete")
def test_successful_valid_enrichment(mock_complete):
    """When LLM returns valid structured enrichment, packet is updated with generated_by='llm'."""
    packet = _make_sample_packet()
    mock_enrichment = PacketEnrichment(
        summary="Enriched analysis: Focus ring friction reported; possible sand or mechanical obstruction.",
        key_observations=["Focus mechanism impaired around 3m focus mark."],
        questions_for_inspector=["Inspect helicoid grease for particulate contamination."],
    )
    mock_complete.return_value = LLMResult(
        content=mock_enrichment.model_dump_json(),
        structured_data=mock_enrichment,
        model="gpt-4o-mini",
    )

    enriched = enrich_packet(packet)

    assert enriched.generated_by == "llm"
    assert "Enriched analysis" in enriched.summary
    assert len(enriched.key_observations) == 1
    assert len(enriched.questions_for_inspector) == 1
    # Verify underlying incident IDs were preserved
    assert enriched.incident_id == packet.incident_id
    assert enriched.resource_id == packet.resource_id


@patch("app.agent.enrichment.complete")
def test_missing_credentials_or_provider_error_falls_back_to_template(mock_complete):
    """When complete() returns None (missing key, timeout, 500 error), falls back to template."""
    packet = _make_sample_packet()
    mock_complete.return_value = None

    enriched = enrich_packet(packet)

    assert enriched.generated_by == "template"
    assert enriched.summary == packet.summary
    assert enriched.key_observations == packet.key_observations


@patch("app.agent.enrichment.complete")
def test_prohibited_action_claims_rejected(mock_complete):
    """If LLM generated text claims to clear holds or assign penalties, reject enrichment."""
    packet = _make_sample_packet()
    # Malicious or hallucinated response claiming hold was cleared
    malicious_enrichment = PacketEnrichment(
        summary="Evaluation complete: Borrower innocent, no hold needed and hold cleared.",
        key_observations=["Safety hold cleared by AI assistant."],
        questions_for_inspector=["None, issue resolved."],
    )
    mock_complete.return_value = LLMResult(
        content=malicious_enrichment.model_dump_json(),
        structured_data=malicious_enrichment,
        model="gpt-4o-mini",
    )

    enriched = enrich_packet(packet)

    # Must reject the enrichment and revert safely to template
    assert enriched.generated_by == "template"
    assert enriched.summary == packet.summary
    assert "hold cleared" not in enriched.summary.lower()


@patch("app.agent.enrichment.complete")
def test_enrichment_exception_falls_back_gracefully(mock_complete):
    """Any unexpected exception during enrichment fails safely to template."""
    packet = _make_sample_packet()
    mock_complete.side_effect = RuntimeError("Provider connection abort")

    enriched = enrich_packet(packet)

    assert enriched.generated_by == "template"
    assert enriched.summary == packet.summary
