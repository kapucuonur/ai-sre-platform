from fastapi import FastAPI, Request, HTTPException, BackgroundTasks, Header
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from typing import Optional
import json
import os
import requests
import subprocess
import db
import ai_service
import action_service
import logging
import stripe
import secrets

# Setup Logging
logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(name)s: %(message)s")
logger = logging.getLogger("main")

app = FastAPI(title="AI SRE Platform API")

# Enable CORS
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

class SettingsModel(BaseModel):
    gemini_api_key: Optional[str] = None
    slack_bot_token: Optional[str] = None
    slack_channel_id: Optional[str] = None
    autonomous_mode: Optional[bool] = None

class ManualActionModel(BaseModel):
    action: str  # approve or reject

class DaemonIncidentModel(BaseModel):
    service: str
    title: str
    logs: str
    status: str
    proposed_command: str
    action_output: str
    duration: Optional[float] = None

class DaemonContainerModel(BaseModel):
    name: str
    status: str

class DaemonStatusModel(BaseModel):
    tenant_id: str
    cpu_temp: str
    memory: str
    disk: str
    containers: list[DaemonContainerModel]

# --- Core Webhooks ---

@app.post("/api/webhook/grafana")
async def grafana_webhook(request: Request, background_tasks: BackgroundTasks, api_key: Optional[str] = None):
    """
    Receives alerts from Grafana and processes them asynchronously.
    """
    try:
        payload = await request.json()
        logger.info("Grafana Webhook received: %s", json.dumps(payload))
        
        # Extract metadata from Grafana alert payload
        alert_title = payload.get("title", "Sistem Alarmı")
        service = "unknown"
        logs_text = "No detailed logs provided in alert payload."
        
        # Try to parse service and logs from common Grafana formats
        if "evalMatches" in payload and payload["evalMatches"]:
            match = payload["evalMatches"][0]
            service = match.get("metric", service)
            logs_text = f"Metric value out of bounds: {match.get('value')} on {match.get('metric')}"
        elif "alerts" in payload and payload["alerts"]:
            alert = payload["alerts"][0]
            service = alert.get("labels", {}).get("alertname", service)
            service = alert.get("labels", {}).get("container", service)
            logs_text = alert.get("annotations", {}).get("description", logs_text)

        # Trigger AI reasoning and Slack notification in background
        api_key_to_use = api_key or "self-hosted"
        background_tasks.add_task(process_incident, service, alert_title, logs_text, payload, api_key_to_use)
        
        return {"status": "processing"}
    except Exception as e:
        logger.error("Failed to parse Grafana webhook: %s", e)
        raise HTTPException(status_code=400, detail=str(e))

def process_incident(service: str, title: str, logs: str, alert_payload: dict, api_key: str = "self-hosted"):
    """Background task to run AI SRE analysis and trigger ChatOps."""
    logger.info("Processing incident for service: %s", service)
    
    # Run AI Analysis
    analysis = ai_service.analyze_incident(service, title, logs)
    
    # Check if autonomous mode is enabled
    autonomous = db.get_setting("autonomous_mode", "false") == "true"
    proposed_cmd = analysis["proposed_command"]
    
    if autonomous and proposed_cmd:
        # Autonomous execution flow
        incident_id = db.create_incident(
            title=title,
            service=service,
            alert_payload=alert_payload,
            logs=logs,
            ai_analysis=analysis["reasoning"],
            proposed_command=proposed_cmd,
            api_key=api_key
        )
        db.update_incident_status(incident_id, "approved")
        
        # Execute the action
        import time
        start_time = time.time()
        output = action_service.execute_command(proposed_cmd)
        duration = time.time() - start_time
        is_success = "Exit Code: 0" in output
        final_status = "resolved" if is_success else "failed"
        
        db.update_incident_status(incident_id, final_status, output, duration=duration)
        
        # Send notification to slack with status
        ai_service.send_to_slack(
            incident_id=incident_id,
            service=service,
            title=title,
            summary=analysis["summary"],
            reasoning=analysis["reasoning"],
            proposed_command=proposed_cmd,
            autonomous_status=final_status,
            action_output=output
        )
        ai_service.send_to_teams(
            incident_id=incident_id,
            service=service,
            title=title,
            summary=analysis["summary"],
            reasoning=analysis["reasoning"],
            proposed_command=proposed_cmd,
            autonomous_status=final_status,
            action_output=output
        )
    else:
        # Standard flow (needs approval for high/critical or just waiting)
        incident_id = db.create_incident(
            title=title,
            service=service,
            alert_payload=alert_payload,
            logs=logs,
            ai_analysis=analysis["reasoning"],
            proposed_command=proposed_cmd,
            api_key=api_key
        )
        
        logger.info("Created incident ID: %s", incident_id)
        
        # If a remediation command is suggested, send interactive Slack card
        if proposed_cmd:
            ai_service.send_to_slack(
                incident_id=incident_id,
                service=service,
                title=title,
                summary=analysis["summary"],
                reasoning=analysis["reasoning"],
                proposed_command=proposed_cmd
            )
            ai_service.send_to_teams(
                incident_id=incident_id,
                service=service,
                title=title,
                summary=analysis["summary"],
                reasoning=analysis["reasoning"],
                proposed_command=proposed_cmd
            )

