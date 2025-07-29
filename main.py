# main.py

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

# Env vars
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY")
SLACK_WEBHOOK_URL = os.getenv("SLACK_WEBHOOK_URL")
CRISP_WEBSITE_ID = os.getenv("CRISP_WEBSITE_ID")
CRISP_TOKEN_ID = os.getenv("CRISP_TOKEN_ID")
CRISP_TOKEN_KEY = os.getenv("CRISP_TOKEN_KEY")

PURIFYX_CONTEXT = """
PurifyX is an AI-powered platform for lead generation, data enrichment, and outreach automation.

🔹 You can use PurifyX to:
- Find targeted B2B leads with emails and phone numbers
- Enrich incomplete lead data using company names, domains, or LinkedIn URLs
- Automate personalized outreach (coming soon)
- Export verified leads in CSV format
- Filter by company size, industry, funding, revenue, tech stack, and more

🔹 Relevant links:
Website: https://www.purifyx.ai
Pricing: https://www.purifyx.ai/pricing
Contact: https://www.purifyx.ai/contact
"""

# Memory
last_user_message = {}
DEDUPLICATION_TIMEOUT = 10
awaiting_issue = set()
fallback_sessions = set()
session_emails = {}

def match_intent(message: str, target: str) -> bool:
    return fuzz.partial_ratio(message.lower(), target.lower()) > 85

def is_greeting(msg: str):
    return any(fuzz.ratio(msg.lower(), g) > 85 for g in ["hi", "hello", "hey", "hy", "yo", "sup"])

def get_ai_reply(user_message: str):
    if not GEMINI_API_KEY:
        return None
    url = f"https://generativelanguage.googleapis.com/v1beta/models/gemini-1.5-pro:generateContent?key={GEMINI_API_KEY}"
    headers = {"Content-Type": "application/json"}
    prompt = {
        "contents": [{
            "parts": [{
                "text": f"""
You are an intelligent, friendly AI assistant for PurifyX.

Context:
{PURIFYX_CONTEXT}

Instructions:
- If the user says something vague like "what are you doing?" or "tell me about purifyx", explain what the platform does.
- If user says "talk to human", "need support", "contact team", etc., respond ONLY with: HUMAN_SUPPORT
- If user says "talk to you again", resume normal AI behavior.

User: {user_message}
"""
            }]
        }]
    }
    try:
        res = requests.post(url, headers=headers, json=prompt)
        return res.json()["candidates"][0]["content"]["parts"][0]["text"].strip()
    except:
        return None

def send_crisp_message(session_id: str, message: str):
    try:
        token = base64.b64encode(f"{CRISP_TOKEN_ID}:{CRISP_TOKEN_KEY}".encode()).decode()
        res = requests.post(
            f"https://api.crisp.chat/v1/website/{CRISP_WEBSITE_ID}/conversation/{session_id}/message",
            headers={
                "Authorization": f"Basic {token}",
                "X-Crisp-Tier": "plugin",
                "Content-Type": "application/json"
            },
            json={
                "type": "text",
                "from": "operator",
                "origin": "chat",
                "content": message
            }
        )
        print(f"[CRISP] Sent: {res.status_code}")
        return res.status_code == 200
    except Exception as e:
        print("[CRISP ERROR]", e)

def send_slack_alert(session_id: str, user_email: str, message: str):
    if not SLACK_WEBHOOK_URL: return
    try:
        requests.post(SLACK_WEBHOOK_URL, json={
            "text": f":raising_hand: Support Request\nSession ID: `{session_id}`\nEmail: `{user_email}`\nIssue: {message}"
        })
    except Exception as e:
        print("[SLACK ERROR]", e)

@app.post("/crisp-webhook")
async def handle_crisp_webhook(request: Request):
    body = await request.json()
    print("[Webhook] Payload received")

    event = body.get("event")
    data = body.get("data", {})
    session_id = data.get("session_id")
    message_from = data.get("from")
    user_message = data.get("content", "")
    user_email = (
        session_emails.get(session_id) or
        data.get("visitor", {}).get("email") or
        "unknown"
    )

    # Capture email from session updates
    if event in ["session:set_email", "website:visit"]:
        email = data.get("email") or data.get("visitor", {}).get("email")
        if session_id and email:
            session_emails[session_id] = email
            print(f"[Email Update] {session_id} → {email}")
        return {"ok": True}

    if event != "message:send" or message_from != "user":
        return {"ok": True, "note": "Ignored"}

    print(f"[User] {user_message} (session: {session_id}, email: {user_email})")

    # Save session email
    if session_id and user_email != "unknown":
        session_emails[session_id] = user_email

    # Deduplication
    now = time()
    last_msg, last_time = last_user_message.get(session_id, ("", 0))
    if user_message == last_msg and (now - last_time) < DEDUPLICATION_TIMEOUT:
        print("[Duplicate] Skipped")
        return {"ok": True}
    last_user_message[session_id] = (user_message, now)

    # Resume AI if user says “talk to you”
    if match_intent(user_message, "talk to you") or match_intent(user_message, "want to talk to bot"):
        fallback_sessions.discard(session_id)
        send_crisp_message(session_id, "I’m back 😊 What would you like help with now?")
        return {"ok": True}

    # Greeting
    if is_greeting(user_message):
        send_crisp_message(session_id, "Hi there 👋 I’m your AI assistant at PurifyX. What can I help you with today?")
        return {"ok": True}

    # Already escalated
    if session_id in fallback_sessions:
        return {"ok": True}

    # AI Response
    reply = get_ai_reply(user_message)

    if reply == "HUMAN_SUPPORT":
        awaiting_issue.add(session_id)
        send_crisp_message(session_id, "Sorry, I don’t have info on that. Could you describe the issue and share your email?")
        return {"ok": True}

    # If already awaiting issue
    if session_id in awaiting_issue:
        if "@" in user_message and "." in user_message:
            session_emails[session_id] = user_message
            user_email = user_message
            return {"ok": True, "note": "Email captured"}
        if user_email != "unknown":
            awaiting_issue.remove(session_id)
            fallback_sessions.add(session_id)
            send_crisp_message(session_id, "Thanks! A human support agent will assist you shortly 🔄")
            send_slack_alert(session_id, user_email, user_message)
            return {"ok": True, "note": "Escalated to human"}
        else:
            send_crisp_message(session_id, "Thanks. Please also share your email so our support team can follow up.")
            return {"ok": True, "note": "Waiting for email"}

    if reply and len(reply.strip()) > 3:
        send_crisp_message(session_id, reply)
        return {"ok": True, "note": "AI answered"}

    # If no reply
    awaiting_issue.add(session_id)
    send_crisp_message(session_id, "Sorry, I don’t have info on that. Could you describe the issue and share your email?")
    return {"ok": True}

@app.get("/")
def root():
    return {"message": "Crisp Chatbot API running"}

@app.post("/chat")
def chat(payload: MsgPayload):
    msg_payloads_collection.insert_one(payload.dict())
    return {"reply": f"Received message: '{payload.content}' from {payload.user_type}"}
