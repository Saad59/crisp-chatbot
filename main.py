from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
import requests
import os
from dotenv import load_dotenv

load_dotenv()

app = FastAPI()

# Allow CORS
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # Adjust for production
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Env variables
OPENROUTER_API_KEY = os.getenv("OPENROUTER_API_KEY")
SLACK_WEBHOOK_URL = os.getenv("SLACK_WEBHOOK_URL")
CRISP_WEBSITE_ID = os.getenv("CRISP_WEBSITE_ID")
CRISP_TOKEN_ID = os.getenv("CRISP_TOKEN_ID")
CRISP_TOKEN_KEY = os.getenv("CRISP_TOKEN_KEY")

# Crisp reply
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

# Slack alert
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

# OpenRouter AI call
def get_ai_reply(user_message: str):
    headers = {
        "Authorization": f"Bearer {OPENROUTER_API_KEY}",
        "Content-Type": "application/json"
    }
    data = {
        "model": "mistralai/mixtral-8x7b-instruct",
        "messages": [
            { "role": "system", "content": "You are a helpful AI customer support agent." },
            { "role": "user", "content": user_message }
        ]
    }
    r = requests.post("https://openrouter.ai/api/v1/chat/completions", headers=headers, json=data)
    if r.status_code == 200:
        return r.json()["choices"][0]["message"]["content"]
    print("[AI] Failed response:", r.text)
    return None

# Crisp webhook
@app.post("/crisp-webhook")
async def handle_crisp_webhook(request: Request):
    body = await request.json()
    print("[Webhook] Payload received")

    # Extract message and session
    try:
        user_message = body["data"]["content"]
        session_id = body["data"]["session_id"]
    except KeyError:
        print("❌ Invalid payload format")
        return {"ok": False, "error": "Invalid payload"}

    print(f"[User] {user_message} (session: {session_id})")

    # Get AI reply
    reply = get_ai_reply(user_message)

    if reply:
        print("[AI] Reply:", reply)
        send_crisp_message(session_id, reply)
        return { "ok": True, "note": "Replied via AI" }
    else:
        print("[AI] No reply generated. Sending to human support...")
        send_slack_alert(f"User said: \"{user_message}\"\nSession: {session_id}")
        send_crisp_message(session_id, "Let me connect you to a support person 🔄")
        return { "ok": True, "note": "Sent to human support" }
