"""
=============================================================================
  ASHTON'S AI ASSISTANT v2
  Telegram bot hosted on Railway
  
  Features:
  - AI-powered briefings via Groq (summarizes everything into one clean message)
  - Smart scheduling (morning brief, midday check, evening recap — not all at once)
  - Inline keyboard buttons (tap instead of type)
  - Crypto price tracking (BTC, ETH, SOL + custom watchlist)
  - Fitness/gym logging with weekly streaks
  - Finance/budget tracking with spending alerts
  - Habit tracker with streaks and stats
  - Google Calendar + Gmail integration
  - Weather, news, motivational quotes
  - Weekly check-in with progress graphs
  - Clean formatted messages that feel premium
=============================================================================
"""

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

# ─── ENV VARS ──────────────────────────────────────────────────────────────────
TELEGRAM_TOKEN     = os.environ.get("TELEGRAM_TOKEN")
CHAT_ID            = os.environ.get("CHAT_ID")
GOOGLE_CREDENTIALS = os.environ.get("GOOGLE_CREDENTIALS")
NEWS_API_KEY       = os.environ.get("NEWS_API_KEY")
GROQ_API_KEY       = os.environ.get("GROQ_API_KEY", "")

SCOPES = [
    "https://www.googleapis.com/auth/calendar.readonly",
    "https://www.googleapis.com/auth/gmail.readonly"
]
REDIRECT_URI = "https://my-assistant-production-2fe1.up.railway.app/oauth2callback"

# ─── PERSISTENT STATE (in-memory, resets on redeploy) ──────────────────────────
DATA_FILE = "assistant_data.json"

def load_data():
    """Load persistent data from file."""
    defaults = {
        "alerted_events": [],
        "habits": {},           # {"workout": [dates], "reading": [dates], ...}
        "workouts": [],         # [{"date": ..., "type": ..., "duration": ..., "notes": ...}]
        "budget": {
            "monthly_limit": 2000,
            "transactions": []  # [{"date": ..., "amount": ..., "category": ..., "note": ...}]
        },
        "crypto_watchlist": ["bitcoin", "ethereum", "solana"],
        "checkin_history": [],
        "settings": {
            "morning_hour": 9,
            "evening_hour": 21,
            "timezone_offset": -7  # PDT
        }
    }
    if os.path.exists(DATA_FILE):
        try:
            with open(DATA_FILE, "r") as f:
                saved = json.load(f)
            for k, v in defaults.items():
                if k not in saved:
                    saved[k] = v
            return saved
        except:
            pass
    return defaults

def save_data():
    """Save data to file."""
    try:
        with open(DATA_FILE, "w") as f:
            json.dump(data, f, indent=2, default=str)
    except Exception as e:
        print(f"[SAVE ERR] {e}")

data = load_data()
alerted_events = set(data.get("alerted_events", []))

# Conversation state
conv_state = {
    "mode": None,       # "checkin", "log_workout", "log_spend", "add_habit"
    "step": 0,
    "temp": {}
}


# ═══════════════════════════════════════════════════════════════════════════════
#  TELEGRAM MESSAGING
# ═══════════════════════════════════════════════════════════════════════════════

def send(message, reply_markup=None, parse_mode="Markdown"):
    """Send a Telegram message with optional inline keyboard."""
    if not TELEGRAM_TOKEN or not CHAT_ID:
        print("[WARN] Telegram creds missing.")
        return
    url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
    payload = {
        "chat_id": CHAT_ID,
        "text": message,
        "parse_mode": parse_mode
    }
    if reply_markup:
        payload["reply_markup"] = json.dumps(reply_markup)
    try:
        r = requests.post(url, json=payload, timeout=15)
        if r.status_code != 200:
            # Retry without parse mode if markdown fails
            payload.pop("parse_mode", None)
            requests.post(url, json=payload, timeout=15)
    except Exception as e:
        print(f"[SEND ERR] {e}")


def send_buttons(message, buttons):
    """Send message with inline keyboard buttons.
    buttons = [{"text": "Label", "data": "callback_data"}, ...]
    """
    keyboard = {"inline_keyboard": []}
    row = []
    for i, btn in enumerate(buttons):
        row.append({
            "text": btn["text"],
            "callback_data": btn["data"]
        })
        if len(row) >= 2 or i == len(buttons) - 1:
            keyboard["inline_keyboard"].append(row)
            row = []
    send(message, reply_markup=keyboard)


def answer_callback(callback_query_id, text=""):
    """Answer a callback query to remove the loading indicator."""
    url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/answerCallbackQuery"
    try:
        requests.post(url, json={
            "callback_query_id": callback_query_id,
            "text": text
        }, timeout=10)
    except:
        pass


# ═══════════════════════════════════════════════════════════════════════════════
#  AI SUMMARIZER (via Groq)
# ═══════════════════════════════════════════════════════════════════════════════

