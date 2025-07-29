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
PurifyX is a powerful lead generation and outreach platform currently in Beta.

🔹 Features:
– Find verified leads
– Enrich contact data (firmographic, social, etc.)
– Automate AI-personalized cold email sequences

🔹 Key links:
– Website: https://www.purifyx.ai
– Pricing: https://www.purifyx.ai/pricing
– Contact: https://www.purifyx.ai/contact
– Terms: https://www.purifyx.ai/terms
– Privacy Policy: https://www.purifyx.ai/privacy-policy
"""

# Memory for deduplication and fallback
last_user_message = {}
DEDUPLICATION_TIMEOUT = 10
awaiting_issue = set()

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
                        "text": f"""You are an AI customer support agent for PurifyX.

Context:
{PURIFYX_CONTEXT}

If the question is unrelated, unclear, or too complex to handle, reply with: "HUMAN_SUPPORT".

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
def send_slack_alert(message: str):
    if not SLACK_WEBHOOK_URL:
        print("[SLACK] Webhook missing.")
        return
    try:
        requests.post(SLACK_WEBHOOK_URL, json={ "text": message })
    except Exception as e:
        print("[SLACK] Error:", e)

# --- Webhook Handler ---
@app.post("/crisp-webhook")
async def handle_crisp_webhook(request: Request):
    body = await request.json()
    print("[Webhook] Payload received")

    # Validate message type
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

    # If user was asked to describe their issue
    if session_id in awaiting_issue:
        awaiting_issue.remove(session_id)

        # Escalate to Slack with issue + user email
        send_crisp_message(session_id, "Thanks! A human support agent will assist you shortly 🔄")
        slack_msg = f"🙋 *User requested support*\nSession ID: `{session_id}`\nEmail: `{user_email}`\nIssue: {user_message}"
        send_slack_alert(slack_msg)
        return {"ok": True, "note": "Sent to Slack"}

    # Try AI response
    reply = get_ai_reply(user_message)

    if reply == "HUMAN_SUPPORT":
        # Ask user to describe issue
        awaiting_issue.add(session_id)
        send_crisp_message(session_id, "Can you please describe the issue you're facing?")
        return {"ok": True, "note": "Asked user for support issue"}

    if reply and reply.strip():
        send_crisp_message(session_id, reply)
        return {"ok": True, "note": "Replied via AI"}

    # AI failed, escalate
    send_crisp_message(session_id, "Let me connect you to a support person 🔄")
    slack_msg = f"⚠️ *AI fallback triggered*\nSession: `{session_id}`\nEmail: `{user_email}`\nMessage: {user_message}"
    send_slack_alert(slack_msg)
    return {"ok": True, "note": "AI fallback"}
