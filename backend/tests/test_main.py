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

def test_stripe_checkout_simulated():
    payload = {
        "plan": "pro",
        "email": "test@trihonor.com"
    }
    response = client.post("/api/stripe/checkout", json=payload)
    assert response.status_code == 200
    data = response.json()
    assert "url" in data
    assert data["simulated"] is True
    assert "payment-success" in data["url"]

def test_stripe_webhook_simulated_success():
    # Simulate a Stripe webhook call for checkout completion
    payload = {
        "type": "checkout.session.completed",
        "data": {
            "object": {
                "customer": "cus_test_123",
                "subscription": "sub_test_123",
                "customer_details": {
                    "email": "test@trihonor.com"
                },
                "metadata": {
                    "plan": "pro",
                    "email": "test@trihonor.com"
                }
            }
        }
    }
    response = client.post("/api/stripe/webhook", json=payload)
    assert response.status_code == 200
    assert response.json() == {"status": "success"}

    # Verify key was created and stored in active keys
    active_keys = db.get_active_api_keys()
    assert len(active_keys) == 1
    api_key = list(active_keys)[0]
    assert api_key.startswith("sre_live_")

    # Verify subscription details
    sub = db.get_subscription_by_customer("cus_test_123")
    assert sub is not None
    assert sub["status"] == "active"
    assert sub["plan"] == "pro"
    assert sub["api_key"] == api_key

def test_daemon_incident_auth_check():
    # 1. First register a subscription so active keys is not empty, which forces auth validation
    db.create_or_update_subscription("cus_auth_test", "sub_auth_test", "pro", "active", "sre_live_validkey")
    
    payload = {
        "service": "auth-test-service",
        "title": "Auth test",
        "logs": "some logs",
        "status": "resolved",
        "proposed_command": "echo",
        "action_output": ""
    }

    # 2. Try with no API Key header (should fail with 401)
    response = client.post("/api/daemon/incident", json=payload)
    assert response.status_code == 401

    # 3. Try with invalid API Key header (should fail with 401)
    response = client.post("/api/daemon/incident", json=payload, headers={"X-SRE-API-Key": "sre_live_invalid"})
    assert response.status_code == 401

    # 4. Try with valid API Key header (should succeed)
    response = client.post("/api/daemon/incident", json=payload, headers={"X-SRE-API-Key": "sre_live_validkey"})
    assert response.status_code == 200
    assert response.json()["status"] == "recorded"

def test_stripe_webhook_cryptographic_signature_success():
    import stripe
    import time
    import json
    
    # Enable signature checking by setting a secret
    original_secret = main.STRIPE_WEBHOOK_SECRET
    main.STRIPE_WEBHOOK_SECRET = "whsec_testsecret"
    
    try:
        event_payload = {
            "object": "event",
            "type": "checkout.session.completed",
            "data": {
                "object": {
                    "customer": "cus_crypto_123",
                    "subscription": "sub_crypto_123",
                    "customer_details": {"email": "crypto@trihonor.com"},
                    "metadata": {"plan": "pro", "email": "crypto@trihonor.com"}
                }
            }
        }
        
        payload_bytes = json.dumps(event_payload).encode("utf-8")
        timestamp = int(time.time())
        # Generate valid Stripe signature header using stripe's internal helper
        sig_payload = f"{timestamp}.".encode("utf-8") + payload_bytes
        signature = stripe.WebhookSignature._compute_signature(sig_payload.decode("utf-8"), "whsec_testsecret")
        headers = {
            "stripe-signature": f"t={timestamp},v1={signature}",
            "Content-Type": "application/json"
        }
        
        response = client.post("/api/stripe/webhook", data=payload_bytes, headers=headers)
        assert response.status_code == 200
        assert response.json() == {"status": "success"}
        
        # Verify db insertion
        sub = db.get_subscription_by_customer("cus_crypto_123")
        assert sub is not None
        assert sub["status"] == "active"
        
    finally:
        main.STRIPE_WEBHOOK_SECRET = original_secret

def test_stripe_webhook_cryptographic_signature_invalid():
    import stripe
    import time
    import json
    
    original_secret = main.STRIPE_WEBHOOK_SECRET
    main.STRIPE_WEBHOOK_SECRET = "whsec_testsecret"
    
    try:
        event_payload = {"object": "event", "type": "checkout.session.completed", "data": {}}
        payload_bytes = json.dumps(event_payload).encode("utf-8")
        
        # Send with invalid signature
        headers = {
            "stripe-signature": f"t={int(time.time())},v1=invalid_signature_hash_xyz",
            "Content-Type": "application/json"
        }
        
        response = client.post("/api/stripe/webhook", data=payload_bytes, headers=headers)
        assert response.status_code == 400
        assert "Invalid signature" in response.json()["detail"]
        
    finally:
        main.STRIPE_WEBHOOK_SECRET = original_secret