def ai_summarize(raw_data, context="morning briefing"):
    """Use Groq to create a clean, intelligent summary."""
    if not GROQ_API_KEY:
        return raw_data  # Fallback: just return raw text

    try:
        headers = {
            "Authorization": f"Bearer {GROQ_API_KEY}",
            "Content-Type": "application/json"
        }
        payload = {
            "model": "llama-3.3-70b-versatile",
            "messages": [
                {
                    "role": "system",
                    "content": (
                        "You are Ashton's personal AI assistant. "
                        "Summarize the following information into a clean, concise Telegram message. "
                        "Use emojis sparingly but effectively. Be direct and useful. "
                        "Sound like a sharp, helpful friend — not a corporate bot. "
                        "Keep it under 300 words. Use bullet points for lists."
                    )
                },
                {
                    "role": "user",
                    "content": f"Context: {context}\n\nRaw data:\n{raw_data}"
                }
            ],
            "max_tokens": 500,
            "temperature": 0.4
        }
        r = requests.post(
            "https://api.groq.com/openai/v1/chat/completions",
            headers=headers,
            json=payload,
            timeout=30
        )
        if r.status_code == 200:
            return r.json()["choices"][0]["message"]["content"].strip()
        else:
            print(f"[AI ERR] {r.status_code}: {r.text[:200]}")
            return raw_data
    except Exception as e:
        print(f"[AI ERR] {e}")
        return raw_data


# ═══════════════════════════════════════════════════════════════════════════════
#  DATA FETCHERS
# ═══════════════════════════════════════════════════════════════════════════════

def get_weather():
    try:
        r = requests.get("https://wttr.in/Abbotsford,BC?format=j1", timeout=20)
        d = r.json()
        c = d["current_condition"][0]
        desc = c["weatherDesc"][0]["value"]
        return f"{desc}, {c['temp_C']}°C (feels {c['FeelsLikeC']}°C), humidity {c['humidity']}%"
    except:
        return "Weather unavailable"


def get_news():
    try:
        url = f"https://newsapi.org/v2/top-headlines?language=en&pageSize=4&apiKey={NEWS_API_KEY}"
        r = requests.get(url, timeout=15)
        articles = r.json().get("articles", [])
        if not articles:
            return "No news available."
        lines = []
        for a in articles:
            title = a.get("title", "").split(" - ")[0]
            lines.append(f"• {title}")
        return "\n".join(lines)
    except:
        return "News unavailable"


def get_quote():
    try:
        r = requests.get("https://zenquotes.io/api/random", timeout=10)
        d = r.json()[0]
        return f'"{d["q"]}"\n— {d["a"]}'
    except:
        return "Keep pushing, you've got this!"


def get_calendar():
    try:
        creds_json = os.environ.get("GOOGLE_TOKEN")
        if not creds_json:
            return "No calendar connected."
        creds = Credentials(**json.loads(creds_json))
        service = build("calendar", "v3", credentials=creds)
        now = datetime.datetime.utcnow().replace(hour=0, minute=0, second=0).isoformat() + "Z"
        events = service.events().list(
            calendarId="primary", timeMin=now,
            maxResults=5, singleEvents=True, orderBy="startTime"
        ).execute().get("items", [])
        if not events:
            return "No events today."
        lines = []
        for e in events:
            start = e["start"].get("dateTime", e["start"].get("date"))
            name = e.get("summary", "Untitled")
            try:
                t = datetime.datetime.fromisoformat(start.replace("Z", "")).strftime("%I:%M %p")
            except:
                t = start
            lines.append(f"• {name} at {t}")
        return "\n".join(lines)
    except Exception as e:
        return f"Calendar error: {e}"


def get_gmail():
    try:
        creds_json = os.environ.get("GOOGLE_TOKEN")
        if not creds_json:
            return "No Gmail connected."
        creds = Credentials(**json.loads(creds_json))
        gmail = build("gmail", "v1", credentials=creds)
        msgs = gmail.users().messages().list(
            userId="me", labelIds=["UNREAD", "INBOX"], maxResults=5
        ).execute().get("messages", [])
        if not msgs:
            return "Inbox clear — no unread emails."
        lines = []
        for m in msgs:
            msg = gmail.users().messages().get(
                userId="me", id=m["id"], format="metadata",
                metadataHeaders=["From", "Subject"]
            ).execute()
            headers = msg.get("payload", {}).get("headers", [])
            subj = next((h["value"] for h in headers if h["name"] == "Subject"), "No subject")
            sender = next((h["value"] for h in headers if h["name"] == "From"), "Unknown")
            sender = sender.split("<")[0].strip()[:25]
            lines.append(f"• {sender}: {subj}")
        return "\n".join(lines)
    except Exception as e:
        return f"Gmail error: {e}"


