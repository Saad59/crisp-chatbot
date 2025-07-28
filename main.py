from fastapi import FastAPI, Request
import requests, os
from dotenv import load_dotenv

load_dotenv()
app = FastAPI()

OPENROUTER_KEY = os.getenv("OPENROUTER_KEY")
SLACK_WEBHOOK_URL = os.getenv("SLACK_WEBHOOK_URL")
CRISP_ID = os.getenv("CRISP_IDENTIFIER")
CRISP_KEY = os.getenv("CRISP_KEY")

@app.get("/")
def root():
    return {"message": "✅ Crisp Chatbot is running"}

@app.post("/crisp-webhook")
async def crisp_webhook(request: Request):
    try:
        body = await request.json()
        data = body.get("data", {})
        message = data.get("content")
        session_id = data.get("session_id")
        user_info = data.get("user", {})
        email = user_info.get("email", "unknown")

        print(f"📩 Message from {email}: {message}")

        # Fallback to Slack if user requests human support
        if "human" in message.lower():
            send_slack_alert(email, message)
            return {"ok": True, "note": "Sent to human support"}

        # Get AI reply
        reply = get_ai_reply(message)

        # If OpenRouter failed, escalate to Slack
        if "sorry" in reply.lower():
            send_slack_alert(email, message)

        send_crisp_reply(session_id, reply)
        return {"ok": True, "note": "Replied via AI"}

    except Exception as e:
        print("❌ Error in /crisp-webhook:", e)
        return {"ok": False, "error": str(e)}

def get_ai_reply(user_input):
    headers = {
        "Authorization": f"Bearer {OPENROUTER_KEY}",
        "Content-Type": "application/json",
    }
    body = {
        "model": "openrouter/mixtral-8x7b-instruct",
        "messages": [{"role": "user", "content": user_input}],
    }

    try:
        response = requests.post("https://openrouter.ai/api/v1/chat/completions", headers=headers, json=body)
        response.raise_for_status()
        return response.json()["choices"][0]["message"]["content"]
    except Exception as e:
        print("❌ OpenRouter API failed:", e)
        return "Sorry, I'm having trouble answering right now."

def send_crisp_reply(session_id, reply):
    url = f"https://api.crisp.chat/v1/webhooks/session/{session_id}/message/send"
    payload = {
        "type": "text",
        "from": "operator",
        "origin": "chat",
        "content": reply
    }
    try:
        r = requests.post(url, auth=(CRISP_ID, CRISP_KEY), json=payload)
        r.raise_for_status()
        print("✅ Reply sent to Crisp")
    except Exception as e:
        print("❌ Failed to send reply to Crisp:", e)

def send_slack_alert(email, message):
    text = f"*🆘 Human Support Requested*\n• Email: `{email}`\n• Message: `{message}`"
    try:
        r = requests.post(SLACK_WEBHOOK_URL, json={"text": text})
        r.raise_for_status()
        print("✅ Slack alert sent")
    except Exception as e:
        print("❌ Failed to send Slack alert:", e)
