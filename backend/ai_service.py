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

def query_claude(prompt: str) -> str:
    """Helper to query Anthropic Claude API."""
    claude_key = db.get_setting("CLAUDE_API_KEY") or db.get_setting("ANTHROPIC_API_KEY")
    if not claude_key:
        logger.warning("Claude API key not set in database.")
        return None
    try:
        url = "https://api.anthropic.com/v1/messages"
        headers = {
            "x-api-key": claude_key,
            "anthropic-version": "2023-06-01",
            "content-type": "application/json"
        }
        payload = {
            "model": "claude-3-5-sonnet-20241022",
            "max_tokens": 1000,
            "messages": [{"role": "user", "content": prompt}]
        }
        resp = requests.post(url, json=payload, headers=headers, timeout=30)
        resp.raise_for_status()
        return resp.json()["content"][0]["text"].strip()
    except Exception as e:
        logger.error("Claude API call failed: %s", e)
        return None

def query_gemini(prompt: str) -> str:
    """Helper to query Google Gemini API."""
    gemini_key = db.get_setting("GEMINI_API_KEY")
    if not gemini_key:
        logger.warning("Gemini API key not set in database.")
        return None
    try:
        url = f"https://generativelanguage.googleapis.com/v1beta/models/gemini-2.5-flash:generateContent?key={gemini_key}"
        headers = {"Content-Type": "application/json"}
        payload = {
            "contents": [{"parts": [{"text": prompt}]}],
            "generationConfig": {"responseMimeType": "application/json"}
        }
        resp = requests.post(url, json=payload, headers=headers, timeout=30)
        resp.raise_for_status()
        return resp.json()["candidates"][0]["content"]["parts"][0]["text"].strip()
    except Exception as e:
        logger.error("Gemini API call failed: %s", e)
        return None

def query_groq(prompt: str) -> str:
    """Helper to query Groq API."""
    groq_key = db.get_setting("GROQ_API_KEY")
    if not groq_key:
        logger.warning("Groq API key not set in database.")
        return None
    try:
        url = "https://api.groq.com/openai/v1/chat/completions"
        headers = {
            "Authorization": f"Bearer {groq_key}",
            "Content-Type": "application/json"
        }
        payload = {
            "model": "llama-3.3-70b-versatile",
            "messages": [{"role": "user", "content": prompt}],
            "response_format": {"type": "json_object"}
        }
        resp = requests.post(url, json=payload, headers=headers, timeout=30)
        resp.raise_for_status()
        return resp.json()["choices"][0]["message"]["content"].strip()
    except Exception as e:
        logger.error("Groq API call failed: %s", e)
        return None

def increment_cost(provider: str):
    """Tracks and accumulates monetary API expenditure."""
    cost_map = {
        "gemini": 0.0001,
        "claude": 0.0050,
    }
    cost = cost_map.get(provider, 0.0)
    if cost <= 0.0:
        return
    
    try:
        with db.get_db() as conn:
            cur = conn.cursor()
            cur.execute("SELECT val FROM settings WHERE key = 'total_spent'")
            row = cur.fetchone()
            current_spent = float(row[0]) if row else 0.0
            new_spent = current_spent + cost
            
            conn.execute(
                "INSERT INTO settings (key, val) VALUES ('total_spent', ?) "
                "ON CONFLICT(key) DO UPDATE SET val = ?",
                (str(new_spent), str(new_spent))
            )
            conn.commit()
            logger.info("Antigravity modunda %s üzerinden $%.4f harcandı. Toplam harcama: $%.4f", provider, cost, new_spent)
    except Exception as e:
        logger.error("Cost tracking error: %s", e)

