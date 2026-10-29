"""
Tests for app/agent/router.py FastAPI endpoints.
"""
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from app.agent.router import router

app = FastAPI()
app.include_router(router)
client = TestClient(app)

def test_api_adversarial_endpoint():
    resp = client.post("/api/admin/adversarial")
    assert resp.status_code == 200
    data = resp.json()
    assert len(data) == 7
    for item in data:
        assert item["passed"] is True

def test_api_trigger_incident_and_get_packet():
    # 1. Trigger incident
    resp_trigger = client.post(
        "/api/agent/trigger-incident",
        json={
            "incident_id": "inc-fan-router-test",
            "resource_id": "res-gpu-01",
            "description": "Loud fan grinding noise reported",
            "reported_by": "alice",
        },
    )
    assert resp_trigger.status_code == 200
    assert "place_precautionary_hold" in resp_trigger.json()["actions"]

    # 2. Fetch packet
    resp_packet = client.get("/api/incidents/inc-fan-router-test/packet")
    assert resp_packet.status_code == 200
    packet = resp_packet.json()
    assert packet["incident_id"] == "inc-fan-router-test"
    assert "cooling fan" in packet["suggested_next_step"].lower()

def test_api_compile_policy():
    resp = client.post(
        "/api/policies/compile",
        json={"text": "3rd-year and above with gpu safety training, max 4 hours"},
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["rules"]["min_year"] == 3
    assert "gpu_safety" in data["rules"]["required_training"]

