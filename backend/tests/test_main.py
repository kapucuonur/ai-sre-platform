import os
import shutil
import pytest
from fastapi.testclient import TestClient

# Set test database directory before importing database/main modules
TEST_DB_DIR = "./backend/test_data"
os.environ["SRE_DB_DIR"] = TEST_DB_DIR

# Clean any existing test data to start fresh
if os.path.exists(TEST_DB_DIR):
    shutil.rmtree(TEST_DB_DIR)

import db
import main

client = TestClient(main.app)

@pytest.fixture(autouse=True)
def setup_and_teardown():
    # Setup: Initialize database and ensure parent directory exists
    os.makedirs(TEST_DB_DIR, exist_ok=True)
    db.init_db()
    yield
    # Teardown: Clean up test database directory after each test
    if os.path.exists(TEST_DB_DIR):
        try:
            shutil.rmtree(TEST_DB_DIR)
        except OSError:
            pass

def test_health():
    response = client.get("/health")
    assert response.status_code == 200
    assert response.json() == {"status": "healthy"}

def test_get_settings_default():
    response = client.get("/api/settings")
    assert response.status_code == 200
    data = response.json()
    assert "gemini_api_key" in data
    assert "slack_bot_token" in data
    assert "slack_channel_id" in data

def test_save_and_get_settings():
    # Save settings
    payload = {
        "gemini_api_key": "AIzaSyTestKey123456",
        "slack_bot_token": "xoxb-test-token-value",
        "slack_channel_id": "C012345678",
        "autonomous_mode": True
    }
    response = client.post("/api/settings", json=payload)
    assert response.status_code == 200
    assert response.json() == {"status": "saved"}
    
    # Get settings (keys should be masked)
    response = client.get("/api/settings")
    assert response.status_code == 200
    data = response.json()
    assert data["gemini_api_key"] == "AIza...3456"
    assert data["slack_bot_token"] == "xoxb...alue"
    assert data["slack_channel_id"] == "C012345678"
    assert data["autonomous_mode"] is True

def test_create_and_list_incidents():
    # Create incident directly in DB helper
    inc_id = db.create_incident(
        title="Test Alert",
        service="test-service",
        alert_payload={"alert": "yes"},
        logs="error occurred",
        ai_analysis="due to test",
        proposed_command="echo hello"
    )
    assert inc_id > 0
    
    # Retrieve incidents via API
    response = client.get("/api/incidents")
    assert response.status_code == 200
    incidents = response.json()
    assert len(incidents) >= 1
    assert incidents[0]["title"] == "Test Alert"
    assert incidents[0]["service"] == "test-service"

def test_history_endpoint():
    # Save an incident
    db.create_incident(
        title="History Test Alert",
        service="history-service",
        alert_payload={"alert": "yes"},
        logs="history error",
        ai_analysis="due to history",
        proposed_command="echo history"
    )
    # Get history
    response = client.get("/api/history")
    assert response.status_code == 200
    history = response.json()
    assert len(history) >= 1
    assert history[0]["title"] == "History Test Alert"
    assert history[0]["service"] == "history-service"

def test_reflexive_past_incidents():
    # Insert a past incident
    db.create_incident(
        title="Reflexive Alert",
        service="reflexive-service",
        alert_payload={},
        logs="error logs",
        ai_analysis="analysis",
        proposed_command="docker restart reflexive"
    )
    # Set status
    db.update_incident_status(1, "resolved", "Exit Code: 0")
    
    past = db.get_past_incidents("reflexive-service", "Reflexive Alert")
    assert len(past) == 1
    assert past[0]["proposed_command"] == "docker restart reflexive"
    assert past[0]["status"] == "resolved"

def test_receive_daemon_incident():
    payload = {
        "service": "daemon-service",
        "title": "Daemon Failure Alert",
        "logs": "stack trace line 10",
        "status": "resolved",
        "proposed_command": "docker restart daemon",
        "action_output": "Exit Code: 0"
    }
    response = client.post("/api/daemon/incident", json=payload)
    assert response.status_code == 200
    assert response.json()["status"] == "recorded"
    
    # Verify in DB
    inc = db.get_incident(response.json()["incident_id"])
    assert inc is not None
    assert inc["service"] == "daemon-service"
    assert inc["status"] == "resolved"