def get_crypto():
    """Fetch crypto prices for watchlist."""
    try:
        coins = data.get("crypto_watchlist", ["bitcoin", "ethereum", "solana"])
        ids = ",".join(coins)
        url = f"https://api.coingecko.com/api/v3/simple/price?ids={ids}&vs_currencies=usd&include_24hr_change=true"
        r = requests.get(url, timeout=15)
        prices = r.json()
        lines = []
        symbols = {
            "bitcoin": "BTC", "ethereum": "ETH", "solana": "SOL",
            "dogecoin": "DOGE", "cardano": "ADA", "ripple": "XRP",
            "litecoin": "LTC", "polkadot": "DOT", "avalanche-2": "AVAX",
            "chainlink": "LINK", "shiba-inu": "SHIB", "polygon": "MATIC"
        }
        for coin in coins:
            if coin in prices:
                price = prices[coin]["usd"]
                change = prices[coin].get("usd_24h_change", 0)
                sym = symbols.get(coin, coin.upper()[:4])
                arrow = "🟢" if change >= 0 else "🔴"
                if price >= 1:
                    price_str = f"${price:,.2f}"
                else:
                    price_str = f"${price:.6f}"
                lines.append(f"{arrow} {sym}: {price_str} ({change:+.1f}%)")
        return "\n".join(lines) if lines else "Crypto data unavailable."
    except Exception as e:
        return f"Crypto error: {e}"


# ═══════════════════════════════════════════════════════════════════════════════
#  SMART BRIEFINGS
# ═══════════════════════════════════════════════════════════════════════════════

def morning_briefing():
    """AI-summarized morning briefing — clean single message."""
    weather = get_weather()
    calendar = get_calendar()
    gmail = get_gmail()
    news = get_news()
    quote = get_quote()
    crypto = get_crypto()

    # Check habit streaks
    today = datetime.date.today().isoformat()
    active_habits = data.get("habits", {})
    habit_status = []
    for habit, dates in active_habits.items():
        streak = calculate_streak(dates)
        habit_status.append(f"{habit}: {streak} day streak")

    raw = (
        f"WEATHER:\n{weather}\n\n"
        f"CALENDAR:\n{calendar}\n\n"
        f"EMAILS:\n{gmail}\n\n"
        f"NEWS:\n{news}\n\n"
        f"CRYPTO:\n{crypto}\n\n"
        f"HABITS:\n" + ("\n".join(habit_status) if habit_status else "No habits tracked yet") + "\n\n"
        f"QUOTE:\n{quote}"
    )

    summary = ai_summarize(raw, "morning briefing for Ashton — start his day right")

    send(f"☀️ *Good morning Ashton*\n\n{summary}")

    # Show quick action buttons
    time.sleep(1)
    send_buttons("What do you need?", [
        {"text": "📊 Crypto", "data": "crypto"},
        {"text": "📧 Emails", "data": "emails"},
        {"text": "💪 Log Workout", "data": "log_workout"},
        {"text": "💰 Log Spend", "data": "log_spend"},
        {"text": "✅ Habits", "data": "habits"},
        {"text": "📋 Full Brief", "data": "full_brief"},
    ])


def evening_recap():
    """Evening summary of the day."""
    today = datetime.date.today().isoformat()

    # Today's workouts
    workouts = [w for w in data.get("workouts", []) if w.get("date") == today]
    workout_txt = ""
    if workouts:
        for w in workouts:
            workout_txt += f"• {w['type']} — {w['duration']} min"
            if w.get("notes"):
                workout_txt += f" ({w['notes']})"
            workout_txt += "\n"
    else:
        workout_txt = "No workout logged today."

    # Today's spending
    transactions = [t for t in data["budget"]["transactions"] if t.get("date") == today]
    spent_today = sum(t["amount"] for t in transactions)
    month_transactions = [
        t for t in data["budget"]["transactions"]
        if t.get("date", "")[:7] == today[:7]
    ]
    spent_month = sum(t["amount"] for t in month_transactions)
    budget_limit = data["budget"]["monthly_limit"]

    spend_txt = f"Today: ${spent_today:.2f}\nThis month: ${spent_month:.2f} / ${budget_limit}"
    if spent_month > budget_limit * 0.8:
        spend_txt += "\n⚠️ You're at 80%+ of your monthly budget!"

    # Habits completed today
    habits_done = []
    habits_missed = []
    for habit, dates in data.get("habits", {}).items():
        if today in dates:
            habits_done.append(f"✅ {habit}")
        else:
            habits_missed.append(f"⬜ {habit}")

    crypto = get_crypto()

    raw = (
        f"WORKOUTS:\n{workout_txt}\n\n"
        f"SPENDING:\n{spend_txt}\n\n"
        f"HABITS DONE:\n" + ("\n".join(habits_done) if habits_done else "None") + "\n"
        f"HABITS MISSED:\n" + ("\n".join(habits_missed) if habits_missed else "All done!") + "\n\n"
        f"CRYPTO:\n{crypto}"
    )

    summary = ai_summarize(raw, "evening recap — summarize Ashton's day, be encouraging but honest")

    send(f"🌙 *Evening Recap*\n\n{summary}")

    if habits_missed:
        send_buttons("Still time to check these off:", [
            {"text": f"✅ {h.replace('⬜ ', '')}", "data": f"habit_done:{h.replace('⬜ ', '')}"}
            for h in habits_missed[:4]
        ])