def analyze_incident(service: str, alert_title: str, logs: str) -> dict:
    """
    Sends logs and alert data through the multi-layered Antigravity AI routing pipeline.
    1. Loop-prevention check (cooldown) if past attempts kept failing.
    2. Local routing check (Qwen 7b) to see if it is simple or complex.
    3. If complex, pro-actively switches between Claude (architectural) and Gemini (routine).
    """
    # 1. Truce Check (Ateşkes)
    truce_key = (service, alert_title)
    now = time.time()
    if truce_key in TRUCE_CACHE:
        last_time, prev_analysis = TRUCE_CACHE[truce_key]
        if now - last_time < TRUCE_COOLDOWN_SECONDS:
            logger.info("Truce active for (%s, %s). Returning previous analysis.", service, alert_title)
            return prev_analysis

    # Fetch past incidents for loop prevention
    past_incidents = db.get_past_incidents(service, alert_title)
    if len(past_incidents) >= 2:
        last_two_failed = all(p.get("status") == "failed" for p in past_incidents[:2])
        if last_two_failed:
            logger.warning("Feedback loop detected! Last 2 recovery attempts failed for (%s, %s). Halting AI intervention.", service, alert_title)
            return {
                "summary": "AI Onarım Döngüsü Engellendi",
                "reasoning": "Bu hata için yapılan son 2 otomatik iyileştirme denemesi başarısız oldu. Sonsuz döngüyü engellemek amacıyla yapay zeka müdahalesi durdurulmuştur. Lütfen sistemi manuel kontrol edin.",
                "proposed_command": ""
            }

    # Apply Log Summarization (Compressor)
    summarized_logs = summarize_log(logs)

    # Stage 1: Local routing check using Qwen 7b
    prompt_local = f"""You are a site reliability analysis router.
Determine if the following alert can be fixed with a simple routine command (like restarting a docker container, deleting temp files, restarting a service).
Service: '{service}'
Alert Title: {alert_title}
Logs:
{summarized_logs}

Output ONLY a JSON response in the following schema:
{{
  "simple": true or false,
  "complexity": 1-10 rating,
  "reasoning": "Turkish explanation of findings",
  "proposed_command": "remediation command if simple, or empty string"
}}
"""
    ollama_enabled = db.get_setting("OLLAMA_ENABLED", "true") == "true"
    is_simple = False
    complexity = 5
    local_data = {}
    
    if ollama_enabled:
        logger.info("Attempting local Qwen routing check...")
        local_result = query_ollama(prompt_local)
        if local_result:
            try:
                cleaned_local = local_result.strip()
                if cleaned_local.startswith("```json"):
                    cleaned_local = cleaned_local[7:]
                if cleaned_local.endswith("```"):
                    cleaned_local = cleaned_local[:-3]
                local_data = json.loads(cleaned_local.strip())
                is_simple = local_data.get("simple", False)
                complexity = local_data.get("complexity", 5)
            except Exception as e:
                logger.warning("Local Qwen JSON parse failed: %s. Raw response: %s", e, local_result)

    if is_simple and local_data.get("proposed_command"):
        logger.info("Local Qwen resolved the incident as simple. Bypassing cloud models.")
        analysis_result = {
            "summary": local_data.get("reasoning", "Basit rutin hata çözümü (Lokal)"),
            "reasoning": f"{local_data.get('reasoning', 'Yerel model tarafından otonom olarak çözüldü.')} (Source: local-ollama)",
            "proposed_command": local_data.get("proposed_command", "")
        }
        TRUCE_CACHE[truce_key] = (now, analysis_result)
        return analysis_result

    # Stage 2 & 3: Cloud routing (Pro-Active Switching: Claude vs Gemini)
    past_context = ""
    if past_incidents:
        past_context = "\nGEÇMİŞ ONARIM GİRİŞİMLERİ (Bu hata/servis için önceki sonuçlar):\n"
        for p in past_incidents:
            status_str = "BAŞARILI ✅" if p["status"] == "resolved" else "BAŞARISIZ ❌"
            past_context += f"- Komut: '{p['proposed_command']}' -> Durum: {status_str}\n"
        past_context += "\nYukarıdaki geçmiş sonuçları dikkate al. Başarısız olan aksiyonları tekrar önerme. Başarılı olanları tercih et.\n"

    prompt_cloud = f"""You are a senior Site Reliability Engineer (SRE).
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

TOKEN TASARRUFU TALİMATI (Compressor):
- Analizini olabildiğince kısa, öz ve net tut.
- Sadece hata kodunu, dosya yolunu, hata tipini ve doğrudan kök nedeni belirt. Gereksiz veya uzun açıklamalar yapmaktan kaçın.

ÖNEMLİ: Cevabı kesinlikle aşağıdaki JSON şemasında dön. Başka hiçbir açıklama, markdown bloğu veya süsleme yapma. Cevap sadece geçerli bir JSON objesi olmalı.

JSON Şeması:
{{
  "summary": "Türkçe kısa 1 satırlık sorun özeti",
  "reasoning": "Türkçe detaylı SRE analizi ve kök neden açıklaması",
  "proposed_command": "Çözüm için sunucuda çalıştırılacak tam bash komutu (veya boş string)"
}}
"""

    gemini_key = db.get_setting("GEMINI_API_KEY")
    claude_key = db.get_setting("CLAUDE_API_KEY") or db.get_setting("ANTHROPIC_API_KEY")
    groq_key = db.get_setting("GROQ_API_KEY")
    result_text = None
    source = None

    # --- LLM Jury Consensus Module ---
    jury_enabled = db.get_setting("llm_jury_enabled", "true") == "true"
    if jury_enabled and complexity >= 7 and gemini_key and groq_key:
        logger.info("Initiating LLM Jury consensus for complexity %d...", complexity)
        res_gemini = query_gemini(prompt_cloud)
        res_groq = query_groq(prompt_cloud)
        
        if res_gemini and res_groq:
            try:
                cleaned_gem = res_gemini.strip()
                if cleaned_gem.startswith("```json"): cleaned_gem = cleaned_gem[7:]
                if cleaned_gem.endswith("```"): cleaned_gem = cleaned_gem[:-3]
                data_gemini = json.loads(cleaned_gem.strip())
                cmd_gemini = data_gemini.get("proposed_command", "").strip()

                cleaned_groq = res_groq.strip()
                if cleaned_groq.startswith("```json"): cleaned_groq = cleaned_groq[7:]
                if cleaned_groq.endswith("```"): cleaned_groq = cleaned_groq[:-3]
                data_groq = json.loads(cleaned_groq.strip())
                cmd_groq = data_groq.get("proposed_command", "").strip()

                norm_gem = re.sub(r"[\s'\"]", "", cmd_gemini).lower()
                norm_groq = re.sub(r"[\s'\"]", "", cmd_groq).lower()

                if norm_gem == norm_groq:
                    logger.info("Jury Consensus reached: %s", cmd_gemini)
                    analysis_result = {
                        "summary": data_gemini.get("summary", "Jüri Konsensüs Çözümü"),
                        "reasoning": f"{data_gemini.get('reasoning', '')} [Jüri kararı ile doğrulandı (Gemini & Groq ortak kararı)]",
                        "proposed_command": cmd_gemini
                    }
                    TRUCE_CACHE[truce_key] = (now, analysis_result)
                    return analysis_result
                else:
                    logger.warning("Jury Disagreement! Gemini proposed '%s' | Groq proposed '%s'. Escalating to Claude Judge...", cmd_gemini, cmd_groq)
                    if claude_key:
                        prompt_judge = f"""You are the Chief SRE Judge for the autonomous platform.
An incident occurred in the service '{service}' with title '{alert_title}'.
Logs:
\"\"\"
{summarized_logs}
\"\"\"

The SRE Jury of two models disagreed on the repair command:
Model 1 (Gemini) proposed: '{cmd_gemini}' (Reason: {data_gemini.get('reasoning')})
Model 2 (Groq) proposed: '{cmd_groq}' (Reason: {data_groq.get('reasoning')})

Determine which command is safer and more correct, or provide a corrected/merged command if both are wrong.
Output ONLY a JSON response in the following schema:
{{
  "summary": "Türkçe kısa sorun özeti",
  "reasoning": "Türkçe jüri anlaşmazlık çözümü ve nihai hakem kararı açıklaması",
  "proposed_command": "Nihai çalıştırılacak komut (veya boş string)"
}}
"""
                        res_claude = query_claude(prompt_judge)
                        if res_claude:
                            cleaned_c = res_claude.strip()
                            if cleaned_c.startswith("```json"): cleaned_c = cleaned_c[7:]
                            if cleaned_c.endswith("```"): cleaned_c = cleaned_c[:-3]
                            data_judge = json.loads(cleaned_c.strip())
                            
                            analysis_result = {
                                "summary": data_judge.get("summary", "Jüri Hakem Kararı"),
                                "reasoning": f"{data_judge.get('reasoning', '')} [Claude Hakem Kararı - Jüri anlaşmazlığı çözüldü]",
                                "proposed_command": data_judge.get("proposed_command", "")
                            }
                            TRUCE_CACHE[truce_key] = (now, analysis_result)
                            return analysis_result
            except Exception as jury_ex:
                logger.error("Jury decision parsing failed: %s", jury_ex)

    # Decide provider based on complexity (if Jury Consensus was not completed)
    primary_provider = "claude" if (complexity >= 8 and claude_key) else "gemini"
    
    # Try primary provider
    if primary_provider == "claude":
        daily_limit = int(db.get_setting("DAILY_CLAUDE_LIMIT", "5"))
        current_calls = get_daily_calls("claude")
        if current_calls < daily_limit:
            logger.info("Routing complex incident (complexity %d) to Claude (%d/%d calls)...", complexity, current_calls, daily_limit)
            result_text = query_claude(prompt_cloud)
            if result_text:
                source = "claude"
                increment_daily_calls("claude")
                increment_cost("claude")
        else:
            logger.warning("Claude daily budget reached. Falling back to Gemini.")
            primary_provider = "gemini"

    if not result_text and primary_provider == "gemini" and gemini_key:
        daily_limit = int(db.get_setting("DAILY_GEMINI_LIMIT", "100"))
        current_calls = get_daily_calls("gemini")
        if current_calls < daily_limit:
            logger.info("Routing incident (complexity %d) to Gemini (%d/%d calls)...", complexity, current_calls, daily_limit)
            url = f"https://generativelanguage.googleapis.com/v1beta/models/gemini-2.5-flash:generateContent?key={gemini_key}"
            headers = {"Content-Type": "application/json"}
            payload = {
                "contents": [{"parts": [{"text": prompt_cloud}]}],
                "generationConfig": {"responseMimeType": "application/json"}
            }
            try:
                resp = requests.post(url, json=payload, headers=headers, timeout=30)
                resp.raise_for_status()
                res_json = resp.json()
                result_text = res_json["candidates"][0]["content"]["parts"][0]["text"].strip()
                source = "gemini"
                increment_daily_calls("gemini")
                increment_cost("gemini")
            except Exception as e:
                logger.error("Gemini API call failed: %s", e)
        else:
            logger.warning("Gemini daily budget reached. Falling back.")

    # Second-chance fallback if primary choice failed/over-budget
    if not result_text:
        # If Claude failed, try Gemini as fallback
        if primary_provider == "claude" and gemini_key:
            daily_limit = int(db.get_setting("DAILY_GEMINI_LIMIT", "100"))
            current_calls = get_daily_calls("gemini")
            if current_calls < daily_limit:
                logger.info("Claude failed. Trying Gemini fallback...")
                url = f"https://generativelanguage.googleapis.com/v1beta/models/gemini-2.5-flash:generateContent?key={gemini_key}"
                headers = {"Content-Type": "application/json"}
                payload = {
                    "contents": [{"parts": [{"text": prompt_cloud}]}],
                    "generationConfig": {"responseMimeType": "application/json"}
                }
                try:
                    resp = requests.post(url, json=payload, headers=headers, timeout=30)
                    resp.raise_for_status()
                    res_json = resp.json()
                    result_text = res_json["candidates"][0]["content"]["parts"][0]["text"].strip()
                    source = "gemini-fallback"
                    increment_daily_calls("gemini")
                    increment_cost("gemini")
                except Exception as e:
                    logger.error("Gemini fallback failed: %s", e)
        # If Gemini failed, try Claude as fallback
        elif primary_provider == "gemini" and claude_key:
            daily_limit = int(db.get_setting("DAILY_CLAUDE_LIMIT", "5"))
            current_calls = get_daily_calls("claude")
            if current_calls < daily_limit:
                logger.info("Gemini failed. Trying Claude fallback...")
                result_text = query_claude(prompt_cloud)
                if result_text:
                    source = "claude-fallback"
                    increment_daily_calls("claude")
                    increment_cost("claude")

    # Local Ollama fallback if all cloud options failed
    if not result_text and ollama_enabled:
        logger.info("All cloud options failed. Falling back to local Ollama...")
        result_text = query_ollama(prompt_cloud)
        if result_text:
            source = "local-ollama-fallback"

    analysis_result = None
    if result_text:
        try:
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

