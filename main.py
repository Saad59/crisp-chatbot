from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
import requests
import os
from dotenv import load_dotenv

load_dotenv()

app = FastAPI()

# CORS setup
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

# Send a message via Crisp
def send_crisp_message(session_id: str, message: str):
    url = f"https://api.crisp.chat/v1/webhooks/session/{session_id}/message/send"
    headers = {
        "Authorization": f"Basic {CRISP_TOKEN_ID}:{CRISP_TOKEN_KEY}",
        "Content-Type": "application/json"
    }
    payload = {
        "type": "text",
        "from": "operator",
        "origin": "chat",
        "content": message
    }
    response = requests.post(url, headers=headers, json=payload)
    print(f"[CRISP] Reply sent: {response.status_code} - {response.text}")
    return response.status_code == 200

# Alert fallback to Slack
def send_slack_alert(message: str):
    if not SLACK_WEBHOOK_URL:
        print("[SLACK] Webhook URL not set")
        return
    payload = { "text": f"🆘 *AI Fallback Alert:*\n{message}" }
    try:
        r = requests.post(SLACK_WEBHOOK_URL, json=payload)
        print(f"[SLACK] Alert sent: {r.status_code}")
    except Exception as e:
        print("[SLACK] Failed to send alert:", e)

# Gemini AI call
def get_ai_reply(user_message: str):
    if not GEMINI_API_KEY:
        print("[GEMINI] API key not set")
        return None

    url = f"https://generativelanguage.googleapis.com/v1beta/models/gemini-1.5-flash:generateContent?key={GEMINI_API_KEY}"
    payload = {
        "contents": [
            {
                "parts": [
                    { "text": f"You are a helpful AI customer support agent.\n\nUser: {user_message}" }
                ]
            }
        ]
    }

    headers = {
        "Content-Type": "application/json"
    }

    response = requests.post(url, json=payload, headers=headers)
    if response.status_code == 200:
        try:
            return response.json()['candidates'][0]['content']['parts'][0]['text']
        except Exception as e:
            print("[GEMINI] Unexpected response structure:", e)
    else:
        print(f"[GEMINI] Error: {response.status_code} - {response.text}")

    return None

# Crisp webhook endpoint
@app.post("/crisp-webhook")
async def handle_crisp_webhook(request: Request):
    body = await request.json()
    print("[Webhook] Payload received")

    try:
        user_message = body["data"]["content"]
        session_id = body["data"]["session_id"]
    except KeyError:
        print("❌ Invalid payload format")
        return {"ok": False, "error": "Invalid payload"}

    print(f"[User] {user_message} (session: {session_id})")

    reply = get_ai_reply(user_message)

    if reply:
        print("[AI] Reply:", reply)
        send_crisp_message(session_id, reply)
        return { "ok": True, "note": "Replied via Gemini AI" }
    else:
        print("[AI] No reply generated. Sending to human support...")
        send_slack_alert(f"User said: \"{user_message}\"\nSession: {session_id}")
        send_crisp_message(session_id, "Let me connect you to a support person 🔄")
        return { "ok": True, "note": "Sent to human support" }