@app.post("/api/daemon/incident")
async def receive_daemon_incident(payload: DaemonIncidentModel, x_sre_api_key: Optional[str] = Header(None, alias="X-SRE-API-Key")):
    """
    Receives healing incidents reported by the local Pi 5 SRE Daemon.
    """
    active_keys = db.get_active_api_keys()
    api_key_to_use = x_sre_api_key or "self-hosted"
    if active_keys:
        if not x_sre_api_key or x_sre_api_key not in active_keys:
            logger.warning("Unauthorized daemon incident report attempt.")
            raise HTTPException(status_code=401, detail="Unauthorized SRE API Key.")

    incident_id = db.create_incident(
        title=payload.title,
        service=payload.service,
        alert_payload={"source": "daemon"},
        logs=payload.logs,
        ai_analysis="Processed by local Pi 5 SRE Daemon.",
        proposed_command=payload.proposed_command,
        duration=payload.duration,
        api_key=api_key_to_use
    )
    # Map status to approved then resolved/failed
    db.update_incident_status(incident_id, "approved")
    db.update_incident_status(incident_id, payload.status, payload.action_output, duration=payload.duration)
    
    # Notify Slack
    ai_service.send_to_slack(
        incident_id=incident_id,
        service=payload.service,
        title=payload.title,
        summary=payload.title,
        reasoning="Processed autonomously by local Pi 5 SRE Daemon.",
        proposed_command=payload.proposed_command,
        autonomous_status=payload.status,
        action_output=payload.action_output
    )
    ai_service.send_to_teams(
        incident_id=incident_id,
        service=payload.service,
        title=payload.title,
        summary=payload.title,
        reasoning="Processed autonomously by local Pi 5 SRE Daemon.",
        proposed_command=payload.proposed_command,
        autonomous_status=payload.status,
        action_output=payload.action_output
    )
    return {"status": "recorded", "incident_id": incident_id}
@app.post("/api/daemon/status")
async def receive_daemon_status(payload: DaemonStatusModel, x_sre_api_key: Optional[str] = Header(None, alias="X-SRE-API-Key")):
    """
    Receives periodic hardware and container status reports from client SRE Daemons.
    """
    active_keys = db.get_active_api_keys()
    api_key_to_use = x_sre_api_key or "self-hosted"
    
    if active_keys:
        if not x_sre_api_key or x_sre_api_key not in active_keys:
            logger.warning("Unauthorized daemon status report attempt.")
            raise HTTPException(status_code=401, detail="Unauthorized SRE API Key.")
            
    containers_json = json.dumps([c.dict() for c in payload.containers])
    db.save_tenant_status(
        api_key=api_key_to_use,
        cpu_temp=payload.cpu_temp,
        memory=payload.memory,
        disk=payload.disk,
        containers_json=containers_json
    )
    return {"status": "saved"}

@app.get("/api/metrics/history")
async def get_metrics_history(x_sre_api_key: Optional[str] = Header(None, alias="X-SRE-API-Key")):
    """
    Returns time-series history metrics for the current tenant.
    """
    api_key_to_use = x_sre_api_key or "self-hosted"
    history = db.get_tenant_metrics_history(api_key_to_use, entity="host")
    return {"history": history}


@app.post("/api/webhook/slack/actions")
async def slack_actions(request: Request, background_tasks: BackgroundTasks):
    """
    Receives interactive button action callbacks from Slack.
    """
    try:
        form_data = await request.form()
        payload_str = form_data.get("payload")
        if not payload_str:
            raise HTTPException(status_code=400, detail="Missing payload")
            
        payload = json.loads(payload_str)
        actions = payload.get("actions", [])
        if not actions:
            raise HTTPException(status_code=400, detail="No action specified")
            
        action = actions[0]
        value = action.get("value", "")
        response_url = payload.get("response_url")
        user_name = payload.get("user", {}).get("name", "Slack User")
        
        if value.startswith("approve_") or value.startswith("reject_"):
            parts = value.split("_")
            action_type = parts[0]
            incident_id = int(parts[1])
            
            # Update Slack message immediately to show processing
            original_blocks = payload.get("message", {}).get("blocks", [])
            
            if action_type == "approve":
                # Handle approval asynchronously
                background_tasks.add_task(
                    run_approved_incident_action, incident_id, response_url, original_blocks, user_name
                )
                
                # Immediate acknowledgment block edit
                updated_blocks = original_blocks[:-1] # Remove actions buttons
                updated_blocks.append({
                    "type": "section",
                    "text": {
                        "type": "mrkdwn",
                        "text": f"⏳ *Uygulanıyor...* (Onaylayan: @{user_name})"
                    }
                })
            else:
                db.update_incident_status(incident_id, "rejected")
                updated_blocks = original_blocks[:-1]
                updated_blocks.append({
                    "type": "section",
                    "text": {
                        "type": "mrkdwn",
                        "text": f"❌ *Reddedildi.* (Reddeden: @{user_name})"
                    }
                })
                
            # Send updated blocks to Slack
            if response_url:
                requests.post(response_url, json={"blocks": updated_blocks}, timeout=5)

        return {"status": "ok"}
    except Exception as e:
        logger.error("Slack action failed: %s", e)
        raise HTTPException(status_code=500, detail=str(e))

