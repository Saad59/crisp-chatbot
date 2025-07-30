from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
import requests
import base64
import os
import re
from dotenv import load_dotenv
from time import time
from models import MsgPayload
from database import msg_payloads_collection

load_dotenv()

app = FastAPI()
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

GEMINI_API_KEY = os.getenv("GEMINI_API_KEY")
SLACK_WEBHOOK_URL = os.getenv("SLACK_WEBHOOK_URL")
CRISP_WEBSITE_ID = os.getenv("CRISP_WEBSITE_ID")
CRISP_TOKEN_ID = os.getenv("CRISP_TOKEN_ID")
CRISP_TOKEN_KEY = os.getenv("CRISP_TOKEN_KEY")

with open("purifyx_context.txt", "r", encoding="utf-8") as f:
    PURIFYX_CONTEXT = f.read()

@app.get("/")
def root():
    return {"message": "Welcome to Crisp Chatbot"}

@app.post("/messages/{msg_name}/")
def add_msg(msg_name: str):
    return {"message": f"Added: {msg_name}"}

@app.get("/messages")
def message_items():
    return {"messages": "List"}

@app.post("/chat")
def chat(payload: MsgPayload):
    payload_dict = payload.dict()
    msg_payloads_collection.insert_one(payload_dict)
    return {"reply": f"Received message: '{payload.content}' from {payload.user_type}"}

# In-memory session state
session_state = {}  # session_id -> {'email': ..., 'issue': ...}

def is_valid_email(email: str):
    return re.match(r"[^@]+@[^@]+\.[^@]+", email)

def extract_email(text: str):
    match = re.search(r"[^@\s]+@[^@\s]+\.[^@\s]+", text)
    return match.group() if match else None

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

def send_slack_alert(session_id: str, user_email: str, message: str):
    if not SLACK_WEBHOOK_URL:
        return
    try:
        payload = {
            "text": f"\ud83d\udce9 *New Support Request*\n\n👤 Email: {user_email}\n💬 Issue: {message}\n🔗 Session ID: {session_id}"
        }
        requests.post(SLACK_WEBHOOK_URL, json=payload)
        print("[SLACK] Alert sent.")
    except Exception as e:
        print("[SLACK] Error:", e)

@app.post("/crisp-webhook")
async def handle_crisp_webhook(request: Request):
    body = await request.json()
    event = body.get("event")
    data = body.get("data", {})
    session_id = data.get("session_id")
    message = data.get("content", "").strip()

    if event != "message:send" or data.get("from") != "user":
        return {"ok": True}

    if not session_id or not message:
        return {"ok": False, "error": "Missing session_id or message"}

    state = session_state.get(session_id, {"email": None, "issue": None})

    # Step 1 - Extract email
    email_candidate = extract_email(message)
    if email_candidate and is_valid_email(email_candidate):
        state["email"] = email_candidate
    elif not state["email"]:
        send_crisp_message(session_id, "Please share your email so we can assist you.")
        session_state[session_id] = state
        return {"ok": True}

    # Step 2 - Extract issue (if not already set)
    if not state["issue"] and not email_candidate:
        state["issue"] = message
        session_state[session_id] = state

    # Step 3 - Escalate to Slack
    if state["email"] and state["issue"]:
        send_slack_alert(session_id, state["email"], state["issue"])
        send_crisp_message(session_id, f"Thanks! ✅ Your message has been shared with our support team. They'll reach out to you soon at: {state['email']}")
        session_state.pop(session_id, None)
        return {"ok": True}

    # Step 4 - Ask for missing info
    if not state["email"]:
        send_crisp_message(session_id, "Could you please share your email so our support team can reach out to you?")
    elif not state["issue"]:
        send_crisp_message(session_id, "Thanks! Now please describe the issue you're facing so we can help.")

    session_state[session_id] = state
    return {"ok": True}