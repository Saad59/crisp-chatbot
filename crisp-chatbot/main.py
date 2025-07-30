# from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
import requests
import base64
import os
from dotenv import load_dotenv
from time import time
from models import MsgPayload
from database import msg_payloads_collection
from rapidfuzz import fuzz

load_dotenv()

app = FastAPI()
messages_list: dict[int, MsgPayload] = {}

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

@app.get("/")
def root():
    return {"message": "Welcome to Crisp Chatbot"}

@app.post("/messages/{msg_name}/")
def add_msg(msg_name: str):
    msg_id = max(messages_list.keys()) + 1 if messages_list else 0
    messages_list[msg_id] = MsgPayload(msg_id=msg_id, msg_name=msg_name)
    return {"message": messages_list[msg_id]}

@app.get("/messages")
def message_items():
    return {"messages:": messages_list}

@app.post("/chat")
def chat(payload: MsgPayload):
    payload_dict = payload.dict()
    msg_payloads_collection.insert_one(payload_dict)
    return {"reply": f"Received message: '{payload.content}' from {payload.user_type}"}

# Session state
session_email_collected = set()
session_issue_collected = {}


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
            "text": f"\ud83d\ude4b *New Support Ticket*\nSession ID: `{session_id}`\nEmail: `{user_email}`\nIssue: {message}"
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
    message_from = data.get("from")
    session_id = data.get("session_id")

    if event in ["session:set_email", "website:visit"]:
        email = data.get("email") or data.get("visitor", {}).get("email")
        if session_id and email:
            session_email_collected.add(session_id)
        return {"ok": True, "note": f"Email updated from {event}"}

    if event != "message:send" or message_from != "user":
        return {"ok": True, "note": "Ignored non-user message"}

    user_message = data.get("content", "").strip()
    user_email = data.get("visitor", {}).get("email") or "unknown"

    if not session_id or not user_message:
        return {"ok": False, "error": "Missing session or message"}

    # Step 1: If we don’t have email
    if user_email == "unknown" and session_id not in session_email_collected:
        send_crisp_message(session_id, "Hi there 👋 Could you please share your email address so we can assist you?")
        return {"ok": True, "note": "Requested email"}

    # Step 2: If we don’t have issue
    if session_id not in session_issue_collected:
        session_issue_collected[session_id] = user_message
        if user_email == "unknown":
            send_crisp_message(session_id, "Thanks! Please also share your email address so we can create a support ticket.")
        else:
            send_slack_alert(session_id, user_email, user_message)
            send_crisp_message(session_id, "Thanks! Your request has been sent to our support team. They'll contact you soon 🔄")
        return {"ok": True, "note": "Issue captured"}

    # Step 3: If we already got email + issue, ignore further fallback
    if session_id in session_email_collected and session_id in session_issue_collected:
        return {"ok": True, "note": "Ticket already created"}

    # Final fallback (very rare)
    send_crisp_message(session_id, "Can you please briefly describe the issue you're facing?")
    return {"ok": True, "note": "Fallback asked for issue"}