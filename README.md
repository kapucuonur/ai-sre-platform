# AI SRE Platform — Self-Hosted MVP

A self-hosted, standalone AI-powered Site Reliability Engineering (SRE) monitoring and self-healing platform. 

This platform connects to your existing alert channels (like Grafana Webhooks), runs instant root-cause analysis on logs using the Google Gemini API, drafts remediation commands (e.g. `docker restart container_name`), and lets you approve or reject them dynamically via **Slack ChatOps** or the built-in **Web Dashboard**.

---

## Architecture Overview

1. **FastAPI Backend**: Exposes webhook receivers for Grafana and Slack callback actions. Exposes REST API for the frontend.
2. **React Frontend (Vite)**: Premium dark-themed dashboard to view system stats, active docker containers, incident histories, and settings.
3. **SQLite Database**: Self-contained state storage for incident logs and credentials.
4. **Docker Socket Mount**: Integrates with the host system's `/var/run/docker.sock` to dynamically run Docker CLI commands (like restarts) upon user approval.

---

## Quick Start (Local Deployment)

### 1. Prerequisite
Ensure you have Docker and Docker-Compose installed on your system.

### 2. Startup
Navigate to the project root directory and run:
```bash
docker compose up --build -d
```

Once running:
* **Web Dashboard**: Open `http://localhost:8080` in your browser.
* **Backend API**: Running at `http://localhost:8003`.

### 3. Setup Credentials
1. Go to the **Settings** tab in the Web Dashboard (`http://localhost:8080`).
2. Add your **Google Gemini API Key** (for SRE reasoning).
3. If you want Slack ChatOps, add your **Slack Bot User OAuth Token** (starts with `xoxb-`) and the target **Slack Channel ID**. Click **Save Settings**.

---

## ChatOps Integration

### Slack App Setup
1. Go to [api.slack.com/apps](https://api.slack.com/apps) and create a new App from scratch.
2. Under **OAuth & Permissions**, add the following bot token scopes:
   - `chat:write`
   - `incoming-webhook`
3. Install the app to your workspace and copy the generated **Bot User OAuth Token** into the platform settings.
4. Invite the bot to your target channel (`/invite @your_bot_name`).
5. Under **Interactivity & Shortcuts**, enable Interactivity and set the **Request URL** to:
   ```text
   http://<YOUR_PUBLIC_IP_OR_TUNNEL_URL>/api/webhook/slack/actions
   ```

---

## Testing the Platform Webhook

You can simulate a Grafana webhook alert to test the entire pipeline:

```bash
curl -X POST http://localhost:8003/api/webhook/grafana \
  -H "Content-Type: application/json" \
  -d '{
    "title": "bikefit-api container error rate spike",
    "alerts": [
      {
        "labels": {
          "alertname": "bikefit-api error",
          "container": "bikefit-api"
        },
        "annotations": {
          "description": "2026-06-26T12:03:52 [ERROR] YOLOv8 inference failed - CUDA out of memory"
        }
      }
    ]
  }'
```

This will trigger the AI Log Analyst reasoning, save the incident, send an interactive card to Slack, and display it instantly in the web dashboard!
