# redeploy trigger
from flask import Flask, redirect, request, session
import threading
import time
import os
import json
import datetime
import secrets
import requests
from google_auth_oauthlib.flow import Flow
from googleapiclient.discovery import build
from google.oauth2.credentials import Credentials

app = Flask(__name__)
app.secret_key = os.environ.get("FLASK_SECRET_KEY", "dev-secret-change-this")

TELEGRAM_TOKEN = os.environ.get("TELEGRAM_TOKEN")
CHAT_ID = os.environ.get("CHAT_ID")
GOOGLE_CREDENTIALS = os.environ.get("GOOGLE_CREDENTIALS")
GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY")

SCOPES = [
    "https://www.googleapis.com/auth/calendar.readonly",
    "https://www.googleapis.com/auth/gmail.readonly"
]
REDIRECT_URI = "https://my-assistant-production-2fe1.up.railway.app/oauth2callback"

alerted_events = set()

checkin_state = {
    "active": False,
    "question_index": 0,
    "answers": []
}

CHECKIN_QUESTIONS = [
    "💪 Did you work out this week? (yes/no)",
    "🥗 Did you eat healthy most days? (yes/no)",
    "😴 Did you get enough sleep? (yes/no)",
    "🎯 Did you make progress on your goals? (yes/no)",
    "🧠 Did you learn something new this week? (yes/no)",
    "📵 Did you avoid too much mindless phone/doomscrolling? (yes/no)",
    "😊 Overall how was your week? (rate 1-10)"
]

CHECKIN_LABELS = [
    "Worked out",
    "Ate healthy",
    "Got enough sleep",
    "Made progress on goals",
    "Learned something new",
    "Avoided doomscrolling",
    "Week rating"
]


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


def ask_ai(prompt):
    try:
        url = f"https://generativelanguage.googleapis.com/v1beta/models/gemini-2.0-flash:generateContent?key={GEMINI_API_KEY}"
        payload = {
            "contents": [{
                "parts": [{
                    "text": f"You are Ashton's personal AI assistant. You know he lives in Abbotsford BC Canada, he's learning Python and building cool projects, he's working on himself and his health. Be friendly, direct and helpful. Keep responses concise for Telegram.\n\nAshton says: {prompt}"
                }]
            }]
        }
        response = requests.post(url, json=payload, timeout=20)
        data = response.json()
        print("Gemini response:", json.dumps(data))
        
        if "candidates" in data:
            return data["candidates"][0]["content"]["parts"][0]["text"]
        elif "error" in data:
            return f"API error: {data['error']['message']}"
        else:
            return f"Unexpected response: {str(data)[:200]}"
    except Exception as e:
        return f"AI error: {str(e)}"


def get_motivation():
    try:
        response = requests.get("https://zenquotes.io/api/today", timeout=10)
        data = response.json()
        quote = data[0]['q']
        author = data[0]['a']
        return f"💪 Quote of the day:\n\"{quote}\"\n— {author}"
    except:
        return "💪 Keep pushing Ashton, you've got this!"


def get_weather():
    try:
        url = "https://wttr.in/Abbotsford,BC?format=j1"
        response = requests.get(url, timeout=10)
        data = response.json()
        current = data["current_condition"][0]
        temp_c = current["temp_C"]
        feels_like = current["FeelsLikeC"]
        desc = current["weatherDesc"][0]["value"]
        humidity = current["humidity"]

        desc_lower = desc.lower()
        if "sun" in desc_lower or "clear" in desc_lower:
            emoji = "☀️"
        elif "cloud" in desc_lower:
            emoji = "☁️"
        elif "rain" in desc_lower or "drizzle" in desc_lower:
            emoji = "🌧️"
        elif "snow" in desc_lower:
            emoji = "❄️"
        elif "thunder" in desc_lower or "storm" in desc_lower:
            emoji = "⛈️"
        elif "fog" in desc_lower or "mist" in desc_lower:
            emoji = "🌫️"
        else:
            emoji = "🌤️"

        return (
            f"{emoji} Abbotsford Weather:\n"
            f"{desc}, {temp_c}°C (feels like {feels_like}°C)\n"
            f"Humidity: {humidity}%"
        )
    except Exception as e:
        return f"🌤️ Weather unavailable"


def get_calendar_events():
    try:
        creds_json = os.environ.get("GOOGLE_TOKEN")
        if not creds_json:
            return "📅 No calendar connected yet."

        creds_data = json.loads(creds_json)
        creds = Credentials(**creds_data)
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
            return "📅 No events today!"

        msg = "📅 Today's events:\n"
        for event in events:
            start = event["start"].get("dateTime", event["start"].get("date"))
            summary = event.get("summary", "No Title")
            dt = datetime.datetime.fromisoformat(start.replace("Z", ""))
            formatted_time = dt.strftime("%I:%M %p")
            msg += f"• {summary} at {formatted_time}\n"
        return msg
    except Exception as e:
        return f"📅 Calendar error: {str(e)}"


