from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
import requests
import base64
import os
from dotenv import load_dotenv
from time import time

load_dotenv()

app = FastAPI()

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Env vars
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY")
SLACK_WEBHOOK_URL = os.getenv("SLACK_WEBHOOK_URL")
CRISP_WEBSITE_ID = os.getenv("CRISP_WEBSITE_ID")
CRISP_TOKEN_ID = os.getenv("CRISP_TOKEN_ID")
CRISP_TOKEN_KEY = os.getenv("CRISP_TOKEN_KEY")

# PurifyX context
PURIFYX_CONTEXT = """
PurifyX is a powerful lead generation ,Data Enrichment and outreach platform currently in Beta.

🔹 Features:
– Find verified leads
– Enrich contact data (emails, phone, social, etc.)
– Automate AI-personalized cold email sequences (Coming Soon)

🔹 Key links:
– Website: https://www.purifyx.ai
– Pricing: https://www.purifyx.ai/pricing
– Contact: https://www.purifyx.ai/contact
– Terms: https://www.purifyx.ai/terms
– Privacy Policy: https://www.purifyx.ai/privacy-policy
"""

# Memory
last_user_message = {}
DEDUPLICATION_TIMEOUT = 10
awaiting_issue = set()
fallback_sessions = set()

# --- Gemini Integration ---
def get_ai_reply(user_message: str):
    if not GEMINI_API_KEY:
        print("[GEMINI] API key missing.")
        return None

    url = f"https://generativelanguage.googleapis.com/v1beta/models/gemini-1.5-flash:generateContent?key={GEMINI_API_KEY}"
    headers = { "Content-Type": "application/json" }

    prompt = {
        "contents": [
            {
                "parts": [
                    {
                        "text": f"""You are an AI customer support assistant for PurifyX.

Context:
{PURIFYX_CONTEXT}

If the user asks to speak with support or says anything like "contact support", "talk to human", or "need help", do not try to answer. Instead, just reply with: HUMAN_SUPPORT.

Otherwise, help them with their question.

User: {user_message}
"""
                    }
                ]
            }
        ]
    }

    try:
        response = requests.post(url, headers=headers, json=prompt)
        if response.status_code == 200:
            data = response.json()
            return data["candidates"][0]["content"]["parts"][0]["text"].strip()
        else:
            print(f"[GEMINI] Error {response.status_code}: {response.text}")
    except Exception as e:
        print("[GEMINI] Request failed:", e)

    return None

# --- Crisp Integration ---
def send_crisp_message(session_id: str, message: str):
    url = f"https://api.crisp.chat/v1/website/{CRISP_WEBSITE_ID}/conversation/{session_id}/message"
    auth_token = f"{CRISP_TOKEN_ID}:{CRISP_TOKEN_KEY}"
    encoded_token = base64.b64encode(auth_token.encode()).decode()

    headers = {
        "Authorization": f"Basic {encoded_token}",
        "X-Crisp-Tier": "plugin",
        "Content-Type": "application/json"
    }

    payload = {
        "type": "text",
        "from": "operator",
        "origin": "chat",
        "content": message
    }

    try:
        res = requests.post(url, headers=headers, json=payload)
        print(f"[CRISP] Sent: {res.status_code}")
        return res.status_code == 200
    except Exception as e:
        print("[CRISP] Send error:", e)
        return False

# --- Slack Integration ---
def send_slack_alert(session_id: str, user_email: str, message: str):
    if not SLACK_WEBHOOK_URL:
        print("[SLACK] Webhook missing.")
        return
    try:
        payload = {
            "text": f"🙋 *Support Request*\nSession ID: `{session_id}`\nEmail: `{user_email}`\nIssue: {message}"
        }
        requests.post(SLACK_WEBHOOK_URL, json=payload)
        print("[SLACK] Alert sent.")
    except Exception as e:
        print("[SLACK] Error:", e)

# --- Webhook Handler ---
@app.post("/crisp-webhook")
async def handle_crisp_webhook(request: Request):
    body = await request.json()
    print("[Webhook] Payload received")

    event = body.get("event")
    data = body.get("data", {})
    message_from = data.get("from")

    if event != "message:send" or message_from != "user":
        return {"ok": True, "note": "Ignored non-user message"}

    session_id = data.get("session_id")
    user_message = data.get("content", "")
    user_email = data.get("user", {}).get("email", "unknown")

    if not session_id or not user_message:
        return {"ok": False, "error": "Missing session or message"}

    print(f"[User] {user_message} (session: {session_id})")

    # Deduplication
    now = time()
    last_msg, last_time = last_user_message.get(session_id, ("", 0))
    if user_message == last_msg and (now - last_time) < DEDUPLICATION_TIMEOUT:
        print("[Deduplication] Skipped")
        return {"ok": True, "note": "Duplicate ignored"}
    last_user_message[session_id] = (user_message, now)

    # Awaiting issue
    if session_id in awaiting_issue:
        awaiting_issue.remove(session_id)
        fallback_sessions.add(session_id)
        send_crisp_message(session_id, "Thanks! A human support agent will assist you shortly 🔄")
        send_slack_alert(session_id, user_email, user_message)
        return {"ok": True, "note": "Escalated to human"}

    # Already escalated
    if session_id in fallback_sessions:
        print("[Fallback] Session already escalated.")
        return {"ok": True, "note": "Already escalated"}

    # Try Gemini
    reply = get_ai_reply(user_message)

    if reply == "HUMAN_SUPPORT":
        awaiting_issue.add(session_id)
        send_crisp_message(session_id, "Can you please describe the issue you're facing?")
        return {"ok": True, "note": "Asked for issue"}
    elif reply and reply.strip():
        send_crisp_message(session_id, reply)
        return {"ok": True, "note": "Answered via AI"}
    else:
        fallback_sessions.add(session_id)
        send_crisp_message(session_id, "Let me connect you to a support person 🔄")
        send_slack_alert(session_id, user_email, user_message)
        return {"ok": True, "note": "Fallback triggered"}
