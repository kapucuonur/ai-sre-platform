from fastapi import FastAPI, Request, HTTPException, BackgroundTasks
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
async def receive_daemon_incident(payload: DaemonIncidentModel):
    """
    Receives healing incidents reported by the local Pi 5 SRE Daemon.
    """
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

# --- Health check ---
@app.get("/health")
def health():
    return {"status": "healthy"}