def get_gmail_summary():
    try:
        creds_json = os.environ.get("GOOGLE_TOKEN")
        if not creds_json:
            return "📧 No Gmail connected yet."

        creds_data = json.loads(creds_json)
        creds = Credentials(**creds_data)
        gmail = build("gmail", "v1", credentials=creds)

        results = gmail.users().messages().list(
            userId="me",
            labelIds=["UNREAD", "INBOX"],
            maxResults=5
        ).execute()

        messages = results.get("messages", [])
        if not messages:
            return "📧 No unread emails!"

        msg_summary = "📧 Unread emails:\n"
        for m in messages:
            msg = gmail.users().messages().get(
                userId="me",
                id=m["id"],
                format="metadata",
                metadataHeaders=["From", "Subject"]
            ).execute()

            headers = msg.get("payload", {}).get("headers", [])
            subject = next((h["value"] for h in headers if h["name"] == "Subject"), "No subject")
            sender = next((h["value"] for h in headers if h["name"] == "From"), "Unknown")
            sender = sender.split("<")[0].strip()
            msg_summary += f"• {sender}: {subject}\n"

        return msg_summary
    except Exception as e:
        return f"📧 Gmail error: {str(e)}"


def get_weekly_calendar_recap():
    try:
        creds_json = os.environ.get("GOOGLE_TOKEN")
        if not creds_json:
            return "📅 No calendar connected."

        creds_data = json.loads(creds_json)
        creds = Credentials(**creds_data)
        service = build("calendar", "v3", credentials=creds)

        now = datetime.datetime.utcnow()
        week_ago = now - datetime.timedelta(days=7)

        events_result = service.events().list(
            calendarId="primary",
            timeMin=week_ago.isoformat() + "Z",
            timeMax=now.isoformat() + "Z",
            singleEvents=True,
            orderBy="startTime"
        ).execute()

        events = events_result.get("items", [])
        if not events:
            return "📅 No events this past week."

        msg = "📅 Your week in review:\n"
        for event in events:
            start = event["start"].get("dateTime", event["start"].get("date"))
            summary = event.get("summary", "No Title")
            try:
                dt = datetime.datetime.fromisoformat(start.replace("Z", ""))
                formatted = dt.strftime("%a %b %d at %I:%M %p")
            except:
                formatted = start
            msg += f"• {summary} — {formatted}\n"
        return msg
    except Exception as e:
        return f"📅 Calendar error: {str(e)}"


def start_weekly_checkin():
    checkin_state["active"] = True
    checkin_state["question_index"] = 0
    checkin_state["answers"] = []
    send_telegram("📋 Hey Ashton! Time for your weekly check-in!\n\nLet's see how this week went 💪")
    time.sleep(2)
    send_telegram(CHECKIN_QUESTIONS[0])


def finish_weekly_checkin():
    checkin_state["active"] = False
    answers = checkin_state["answers"]
    labels = CHECKIN_LABELS

    summary = "✅ Weekly Check-in Results:\n\n"
    for i, label in enumerate(labels):
        if i < len(answers):
            answer = answers[i]
            if i == 6:
                summary += f"• {label}: {answer}/10\n"
            else:
                emoji = "✅" if answer.lower() == "yes" else "❌"
                summary += f"{emoji} {label}\n"

    yes_count = sum(1 for a in answers[:6] if a.lower() == "yes")
    summary += f"\n🏆 Score: {yes_count}/6 habits completed!"

    if yes_count == 6:
        summary += "\n\n🔥 Perfect week Ashton!! Absolutely killing it!"
    elif yes_count >= 4:
        summary += "\n\n👊 Solid week! Keep building on this!"
    elif yes_count >= 2:
        summary += "\n\n💪 Room to grow — next week is a new chance!"
    else:
        summary += "\n\n🌱 Rough week, that's okay. Reset and go again!"

    send_telegram(summary)
    time.sleep(2)
    recap = get_weekly_calendar_recap()
    send_telegram(recap)


def check_upcoming_events():
    try:
        creds_json = os.environ.get("GOOGLE_TOKEN")
        if not creds_json:
            return

        creds_data = json.loads(creds_json)
        creds = Credentials(**creds_data)
        service = build("calendar", "v3", credentials=creds)

        now = datetime.datetime.utcnow()
        soon = now + datetime.timedelta(minutes=31)

        events_result = service.events().list(
            calendarId="primary",
            timeMin=now.isoformat() + "Z",
            timeMax=soon.isoformat() + "Z",
            singleEvents=True,
            orderBy="startTime"
        ).execute()

        events = events_result.get("items", [])
        for event in events:
            event_id = event["id"]
            if event_id not in alerted_events:
                summary = event.get("summary", "No Title")
                start = event["start"].get("dateTime", event["start"].get("date"))
                dt = datetime.datetime.fromisoformat(start.replace("Z", "+00:00"))
                local_time = dt.astimezone().strftime("%I:%M %p")
                send_telegram(f"⏰ Heads up! '{summary}' starts at {local_time} — in about 30 minutes!")
                alerted_events.add(event_id)
    except Exception as e:
        print(f"Upcoming events error: {e}")


