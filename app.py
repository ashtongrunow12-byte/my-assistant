from flask import Flask, redirect, request, session
import os
import json
import requests
import secrets
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import Flow
from googleapiclient.discovery import build
import datetime
import tempfile

app = Flask(__name__)
app.secret_key = os.urandom(24)

TELEGRAM_TOKEN = "8127824873:AAHCEOLuDHvmh22Ospprnyn4zi-BYUzq6nE"
CHAT_ID = "8798214200"

SCOPES = ['https://www.googleapis.com/auth/calendar.readonly']
REDIRECT_URI = 'https://my-assistant-production-2fe1.up.railway.app/oauth2callback'

def send_telegram(message):
    url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
    requests.post(url, json={"chat_id": CHAT_ID, "text": message})

def get_flow(state=None, code_verifier=None):
    creds_json = os.environ.get("GOOGLE_CREDENTIALS")
    creds_dict = json.loads(creds_json)

    flow = Flow.from_client_config(
        creds_dict,
        scopes=['https://www.googleapis.com/auth/calendar.readonly'],
        redirect_uri=REDIRECT_URI,
        state=state
    )

    if code_verifier:
        flow.code_verifier = code_verifier

    return flow

@app.route('/login')
def login():
    code_verifier = secrets.token_urlsafe(64)
    session['code_verifier'] = code_verifier

    flow = get_flow(code_verifier=code_verifier)
    auth_url, state = flow.authorization_url(
        access_type='offline',
        include_granted_scopes='true',
        code_challenge_method='S256'
    )
    session['state'] = state
    return redirect(auth_url)

@app.route('/oauth2callback')
def oauth2callback():
    flow = get_flow(
        state=session['state'],
        code_verifier=session['code_verifier']
    )
    flow.fetch_token(authorization_response=request.url)

    creds = flow.credentials
    service = build('calendar', 'v3', credentials=creds)

    now = datetime.datetime.utcnow().isoformat() + 'Z'
    events_result = service.events().list(
        calendarId='primary',
        timeMin=now,
        maxResults=5,
        singleEvents=True,
        orderBy='startTime'
    ).execute()

    events = events_result.get('items', [])

    if not events:
        send_telegram('No upcoming events found!')
    else:
        msg = "📅 Your next events:\n"
        for event in events:
            start = event['start'].get('dateTime', event['start'].get('date'))
            msg += f"• {event['summary']} at {start}\n"
        send_telegram(msg)

    return 'Calendar checked! Check your Telegram!'

if __name__ == '__main__':
    port = int(os.environ.get('PORT', 8080))
    app.run(host='0.0.0.0', port=port, debug=False)