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

# --- Core Webhooks ---

@app.post("/api/webhook/grafana")
async def grafana_webhook(request: Request, background_tasks: BackgroundTasks):
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
        background_tasks.add_task(process_incident, service, alert_title, logs_text, payload)
        
        return {"status": "processing"}
    except Exception as e:
        logger.error("Failed to parse Grafana webhook: %s", e)
        raise HTTPException(status_code=400, detail=str(e))

def process_incident(service: str, title: str, logs: str, alert_payload: dict):
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
            proposed_command=proposed_cmd
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
    else:
        # Standard flow (needs approval for high/critical or just waiting)
        incident_id = db.create_incident(
            title=title,
            service=service,
            alert_payload=alert_payload,
            logs=logs,
            ai_analysis=analysis["reasoning"],
            proposed_command=proposed_cmd
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

@app.post("/api/daemon/incident")
async def receive_daemon_incident(payload: DaemonIncidentModel, x_sre_api_key: Optional[str] = Header(None, alias="X-SRE-API-Key")):
    """
    Receives healing incidents reported by the local Pi 5 SRE Daemon.
    """
    active_keys = db.get_active_api_keys()
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
        duration=payload.duration
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
    return {"status": "recorded", "incident_id": incident_id}

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
async def get_system_status():
    """Fetches real-time hardware status metrics and active containers."""
    stats = {
        "cpu_temp": "Bilinmiyor",
        "memory": "Bilinmiyor",
        "disk": "Bilinmiyor",
        "containers": []
    }
    
    try:
        # CPU temp (supports macOS & Linux)
        if os.path.exists("/usr/sbin/system_profiler") or os.name == "posix" and not os.path.exists("/sys/class/thermal"):
            # Mock or macOS temp
            stats["cpu_temp"] = "45.0°C"
        else:
            temp_res = subprocess.run(["cat", "/sys/class/thermal/thermal_zone0/temp"], capture_output=True, text=True, timeout=2)
            temp_milli = int(temp_res.stdout.strip())
            stats["cpu_temp"] = f"{temp_milli / 1000.0:.1f}°C"
    except Exception:
        pass
        
    try:
        # Memory
        free = subprocess.run(["free", "-h"], capture_output=True, text=True, timeout=2)
        stats["memory"] = free.stdout.strip().split("\n")[1].split()[2] + " / " + free.stdout.strip().split("\n")[1].split()[1]
    except Exception:
        pass
        
    try:
        # Disk usage
        df = subprocess.run(["df", "-h", "/"], capture_output=True, text=True, timeout=2)
        df_line = df.stdout.strip().split("\n")[-1].split()
        stats["disk"] = f"{df_line[2]} / {df_line[1]} ({df_line[4]} used)"
    except Exception:
        pass

    try:
        # Active containers
        docker_res = subprocess.run(
            ["docker", "ps", "--format", "{{.Names}}|{{.Status}}"],
            capture_output=True, text=True, timeout=5
        )
        containers = docker_res.stdout.strip().splitlines()
        for c in containers:
            parts = c.split("|")
            stats["containers"].append({
                "name": parts[0],
                "status": parts[1]
            })
    except Exception:
        pass
        
    return stats

@app.get("/api/incidents")
async def list_incidents():
    return db.get_all_incidents()

@app.get("/api/history")
async def get_history():
    return db.get_all_incidents()

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
        success_url = f"http://localhost:5173/payment-success?session_id={mock_session_id}&plan={payload.plan}&email={payload.email}"
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
            success_url="http://localhost:5173/payment-success?session_id={CHECKOUT_SESSION_ID}",
            cancel_url="http://localhost:5173/pricing",
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

# --- Frontend SPA Static serving ---
frontend_dist_path = os.path.join(os.path.dirname(__file__), "frontend_dist")

@app.get("/sre/{path:path}")
def serve_frontend_spa(path: str):
    index_path = os.path.join(frontend_dist_path, "index.html")
    if not path or "." not in path:
        if os.path.exists(index_path):
            return FileResponse(index_path)
    raise HTTPException(status_code=404, detail="File not found")

if os.path.exists(frontend_dist_path):
    app.mount("/sre", StaticFiles(directory=frontend_dist_path, html=True), name="sre")

# --- Health check ---
@app.get("/health")
def health():
    return {"status": "healthy"}
