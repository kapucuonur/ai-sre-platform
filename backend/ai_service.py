import requests
import json
import db
import re
import sqlite3
import logging
import time
from datetime import datetime, timezone

logger = logging.getLogger("ai_service")

# Truce cache for ai-sre-platform
TRUCE_CACHE = {} # (service, alert_title) -> (timestamp, analysis_dict)
TRUCE_COOLDOWN_SECONDS = 600

def get_daily_calls(model_provider: str) -> int:
    today_str = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    try:
        with db.get_db() as conn:
            conn.execute("""
                CREATE TABLE IF NOT EXISTS daily_api_usage (
                    provider TEXT,
                    day TEXT,
                    count INTEGER,
                    PRIMARY KEY (provider, day)
                )
            """)
            cur = conn.cursor()
            cur.execute(
                "SELECT count FROM daily_api_usage WHERE provider = ? AND day = ?",
                (model_provider, today_str)
            )
            row = cur.fetchone()
            return row[0] if row else 0
    except Exception as e:
        logger.error("Token budget check error: %s", e)
        return 0

def increment_daily_calls(model_provider: str):
    today_str = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    try:
        with db.get_db() as conn:
            conn.execute("""
                CREATE TABLE IF NOT EXISTS daily_api_usage (
                    provider TEXT,
                    day TEXT,
                    count INTEGER,
                    PRIMARY KEY (provider, day)
                )
            """)
            conn.execute(
                "INSERT INTO daily_api_usage (provider, day, count) VALUES (?, ?, 1) "
                "ON CONFLICT(provider, day) DO UPDATE SET count = count + 1",
                (model_provider, today_str)
            )
            conn.commit()
    except Exception as e:
        logger.error("Token budget update error: %s", e)

def summarize_log(raw_msg: str) -> str:
    """Pre-processes and summarizes logs to reduce tokens sent to LLM."""
    if not raw_msg or len(raw_msg) < 500:
        return raw_msg

    lines = raw_msg.splitlines()
    if len(lines) <= 6:
        return raw_msg

    error_indicators = ["error", "exception", "failed", "traceback", "critical", "fatal"]
    critical_lines = []
    for i, line in enumerate(lines):
        if any(ind in line.lower() for ind in error_indicators):
            critical_lines.append((i, line))

    summary_lines = []
    summary_lines.append(f"[Start of Log Excerpt] {lines[0]}")

    added_indices = {0}
    for idx, line in critical_lines[:3]:
        for neighbor in range(max(0, idx - 1), min(len(lines), idx + 2)):
            if neighbor not in added_indices:
                summary_lines.append(f"Line {neighbor + 1}: {lines[neighbor]}")
                added_indices.add(neighbor)

    summary_lines.append("... [truncated intermediate lines] ...")
    for idx in range(max(0, len(lines) - 3), len(lines)):
        if idx not in added_indices:
            summary_lines.append(f"Line {idx + 1}: {lines[idx]}")

    return "\n".join(summary_lines)

def query_ollama(prompt: str) -> str:
    """Helper to query local Ollama instance."""
    ollama_url = db.get_setting("OLLAMA_URL", "http://192.168.1.116:11434")
    try:
        resp = requests.post(
            f"{ollama_url}/api/generate",
            json={
                "model": "qwen2.5-coder:7b",
                "prompt": prompt,
                "stream": False,
                "options": {"temperature": 0.1, "num_predict": 800}
            },
            timeout=60
        )
        resp.raise_for_status()
        return resp.json().get("response", "").strip()
    except Exception as e:
        logger.warning("Ollama call failed: %s", e)
        return None