def run_approved_incident_action(incident_id: int, response_url: str, original_blocks: list, user_name: str):
    """Executes the command and updates both database and Slack in background."""
    incident = db.get_incident(incident_id)
    if not incident or incident["status"] != "pending":
        return
        
    db.update_incident_status(incident_id, "approved")
    
    # Run the remediation command
    import time
    start_time = time.time()
    command = incident["proposed_command"]
    output = action_service.execute_command(command)
    duration = time.time() - start_time
    
    # Check execution success (we flag exit code in output)
    is_success = "Exit Code: 0" in output
    final_status = "resolved" if is_success else "failed"
    
    db.update_incident_status(incident_id, final_status, output, duration=duration)
    
    # Update Slack Card with outcome
    updated_blocks = original_blocks[:-1] # Remove temporary "Running" section
    badge = "✅" if is_success else "⚠️"
    msg = f"{badge} *Tamamlandı!* (Onaylayan: @{user_name})\n\n```\n{output[:400]}\n```"
    updated_blocks.append({
        "type": "section",
        "text": {
            "type": "mrkdwn",
            "text": msg
        }
    })
    
    if response_url:
        requests.post(response_url, json={"blocks": updated_blocks}, timeout=5)

# --- Web UI REST Endpoints ---

@app.get("/api/status")
async def get_system_status(x_sre_api_key: Optional[str] = Header(None, alias="X-SRE-API-Key")):
    """Fetches hardware status metrics and active containers, filtered for privacy."""
    api_key_to_use = x_sre_api_key or "self-hosted"
    
    # Whitelist of containers to show in the public dashboard (excludes private containers)
    ALLOWED_CONTAINERS = {
        "ai-sre-platform",
        "bikefit-api",
        "bikefit-frontend",
        "coachonurai-api",
        "trihonor-api-prod",
        "trihonor-db-prod",
        "sre-daemon"
    }

    # Try to fetch last reported status for this tenant from database
    tenant_data = db.get_tenant_status(api_key_to_use)
    if tenant_data:
        from datetime import datetime, timezone
        updated_at = tenant_data.get("updated_at")
        is_online = False
        if updated_at:
            try:
                diff = datetime.now(timezone.utc) - datetime.fromisoformat(updated_at)
                is_online = diff.total_seconds() < 300 # 5 minutes
            except Exception:
                pass
                
        try:
            raw_containers = json.loads(tenant_data.get("containers", "[]"))
            filtered_containers = [
                c for c in raw_containers if c.get("name") in ALLOWED_CONTAINERS
            ]
            return {
                "status": "online" if is_online else "offline",
                "cpu_temp": tenant_data.get("cpu_temp", "N/A"),
                "memory": tenant_data.get("memory", "N/A"),
                "disk": tenant_data.get("disk", "N/A"),
                "containers": filtered_containers
            }
        except Exception:
            pass

    stats = {
        "status": "offline",
        "cpu_temp": "N/A",
        "memory": "N/A",
        "disk": "N/A",
        "containers": []
    }
    return stats

@app.get("/install.sh")
def serve_install_script():
    """
    Serves the autonomous SRE Daemon installation script.
    """
    from fastapi.responses import Response
    script = """#!/bin/bash
set -e

# SRE Daemon Installer
# TriHonor Oy · Tampere, Finland

echo "🚀 Starting SRE Daemon installation..."

if [ -z "$SRE_API_KEY" ]; then
    echo "❌ Error: SRE_API_KEY environment variable is required."
    exit 1
fi

PLATFORM_URL=${SRE_PLATFORM_URL:-"https://sre-api.trihonor.com"}
INSTALL_DIR="/home/pi/sre"

# Fallback to /opt if not on pi
if [ ! -d "/home/pi" ]; then
    INSTALL_DIR="/opt/sre-daemon"
fi

echo "📂 Installation directory: $INSTALL_DIR"
sudo mkdir -p "$INSTALL_DIR"
sudo chown -R $USER:$USER "$INSTALL_DIR"

echo "📦 Installing system dependencies..."
if command -v apt-get &> /dev/null; then
    sudo apt-get update -y -q
    sudo apt-get install -y python3 python3-requests python3-yaml curl git sshpass
elif command -v yum &> /dev/null; then
    sudo yum install -y python3 python3-requests python3-pip curl git
    pip3 install pyyaml requests
else
    echo "⚠️ Unknown OS package manager. Please ensure Python3, requests, and pyyaml are installed."
fi

echo "📥 Downloading latest SRE Daemon release..."
curl -sSL "https://raw.githubusercontent.com/kapucuonur/sre-daemon/main/sre_daemon.py" -o "$INSTALL_DIR/sre_daemon.py"
chmod +x "$INSTALL_DIR/sre_daemon.py"

echo "⚙️ Configuring environment..."
cat << EOF > "$INSTALL_DIR/.env"
# SRE Daemon Configuration
SRE_API_KEY=$SRE_API_KEY
SRE_PLATFORM_URL=$PLATFORM_URL
PI_OLLAMA_URL=http://localhost:11434
HEAL_LOG=$INSTALL_DIR/heal_log.jsonl
EOF

echo "🖥️ Registering systemd service..."
cat << EOF | sudo tee /etc/systemd/system/sre-daemon.service > /dev/null
[Unit]
Description=TriHonor SRE Daemon - Autonomous Self-Healing Platform Client
After=network.target docker.service

[Service]
Type=simple
User=$USER
WorkingDirectory=$INSTALL_DIR
ExecStart=/usr/bin/python3 $INSTALL_DIR/sre_daemon.py
Restart=always
RestartSec=5
EnvironmentFile=$INSTALL_DIR/.env

[Install]
WantedBy=multi-user.target
EOF

echo "🔄 Reloading systemd and starting service..."
sudo systemctl daemon-reload
sudo systemctl enable sre-daemon.service
sudo systemctl restart sre-daemon.service

echo "✅ SRE Daemon successfully installed and running!"
sudo systemctl status sre-daemon.service --no-pager | grep -E "Active:"
"""
    return Response(content=script, media_type="text/plain")

