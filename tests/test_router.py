"""
tests/test_router.py - Tests for app/engine/router.py endpoints.
"""
from fastapi import FastAPI
from fastapi.testclient import TestClient
from app.engine.router import router

app = FastAPI()
app.include_router(router)
client = TestClient(app)

def test_api_evaluate_qualified_user():
    payload = {
        "user_id": "u-grad-01",
        "user_role": "borrower",
        "user_year": 4,
        "user_training": ["gpu_safety"],
        "resource_id": "res-gpu-01",
        "resource_rules": {"min_year": 3, "required_training": ["gpu_safety"]},
        "resource_readiness": "ready",
        "start_iso": "2026-11-01T10:00:00+00:00",
        "end_iso": "2026-11-01T12:00:00+00:00",
        "justification": "Evaluating thesis models",
    }
    resp = client.post("/api/engine/evaluate", json=payload)
    assert resp.status_code == 200
    data = resp.json()
    assert data["verdict"] == "recommend_approve"
    assert "Recommended for approval" in data["explanation"]

def test_api_evaluate_missing_training():
    payload = {
        "user_id": "u-undergrad-01",
        "user_role": "borrower",
        "user_year": 3,
        "user_training": [],
        "resource_id": "res-gpu-01",
        "resource_rules": {"required_training": ["gpu_safety"]},
        "resource_readiness": "ready",
        "start_iso": "2026-11-01T10:00:00+00:00",
        "end_iso": "2026-11-01T12:00:00+00:00",
    }
    resp = client.post("/api/engine/evaluate", json=payload)
    assert resp.status_code == 200
    data = resp.json()
    assert data["verdict"] == "recommend_reject"
    assert "gpu_safety" in data["explanation"]

def test_api_fairness_summary():
    resp = client.get("/api/engine/fairness/summary")
    assert resp.status_code == 200
    data = resp.json()
    assert "weights_config" in data
    assert "simulation_metrics" in data
    assert data["simulation_metrics"]["gini_engine"] == 0.4676