def midday_check():
    """Quick midday nudge."""
    today = datetime.date.today().isoformat()
    habits_missed = []
    for habit, dates in data.get("habits", {}).items():
        if today not in dates:
            habits_missed.append(habit)

    workouts = [w for w in data.get("workouts", []) if w.get("date") == today]

    msg = "🕐 *Midday Check*\n\n"
    if not workouts:
        msg += "💪 No workout logged yet today.\n"
    if habits_missed:
        msg += f"📋 {len(habits_missed)} habits still to do: {', '.join(habits_missed)}\n"
    else:
        msg += "✅ All habits done so far!\n"

    crypto = get_crypto()
    msg += f"\n📊 *Markets*\n{crypto}"

    send(msg)


# ═══════════════════════════════════════════════════════════════════════════════
#  FITNESS TRACKING
# ═══════════════════════════════════════════════════════════════════════════════

def start_workout_log():
    conv_state["mode"] = "log_workout"
    conv_state["step"] = 0
    conv_state["temp"] = {}
    send_buttons("What type of workout?", [
        {"text": "🏋️ Weights", "data": "wtype:weights"},
        {"text": "🏃 Cardio", "data": "wtype:cardio"},
        {"text": "🧘 Yoga/Stretch", "data": "wtype:yoga"},
        {"text": "🥊 Combat/Sports", "data": "wtype:combat"},
    ])


def finish_workout_log():
    w = conv_state["temp"]
    entry = {
        "date": datetime.date.today().isoformat(),
        "type": w.get("type", "general"),
        "duration": w.get("duration", 0),
        "notes": w.get("notes", "")
    }
    data.setdefault("workouts", []).append(entry)
    save_data()
    conv_state["mode"] = None

    # Count this week's workouts
    week_start = (datetime.date.today() - datetime.timedelta(days=datetime.date.today().weekday())).isoformat()
    week_workouts = [w for w in data["workouts"] if w.get("date", "") >= week_start]

    send(
        f"💪 *Workout Logged!*\n\n"
        f"Type: {entry['type']}\n"
        f"Duration: {entry['duration']} min\n"
        f"Notes: {entry['notes'] or 'None'}\n\n"
        f"📅 This week: {len(week_workouts)} workouts"
    )


# ═══════════════════════════════════════════════════════════════════════════════
#  BUDGET / FINANCE
# ═══════════════════════════════════════════════════════════════════════════════

def start_spend_log():
    conv_state["mode"] = "log_spend"
    conv_state["step"] = 0
    conv_state["temp"] = {}
    send("💰 How much did you spend? (just the number, e.g. 45.50)")


def finish_spend_log():
    t = conv_state["temp"]
    entry = {
        "date": datetime.date.today().isoformat(),
        "amount": t.get("amount", 0),
        "category": t.get("category", "other"),
        "note": t.get("note", "")
    }
    data["budget"]["transactions"].append(entry)
    save_data()
    conv_state["mode"] = None

    # Monthly total
    month = datetime.date.today().isoformat()[:7]
    month_total = sum(
        tx["amount"] for tx in data["budget"]["transactions"]
        if tx.get("date", "")[:7] == month
    )
    limit = data["budget"]["monthly_limit"]
    pct = (month_total / limit * 100) if limit > 0 else 0

    bar = "█" * int(pct / 5) + "░" * (20 - int(pct / 5))

    msg = (
        f"💰 *Expense Logged*\n\n"
        f"Amount: ${entry['amount']:.2f}\n"
        f"Category: {entry['category']}\n"
        f"Note: {entry['note'] or 'None'}\n\n"
        f"📊 *Monthly Budget*\n"
        f"[{bar}] {pct:.0f}%\n"
        f"${month_total:.2f} / ${limit:.2f}"
    )

    if pct > 90:
        msg += "\n\n🚨 You're almost at your budget limit!"
    elif pct > 75:
        msg += "\n\n⚠️ Over 75% of budget used."

    send(msg)


def get_budget_summary():
    month = datetime.date.today().isoformat()[:7]
    transactions = [t for t in data["budget"]["transactions"] if t.get("date", "")[:7] == month]
    total = sum(t["amount"] for t in transactions)
    limit = data["budget"]["monthly_limit"]

    # Group by category
    categories = {}
    for t in transactions:
        cat = t.get("category", "other")
        categories[cat] = categories.get(cat, 0) + t["amount"]

    msg = f"💰 *Budget — {datetime.date.today().strftime('%B %Y')}*\n\n"
    msg += f"Spent: ${total:.2f} / ${limit:.2f}\n"
    pct = (total / limit * 100) if limit > 0 else 0
    bar = "█" * int(pct / 5) + "░" * (20 - int(pct / 5))
    msg += f"[{bar}] {pct:.0f}%\n\n"

    if categories:
        msg += "*By category:*\n"
        for cat, amt in sorted(categories.items(), key=lambda x: -x[1]):
            msg += f"• {cat}: ${amt:.2f}\n"

    msg += f"\n💵 Remaining: ${max(0, limit - total):.2f}"
    send(msg)