def analyze_incident(service: str, alert_title: str, logs: str) -> dict:
    """
    Sends logs and alert data to Gemini/Ollama to get root-cause analysis and proposed action.
    Returns a dict with summary, reasoning, and proposed_command.
    """
    # 1. Truce Check (Ateşkes)
    truce_key = (service, alert_title)
    now = time.time()
    if truce_key in TRUCE_CACHE:
        last_time, prev_analysis = TRUCE_CACHE[truce_key]
        if now - last_time < TRUCE_COOLDOWN_SECONDS:
            logger.info("Truce active for (%s, %s). Returning previous analysis.", service, alert_title)
            return prev_analysis

    # Apply Log Summarization
    summarized_logs = summarize_log(logs)

    past_incidents = db.get_past_incidents(service, alert_title)
    past_context = ""
    if past_incidents:
        past_context = "\nGEÇMİŞ ONARIM GİRİŞİMLERİ (Bu hata/servis için önceki sonuçlar):\n"
        for p in past_incidents:
            status_str = "BAŞARILI ✅" if p["status"] == "resolved" else "BAŞARISIZ ❌"
            past_context += f"- Komut: '{p['proposed_command']}' -> Durum: {status_str}\n"
        past_context += "\nYukarıdaki geçmiş sonuçları dikkate al. Başarısız olan aksiyonları tekrar önerme. Başarılı olanları tercih et.\n"

    prompt = f"""You are a senior Site Reliability Engineer (SRE).
An alert was triggered for the service: '{service}'
Alert Title: {alert_title}

Recent log output:
\"\"\"
{summarized_logs}
\"\"\"
{past_context}
GÖREV:
1. Analiz et ve sorunun kök nedenini belirle (root-cause).
2. Bu sorunu çözmek veya sistemi düzeltmek için çalıştırılabilecek en mantıklı, güvenli kabuk (shell/bash) komutunu belirle (örn: 'docker restart {service}').
3. Eğer durum kritik değilse veya otomatik olarak çalıştırılacak güvenli bir komut yoksa 'proposed_command' alanını boş bırak.

ÖNEMLİ: Cevabı kesinlikle aşağıdaki JSON şemasında dön. Başka hiçbir açıklama, markdown bloğu veya süsleme yapma. Cevap sadece geçerli bir JSON objesi olmalı.

JSON Şeması:
{{
  "summary": "Türkçe kısa 1 satırlık sorun özeti",
  "reasoning": "Türkçe detaylı SRE analizi ve kök neden açıklaması",
  "proposed_command": "Çözüm için sunucuda çalıştırılacak tam bash komutu (veya boş string)"
}}
"""

    gemini_key = db.get_setting("GEMINI_API_KEY")
    result_text = None
    source = None

    # 1. Local Ollama (Local First)
    ollama_enabled = db.get_setting("OLLAMA_ENABLED", "true") == "true"
    if ollama_enabled:
        logger.info("Attempting local Ollama analysis...")
        result_text = query_ollama(prompt)
        if result_text:
            source = "local-ollama"

    # 2. Cloud Gemini (under budget control)
    if not result_text and gemini_key:
        daily_limit = int(db.get_setting("DAILY_GEMINI_LIMIT", "100"))
        current_calls = get_daily_calls("gemini")
        if current_calls < daily_limit:
            logger.info("Attempting cloud Gemini analysis (%d/%d calls)...", current_calls, daily_limit)
            url = f"https://generativelanguage.googleapis.com/v1beta/models/gemini-2.5-flash:generateContent?key={gemini_key}"
            headers = {"Content-Type": "application/json"}
            payload = {
                "contents": [{"parts": [{"text": prompt}]}],
                "generationConfig": {"responseMimeType": "application/json"}
            }
            try:
                resp = requests.post(url, json=payload, headers=headers, timeout=30)
                resp.raise_for_status()
                res_json = resp.json()
                result_text = res_json["candidates"][0]["content"]["parts"][0]["text"].strip()
                source = "gemini"
                increment_daily_calls("gemini")
            except Exception as e:
                logger.error("Gemini API call failed: %s", e)
        else:
            logger.warning("Gemini daily API budget reached. Falling back.")

    analysis_result = None
    if result_text:
        try:
            # Clean markdown codeblocks
            cleaned = result_text.strip()
            if cleaned.startswith("```json"):
                cleaned = cleaned[7:]
            if cleaned.endswith("```"):
                cleaned = cleaned[:-3]
            data = json.loads(cleaned.strip())
            analysis_result = {
                "summary": data.get("summary", "Bilinmeyen Sorun"),
                "reasoning": f"{data.get('reasoning', 'Açıklama üretilemedi.')} (Source: {source})",
                "proposed_command": data.get("proposed_command", "")
            }
        except Exception as e:
            logger.error("Failed to parse JSON response: %s. Raw content: %s", e, result_text)

    if not analysis_result:
        analysis_result = {
            "summary": f"Analiz Hatası: {alert_title}",
            "reasoning": "Hiçbir AI modelinden geçerli bir analiz sonucu alınamadı.",
            "proposed_command": ""
        }

    # Cache result under Truce
    TRUCE_CACHE[truce_key] = (now, analysis_result)
    return analysis_result