@app.get("/api/incidents")
async def list_incidents(x_sre_api_key: Optional[str] = Header(None, alias="X-SRE-API-Key")):
    api_key_to_use = x_sre_api_key or "self-hosted"
    return db.get_all_incidents(api_key_to_use)

@app.get("/api/history")
async def get_history(x_sre_api_key: Optional[str] = Header(None, alias="X-SRE-API-Key")):
    api_key_to_use = x_sre_api_key or "self-hosted"
    return db.get_all_incidents(api_key_to_use)

@app.get("/api/analytics/fatigue")
async def get_fatigue_analytics(x_sre_api_key: Optional[str] = Header(None, alias="X-SRE-API-Key")):
    api_key_to_use = x_sre_api_key or "self-hosted"
    return db.get_alert_fatigue(api_key_to_use)

@app.post("/api/incidents/{incident_id}/action")
async def trigger_manual_action(incident_id: int, payload: ManualActionModel, background_tasks: BackgroundTasks):
    """
    Enables approving/rejecting proposed incident fixes directly from the Web UI dashboard.
    """
    incident = db.get_incident(incident_id)
    if not incident:
        raise HTTPException(status_code=404, detail="Incident not found")
        
    if incident["status"] != "pending":
        raise HTTPException(status_code=400, detail="Incident is not in pending state")
        
    if payload.action == "approve":
        background_tasks.add_task(run_approved_incident_action, incident_id, None, [], "Web Dashboard Admin")
        return {"status": "approving"}
    elif payload.action == "reject":
        db.update_incident_status(incident_id, "rejected")
        return {"status": "rejected"}
        
    raise HTTPException(status_code=400, detail="Invalid action")

@app.get("/api/settings")
async def get_settings():
    settings = db.get_all_settings()
    # Mask API keys for safety
    def mask_key(k: str) -> str:
        if not k:
            return ""
        if len(k) < 8:
            return "***"
        return k[:4] + "..." + k[-4:]

    return {
        "gemini_api_key": mask_key(settings.get("GEMINI_API_KEY", "")),
        "slack_bot_token": mask_key(settings.get("SLACK_BOT_TOKEN", "")),
        "slack_channel_id": settings.get("SLACK_CHANNEL_ID", ""),
        "autonomous_mode": settings.get("autonomous_mode", "false") == "true"
    }

@app.post("/api/settings")
async def save_settings(payload: SettingsModel):
    settings = db.get_all_settings()
    
    if payload.gemini_api_key and not payload.gemini_api_key.startswith("..."):
        db.set_setting("GEMINI_API_KEY", payload.gemini_api_key)
        
    if payload.slack_bot_token and not payload.slack_bot_token.startswith("..."):
        db.set_setting("SLACK_BOT_TOKEN", payload.slack_bot_token)
        
    if payload.slack_channel_id is not None:
        db.set_setting("SLACK_CHANNEL_ID", payload.slack_channel_id)

    if payload.autonomous_mode is not None:
        db.set_setting("autonomous_mode", "true" if payload.autonomous_mode else "false")
        
    return {"status": "saved"}