# ═══════════════════════════════════════════════════════════════════════════════
#  HABIT TRACKING
# ═══════════════════════════════════════════════════════════════════════════════

def calculate_streak(dates):
    """Calculate current streak from a list of date strings."""
    if not dates:
        return 0
    sorted_dates = sorted(set(dates), reverse=True)
    today = datetime.date.today()
    streak = 0
    for i, d in enumerate(sorted_dates):
        expected = (today - datetime.timedelta(days=i)).isoformat()
        if d == expected:
            streak += 1
        else:
            break
    return streak


def show_habits():
    today = datetime.date.today().isoformat()
    habits = data.get("habits", {})

    if not habits:
        send(
            "📋 *No habits tracked yet.*\n\n"
            "Add one with /addhabit or tap below."
        )
        send_buttons("Quick add:", [
            {"text": "💪 Workout", "data": "addhabit:workout"},
            {"text": "📚 Reading", "data": "addhabit:reading"},
            {"text": "🧘 Meditate", "data": "addhabit:meditate"},
            {"text": "💧 Water", "data": "addhabit:water"},
        ])
        return

    msg = "📋 *Today's Habits*\n\n"
    buttons = []
    for habit, dates in habits.items():
        done = today in dates
        streak = calculate_streak(dates)
        icon = "✅" if done else "⬜"
        fire = "🔥" if streak >= 7 else ("💫" if streak >= 3 else "")
        msg += f"{icon} *{habit}* — {streak} day streak {fire}\n"
        if not done:
            buttons.append({"text": f"✅ {habit}", "data": f"habit_done:{habit}"})

    send(msg)
    if buttons:
        send_buttons("Mark complete:", buttons)


def mark_habit_done(habit_name):
    today = datetime.date.today().isoformat()
    if habit_name not in data.get("habits", {}):
        data.setdefault("habits", {})[habit_name] = []
    if today not in data["habits"][habit_name]:
        data["habits"][habit_name].append(today)
        save_data()
    streak = calculate_streak(data["habits"][habit_name])
    fire = "🔥" if streak >= 7 else ("💫" if streak >= 3 else "👍")
    send(f"✅ *{habit_name}* done! {fire}\nStreak: {streak} days")


# ═══════════════════════════════════════════════════════════════════════════════
#  WEEKLY CHECK-IN (upgraded)
# ═══════════════════════════════════════════════════════════════════════════════

CHECKIN_QUESTIONS = [
    "💪 Did you work out this week?",
    "🥗 Did you eat healthy most days?",
    "😴 Did you get enough sleep?",
    "🎯 Did you make progress on your goals?",
    "🧠 Did you learn something new?",
    "📵 Did you avoid doomscrolling?",
    "😊 Rate your week 1-10:"
]

CHECKIN_LABELS = [
    "Worked out", "Ate healthy", "Slept well",
    "Goal progress", "Learned new", "No doomscroll", "Rating"
]


def start_checkin():
    conv_state["mode"] = "checkin"
    conv_state["step"] = 0
    conv_state["temp"] = {"answers": []}
    send("📋 *Weekly Check-in*\n\nLet's see how this week went!")
    time.sleep(1)
    send_buttons(CHECKIN_QUESTIONS[0], [
        {"text": "✅ Yes", "data": "checkin:yes"},
        {"text": "❌ No", "data": "checkin:no"},
    ])


def process_checkin_answer(answer):
    conv_state["temp"]["answers"].append(answer)
    next_step = conv_state["step"] + 1
    conv_state["step"] = next_step

    if next_step < len(CHECKIN_QUESTIONS):
        q = CHECKIN_QUESTIONS[next_step]
        if next_step == 6:
            # Rating question — no buttons
            send(q + " (type a number)")
        else:
            send_buttons(q, [
                {"text": "✅ Yes", "data": "checkin:yes"},
                {"text": "❌ No", "data": "checkin:no"},
            ])
    else:
        finish_checkin()


