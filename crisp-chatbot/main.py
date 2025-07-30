from fastapi import FastAPI, Request
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

with open("purifyx_context.txt", "r", encoding="utf-8") as f:
    PURIFYX_CONTEXT = f.read()

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

# In-memory session state
last_user_message = {}
DEDUPLICATION_TIMEOUT = 10
awaiting_issue = {}     # session_id -> True
awaiting_email = {}     # session_id -> issue string
session_emails = {}
escalated_sessions = set()  # sessions already escalated to avoid fallback reuse

def match_intent(message: str, target: str) -> bool:
    return fuzz.partial_ratio(message.lower(), target.lower()) > 85

def is_greeting(message: str) -> bool:
    greetings = ["hi", "hello", "hey", "hy", "yo", "sup"]
    return any(fuzz.ratio(message.lower(), g) > 85 for g in greetings)

def get_ai_reply(user_message: str):
    if not GEMINI_API_KEY:
        return None

    url = f"https://generativelanguage.googleapis.com/v1beta/models/gemini-1.5-flash:generateContent?key={GEMINI_API_KEY}"
    headers = {"Content-Type": "application/json"}

    prompt = {
        "contents": [
            {
                "parts": [
                    {
                        "text": f"""You are an AI customer support assistant for PurifyX.

Context:
{PURIFYX_CONTEXT}

If the user asks to speak with support or says anything like \"contact support\", \"talk to human\", or \"need help\", do not try to answer. Instead, just reply with: HUMAN_SUPPORT.

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
            "text": f"\ud83d\ude4b *Support Request*\nSession ID: `{session_id}`\nEmail: `{user_email}`\nIssue: {message}"
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
            session_emails[session_id] = email
        return {"ok": True, "note": f"Email updated from {event}"}

    if event != "message:send" or message_from != "user":
        return {"ok": True, "note": "Ignored non-user message"}

    user_message = data.get("content", "").strip()
    user_email = (
        session_emails.get(session_id)
        or data.get("visitor", {}).get("email")
        or "unknown"
    )

    if not session_id or not user_message:
        return {"ok": False, "error": "Missing session or message"}

    now = time()
    last_msg, last_time = last_user_message.get(session_id, ("", 0))
    if user_message == last_msg and (now - last_time) < DEDUPLICATION_TIMEOUT:
        return {"ok": True, "note": "Duplicate ignored"}
    last_user_message[session_id] = (user_message, now)

    if is_greeting(user_message):
        send_crisp_message(session_id, "Hi there \ud83d\udc4b I\u2019m your AI assistant at PurifyX. What can I help you with today?")
        return {"ok": True}

    if match_intent(user_message, "talk to you"):
        awaiting_issue.pop(session_id, None)
        awaiting_email.pop(session_id, None)
        escalated_sessions.discard(session_id)
        send_crisp_message(session_id, "I\u2019m back \ud83d\ude0a What would you like help with now?")
        return {"ok": True}

    if match_intent(user_message, "support") or match_intent(user_message, "contact") or match_intent(user_message, "human") or "credits" in user_message.lower():
        if session_id not in awaiting_issue and session_id not in awaiting_email and session_id not in escalated_sessions:
            awaiting_issue[session_id] = True
            send_crisp_message(session_id, "Got it! Could you please explain the issue you're facing?")
            return {"ok": True}

    if awaiting_issue.get(session_id):
        awaiting_issue.pop(session_id)
        awaiting_email[session_id] = user_message
        if user_email == "unknown":
            send_crisp_message(session_id, "Thanks for the details. Could you please share your email so our support team can reach you?")
            return {"ok": True}
        else:
            issue = awaiting_email.pop(session_id)
            send_slack_alert(session_id, user_email, issue)
            escalated_sessions.add(session_id)
            awaiting_issue.pop(session_id, None)
            last_user_message.pop(session_id, None)
            send_crisp_message(session_id, "Thanks! I\u2019ve alerted our support team. They\u2019ll reach out to you shortly \ud83d\udd04")
            return {"ok": True, "note": "Escalated with issue"}

    if awaiting_email.get(session_id) and user_email != "unknown":
        issue = awaiting_email.pop(session_id)
        send_slack_alert(session_id, user_email, issue)
        escalated_sessions.add(session_id)
        awaiting_issue.pop(session_id, None)
        last_user_message.pop(session_id, None)
        send_crisp_message(session_id, "Thanks! I\u2019ve alerted our support team. They\u2019ll reach out to you shortly \ud83d\udd04")
        return {"ok": True, "note": "Escalated with email"}

    if session_id in escalated_sessions:
        return {"ok": True, "note": "Escalation complete, skipping fallback"}

    reply = get_ai_reply(user_message)
    if reply == "HUMAN_SUPPORT":
        if session_id not in awaiting_issue and session_id not in awaiting_email:
            awaiting_issue[session_id] = True
            send_crisp_message(session_id, "Sure. Could you please describe your issue?")
            return {"ok": True, "note": "Asked for issue"}
    elif reply:
        send_crisp_message(session_id, reply)
        return {"ok": True, "note": "AI answered"}
    else:
        awaiting_issue[session_id] = True
        send_crisp_message(session_id, "I couldn\u2019t quite get that. Could you please describe the issue you're facing?")
        return {"ok": True, "note": "Fallback AI prompt"}