# --- Strategy Registry Analytics ---
@app.get("/api/registry/analytics")
async def get_registry_analytics():
    import subprocess
    import json
    cmd = [
        "sshpass", "-p", "pi", 
        "ssh", "-o", "StrictHostKeyChecking=no", "-o", "PubkeyAuthentication=no", "-o", "PreferredAuthentications=password",
        "pi@192.168.1.116",
        "python3 -c 'import sqlite3, json; conn = sqlite3.connect(\"/home/pi/sre/sre_state.db\"); conn.row_factory = sqlite3.Row; print(json.dumps([dict(r) for r in conn.execute(\"SELECT error_hash, command, success_count, fail_count, weight, is_blacklisted, last_used FROM strategy_registry\").fetchall()]))'"
    ]
    try:
        res = subprocess.run(cmd, capture_output=True, text=True, timeout=10)
        if res.returncode != 0:
            logger.warning("SSH to Pi failed: %s", res.stderr)
            return {"total_strategies": 0, "blacklist_rate": 0.0, "total_savings_tokens": 0, "entries": []}
        
        entries = json.loads(res.stdout.strip())
        total_strategies = len(entries)
        blacklist_count = sum(1 for e in entries if e.get("is_blacklisted") == 1)
        blacklist_rate = round((blacklist_count / total_strategies) * 100, 1) if total_strategies > 0 else 0.0
        
        # Each successful cache hit saves an LLM query (average input 1000 + output 200 tokens = 1200 tokens)
        total_success_hits = sum(e.get("success_count", 0) for e in entries)
        total_savings_tokens = total_success_hits * 1200
        
        return {
            "total_strategies": total_strategies,
            "blacklist_rate": blacklist_rate,
            "total_savings_tokens": total_savings_tokens,
            "entries": entries
        }
    except Exception as e:
        logger.error("Failed to query registry analytics: %s", e)
        return {"total_strategies": 0, "blacklist_rate": 0.0, "total_savings_tokens": 0, "entries": []}

class CheckoutModel(BaseModel):
    plan: str
    email: str

STRIPE_SECRET_KEY = os.getenv("STRIPE_SECRET_KEY", "sk_test_mock")
STRIPE_WEBHOOK_SECRET = os.getenv("STRIPE_WEBHOOK_SECRET", "whsec_mock")
stripe.api_key = STRIPE_SECRET_KEY

@app.post("/api/stripe/checkout")
async def stripe_checkout(payload: CheckoutModel):
    if STRIPE_SECRET_KEY == "sk_test_mock":
        mock_session_id = f"cs_test_{secrets.token_hex(16)}"
        success_url = f"https://sre.trihonor.com/payment-success?session_id={mock_session_id}&plan={payload.plan}&email={payload.email}"
        return {"url": success_url, "simulated": True}
        
    try:
        price_id = os.getenv(f"STRIPE_PRICE_{payload.plan.upper()}")
        checkout_session = stripe.checkout.Session.create(
            payment_method_types=["card"],
            customer_email=payload.email,
            line_items=[
                {
                    "price": price_id,
                    "quantity": 1,
                } if price_id else {
                    "price_data": {
                        "currency": "eur",
                        "product_data": {
                            "name": f"SRE Daemon {payload.plan.capitalize()} Plan",
                            "description": "Autonomous infrastructure healing service subscription.",
                        },
                        "unit_amount": 4900 if payload.plan == "pro" else 14900,
                        "recurring": {"interval": "month"},
                    },
                    "quantity": 1,
                }
            ],
            mode="subscription",
            success_url="https://sre.trihonor.com/payment-success?session_id={CHECKOUT_SESSION_ID}",
            cancel_url="https://sre.trihonor.com/#pricing",
            metadata={
                "plan": payload.plan,
                "email": payload.email
            }
        )
        return {"url": checkout_session.url, "simulated": False}
    except Exception as e:
        logger.error("Failed to create Stripe checkout session: %s", e)
        raise HTTPException(status_code=500, detail=str(e))

@app.post("/api/stripe/webhook")
async def stripe_webhook(request: Request):
    payload = await request.body()
    sig_header = request.headers.get("stripe-signature")
    
    event = None
    if STRIPE_WEBHOOK_SECRET == "whsec_mock" or not sig_header:
        try:
            event = json.loads(payload)
        except Exception:
            raise HTTPException(status_code=400, detail="Invalid mock payload.")
    else:
        try:
            event = stripe.Webhook.construct_event(
                payload, sig_header, STRIPE_WEBHOOK_SECRET
            )
            if hasattr(event, "to_dict"):
                event = event.to_dict()
            else:
                event = dict(event)
        except stripe.error.SignatureVerificationError as e:
            logger.error("Webhook signature verification failed: %s", e)
            raise HTTPException(status_code=400, detail="Invalid signature.")
        except Exception as e:
            logger.error("Webhook parsing error: %s", e)
            raise HTTPException(status_code=400, detail=str(e))
            
    event_type = event.get("type")
    logger.info("Stripe Webhook event received: %s", event_type)
    
    if event_type in ("checkout.session.completed", "customer.subscription.created"):
        session = event.get("data", {}).get("object", {})
        customer_id = session.get("customer")
        subscription_id = session.get("subscription")
        
        metadata = session.get("metadata", {})
        plan = metadata.get("plan", "pro")
        email = metadata.get("email") or session.get("customer_details", {}).get("email")
        
        api_key = f"sre_live_{secrets.token_hex(16)}"
        
        db.create_or_update_subscription(
            customer_id=customer_id,
            subscription_id=subscription_id,
            plan=plan,
            status="active",
            api_key=api_key
        )
        
        if email:
            send_onboarding_email(email, api_key, plan)
            
    elif event_type == "customer.subscription.updated":
        sub = event.get("data", {}).get("object", {})
        customer_id = sub.get("customer")
        status = sub.get("status")
        db.create_or_update_subscription(
            customer_id=customer_id,
            subscription_id=sub.get("id"),
            plan="pro",
            status=status
        )
        
    elif event_type == "customer.subscription.deleted":
        sub = event.get("data", {}).get("object", {})
        customer_id = sub.get("customer")
        db.create_or_update_subscription(
            customer_id=customer_id,
            subscription_id=sub.get("id"),
            plan="pro",
            status="canceled"
        )
        
    return {"status": "success"}