def finish_checkin():
    answers = conv_state["temp"]["answers"]
    conv_state["mode"] = None

    yes_count = sum(1 for a in answers[:6] if a.lower() == "yes")

    msg = "📋 *Weekly Check-in Results*\n\n"
    for i, label in enumerate(CHECKIN_LABELS):
        if i < len(answers):
            if i == 6:
                msg += f"⭐ {label}: {answers[i]}/10\n"
            else:
                icon = "✅" if answers[i].lower() == "yes" else "❌"
                msg += f"{icon} {label}\n"

    msg += f"\n🏆 *Score: {yes_count}/6*\n"

    if yes_count == 6:
        msg += "\n🔥 PERFECT WEEK! Absolutely killing it!"
    elif yes_count >= 4:
        msg += "\n👊 Solid week! Keep this momentum!"
    elif yes_count >= 2:
        msg += "\n💪 Room to grow — you'll crush next week."
    else:
        msg += "\n🌱 Rough week, but you showed up. Reset and go."

    # Save to history
    data.setdefault("checkin_history", []).append({
        "date": datetime.date.today().isoformat(),
        "score": yes_count,
        "rating": answers[6] if len(answers) > 6 else "?",
        "answers": answers
    })
    save_data()

    # AI-powered insight
    if GROQ_API_KEY and len(data["checkin_history"]) >= 2:
        recent = data["checkin_history"][-4:]
        raw = json.dumps(recent, indent=2)
        insight = ai_summarize(
            raw,
            "analyze Ashton's last few weekly check-ins. Give a 2-sentence trend insight."
        )
        msg += f"\n\n🧠 *AI Insight:* {insight}"

    send(msg)


# ═══════════════════════════════════════════════════════════════════════════════
#  COMMAND / CALLBACK HANDLERS
# ═══════════════════════════════════════════════════════════════════════════════

def handle_callback(callback_query):
    """Handle inline button presses."""
    cb_data = callback_query.get("data", "")
    cb_id = callback_query.get("id", "")

    answer_callback(cb_id)

    # Workout type selection
    if cb_data.startswith("wtype:"):
        wtype = cb_data.split(":")[1]
        conv_state["temp"]["type"] = wtype
        conv_state["step"] = 1
        send("⏱ How many minutes? (just the number)")

    # Habit done
    elif cb_data.startswith("habit_done:"):
        habit = cb_data.split(":", 1)[1]
        mark_habit_done(habit)

    # Add habit
    elif cb_data.startswith("addhabit:"):
        habit = cb_data.split(":", 1)[1]
        data.setdefault("habits", {})[habit] = []
        save_data()
        send(f"✅ *{habit}* added to your habits!")

    # Spend category
    elif cb_data.startswith("scat:"):
        cat = cb_data.split(":")[1]
        conv_state["temp"]["category"] = cat
        conv_state["step"] = 2
        send("📝 Any note? (or type 'skip')")

    # Check-in answer
    elif cb_data.startswith("checkin:"):
        answer = cb_data.split(":")[1]
        process_checkin_answer(answer)

    # Quick menu actions
    elif cb_data == "crypto":
        send(f"📊 *Crypto Prices*\n\n{get_crypto()}")
    elif cb_data == "emails":
        send(f"📧 *Unread Emails*\n\n{get_gmail()}")
    elif cb_data == "log_workout":
        start_workout_log()
    elif cb_data == "log_spend":
        start_spend_log()
    elif cb_data == "habits":
        show_habits()
    elif cb_data == "full_brief":
        send_full_brief()


def send_full_brief():
    """Non-AI full data dump."""
    weather = get_weather()
    calendar = get_calendar()
    gmail = get_gmail()
    news = get_news()
    crypto = get_crypto()
    quote = get_quote()

    msg = (
        f"📋 *Full Briefing*\n\n"
        f"🌤 *Weather*\n{weather}\n\n"
        f"📰 *News*\n{news}\n\n"
        f"📊 *Crypto*\n{crypto}\n\n"
        f"📅 *Calendar*\n{calendar}\n\n"
        f"📧 *Email*\n{gmail}\n\n"
        f"💬 *Quote*\n{quote}"
    )
    send(msg)


