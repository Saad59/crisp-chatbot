from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
import requests
import base64
import os
from dotenv import load_dotenv
from datetime import datetime
import re

load_dotenv()

app = FastAPI()

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

SLACK_WEBHOOK_URL = os.getenv("SLACK_WEBHOOK_URL")
CRISP_WEBSITE_ID = os.getenv("CRISP_WEBSITE_ID")
CRISP_TOKEN_ID = os.getenv("CRISP_TOKEN_ID")
CRISP_TOKEN_KEY = os.getenv("CRISP_TOKEN_KEY")

# In-memory session state
session_state = {}  # session_id: {email: ..., issue: ...}

EMAIL_REGEX = re.compile(r"[^@\s]+@[^@\s]+\.[^@\s]+")

def is_valid_email(email: str) -> bool:
    return bool(EMAIL_REGEX.fullmatch(email.strip()))

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
        requests.post(url, headers=headers, json=payload)
    except Exception as e:
        print("[CRISP] Send error:", e)

def send_slack_alert(email: str, issue: str):
    if not SLACK_WEBHOOK_URL:
        return
    try:
        now = datetime.now().strftime("%d %B %Y, %-I:%M %p PKT")
        payload = {
            "text": f"\ud83d\udce9 *New Support Request*\n\n\ud83d\udc64 Email: {email}\n\ud83d\udcac Issue: {issue}\n\n\u23f0 Received: {now}"
        }
        requests.post(SLACK_WEBHOOK_URL, json=payload)
    except Exception as e:
        print("[SLACK] Error:", e)

@app.post("/crisp-webhook")
async def handle_crisp_webhook(request: Request):
    body = await request.json()
    event = body.get("event")
    data = body.get("data", {})
    session_id = data.get("session_id")
    message_from = data.get("from")

    if not session_id:
        return {"ok": False, "error": "No session ID"}

    if event != "message:send" or message_from != "user":
        return {"ok": True, "note": "Non-user message ignored"}

    user_msg = data.get("content", "").strip()
    if not user_msg:
        return {"ok": False, "error": "No message content"}

    state = session_state.setdefault(session_id, {"email": None, "issue": None})

    # CASE A: email + issue in one
    if is_valid_email(user_msg) and len(user_msg.split()) > 4:
        state["email"] = user_msg
        state["issue"] = user_msg
    elif is_valid_email(user_msg):
        state["email"] = user_msg
    elif not state["issue"]:
        state["issue"] = user_msg

    # EVALUATION
    if not state["email"] and not state["issue"]:
        send_crisp_message(session_id, "Hi! 👋 I’m here to help you with any support issues.\n\nTo get started, could you please tell me:\n1. Your email address 📧\n2. A short message about your issue or question 📝")
    elif not state["email"]:
        send_crisp_message(session_id, "Thanks! Can you also share your email address so our support team can reach out to you?")
    elif not is_valid_email(state["email"]):
        state["email"] = None
        send_crisp_message(session_id, "Hmm, that doesn't look like a valid email. Could you please check and send a valid email address?")
    elif not state["issue"]:
        send_crisp_message(session_id, "Thanks! Could you now describe the issue you’re facing so we can assist you?")
    else:
        send_slack_alert(state["email"], state["issue"])
        send_crisp_message(session_id, f"Thanks! ✅\n\nYour message has been shared with our support team. They’ll reach out to you soon at: {state['email']}\n\nIf you have more details to add, feel free to reply anytime. 😊")
        session_state.pop(session_id, None)  # Clean up

    return {"ok": True, "state": state}
