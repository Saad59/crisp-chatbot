from fastapi import FastAPI, Request
import requests, os
from dotenv import load_dotenv

load_dotenv()
app = FastAPI()

OPENROUTER_KEY = os.getenv("OPENROUTER_KEY")
SLACK_WEBHOOK_URL = os.getenv("SLACK_WEBHOOK_URL")
CRISP_ID = os.getenv("CRISP_IDENTIFIER")
CRISP_KEY = os.getenv("CRISP_KEY")

@app.post("/crisp-webhook")
async def crisp_webhook(request: Request):
    body = await request.json()
    try:
        message = body["data"]["content"]
        session_id = body["data"]["session_id"]
        email = body["data"]["user"]["email"]

        print(f"📩 Message from {email}: {message}")

        if "human" in message.lower():
            send_slack_alert(email, message)
            return {"ok": True}

        reply = get_ai_reply(message)
        send_crisp_reply(session_id, reply)
    except Exception as e:
        print("Error:", e)

    return {"ok": True}

def get_ai_reply(user_input):
    headers = {
        "Authorization": f"Bearer {OPENROUTER_KEY}",
        "Content-Type": "application/json",
    }
    body = {
        "model": "openrouter/mixtral-8x7b-instruct",
        "messages": [{"role": "user", "content": user_input}],
    }
    response = requests.post("https://openrouter.ai/api/v1/chat/completions", headers=headers, json=body)
    return response.json()["choices"][0]["message"]["content"]

def send_crisp_reply(session_id, reply):
    url = f"https://api.crisp.chat/v1/webhooks/session/{session_id}/message/send"
    payload = {
        "type": "text",
        "from": "operator",
        "origin": "chat",
        "content": reply
    }
    requests.post(url, auth=(CRISP_ID, CRISP_KEY), json=payload)

def send_slack_alert(email, message):
    text = f"*🆘 User requesting human support!*\nEmail: `{email}`\nMessage: `{message}`"
    requests.post(SLACK_WEBHOOK_URL, json={"text": text})
