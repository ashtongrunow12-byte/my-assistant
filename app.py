# redeploy trigger
from flask import Flask, redirect, request, session
import os
import json
import datetime
import secrets
import requests
from google_auth_oauthlib.flow import Flow
from googleapiclient.discovery import build

app = Flask(__name__)
app.secret_key = os.environ.get("FLASK_SECRET_KEY", "dev-secret-change-this")

TELEGRAM_TOKEN = os.environ.get("TELEGRAM_TOKEN")
CHAT_ID = os.environ.get("CHAT_ID")
GOOGLE_CREDENTIALS = os.environ.get("GOOGLE_CREDENTIALS")

SCOPES = ["https://www.googleapis.com/auth/calendar.readonly"]
REDIRECT_URI = "https://my-assistant-production-2fe1.up.railway.app/oauth2callback"


def send_telegram(message):
    if not TELEGRAM_TOKEN or not CHAT_ID:
        print("Telegram variables are missing.")
        return

    url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
    response = requests.post(
        url,
        json={"chat_id": CHAT_ID, "text": message},
        timeout=15
    )
    print("Telegram status:", response.status_code)
    print("Telegram response:", response.text)


def get_flow(state=None, code_verifier=None):
    if not GOOGLE_CREDENTIALS:
        raise ValueError("GOOGLE_CREDENTIALS environment variable is missing.")

    creds_dict = json.loads(GOOGLE_CREDENTIALS)

    flow = Flow.from_client_config(
        creds_dict,
        scopes=SCOPES,
        redirect_uri=REDIRECT_URI,
        state=state
    )

    if code_verifier:
        flow.code_verifier = code_verifier

    return flow


@app.route("/")
def home():
    return '<a href="/login">Click here to connect Google Calendar</a>'


@app.route('/brief')
def manual_brief():
    now = datetime.datetime.now()
    greeting = f"🌅 Good morning Ashton! Here's your briefing for today:\n\n"
    send_telegram(greeting)
    return 'Briefing sent! Check Telegram!'


@app.route("/login")
def login():
    code_verifier = secrets.token_urlsafe(64)
    session["code_verifier"] = code_verifier

    flow = get_flow(code_verifier=code_verifier)
    auth_url, state = flow.authorization_url(
        access_type="offline",
        include_granted_scopes="true",
        code_challenge_method="S256"
    )

    session["state"] = state
    return redirect(auth_url)


@app.route('/oauth2callback')
def oauth2callback():
    if "state" not in session or "code_verifier" not in session:
        return "Session expired...", 400

    flow = get_flow(
        state=session["state"],
        code_verifier=session["code_verifier"]
    )

    flow.fetch_token(authorization_response=request.url)

    creds = flow.credentials
    service = build("calendar", "v3", credentials=creds)

    now = datetime.datetime.utcnow().replace(hour=0, minute=0, second=0).isoformat() + "Z"
    events_result = service.events().list(
        calendarId="primary",
        timeMin=now,
        maxResults=5,
        singleEvents=True,
        orderBy="startTime"
    ).execute()

    events = events_result.get("items", [])

    if not events:
        send_telegram("No upcoming events found!")
    else:
        msg = "📅 Your next events:\n"
        for event in events:
            start = event["start"].get("dateTime", event["start"].get("date"))
            summary = event.get("summary", "No Title")

            dt = datetime.datetime.fromisoformat(start.replace("Z", ""))
            formatted_time = dt.strftime("%I:%M %p")

            msg += f"• {summary} at {formatted_time}\n"
        send_telegram(msg)

    return "Calendar checked. Check your Telegram!"             



if __name__ == "__main__":
    port = int(os.environ.get("PORT", 8080))
    app.run(host="0.0.0.0", port=port, debug=False)