def send_onboarding_email(email: str, api_key: str, plan: str):
    import smtplib
    from email.mime.text import MIMEText
    from email.mime.multipart import MIMEMultipart

    smtp_host = os.getenv("SMTP_HOST")
    smtp_port = os.getenv("SMTP_PORT")
    smtp_user = os.getenv("SMTP_USERNAME")
    smtp_pass = os.getenv("SMTP_PASSWORD")
    smtp_from = os.getenv("SMTP_FROM_EMAIL", "noreply@trihonor.com")

    logger.info(
        "\n"
        "=================================================================\n"
        "📧 ONBOARDING EMAIL SENT TO: %s\n"
        "Plan: %s\n"
        "API Key: %s\n"
        "Welcome to SRE Daemon! Start self-healing your infrastructure:\n"
        "curl -sSL https://sre-daemon.com/install.sh | SRE_API_KEY=%s bash\n"
        "=================================================================\n",
        email, plan.upper(), api_key, api_key
    )

    if smtp_host and smtp_port and smtp_user and smtp_pass:
        try:
            msg = MIMEMultipart()
            msg["From"] = smtp_from
            msg["To"] = email
            msg["Subject"] = "Welcome to SRE Daemon - Your API Key"

            body = f"""Hi there,

Thank you for subscribing to SRE Daemon {plan.upper()} Plan!

Your unique API Key is:
{api_key}

To install and register SRE Daemon on your server, run the following command:
curl -sSL https://sre.trihonor.com/install.sh | SRE_API_KEY={api_key} SRE_PLATFORM_URL=https://sre-api.trihonor.com bash

Best regards,
The TriHonor Team
"""
            msg.attach(MIMEText(body, "plain"))

            with smtplib.SMTP(smtp_host, int(smtp_port)) as server:
                server.starttls()
                server.login(smtp_user, smtp_pass)
                server.send_message(msg)
            logger.info("Successfully sent onboarding email to %s via SMTP.", email)
        except Exception as e:
            logger.error("Failed to send onboarding email via SMTP: %s", e)

@app.post("/api/incidents/{incident_id}/rollback")
async def rollback_incident(incident_id: int):
    """
    Reverts file changes applied in a self-healing incident.
    """
    from pathlib import Path
    incident = db.get_incident(incident_id)
    if not incident:
        raise HTTPException(status_code=404, detail="Incident not found")
        
    if incident["status"] != "resolved" and incident["status"] != "failed":
        raise HTTPException(status_code=400, detail="Only resolved or failed incidents can be rolled back")
        
    cmd = incident["proposed_command"]
    reverted_files = []
    
    # Check if proposed_command is a JSON list of actions
    if cmd.strip().startswith("["):
        try:
            actions = json.loads(cmd)
            for act in actions:
                act_type = act.get("type")
                target = act.get("target")
                if act_type in ("replace", "write") and target:
                    target_path = Path(target)
                    # Resolve git repository of target_path
                    repo_dir = None
                    try:
                        res = subprocess.run(
                            ["git", "-C", str(target_path.parent), "rev-parse", "--show-toplevel"],
                            capture_output=True, text=True, timeout=5
                        )
                        if res.returncode == 0:
                            repo_dir = Path(res.stdout.strip()).resolve()
                    except Exception:
                        pass
                        
                    if repo_dir:
                        # Revert changes to file
                        rel_path = target_path.relative_to(repo_dir)
                        revert_res = subprocess.run(
                            ["git", "-C", str(repo_dir), "checkout", "--", str(rel_path)],
                            capture_output=True, text=True, timeout=5
                        )
                        if revert_res.returncode == 0:
                            reverted_files.append(f"Reverted file: {target}")
                            logger.info("Successfully rolled back %s via git checkout", target)
                        else:
                            reverted_files.append(f"Failed to revert {target}: {revert_res.stderr}")
                    else:
                        reverted_files.append(f"Not a git repository: {target} (cannot auto-revert)")
        except Exception as e:
            logger.error("Failed to parse actions JSON for rollback: %s", e)
            raise HTTPException(status_code=500, detail=f"Rollback failed: {str(e)}")
    else:
        reverted_files.append("No file changes to revert (shell command restart).")

    db.update_incident_status(incident_id, "rolled_back", action_output="\n".join(reverted_files))
    return {"status": "rolled_back", "details": reverted_files}