def handle_text_message(text):
    """Handle text messages — commands or conversation flow."""
    text_lower = text.strip().lower()

    # Conversation mode handling
    if conv_state["mode"] == "log_workout":
        if conv_state["step"] == 1:
            try:
                conv_state["temp"]["duration"] = int(text_lower)
                conv_state["step"] = 2
                send("📝 Any notes? (or 'skip')")
            except:
                send("Send a number for minutes.")
        elif conv_state["step"] == 2:
            conv_state["temp"]["notes"] = "" if text_lower == "skip" else text
            finish_workout_log()
        return

    if conv_state["mode"] == "log_spend":
        if conv_state["step"] == 0:
            try:
                conv_state["temp"]["amount"] = float(text_lower.replace("$", ""))
                conv_state["step"] = 1
                send_buttons("Category?", [
                    {"text": "🍔 Food", "data": "scat:food"},
                    {"text": "🚗 Transport", "data": "scat:transport"},
                    {"text": "🛒 Shopping", "data": "scat:shopping"},
                    {"text": "🎮 Entertainment", "data": "scat:entertainment"},
                    {"text": "📱 Bills", "data": "scat:bills"},
                    {"text": "❓ Other", "data": "scat:other"},
                ])
            except:
                send("Send a number for the amount (e.g. 45.50)")
        elif conv_state["step"] == 2:
            conv_state["temp"]["note"] = "" if text_lower == "skip" else text
            finish_spend_log()
        return

    if conv_state["mode"] == "checkin":
        process_checkin_answer(text)
        return

    # ─── COMMANDS ──────────────────────────────────────────────────────
    if text_lower in ["brief", "/brief", "/start", "morning"]:
        morning_briefing()

    elif text_lower in ["evening", "/evening", "recap"]:
        evening_recap()

    elif text_lower in ["crypto", "/crypto", "prices"]:
        send(f"📊 *Crypto Prices*\n\n{get_crypto()}")

    elif text_lower in ["weather", "/weather"]:
        send(f"🌤 *Weather*\n\n{get_weather()}")

    elif text_lower in ["news", "/news"]:
        send(f"📰 *News*\n\n{get_news()}")

    elif text_lower in ["events", "/events", "calendar"]:
        send(f"📅 *Calendar*\n\n{get_calendar()}")

    elif text_lower in ["emails", "/emails", "mail"]:
        send(f"📧 *Email*\n\n{get_gmail()}")

    elif text_lower in ["quote", "/quote"]:
        send(f"💬 {get_quote()}")

    elif text_lower in ["workout", "/workout", "gym"]:
        start_workout_log()

    elif text_lower in ["spend", "/spend", "spent"]:
        start_spend_log()

    elif text_lower in ["habits", "/habits"]:
        show_habits()

    elif text_lower in ["budget", "/budget"]:
        get_budget_summary()

    elif text_lower in ["checkin", "/checkin"]:
        start_checkin()

    elif text_lower.startswith("/setbudget"):
        try:
            amt = float(text_lower.split()[-1])
            data["budget"]["monthly_limit"] = amt
            save_data()
            send(f"✅ Monthly budget set to ${amt:.2f}")
        except:
            send("Usage: /setbudget 2000")

    elif text_lower.startswith("/addhabit"):
        parts = text.split(maxsplit=1)
        if len(parts) > 1:
            habit = parts[1].strip()
            data.setdefault("habits", {})[habit] = []
            save_data()
            send(f"✅ Habit *{habit}* added!")
        else:
            send_buttons("Quick add:", [
                {"text": "💪 Workout", "data": "addhabit:workout"},
                {"text": "📚 Reading", "data": "addhabit:reading"},
                {"text": "🧘 Meditate", "data": "addhabit:meditate"},
                {"text": "💧 Water", "data": "addhabit:water"},
            ])

    elif text_lower.startswith("/addcrypto"):
        parts = text.split(maxsplit=1)
        if len(parts) > 1:
            coin = parts[1].strip().lower()
            if coin not in data["crypto_watchlist"]:
                data["crypto_watchlist"].append(coin)
                save_data()
                send(f"✅ Added *{coin}* to watchlist.")
            else:
                send(f"Already watching {coin}.")
        else:
            send("Usage: /addcrypto dogecoin")

    elif text_lower in ["help", "/help", "menu"]:
        send(
            "🤖 *Ashton's AI Assistant*\n\n"
            "*Daily:*\n"
            "• `brief` — AI morning briefing\n"
            "• `evening` — day recap\n"
            "• `weather` / `news` / `quote`\n"
            "• `events` / `emails`\n\n"
            "*Tracking:*\n"
            "• `workout` — log a workout\n"
            "• `spend` — log expense\n"
            "• `habits` — view/complete habits\n"
            "• `budget` — monthly budget view\n"
            "• `checkin` — weekly check-in\n\n"
            "*Markets:*\n"
            "• `crypto` — price check\n"
            "• `/addcrypto [coin]` — add to watchlist\n\n"
            "*Settings:*\n"
            "• `/setbudget [amount]`\n"
            "• `/addhabit [name]`"
        )

    else:
        # Unknown — send to AI for freeform response
        if GROQ_API_KEY:
            response = ai_summarize(
                f"User said: {text}",
                "Ashton sent a casual message to his AI assistant. Respond helpfully and briefly."
            )
            send(response)
        else:
            send("Didn't catch that. Type `help` for commands.")


# ═══════════════════════════════════════════════════════════════════════════════
#  TELEGRAM LISTENER (supports both messages and callback queries)
# ═══════════════════════════════════════════════════════════════════════════════

