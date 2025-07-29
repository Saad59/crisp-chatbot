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

@app.get("/")
def root() -> dict[str, str]:
    return {"message": "Welcome to Crisp Chatbot"}

@app.get("/about")
def about() -> dict[str, str]:
    return {"message": "This is the about page."}

@app.post("/messages/{msg_name}/")
def add_msg(msg_name: str) -> dict[str, MsgPayload]:
    msg_id = max(messages_list.keys()) + 1 if messages_list else 0
    messages_list[msg_id] = MsgPayload(msg_id=msg_id, msg_name=msg_name)
    return {"message": messages_list[msg_id]}

@app.get("/messages")
def message_items() -> dict[str, dict[int, MsgPayload]]:
    return {"messages:": messages_list}

@app.post("/chat")
def chat(payload: MsgPayload):
    payload_dict = payload.dict()
    msg_payloads_collection.insert_one(payload_dict)
    return {"reply": f"Received message: '{payload.content}' from {payload.user_type}"}

# Memory
last_user_message = {}
DEDUPLICATION_TIMEOUT = 10
awaiting_issue = set()
fallback_sessions = set()
session_emails = {}

# Intent matcher
def match_intent(message: str, target: str) -> bool:
    return fuzz.partial_ratio(message.lower(), target.lower()) > 85

def get_ai_reply(user_message: str):
    if not GEMINI_API_KEY:
        return None
    url = f"https://generativelanguage.googleapis.com/v1beta/models/gemini-1.5-flash:generateContent?key={GEMINI_API_KEY}"
    headers = {"Content-Type": "application/json"}
    prompt = {
        "contents": [
            {"parts": [{"text": f"""You are an AI customer support assistant for PurifyX.

Context:
{PURIFYX_CONTEXT}

If the user asks to speak with support or says anything like \"contact support\", \"talk to human\", or \"need help\", do not try to answer. Instead, just reply with: HUMAN_SUPPORT.

Otherwise, help them with their question.

User: {user_message}"""}]}
        ]
    }
    try:
        response = requests.post(url, headers=headers, json=prompt)
        if response.status_code == 200:
            data = response.json()
            return data["candidates"][0]["content"]["parts"][0]["text"].strip()
    except Exception:
        pass
    return None

def send_crisp_message(session_id: str, message: str):
    url = f"https://api.crisp.chat/v1/website/{CRISP_WEBSITE_ID}/conversation/{session_id}/message"
    token = base64.b64encode(f"{CRISP_TOKEN_ID}:{CRISP_TOKEN_KEY}".encode()).decode()
    headers = {
        "Authorization": f"Basic {token}",
        "X-Crisp-Tier": "plugin",
        "Content-Type": "application/json"
    }
    payload = {"type": "text", "from": "operator", "origin": "chat", "content": message}
    try:
        return requests.post(url, headers=headers, json=payload).status_code == 200
    except Exception:
        return False

def send_slack_alert(session_id: str, user_email: str, message: str):
    if SLACK_WEBHOOK_URL:
        payload = {
            "text": f":raising_hand: *Support Request*\nSession ID: `{session_id}`\nEmail: `{user_email}`\nIssue: {message}"
        }
        try:
            requests.post(SLACK_WEBHOOK_URL, json=payload)
        except Exception:
            pass

@app.post("/crisp-webhook")
async def handle_crisp_webhook(request: Request):
    body = await request.json()
    event, data = body.get("event"), body.get("data", {})
    message_from, session_id = data.get("from"), data.get("session_id")

    if event in ["session:set_email", "website:visit"]:
        email = data.get("email") or data.get("visitor", {}).get("email")
        if session_id and email:
            session_emails[session_id] = email
        return {"ok": True, "note": "Email captured"}

    if event != "message:send" or message_from != "user":
        return {"ok": True, "note": "Ignored non-user message"}

    user_message = data.get("content", "")
    user_email = session_emails.get(session_id, "unknown")

    if session_id and user_email != "unknown":
        session_emails[session_id] = user_email

    if not session_id or not user_message:
        return {"ok": False, "error": "Missing session or message"}

    now = time()
    last_msg, last_time = last_user_message.get(session_id, ("", 0))
    if user_message == last_msg and (now - last_time) < DEDUPLICATION_TIMEOUT:
        return {"ok": True, "note": "Duplicate ignored"}
    last_user_message[session_id] = (user_message, now)

    if session_id in awaiting_issue:
        awaiting_issue.remove(session_id)
        fallback_sessions.add(session_id)
        send_crisp_message(session_id, "Thanks! A human support agent will assist you shortly 🔄")
        send_slack_alert(session_id, user_email, user_message)
        return {"ok": True, "note": "Escalated to human"}

    if session_id in fallback_sessions:
        if match_intent(user_message, "I want to talk to you"):
            fallback_sessions.remove(session_id)
            send_crisp_message(session_id, "Sure, I’m back! What would you like help with?")
            return {"ok": True, "note": "Bot re-activated"}
        return {"ok": True, "note": "Already escalated"}

    if match_intent(user_message, "support") or match_intent(user_message, "contact"):
        awaiting_issue.add(session_id)
        send_crisp_message(session_id, "Can you please describe the issue you're facing?")
        return {"ok": True, "note": "Support intent detected"}

    reply = get_ai_reply(user_message)

    if reply == "HUMAN_SUPPORT":
        awaiting_issue.add(session_id)
        send_crisp_message(session_id, "Can you please describe the issue you're facing?")
        return {"ok": True, "note": "AI requested escalation"}
    elif reply:
        send_crisp_message(session_id, reply)
        return {"ok": True, "note": "Answered via AI"}
    else:
        fallback_sessions.add(session_id)
        send_crisp_message(session_id, "Let me connect you to a support person 🔄")
        send_slack_alert(session_id, user_email, user_message)
        return {"ok": True, "note": "Fallback triggered"}
