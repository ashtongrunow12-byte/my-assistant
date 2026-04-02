from flask import Flask, redirect, request, session
import os
import json
import requests
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

def get_credentials_file():
    creds_json = os.environ.get('GOOGLE_CREDENTIALS')
    tmp = tempfile.NamedTemporaryFile(mode='w', suffix='.json', delete=False)
    tmp.write(creds_json)
    tmp.close()
    return tmp.name

@app.route('/')
def home():
    return '<a href="/login">Click here to connect Google Calendar</a>'

@app.route('/login')
def login():
    creds_file = get_credentials_file()
    flow = Flow.from_client_secrets_file(
        creds_file,
        scopes=SCOPES,
        redirect_uri=REDIRECT_URI
    )
    flow.code_challenge_method = None
    auth_url, state = flow.authorization_url(
        access_type='offline',
        include_granted_scopes='true'
    )
    session['state'] = state
    session.modified = True
    return redirect(auth_url)

@app.route('/oauth2callback')
def callback():
    creds_file = get_credentials_file()
    state = request.args.get('state')
    flow = Flow.from_client_secrets_file(
        creds_file,
        scopes=SCOPES,
        redirect_uri=REDIRECT_URI,
        state=state
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