def send_to_teams(incident_id: int, service: str, title: str, summary: str, reasoning: str, proposed_command: str, autonomous_status: str = None, action_output: str = None) -> bool:
    """
    Sends the formatted incident blocks to the configured Microsoft Teams Webhook.
    """
    teams_webhook = db.get_setting("TEAMS_WEBHOOK_URL")
    if not teams_webhook:
        logger.debug("Teams webhook URL not configured, skipping Teams alert.")
        return False

    # Build Adaptive Card JSON payload
    teams_payload = {
        "type": "message",
        "attachments": [
            {
                "contentType": "application/vnd.microsoft.card.adaptive",
                "content": {
                    "type": "AdaptiveCard",
                    "version": "1.4",
                    "body": [
                        {
                            "type": "TextBlock",
                            "size": "medium",
                            "weight": "bolder",
                            "text": f"🚨 AI SRE: Incident Detected in {service.upper()}",
                            "style": "heading"
                        },
                        {
                            "type": "FactSet",
                            "facts": [
                                {"title": "Incident ID:", "value": str(incident_id)},
                                {"title": "Title:", "value": title},
                                {"title": "Summary:", "value": summary}
                            ]
                        },
                        {
                            "type": "TextBlock",
                            "text": "**Reasoning / Analysis:**\n" + reasoning,
                            "wrap": True
                        }
                    ]
                }
            }
        ]
    }

    if proposed_command:
        teams_payload["attachments"][0]["content"]["body"].append({
            "type": "TextBlock",
            "text": f"**Proposed Command:**\n`{proposed_command}`",
            "wrap": True
        })

    if autonomous_status:
        teams_payload["attachments"][0]["content"]["body"].append({
            "type": "TextBlock",
            "text": f"**Execution Status:** {autonomous_status.upper()}",
            "color": "good" if autonomous_status == "resolved" else "attention",
            "weight": "bolder"
        })

    if action_output:
        teams_payload["attachments"][0]["content"]["body"].append({
            "type": "TextBlock",
            "text": f"**Output:**\n```\n{action_output[:500]}\n```",
            "wrap": True
        })

    dashboard_url = db.get_setting("DASHBOARD_URL", "https://sre.trihonor.com")
    teams_payload["attachments"][0]["content"]["actions"] = [
        {
            "type": "Action.OpenUrl",
            "title": "View Dashboard 🖥️",
            "url": dashboard_url
        }
    ]

    try:
        r = requests.post(teams_webhook, json=teams_payload, timeout=10)
        return r.status_code in (200, 201, 202)
    except Exception as e:
        logger.error("Failed to send MS Teams notification: %s", e)
        return False
