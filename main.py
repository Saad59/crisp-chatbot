from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
import requests
import base64
import os
from dotenv import load_dotenv
from time import time

# In-memory store to avoid duplicate replies
last_user_message = {}  # session_id -> (content, timestamp)
DEDUPLICATION_TIMEOUT = 10  # seconds
fallback_sessions = set()

load_dotenv()

app = FastAPI()

# CORS for local testing or frontend integration
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Environment variables
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY")
SLACK_WEBHOOK_URL = os.getenv("SLACK_WEBHOOK_URL")
CRISP_WEBSITE_ID = os.getenv("CRISP_WEBSITE_ID")
CRISP_TOKEN_ID = os.getenv("CRISP_TOKEN_ID")
CRISP_TOKEN_KEY = os.getenv("CRISP_TOKEN_KEY")

# Send message to Crisp
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
        response = requests.post(url, headers=headers, json=payload)
        print(f"[CRISP] Reply sent: {response.status_code} - {response.text}")
        return response.status_code == 200
    except Exception as e:
        print("[CRISP] Failed to send:", e)
        return False

# Send fallback alert to Slack
def send_slack_alert(message: str):
    if not SLACK_WEBHOOK_URL:
        print("[SLACK] Webhook not configured.")
        return
    try:
        payload = { "text": f"🆘 *AI Fallback Triggered:*\n{message}" }
        r = requests.post(SLACK_WEBHOOK_URL, json=payload)
        print(f"[SLACK] Alert sent: {r.status_code}")
    except Exception as e:
        print("[SLACK] Error sending alert:", e)

# Get reply from Gemini
def get_ai_reply(user_message: str):
    if not GEMINI_API_KEY:
        print("[GEMINI] Missing API key")
        return None

    url = f"https://generativelanguage.googleapis.com/v1beta/models/gemini-1.5-flash:generateContent?key={GEMINI_API_KEY}"
    headers = {
        "Content-Type": "application/json"
    }
    payload = {
        "contents": [
            {
                "parts": [
                    {
                        "text": f"You are a helpful AI customer support assistant.\nUser: {user_message}"
                    }
                ]
            }
        ]
    }

    try:
        response = requests.post(url, headers=headers, json=payload)
        if response.status_code == 200:
            data = response.json()
            return data['candidates'][0]['content']['parts'][0]['text']
        elif response.status_code == 429:
            print("[GEMINI] Quota exceeded or rate-limited")
        else:
            print(f"[GEMINI] Error: {response.status_code} - {response.text}")
    except Exception as e:
        print("[GEMINI] Request failed:", e)

    return None

# Crisp Webhook Handler
@app.post("/crisp-webhook")
async def handle_crisp_webhook(request: Request):
    body = await request.json()
    print("[Webhook] Payload received")

    # Only handle user messages
    event_type = body.get("event")
    message_from = body.get("data", {}).get("from")
    if event_type != "message:send" or message_from != "user":
        print(f"Ignored: event={event_type}, from={message_from}")
        return {"ok": True, "note": "Ignored non-user message"}

    try:
        user_message = body["data"]["content"]
        session_id = body["data"]["session_id"]
    except KeyError:
        print("❌ Invalid payload format")
        return {"ok": False, "error": "Invalid payload"}

    print(f"[User] {user_message} (session: {session_id})")

    # Deduplication check
    now = time()
    last_msg, last_time = last_user_message.get(session_id, ("", 0))
    if user_message == last_msg and (now - last_time) < DEDUPLICATION_TIMEOUT:
        print("[Deduplication] Skipped duplicate message")
        return {"ok": True, "note": "Duplicate message ignored"}

    # Cache latest message
    last_user_message[session_id] = (user_message, now)

    # Get Gemini AI reply
    reply = get_ai_reply(user_message)

    if reply and reply.strip():
        print("[AI] Reply:", reply)
        send_crisp_message(session_id, reply)
        return {"ok": True, "note": "Replied via Gemini AI"}
    else:
        if session_id not in fallback_sessions:
            print("[AI] No valid reply. Fallback initiated.")
            fallback_sessions.add(session_id)
            send_crisp_message(session_id, "Let me connect you to a support person 🔄")
            send_slack_alert(f"User said: \"{user_message}\"\nSession: {session_id}")
        else:
            print("[Fallback] Already escalated this session.")

        return {"ok": True, "note": "Fallback response sent"}