@app.get("/api/incidents/{incident_id}/report")
def generate_incident_report(incident_id: int):
    """
    Generates a beautifully formatted HTML incident post-mortem report.
    """
    incident = db.get_incident(incident_id)
    if not incident:
        raise HTTPException(status_code=404, detail="Incident not found")

    from fastapi.responses import HTMLResponse
    
    status_emoji = {
        "resolved": "✅ Resolved",
        "failed": "❌ Failed",
        "rolled_back": "🔄 Rolled Back",
        "pending": "⏳ Pending Approval",
        "approved": "⚙️ Approved",
        "rejected": "🚫 Rejected"
    }.get(incident["status"], incident["status"])

    duration_str = f"{incident['duration']:.2f}s" if incident.get("duration") else "N/A"
    
    # Try parsing proposed_command as JSON to format file changes
    proposed_content = ""
    cmd = incident["proposed_command"]
    if cmd and cmd.strip().startswith("["):
        try:
            actions = json.loads(cmd)
            for act in actions:
                act_type = act.get("type", "unknown")
                target = act.get("target", "unknown")
                payload = act.get("payload", "")
                search = act.get("search", "")
                replace = act.get("replace", "")
                
                proposed_content += f"<div class='action-card'><strong>Type:</strong> {act_type}<br><strong>Target File:</strong> <code>{target}</code>"
                if search or replace:
                    proposed_content += f"<pre><code>- {search}\\n+ {replace}</code></pre>"
                elif payload:
                    proposed_content += f"<pre><code>{payload}</code></pre>"
                proposed_content += "</div>"
        except Exception:
            proposed_content = f"<pre><code>{cmd}</code></pre>"
    else:
        proposed_content = f"<pre><code>{cmd or 'None'}</code></pre>"

    html_content = f"""<!DOCTYPE html>
<html>
<head>
    <meta charset="utf-8">
    <title>SRE Post-Mortem Report - Incident #{incident_id}</title>
    <style>
        @import url('https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700&family=JetBrains+Mono&display=swap');
        body {{
            font-family: 'Inter', sans-serif;
            background-color: #060a0f;
            color: #e2e8f0;
            margin: 0;
            padding: 40px;
            line-height: 1.6;
        }}
        .container {{
            max-width: 800px;
            margin: 0 auto;
            background-color: #0b131c;
            border: 1px solid rgba(255, 255, 255, 0.08);
            border-radius: 12px;
            padding: 40px;
            box-shadow: 0 20px 40px rgba(0, 0, 0, 0.5);
        }}
        .header {{
            display: flex;
            justify-content: space-between;
            align-items: center;
            border-bottom: 1px solid rgba(255, 255, 255, 0.08);
            padding-bottom: 24px;
            margin-bottom: 32px;
        }}
        .logo {{
            font-size: 20px;
            font-weight: 700;
            color: #f8fafc;
        }}
        .logo span {{
            color: #22d3ee;
        }}
        .report-title {{
            font-size: 24px;
            font-weight: 700;
            margin: 0;
            color: #f8fafc;
        }}
        .status-badge {{
            display: inline-block;
            padding: 6px 16px;
            border-radius: 20px;
            font-size: 13px;
            font-weight: 600;
            background-color: rgba(34, 211, 238, 0.1);
            color: #22d3ee;
            border: 1px solid rgba(34, 211, 238, 0.2);
        }}
        .status-resolved {{
            background-color: rgba(74, 222, 128, 0.1);
            color: #4ade80;
            border-color: rgba(74, 222, 128, 0.2);
        }}
        .status-failed, .status-rolled_back {{
            background-color: rgba(239, 68, 68, 0.1);
            color: #f87171;
            border-color: rgba(239, 68, 68, 0.2);
        }}
        .grid {{
            display: grid;
            grid-template-columns: 1fr 1fr;
            gap: 20px;
            margin-bottom: 32px;
        }}
        .meta-item {{
            background-color: rgba(255, 255, 255, 0.02);
            border: 1px solid rgba(255, 255, 255, 0.04);
            border-radius: 8px;
            padding: 16px;
        }}
        .meta-label {{
            font-size: 11px;
            color: #94a3b8;
            text-transform: uppercase;
            letter-spacing: 0.05em;
            margin-bottom: 4px;
        }}
        .meta-value {{
            font-size: 15px;
            font-weight: 600;
            color: #f1f5f9;
        }}
        h3 {{
            font-size: 16px;
            font-weight: 600;
            color: #f8fafc;
            margin-top: 32px;
            margin-bottom: 12px;
            border-left: 3px solid #22d3ee;
            padding-left: 12px;
        }}
        pre {{
            background-color: #04070a;
            border: 1px solid rgba(255, 255, 255, 0.06);
            border-radius: 8px;
            padding: 16px;
            overflow-x: auto;
            font-family: 'JetBrains Mono', monospace;
            font-size: 13px;
            color: #38bdf8;
            margin: 0;
        }}
        .action-card {{
            background-color: rgba(255, 255, 255, 0.01);
            border: 1px solid rgba(255, 255, 255, 0.04);
            border-radius: 8px;
            padding: 16px;
            margin-bottom: 12px;
        }}
        .footer {{
            margin-top: 48px;
            border-top: 1px solid rgba(255, 255, 255, 0.08);
            padding-top: 20px;
            text-align: center;
            font-size: 12px;
            color: #475569;
        }}
        @media print {{
            body {{
                background-color: #fff;
                color: #000;
                padding: 0;
            }}
            .container {{
                border: none;
                box-shadow: none;
                padding: 0;
                background-color: #fff;
            }}
            .meta-item {{
                background-color: #f8fafc;
                border: 1px solid #e2e8f0;
            }}
            pre, .action-card {{
                background-color: #f8fafc;
                border: 1px solid #e2e8f0;
                color: #000;
            }}
            .report-title, h3, .logo {{
                color: #000 !important;
            }}
        }}
    </style>
</head>
<body>
    <div class="container">
        <div class="header">
            <div>
                <div class="logo">SRE<span>daemon</span></div>
                <h1 class="report-title">Incident Post-Mortem Report</h1>
            </div>
            <div class="status-badge status-{incident['status']}">
                {status_emoji}
            </div>
        </div>
        
        <div class="grid">
            <div class="meta-item">
                <div class="meta-label">Incident ID</div>
                <div class="meta-value">#{incident_id}</div>
            </div>
            <div class="meta-item">
                <div class="meta-label">Service Name</div>
                <div class="meta-value" style="text-transform: uppercase;">{incident['service']}</div>
            </div>
            <div class="meta-item">
                <div class="meta-label">Incident Time</div>
                <div class="meta-value">{incident['created_at'].replace('T', ' ')[:19]}</div>
            </div>
            <div class="meta-item">
                <div class="meta-label">Recovery Duration</div>
                <div class="meta-value">{duration_str}</div>
            </div>
        </div>
        
        <h3>Incident Title / Cause</h3>
        <p style="margin: 0; font-weight: 500;">{incident['title']}</p>
        
        <h3>Raw Logs & Context</h3>
        <pre style="color: #f87171; max-height: 250px;">{incident['logs']}</pre>
        
        <h3>AI Root Cause Analysis</h3>
        <p style="margin: 0;">{incident['ai_analysis']}</p>
        
        <h3>Proposed Remediation Actions</h3>
        {proposed_content}
        
        <h3>Execution Output & Results</h3>
        <pre style="max-height: 250px;">{incident['action_output'] or 'No action output logged.'}</pre>
        
        <div class="footer">
            Report generated autonomously by TriHonor SRE Daemon Platform · Tampere, Finland
        </div>
    </div>
</body>
</html>"""
    return HTMLResponse(content=html_content)

