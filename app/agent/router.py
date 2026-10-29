"""
app/agent/router.py - Agent, Evidence Packet, Policy Compiler, and Adversarial endpoints.
Can be mounted directly into FastAPI main app by P1.
"""
from __future__ import annotations
from typing import Any, Dict, List, Optional
from fastapi import APIRouter, HTTPException, status
from pydantic import BaseModel

from app.agent.custodian import CustodianAgent
from app.agent.packet import get_packet_by_incident_id, get_packet_store
from app.agent.policy import compile_policy
from app.adversarial.suite import run_adversarial_suite

router = APIRouter(tags=["agent"])

class PolicyCompileRequest(BaseModel):
    text: str

class IncidentTriggerRequest(BaseModel):
    incident_id: str
    resource_id: str
    description: str
    reported_by: Optional[str] = "borrower"

@router.get("/api/incidents/{incident_id}/packet")
async def get_incident_packet(incident_id: str):
    """
    Retrieve the evidence packet prepared by the Custodian Agent for human review.
    """
    packet = get_packet_by_incident_id(incident_id)
    if not packet:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Evidence packet for incident '{incident_id}' not found.",
        )
    return packet

@router.post("/api/admin/adversarial")
async def execute_adversarial_suite():
    """
    Run scripted adversarial security and boundary tests.
    Labelled as 'scripted adversarial tests'.
    """
    results = run_adversarial_suite()
    return results

@router.post("/api/policies/compile")
async def compile_policy_endpoint(payload: PolicyCompileRequest):
    """
    Compile natural language policy text into structured ResourceRules.
    Requires human owner confirmation before applying to resource.
    """
    compiled = compile_policy(payload.text)
    return compiled

@router.post("/api/agent/trigger-incident")
async def trigger_custodian_incident(payload: IncidentTriggerRequest):
    """
    Trigger Custodian Agent triage for an equipment incident.
    Places precautionary hold, drafts packet, and alerts owner.
    """
    agent = CustodianAgent()
    results = agent.on_incident(
        incident_id=payload.incident_id,
        incident_data={
            "id": payload.incident_id,
            "resource_id": payload.resource_id,
            "description": payload.description,
            "reported_by": payload.reported_by,
        },
    )
    return {"status": "processed", "actions": [r.action for r in results]}