def telegram_listener():
    last_update_id = None
    while True:
        try:
            url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/getUpdates"
            params = {"timeout": 15, "offset": last_update_id, "allowed_updates": ["message", "callback_query"]}
            r = requests.get(url, params=params, timeout=20)
            updates = r.json().get("result", [])

            for update in updates:
                last_update_id = update["update_id"] + 1

                # Callback query (button press)
                if "callback_query" in update:
                    cb = update["callback_query"]
                    cb_chat_id = str(cb.get("message", {}).get("chat", {}).get("id", ""))
                    if cb_chat_id == CHAT_ID:
                        handle_callback(cb)
                    continue

                # Text message
                message = update.get("message", {})
                text = message.get("text", "").strip()
                chat_id = str(message.get("chat", {}).get("id", ""))

                if chat_id != CHAT_ID or not text:
                    continue

                handle_text_message(text)

        except Exception as e:
            print(f"[LISTENER ERR] {e}")
        time.sleep(2)


# ═══════════════════════════════════════════════════════════════════════════════
#  SCHEDULER
# ═══════════════════════════════════════════════════════════════════════════════

def check_upcoming_events():
    try:
        creds_json = os.environ.get("GOOGLE_TOKEN")
        if not creds_json:
            return
        creds = Credentials(**json.loads(creds_json))
        service = build("calendar", "v3", credentials=creds)
        now = datetime.datetime.utcnow()
        soon = now + datetime.timedelta(minutes=31)
        events = service.events().list(
            calendarId="primary",
            timeMin=now.isoformat() + "Z",
            timeMax=soon.isoformat() + "Z",
            singleEvents=True, orderBy="startTime"
        ).execute().get("items", [])
        for event in events:
            eid = event["id"]
            if eid not in alerted_events:
                name = event.get("summary", "Untitled")
                start = event["start"].get("dateTime", event["start"].get("date"))
                try:
                    t = datetime.datetime.fromisoformat(start.replace("Z", "+00:00"))
                    t_str = t.astimezone().strftime("%I:%M %p")
                except:
                    t_str = start
                send(f"⏰ *{name}* starts at {t_str} — about 30 min!")
                alerted_events.add(eid)
    except Exception as e:
        print(f"[EVENT CHECK ERR] {e}")


def scheduler():
    while True:
        now = datetime.datetime.now()
        h, m = now.hour, now.minute

        morning_h = data.get("settings", {}).get("morning_hour", 9)
        evening_h = data.get("settings", {}).get("evening_hour", 21)

        if h == morning_h and m == 0:
            morning_briefing()
            time.sleep(61)

        if h == 13 and m == 0:
            midday_check()
            time.sleep(61)

        if h == evening_h and m == 0:
            evening_recap()
            time.sleep(61)

        # Weekly check-in: Sunday 10am
        if now.weekday() == 6 and h == 10 and m == 0:
            start_checkin()
            time.sleep(61)

        check_upcoming_events()
        time.sleep(60)


# ═══════════════════════════════════════════════════════════════════════════════
#  FLASK ROUTES (for Google OAuth)
# ═══════════════════════════════════════════════════════════════════════════════

def get_flow(state=None, code_verifier=None):
    if not GOOGLE_CREDENTIALS:
        raise ValueError("GOOGLE_CREDENTIALS env var missing.")
    creds_dict = json.loads(GOOGLE_CREDENTIALS)
    flow = Flow.from_client_config(creds_dict, scopes=SCOPES, redirect_uri=REDIRECT_URI, state=state)
    if code_verifier:
        flow.code_verifier = code_verifier
    return flow


@app.route("/")
def home():
    return '<h2>Ashton\'s AI Assistant</h2><p><a href="/login">Connect Google</a></p>'


@app.route("/login")
def login():
    cv = secrets.token_urlsafe(64)
    session["code_verifier"] = cv
    flow = get_flow(code_verifier=cv)
    auth_url, state = flow.authorization_url(
        access_type="offline", include_granted_scopes="true",
        code_challenge_method="S256", prompt="consent"
    )
    session["state"] = state
    return redirect(auth_url)


@app.route("/oauth2callback")
def oauth2callback():
    if "state" not in session or "code_verifier" not in session:
        return "Session expired.", 400
    flow = get_flow(state=session["state"], code_verifier=session["code_verifier"])
    flow.fetch_token(authorization_response=request.url)
    creds = flow.credentials
    token_data = {
        "token": creds.token, "refresh_token": creds.refresh_token,
        "token_uri": creds.token_uri, "client_id": creds.client_id,
        "client_secret": creds.client_secret, "scopes": list(creds.scopes)
    }
    print("GOOGLE_TOKEN:", json.dumps(token_data))
    send("✅ Google account connected!")
    return "Connected! Check Telegram."


@app.route("/brief")
def manual_brief():
    morning_briefing()
    return "Briefing sent!"


# ═══════════════════════════════════════════════════════════════════════════════
#  STARTUP
# ═══════════════════════════════════════════════════════════════════════════════

def start_threads():
    threading.Thread(target=scheduler, daemon=True).start()
    threading.Thread(target=telegram_listener, daemon=True).start()
    print("[OK] Scheduler + listener started.")


if __name__ == "__main__":
    start_threads()
    port = int(os.environ.get("PORT", 8080))
    app.run(host="0.0.0.0", port=port, debug=False)
else:
    start_threads()