@app.get("/api/stripe/subscription")
def get_stripe_subscription(session_id: str):
    if session_id.startswith("cs_test_"):
        api_key = f"sre_live_mock_{session_id[-8:]}"
        db.create_or_update_subscription(
            customer_id=f"cus_{session_id[-8:]}",
            subscription_id=f"sub_{session_id[-8:]}",
            plan="pro",
            status="active",
            api_key=api_key
        )
        return {
            "status": "active",
            "plan": "pro",
            "api_key": api_key,
            "email": "user@example.com"
        }
        
    try:
        session = stripe.checkout.Session.retrieve(session_id)
        sub_id = session.get("subscription")
        if not sub_id:
            return {"status": "pending"}
            
        with db.get_db() as conn:
            cur = conn.cursor()
            cur.execute("SELECT * FROM subscriptions WHERE stripe_subscription_id = ?", (sub_id,))
            row = cur.fetchone()
            if row:
                return {
                    "status": row["status"],
                    "plan": row["plan"],
                    "api_key": row["api_key"],
                    "email": session.get("customer_details", {}).get("email")
                }
            else:
                return {"status": "pending"}
    except Exception as e:
        logger.error("Failed to retrieve subscription: %s", e)
        raise HTTPException(status_code=500, detail=str(e))

# --- Landing page at root ---
landing_page_path = os.path.join(os.path.dirname(__file__), "landing.html")

@app.get("/")
def serve_landing():
    if os.path.exists(landing_page_path):
        return FileResponse(landing_page_path)
    raise HTTPException(status_code=404, detail="Landing page not found")

@app.get("/payment-success")
def serve_payment_success():
    success_page = os.path.join(os.path.dirname(__file__), "payment_success.html")
    if os.path.exists(success_page):
        return FileResponse(success_page)
    # fallback: simple inline response
    from fastapi.responses import HTMLResponse
    return HTMLResponse("""
    <html><body style="font-family:sans-serif;text-align:center;padding:60px;background:#070d1a;color:#f1f5f9">
    <h1 style="color:#4ade80">✓ Payment Successful!</h1>
    <p>Check your email for your API key and installation instructions.</p>
    <a href="/" style="color:#a78bfa">← Back to home</a>
    </body></html>
    """)

# --- React Dashboard SPA at /dashboard/ ---
frontend_dist_path = os.path.join(os.path.dirname(__file__), "frontend_dist")

@app.get("/dashboard")
@app.get("/dashboard/{path:path}")
def serve_dashboard_spa(path: str = ""):
    full_path = os.path.join(frontend_dist_path, path)
    if path and "." in path.split("/")[-1] and os.path.exists(full_path):
        return FileResponse(full_path)
    index_path = os.path.join(frontend_dist_path, "index.html")
    if os.path.exists(index_path):
        return FileResponse(index_path)
    raise HTTPException(status_code=404, detail="Not found")

if os.path.exists(frontend_dist_path):
    assets_path = os.path.join(frontend_dist_path, "assets")
    if os.path.exists(assets_path):
        app.mount("/dashboard/assets", StaticFiles(directory=assets_path), name="dashboard-assets")

# --- Health check ---
@app.get("/health")
def health():
    return {"status": "healthy"}
