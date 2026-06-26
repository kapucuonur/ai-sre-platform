import requests
import json
import db
import logging

logger = logging.getLogger("ai_service")

def analyze_incident(service: str, alert_title: str, logs: str) -> dict:
    """
    Sends logs and alert data to Gemini to get root-cause analysis and proposed action.
    Returns a dict with summary, reasoning, and proposed_command.
    """
    gemini_key = db.get_setting("GEMINI_API_KEY")
    if not gemini_key:
        return {
            "summary": "AI configuration missing",
            "reasoning": "Please configure GEMINI_API_KEY in Settings to enable automatic root-cause analysis.",
            "proposed_command": ""
        }

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
{logs}
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

    url = f"https://generativelanguage.googleapis.com/v1beta/models/gemini-2.5-flash:generateContent?key={gemini_key}"
    headers = {"Content-Type": "application/json"}
    payload = {
        "contents": [
            {
                "parts": [
                    {"text": prompt}
                ]
            }
        ],
        "generationConfig": {
            "responseMimeType": "application/json"
        }
    }

    try:
        resp = requests.post(url, json=payload, headers=headers, timeout=30)
        resp.raise_for_status()
        res_json = resp.json()
        raw_text = res_json["candidates"][0]["content"]["parts"][0]["text"].strip()
        
        # Parse JSON output
        data = json.loads(raw_text)
        return {
            "summary": data.get("summary", "Bilinmeyen Sorun"),
            "reasoning": data.get("reasoning", "Açıklama üretilemedi."),
            "proposed_command": data.get("proposed_command", "")
        }
    except Exception as e:
        logger.error("Gemini API call failed: %s", e)
        return {
            "summary": f"Analiz Hatası: {alert_title}",
            "reasoning": f"Gemini API ile iletişim kurulamadı veya JSON ayrıştırılamadı. Hata: {str(e)}",
            "proposed_command": ""
        }

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