def format_slack_blocks(incident_id: int, service: str, title: str, summary: str, reasoning: str, proposed_command: str, autonomous_status: str = None, action_output: str = None) -> dict:
    """
    Formats the incident report as Slack Block Kit blocks.
    """
    blocks = [
        {
            "type": "header",
            "text": {
                "type": "plain_text",
                "text": f"🚨 AI SRE: Incident Detected in {service.upper()}",
                "emoji": True
            }
        },
        {
            "type": "section",
            "text": {
                "type": "mrkdwn",
                "text": f"*Alert:* {title}\n*Summary:* {summary}"
            }
        },
        {
            "type": "section",
            "text": {
                "type": "mrkdwn",
                "text": f"*AI Analysis & Root Cause:*\n{reasoning}"
            }
        }
    ]

    if proposed_command:
        blocks.append({
            "type": "section",
            "text": {
                "type": "mrkdwn",
                "text": f"*Proposed Remediation Action:*\n`{proposed_command}`"
            }
        })
        if autonomous_status:
            badge = "✅" if autonomous_status == "resolved" else "⚠️"
            status_label = "SUCCESS" if autonomous_status == "resolved" else "FAILED"
            msg = f"{badge} *Autonomous Action {status_label}*\n\n```\n{action_output[:400] if action_output else ''}\n```"
            blocks.append({
                "type": "section",
                "text": {
                    "type": "mrkdwn",
                    "text": msg
                }
            })
        else:
            blocks.append({
                "type": "actions",
                "block_id": f"incident_{incident_id}",
                "elements": [
                    {
                        "type": "button",
                        "text": {
                            "type": "plain_text",
                            "text": "Approve ✅",
                            "emoji": True
                        },
                        "style": "primary",
                        "value": f"approve_{incident_id}",
                        "action_id": "approve_action"
                    },
                    {
                        "type": "button",
                        "text": {
                            "type": "plain_text",
                            "text": "Reject ❌",
                            "emoji": True
                        },
                        "style": "danger",
                        "value": f"reject_{incident_id}",
                        "action_id": "reject_action"
                    }
                ]
            })
    else:
        blocks.append({
            "type": "section",
            "text": {
                "type": "mrkdwn",
                "text": "_No automatic recovery action is proposed for this alert._"
            }
        })

    return {"blocks": blocks}

def send_to_slack(incident_id: int, service: str, title: str, summary: str, reasoning: str, proposed_command: str, autonomous_status: str = None, action_output: str = None) -> bool:
    """
    Sends the formatted incident blocks to the configured Slack channel.
    """
    slack_token = db.get_setting("SLACK_BOT_TOKEN")
    slack_channel = db.get_setting("SLACK_CHANNEL_ID")
    
    if not slack_token or not slack_channel:
        logger.warning("Slack credentials missing, skipping ChatOps alert.")
        return False

    payload = format_slack_blocks(incident_id, service, title, summary, reasoning, proposed_command, autonomous_status, action_output)
    payload["channel"] = slack_channel

    headers = {
        "Authorization": f"Bearer {slack_token}",
        "Content-Type": "application/json"
    }

    try:
        r = requests.post("https://slack.com/api/chat.postMessage", json=payload, headers=headers, timeout=10)
        r_json = r.json()
        if not r_json.get("ok"):
            logger.error("Slack postMessage error: %s", r_json.get("error"))
            return False
        return True
    except Exception as e:
        logger.error("Failed to send Slack notification: %s", e)
        return False