def handle_telegram_commands():
    last_update_id = None
    while True:
        try:
            url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/getUpdates"
            params = {"timeout": 10, "offset": last_update_id}
            response = requests.get(url, params=params, timeout=15)
            data = response.json()

            for update in data.get("result", []):
                last_update_id = update["update_id"] + 1
                message = update.get("message", {})
                text = message.get("text", "").strip()
                chat_id = str(message.get("chat", {}).get("id", ""))

                if chat_id != CHAT_ID:
                    continue

                text_lower = text.lower()

                if checkin_state["active"]:
                    checkin_state["answers"].append(text_lower)
                    next_index = checkin_state["question_index"] + 1
                    checkin_state["question_index"] = next_index

                    if next_index < len(CHECKIN_QUESTIONS):
                        send_telegram(CHECKIN_QUESTIONS[next_index])
                    else:
                        finish_weekly_checkin()
                    continue

                if text_lower in ["brief", "/brief"]:
                    motivation = get_motivation()
                    calendar = get_calendar_events()
                    gmail = get_gmail_summary()
                    weather = get_weather()
                    msg = f"🌅 Here's your briefing Ashton!\n\n{weather}\n\n{motivation}\n\n{calendar}\n\n{gmail}"
                    send_telegram(msg)

                elif text_lower in ["events", "/events"]:
                    send_telegram(get_calendar_events())

                elif text_lower in ["quote", "/quote"]:
                    send_telegram(get_motivation())

                elif text_lower in ["emails", "/emails"]:
                    send_telegram(get_gmail_summary())

                elif text_lower in ["weather", "/weather"]:
                    send_telegram(get_weather())

                elif text_lower in ["checkin", "/checkin"]:
                    start_weekly_checkin()

                elif text_lower in ["help", "/help"]:
                    send_telegram(
                        "🤖 Commands you can send me:\n\n"
                        "brief — morning briefing\n"
                        "events — today's calendar\n"
                        "quote — motivational quote\n"
                        "emails — unread emails\n"
                        "weather — current weather\n"
                        "checkin — start weekly check-in\n"
                        "help — show this list\n\n"
                        "💬 Or just ask me anything and I'll reply with AI!"
                    )

                else:
                    # Everything else goes to AI
                    send_telegram("🤔 Thinking...")
                    reply = ask_ai(text)
                    send_telegram(reply)

        except Exception as e:
            print(f"Telegram listener error: {e}")
        time.sleep(2)


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
    motivation = get_motivation()
    calendar = get_calendar_events()
    gmail = get_gmail_summary()
    weather = get_weather()
    msg = f"🌅 Good morning Ashton!\n\n{weather}\n\n{motivation}\n\n{calendar}\n\n{gmail}"
    send_telegram(msg)
    return 'Briefing sent! Check Telegram!'


@app.route('/debug')
def debug():
    token = os.environ.get("GOOGLE_TOKEN")
    if token:
        return f"Token found! Length: {len(token)}"
    else:
        return "No token found!"


@app.route("/login")
def login():
    code_verifier = secrets.token_urlsafe(64)
    session["code_verifier"] = code_verifier
    flow = get_flow(code_verifier=code_verifier)
    auth_url, state = flow.authorization_url(
        access_type="offline",
        include_granted_scopes="true",
        code_challenge_method="S256",
        prompt="consent"
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

    token_data = {
        "token": creds.token,
        "refresh_token": creds.refresh_token,
        "token_uri": creds.token_uri,
        "client_id": creds.client_id,
        "client_secret": creds.client_secret,
        "scopes": list(creds.scopes)
    }
    print("GOOGLE_TOKEN:", json.dumps(token_data))

    return "Calendar checked. Check your Telegram!"


def run_schedule():
    while True:
        now = datetime.datetime.now()

        if now.hour == 12 and now.minute == 0:
            motivation = get_motivation()
            calendar = get_calendar_events()
            gmail = get_gmail_summary()
            weather = get_weather()
            msg = f"🌅 Good morning Ashton!\n\n{weather}\n\n{motivation}\n\n{calendar}\n\n{gmail}"
            send_telegram(msg)
            time.sleep(61)

        if now.weekday() == 0 and now.hour == 4 and now.minute == 0:
            start_weekly_checkin()
            time.sleep(61)

        check_upcoming_events()
        time.sleep(60)


def start_scheduler():
    thread = threading.Thread(target=run_schedule, daemon=True)
    thread.start()


def start_bot_listener():
    thread = threading.Thread(target=handle_telegram_commands, daemon=True)
    thread.start()


if __name__ == "__main__":
    start_scheduler()
    start_bot_listener()
    port = int(os.environ.get("PORT", 8080))
    app.run(host="0.0.0.0", port=port, debug=False)
else:
    start_scheduler()
    start_bot_listener()