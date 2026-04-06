"""
=============================================================================
  ASHTON'S AI ASSISTANT v4 — BUILT FOR ASHTON
  
  Profile: 22, Abbotsford BC, night owl (bed 4-6am, wake ~11am)
  Income: $1515 CAD/mo, rent $850, weed $140, subs $120
  Goals: get a car (<$5k), get a job, grow Discord to 20k
  Fitness: bodyweight strength at home, bot suggests workouts
  Crypto: XRP + stablecoins, wants trading signals when ready
  Content: Twitch/TikTok/YouTube, posts when inspired
  Personality: honest, not babying, follows up, sarcastic mix
  
  Infrastructure:
  - Webhook mode (instant, no polling)
  - Google token auto-refresh
  - Graceful error handling
  - Groq rate limit protection
  - Voice note sending (TTS via bot)
  - Photo receipt scanning for expenses
  - Discord member count tracking
=============================================================================
"""

from flask import Flask, redirect, request, session
import threading, time, os, json, datetime, secrets, requests, re, logging

logging.basicConfig(level=logging.INFO, format='[%(asctime)s] %(message)s', datefmt='%H:%M:%S')
log = logging.getLogger("assistant")

app = Flask(__name__)
app.secret_key = os.environ.get("FLASK_SECRET_KEY", "dev-secret-change-this")

TELEGRAM_TOKEN = os.environ.get("TELEGRAM_TOKEN")
CHAT_ID = os.environ.get("CHAT_ID")
GOOGLE_CREDENTIALS = os.environ.get("GOOGLE_CREDENTIALS")
NEWS_API_KEY = os.environ.get("NEWS_API_KEY")
GROQ_API_KEY = os.environ.get("GROQ_API_KEY", "")
WEBHOOK_URL = os.environ.get("WEBHOOK_URL", "")
DISCORD_INVITE = "https://discord.gg/rQQGtMf8"
DISCORD_SERVER_ID = os.environ.get("DISCORD_SERVER_ID", "")

SCOPES = ["https://www.googleapis.com/auth/calendar.readonly", "https://www.googleapis.com/auth/gmail.readonly"]
REDIRECT_URI = "https://my-assistant-production-2fe1.up.railway.app/oauth2callback"

DATA_FILE = "assistant_data.json"
NAME = "Ashton"
TIMEZONE = "America/Vancouver"

# Ashton's fixed expenses
FIXED_EXPENSES = {"rent": 850, "weed": 140, "subscriptions": 120}
MONTHLY_INCOME = 1515
FIXED_TOTAL = sum(FIXED_EXPENSES.values())
DISPOSABLE = MONTHLY_INCOME - FIXED_TOTAL  # ~$405

DEFAULT_DATA = {
    "habits": {},
    "workouts": [],
    "budget": {"monthly_limit": DISPOSABLE, "transactions": [], "savings": 0, "car_fund": 0},
    "crypto_watchlist": ["ripple", "tether", "usd-coin", "bitcoin", "ethereum", "solana"],
    "crypto_alerts": [],
    "checkin_history": [],
    "reminders": [],
    "todos": [],
    "sleep_log": [],
    "goals": [
        {"name": "Get a car", "target": 5000, "progress": 0, "deadline": "2026-12-31", "type": "savings"},
        {"name": "Get a job", "target": 1, "progress": 0, "deadline": "2026-08-01", "type": "milestone"},
        {"name": "Discord 20k members", "target": 20000, "progress": 0, "deadline": "2027-06-01", "type": "growth"}
    ],
    "meals": [],
    "water": {"daily_goal": 8, "log": {}},
    "mood_log": [],
    "job_apps": [],
    "discord_history": [],
    "content_log": [],
    "alerted_events": [],
    "settings": {
        "morning_hour": 11,
        "midday_hour": 15,
        "evening_hour": 22,
        "notify_interval": 2,
        "water_reminder": True,
        "weather_alerts": True,
        "crypto_alerts": True
    },
    "last_groq_call": 0
}

def load_data():
    if os.path.exists(DATA_FILE):
        try:
            with open(DATA_FILE, "r") as f:
                saved = json.load(f)
            for k, v in DEFAULT_DATA.items():
                if k not in saved:
                    saved[k] = v
            return saved
        except:
            pass
    return dict(DEFAULT_DATA)

def save_data():
    try:
        with open(DATA_FILE, "w") as f:
            json.dump(data, f, indent=2, default=str)
    except Exception as e:
        log.error(f"Save failed: {e}")

data = load_data()
alerted_events = set(data.get("alerted_events", []))
conv_state = {"mode": None, "step": 0, "temp": {}}
last_news_articles = []

# ═══════════════════════════════════════════════════════════════════════════════
#  TELEGRAM
# ═══════════════════════════════════════════════════════════════════════════════

def send(msg, reply_markup=None, parse_mode=None):
    if not TELEGRAM_TOKEN or not CHAT_ID:
        return
    url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
    payload = {"chat_id": CHAT_ID, "text": msg}
    if parse_mode:
        payload["parse_mode"] = parse_mode
    if reply_markup:
        payload["reply_markup"] = json.dumps(reply_markup)
    try:
        r = requests.post(url, json=payload, timeout=15)
        if r.status_code != 200:
            log.warning(f"Send failed: {r.text[:100]}")
    except Exception as e:
        log.error(f"Send error: {e}")

def send_buttons(msg, buttons):
    kb = {"inline_keyboard": []}
    row = []
    for i, btn in enumerate(buttons):
        row.append({"text": btn["text"], "callback_data": btn["data"]})
        if len(row) >= 2 or i == len(buttons) - 1:
            kb["inline_keyboard"].append(row)
            row = []
    send(msg, reply_markup=kb)

def answer_cb(cb_id):
    try:
        requests.post(f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/answerCallbackQuery", json={"callback_query_id": cb_id}, timeout=10)
    except:
        pass

def send_voice(text):
    """Send a voice note using Google TTS."""
    try:
        tts_url = f"https://translate.google.com/translate_tts?ie=UTF-8&q={requests.utils.quote(text[:200])}&tl=en&client=tw-ob"
        r = requests.get(tts_url, timeout=15)
        if r.status_code == 200:
            url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendVoice"
            requests.post(url, data={"chat_id": CHAT_ID}, files={"voice": ("msg.mp3", r.content, "audio/mpeg")}, timeout=15)
            return True
    except Exception as e:
        log.error(f"Voice error: {e}")
    return False

# ═══════════════════════════════════════════════════════════════════════════════
#  AI (Groq with rate limiting)
# ═══════════════════════════════════════════════════════════════════════════════

def ai_call(prompt, system_msg=None, max_tokens=500):
    if not GROQ_API_KEY:
        return None
    now = time.time()
    if now - data.get("last_groq_call", 0) < 3:
        time.sleep(3)
    data["last_groq_call"] = time.time()
    try:
        msgs = []
        if system_msg:
            msgs.append({"role": "system", "content": system_msg})
        msgs.append({"role": "user", "content": prompt})
        r = requests.post("https://api.groq.com/openai/v1/chat/completions",
            headers={"Authorization": f"Bearer {GROQ_API_KEY}", "Content-Type": "application/json"},
            json={"model": "llama-3.3-70b-versatile", "messages": msgs, "max_tokens": max_tokens, "temperature": 0.4},
            timeout=30)
        if r.status_code == 200:
            return r.json()["choices"][0]["message"]["content"].strip()
        if r.status_code == 429:
            log.warning("Groq rate limited")
            return None
        log.warning(f"Groq {r.status_code}")
        return None
    except Exception as e:
        log.error(f"AI error: {e}")
        return None

def ai_summarize(raw, context=""):
    r = ai_call(f"Context: {context}\n\nData:\n{raw}",
        f"You are {NAME}'s personal AI assistant. You're honest, slightly sarcastic but supportive. "
        f"{NAME} is 22, night owl, tight budget, wants to build strength and grow his online presence. "
        "Summarize concisely for Telegram. No fluff. Under 250 words. Use emojis sparingly.")
    return r or raw

# ═══════════════════════════════════════════════════════════════════════════════
#  GOOGLE TOKEN AUTO-REFRESH
# ═══════════════════════════════════════════════════════════════════════════════

def get_google_creds():
    """Get Google credentials with auto-refresh."""
    cj = os.environ.get("GOOGLE_TOKEN")
    if not cj:
        return None
    try:
        creds_data = json.loads(cj)
        creds = Credentials(**creds_data)
        if creds.expired and creds.refresh_token:
            from google.auth.transport.requests import Request
            creds.refresh(Request())
            new_token = {
                "token": creds.token, "refresh_token": creds.refresh_token,
                "token_uri": creds.token_uri, "client_id": creds.client_id,
                "client_secret": creds.client_secret, "scopes": list(creds.scopes)
            }
            os.environ["GOOGLE_TOKEN"] = json.dumps(new_token)
            log.info("Google token refreshed")
        return creds
    except Exception as e:
        log.error(f"Google creds error: {e}")
        return None

# ═══════════════════════════════════════════════════════════════════════════════
#  DATA FETCHERS (all with error handling)
# ═══════════════════════════════════════════════════════════════════════════════

def get_weather():
    try:
        r = requests.get("https://wttr.in/Abbotsford,BC?format=j1", timeout=15)
        c = r.json()["current_condition"][0]
        return f"{c['weatherDesc'][0]['value']}, {c['temp_C']}C (feels {c['FeelsLikeC']}C), humidity {c['humidity']}%"
    except:
        return "Weather unavailable"

def get_weather_forecast():
    try:
        r = requests.get("https://wttr.in/Abbotsford,BC?format=j1", timeout=15)
        d = r.json()
        cur = d["current_condition"][0]
        cur_desc = cur["weatherDesc"][0]["value"].lower()
        cur_temp = int(cur["temp_C"])
        hourly = d.get("weather", [{}])[0].get("hourly", [])
        now_h = datetime.datetime.now().hour
        alerts = []
        for h in hourly:
            ht = int(h.get("time", "0").replace("00", "").strip() or "0")
            if ht <= now_h or ht > now_h + 4:
                continue
            rain = int(h.get("chanceofrain", "0"))
            temp = int(h.get("tempC", cur_temp))
            desc = h.get("weatherDesc", [{}])[0].get("value", "").lower()
            if rain > 50 and "rain" not in cur_desc:
                alerts.append(f"Rain likely ~{ht}:00 ({rain}%)")
            if rain > 70:
                alerts.append(f"Heavy rain ~{ht}:00 - grab umbrella!")
            if abs(temp - cur_temp) >= 5:
                alerts.append(f"Temp changing to {temp}C by {ht}:00")
            if "snow" in desc and "snow" not in cur_desc:
                alerts.append(f"Snow expected ~{ht}:00")
        return alerts
    except:
        return []

def get_news():
    try:
        r = requests.get(f"https://newsapi.org/v2/top-headlines?language=en&pageSize=5&apiKey={NEWS_API_KEY}", timeout=15)
        articles = r.json().get("articles", [])
        lines = [f"{i+1}. {a.get('title', '').split(' - ')[0]}" for i, a in enumerate(articles)]
        return "\n".join(lines), articles
    except:
        return "News unavailable", []

def get_quote():
    try:
        r = requests.get("https://zenquotes.io/api/random", timeout=10)
        d = r.json()[0]
        return f'"{d["q"]}"\n- {d["a"]}'
    except:
        return "Keep grinding."

def get_calendar():
    creds = get_google_creds()
    if not creds:
        return "No calendar connected."
    try:
        svc = build("calendar", "v3", credentials=creds)
        now = datetime.datetime.utcnow().replace(hour=0, minute=0, second=0).isoformat() + "Z"
        evts = svc.events().list(calendarId="primary", timeMin=now, maxResults=5, singleEvents=True, orderBy="startTime").execute().get("items", [])
        if not evts:
            return "Nothing on the calendar."
        lines = []
        for e in evts:
            start = e["start"].get("dateTime", e["start"].get("date"))
            name = e.get("summary", "?")
            try:
                t = datetime.datetime.fromisoformat(start.replace("Z", "")).strftime("%I:%M %p")
            except:
                t = start
            lines.append(f"- {name} at {t}")
        return "\n".join(lines)
    except Exception as e:
        return f"Calendar error: {e}"

def get_gmail():
    creds = get_google_creds()
    if not creds:
        return "No Gmail connected."
    try:
        g = build("gmail", "v1", credentials=creds)
        msgs = g.users().messages().list(userId="me", labelIds=["UNREAD", "INBOX"], maxResults=5).execute().get("messages", [])
        if not msgs:
            return "Inbox clear."
        lines = []
        for m in msgs:
            msg = g.users().messages().get(userId="me", id=m["id"], format="metadata", metadataHeaders=["From", "Subject"]).execute()
            hdrs = msg.get("payload", {}).get("headers", [])
            subj = next((h["value"] for h in hdrs if h["name"] == "Subject"), "?")
            sender = next((h["value"] for h in hdrs if h["name"] == "From"), "?").split("<")[0].strip()[:25]
            lines.append(f"- {sender}: {subj}")
        return "\n".join(lines)
    except Exception as e:
        return f"Gmail error: {e}"

def get_crypto():
    try:
        coins = data.get("crypto_watchlist", ["ripple", "tether", "bitcoin"])
        ids = ",".join(coins)
        r = requests.get(f"https://api.coingecko.com/api/v3/simple/price?ids={ids}&vs_currencies=usd,cad&include_24hr_change=true&include_24hr_vol=true", timeout=15)
        prices = r.json()
        syms = {"bitcoin": "BTC", "ethereum": "ETH", "solana": "SOL", "ripple": "XRP", "tether": "USDT", "usd-coin": "USDC", "dogecoin": "DOGE", "cardano": "ADA"}
        lines = []
        for c in coins:
            if c in prices:
                p = prices[c].get("usd", 0)
                cad = prices[c].get("cad", 0)
                ch = prices[c].get("usd_24h_change", 0)
                s = syms.get(c, c.upper()[:4])
                arrow = "+" if ch >= 0 else ""
                ps = f"${p:,.2f}" if p >= 1 else f"${p:.4f}"
                lines.append(f"{'G' if ch>=0 else 'R'} {s}: {ps} USD / ${cad:,.2f} CAD ({arrow}{ch:.1f}%)")
        return "\n".join(lines) if lines else "Crypto unavailable"
    except:
        return "Crypto unavailable"

def get_crypto_analysis():
    """AI-powered crypto analysis for XRP and watchlist."""
    prices = get_crypto()
    analysis = ai_call(
        f"Current prices:\n{prices}\n\n"
        f"{NAME} watches XRP and stablecoins mainly. He wants to get into day trading and swing trading soon.\n"
        "Give a brief market analysis: what's moving, any notable trends, and one actionable insight. "
        "Keep it under 150 words. Be direct.",
        "You are a crypto market analyst. Brief, no hype, honest about risks."
    )
    return analysis or prices

def get_discord_count():
    """Get Discord server member count."""
    try:
        r = requests.get(f"https://discord.com/api/v10/invites/rQQGtMf8?with_counts=true", timeout=10)
        if r.status_code == 200:
            d = r.json()
            total = d.get("approximate_member_count", 0)
            online = d.get("approximate_presence_count", 0)
            data.setdefault("discord_history", []).append({"date": datetime.date.today().isoformat(), "members": total})
            data["discord_history"] = data["discord_history"][-90:]
            # Update goal progress
            for g in data.get("goals", []):
                if "discord" in g.get("name", "").lower():
                    g["progress"] = total
            save_data()
            return total, online
        return 0, 0
    except:
        return 0, 0

# ═══════════════════════════════════════════════════════════════════════════════
#  SMART BRIEFINGS
# ═══════════════════════════════════════════════════════════════════════════════

def morning_briefing():
    weather = get_weather()
    alerts = get_weather_forecast()
    calendar = get_calendar()
    gmail = get_gmail()
    news_text, _ = get_news()
    quote = get_quote()
    crypto = get_crypto()
    dc_members, dc_online = get_discord_count()
    today = datetime.date.today().isoformat()

    # Budget snapshot
    month = today[:7]
    month_spent = sum(t["amount"] for t in data["budget"]["transactions"] if t.get("date", "")[:7] == month)
    remaining = DISPOSABLE - month_spent

    # Habits
    habit_lines = [f"{h}: {calculate_streak(d)} day streak" for h, d in data.get("habits", {}).items()]

    # Reminders
    rems = [r for r in data.get("reminders", []) if not r.get("done") and r.get("datetime", "").startswith(today)]

    # Pending job apps
    pending_apps = len([j for j in data.get("job_apps", []) if j.get("status") == "applied"])

    raw = (
        f"WEATHER: {weather}\n"
        f"{'WEATHER ALERTS: ' + ' | '.join(alerts) if alerts else ''}\n"
        f"CALENDAR:\n{calendar}\n"
        f"EMAIL:\n{gmail}\n"
        f"NEWS:\n{news_text}\n"
        f"CRYPTO:\n{crypto}\n"
        f"BUDGET: ${month_spent:.0f} spent this month, ${remaining:.0f} left of ${DISPOSABLE:.0f} disposable\n"
        f"DISCORD: {dc_members} members ({dc_online} online)\n"
        f"HABITS:\n" + ("\n".join(habit_lines) or "None tracked yet") + "\n"
        f"{'REMINDERS: ' + ', '.join(r['task'] for r in rems) if rems else ''}\n"
        f"{'JOB APPS: ' + str(pending_apps) + ' pending' if pending_apps else ''}\n"
        f"QUOTE: {quote}"
    )

    summary = ai_summarize(raw, f"morning briefing for {NAME}. He just woke up around 11am. Be real, not corny.")
    send(f"Yo {NAME}\n\n{summary}")

    time.sleep(1)
    send_buttons("What's the move?", [
        {"text": "News", "data": "deep_news"},
        {"text": "Crypto", "data": "crypto_analysis"},
        {"text": "Workout", "data": "suggest_workout"},
        {"text": "Meal", "data": "log_meal_prompt"},
        {"text": "Habits", "data": "habits"},
        {"text": "Discord", "data": "discord_stats"},
    ])

def evening_recap():
    today = datetime.date.today().isoformat()
    wk = [w for w in data.get("workouts", []) if w.get("date") == today]
    wk_txt = "\n".join(f"- {w['type']} {w['duration']}min" for w in wk) or "No workout"
    txns = [t for t in data["budget"]["transactions"] if t.get("date") == today]
    spent = sum(t["amount"] for t in txns)
    month_spent = sum(t["amount"] for t in data["budget"]["transactions"] if t.get("date", "")[:7] == today[:7])
    meals = [m for m in data.get("meals", []) if m.get("date") == today]
    cal = sum(m.get("calories", 0) for m in meals)
    water = data.get("water", {}).get("log", {}).get(today, 0)
    done = [h for h, d in data.get("habits", {}).items() if today in d]
    missed = [h for h, d in data.get("habits", {}).items() if today not in d]
    moods = [m for m in data.get("mood_log", []) if m.get("date") == today]
    dc_members, dc_online = get_discord_count()

    raw = (
        f"WORKOUT: {wk_txt}\n"
        f"NUTRITION: {cal}kcal across {len(meals)} meals\n"
        f"WATER: {water}/{data.get('water',{}).get('daily_goal',8)}\n"
        f"SPENDING: ${spent:.2f} today, ${month_spent:.2f} this month (${max(0,DISPOSABLE-month_spent):.2f} left)\n"
        f"HABITS DONE: {', '.join(done) or 'None'}\n"
        f"MISSED: {', '.join(missed) or 'All done!'}\n"
        f"MOOD: {moods[-1].get('label','?') if moods else 'Not logged'}\n"
        f"DISCORD: {dc_members} members\n"
        f"CRYPTO:\n{get_crypto()}"
    )

    summary = ai_summarize(raw, f"evening recap for {NAME}. Be honest - call out what he skipped. Encourage what he did. Don't baby him.")
    send(f"End of day {NAME}\n\n{summary}")

    if missed:
        send_buttons("Still time:", [{"text": f"Done {h}", "data": f"habit_done:{h}"} for h in missed[:4]])

def midday_check():
    today = datetime.date.today().isoformat()
    missed = [h for h, d in data.get("habits", {}).items() if today not in d]
    wk = [w for w in data.get("workouts", []) if w.get("date") == today]
    water = data.get("water", {}).get("log", {}).get(today, 0)
    meals = [m for m in data.get("meals", []) if m.get("date") == today]
    alerts = get_weather_forecast()

    msg = f"Midday check {NAME}\n\n"
    if not wk:
        msg += "No workout yet\n"
    msg += f"Water: {water}/{data.get('water',{}).get('daily_goal',8)}\n"
    msg += f"Meals: {len(meals)}\n"
    if missed:
        msg += f"Habits left: {', '.join(missed)}\n"
    if alerts:
        msg += "\nWeather heads up:\n" + "\n".join(alerts[:3])
    msg += f"\n\nCrypto:\n{get_crypto()}"
    send(msg)

# ═══════════════════════════════════════════════════════════════════════════════
#  WORKOUT SUGGESTIONS (bodyweight strength)
# ═══════════════════════════════════════════════════════════════════════════════

def suggest_workout():
    recent = data.get("workouts", [])[-7:]
    recent_types = [w.get("type", "") for w in recent]

    workout = ai_call(
        f"Recent workouts this week: {json.dumps(recent_types)}\n"
        f"{NAME} trains bodyweight at home. Goal is STRENGTH not size. "
        "Suggest a quick 20-30 min workout. Include exercise names, sets, reps. "
        "Alternate muscle groups from recent sessions. Keep it simple and effective. "
        "Format as a clean list.",
        "You are a bodyweight fitness coach. Brief, practical, no fluff."
    )
    if workout:
        send(f"Today's workout:\n\n{workout}")
        send_buttons("After you're done:", [
            {"text": "Done! Log it", "data": "log_workout_quick"},
            {"text": "Give me a different one", "data": "suggest_workout"},
            {"text": "Skip today", "data": "skip_workout"},
        ])
    else:
        send("Couldn't generate workout. Try: pushups, squats, planks - 3 sets each.")

# ═══════════════════════════════════════════════════════════════════════════════
#  ALL TRACKERS
# ═══════════════════════════════════════════════════════════════════════════════

def calculate_streak(dates):
    if not dates: return 0
    sd = sorted(set(dates), reverse=True)
    today = datetime.date.today()
    streak = 0
    for i, d in enumerate(sd):
        if d == (today - datetime.timedelta(days=i)).isoformat():
            streak += 1
        else:
            break
    return streak

def show_habits():
    today = datetime.date.today().isoformat()
    habits = data.get("habits", {})
    if not habits:
        send("No habits yet.")
        send_buttons("Quick add:", [
            {"text": "Workout", "data": "addhabit:workout"},
            {"text": "Reading", "data": "addhabit:reading"},
            {"text": "Meditate", "data": "addhabit:meditate"},
            {"text": "Water 8 cups", "data": "addhabit:water"},
            {"text": "No junk food", "data": "addhabit:no junk food"},
            {"text": "Code/build", "data": "addhabit:code"},
        ])
        return
    msg = f"Habits\n\n"
    buttons = []
    for h, dates in habits.items():
        done_today = today in dates
        streak = calculate_streak(dates)
        icon = "[x]" if done_today else "[ ]"
        fire = " FIRE" if streak >= 7 else (" *" if streak >= 3 else "")
        msg += f"{icon} {h} - {streak} days{fire}\n"
        if not done_today:
            buttons.append({"text": f"Done {h}", "data": f"habit_done:{h}"})
    send(msg)
    if buttons:
        send_buttons("Check off:", buttons)

def mark_habit_done(h):
    today = datetime.date.today().isoformat()
    data.setdefault("habits", {}).setdefault(h, [])
    if today not in data["habits"][h]:
        data["habits"][h].append(today)
        save_data()
    streak = calculate_streak(data["habits"][h])
    msg = f"Done: {h}! Streak: {streak} days"
    if streak == 7:
        msg += "\n\n1 week streak. Respect."
    elif streak == 30:
        msg += "\n\n30 days. You're actually locked in."
    send(msg)

def start_workout_log():
    conv_state["mode"] = "log_workout"
    conv_state["step"] = 0
    conv_state["temp"] = {}
    send_buttons("Type?", [
        {"text": "Upper body", "data": "wtype:upper"},
        {"text": "Lower body", "data": "wtype:lower"},
        {"text": "Full body", "data": "wtype:full"},
        {"text": "Core", "data": "wtype:core"},
        {"text": "Cardio", "data": "wtype:cardio"},
        {"text": "Stretch", "data": "wtype:stretch"},
    ])

def finish_workout_log():
    w = conv_state["temp"]
    entry = {"date": datetime.date.today().isoformat(), "type": w.get("type", "general"), "duration": w.get("duration", 0), "notes": w.get("notes", "")}
    data.setdefault("workouts", []).append(entry)
    # Auto-mark habit if exists
    today = datetime.date.today().isoformat()
    if "workout" in data.get("habits", {}):
        if today not in data["habits"]["workout"]:
            data["habits"]["workout"].append(today)
    save_data()
    conv_state["mode"] = None
    ws = (datetime.date.today() - datetime.timedelta(days=datetime.date.today().weekday())).isoformat()
    wc = len([x for x in data["workouts"] if x.get("date", "") >= ws])
    send(f"Logged: {entry['type']} - {entry['duration']}min\nThis week: {wc} workouts")

def estimate_calories(food):
    r = ai_call(
        f'Estimate calories for: "{food}"\n'
        f'{NAME} eats a lot of meat, veggies, tea, and sugar-heavy stuff.\n'
        'Return ONLY JSON: {"food":"name","calories":num,"protein_g":num,"carbs_g":num,"fat_g":num}',
        "Estimate calories realistically. Return only JSON. No markdown."
    )
    if r:
        try:
            return json.loads(r.strip().strip("`").replace("json\n", "").strip())
        except:
            pass
    return {"food": food, "calories": 0, "protein_g": 0, "carbs_g": 0, "fat_g": 0}

def log_meal(food):
    est = estimate_calories(food)
    entry = {"date": datetime.date.today().isoformat(), "time": datetime.datetime.now().strftime("%H:%M"),
             "food": est.get("food", food), "calories": est.get("calories", 0),
             "protein": est.get("protein_g", 0), "carbs": est.get("carbs_g", 0), "fat": est.get("fat_g", 0)}
    data.setdefault("meals", []).append(entry)
    save_data()
    today = datetime.date.today().isoformat()
    tm = [m for m in data["meals"] if m.get("date") == today]
    tc = sum(m.get("calories", 0) for m in tm)
    tp = sum(m.get("protein", 0) for m in tm)
    send(f"Logged: {entry['food']}\n~{entry['calories']}kcal | P:{entry['protein']}g C:{entry['carbs']}g F:{entry['fat']}g\n\nToday: {tc}kcal, {tp}g protein ({len(tm)} meals)")

def log_water(n=1):
    today = datetime.date.today().isoformat()
    wl = data.setdefault("water", {"daily_goal": 8, "log": {}})
    cur = wl["log"].get(today, 0) + n
    wl["log"][today] = cur
    save_data()
    goal = wl["daily_goal"]
    msg = f"Water: {cur}/{goal}"
    if cur >= goal:
        msg += " - goal hit!"
    send(msg)

def water_reminder():
    today = datetime.date.today().isoformat()
    cur = data.get("water", {}).get("log", {}).get(today, 0)
    goal = data.get("water", {}).get("daily_goal", 8)
    hour = datetime.datetime.now().hour
    expected = int(goal * (hour - 8) / 14) if hour > 8 else 0
    if cur < expected and cur < goal:
        send_buttons(f"Drink water {NAME}. {cur}/{goal} so far.", [
            {"text": "Drank 1", "data": "water:1"},
            {"text": "Drank 2", "data": "water:2"},
        ])

def log_mood(score):
    labels = {5: "Great", 4: "Good", 3: "Okay", 2: "Low", 1: "Bad"}
    entry = {"date": datetime.date.today().isoformat(), "time": datetime.datetime.now().strftime("%H:%M"),
             "score": score, "label": labels.get(score, "?")}
    data.setdefault("mood_log", []).append(entry)
    save_data()
    week = data["mood_log"][-7:]
    avg_m = sum(m.get("score", 3) for m in week) / max(len(week), 1)
    send(f"Logged: {labels.get(score, '?')}\nWeek avg: {avg_m:.1f}/5")

def start_spend_log():
    conv_state["mode"] = "log_spend"
    conv_state["step"] = 0
    conv_state["temp"] = {}
    send("How much? (number)")

def finish_spend_log():
    t = conv_state["temp"]
    entry = {"date": datetime.date.today().isoformat(), "amount": t.get("amount", 0),
             "category": t.get("category", "other"), "note": t.get("note", "")}
    data["budget"]["transactions"].append(entry)
    save_data()
    conv_state["mode"] = None
    month = datetime.date.today().isoformat()[:7]
    total = sum(tx["amount"] for tx in data["budget"]["transactions"] if tx.get("date", "")[:7] == month)
    remaining = DISPOSABLE - total
    pct = min(100, int(total / DISPOSABLE * 100)) if DISPOSABLE > 0 else 0
    msg = f"Logged: ${entry['amount']:.2f} ({entry['category']})\n\nSpent: ${total:.2f} / ${DISPOSABLE:.2f} disposable ({pct}%)\nLeft: ${remaining:.2f}"
    if remaining < 50:
        msg += f"\n\nYo {NAME}, you have less than $50 left for the month. Chill on spending."
    elif remaining < 100:
        msg += "\n\nGetting tight. Be careful."
    send(msg)

def get_budget_summary():
    month = datetime.date.today().isoformat()[:7]
    txns = [t for t in data["budget"]["transactions"] if t.get("date", "")[:7] == month]
    total = sum(t["amount"] for t in txns)
    cats = {}
    for t in txns:
        c = t.get("category", "other")
        cats[c] = cats.get(c, 0) + t["amount"]
    remaining = DISPOSABLE - total
    msg = f"Budget - {datetime.date.today().strftime('%B %Y')}\n\n"
    msg += f"Income: ${MONTHLY_INCOME} CAD\n"
    msg += f"Fixed: ${FIXED_TOTAL} (rent ${FIXED_EXPENSES['rent']}, weed ${FIXED_EXPENSES['weed']}, subs ${FIXED_EXPENSES['subscriptions']})\n"
    msg += f"Disposable: ${DISPOSABLE}\n"
    msg += f"Spent: ${total:.2f}\n"
    msg += f"Remaining: ${remaining:.2f}\n"
    if cats:
        msg += "\nBreakdown:\n" + "\n".join(f"- {c}: ${a:.2f}" for c, a in sorted(cats.items(), key=lambda x: -x[1]))
    send(msg)

def parse_reminder(text):
    r = ai_call(
        f'Parse reminder: "{text}"\nNow: {datetime.datetime.now().strftime("%Y-%m-%d %H:%M")}\nTimezone: Pacific\nReturn ONLY JSON: {{"task":"what","datetime":"YYYY-MM-DD HH:MM"}}',
        "Parse reminders to JSON. Return only valid JSON, nothing else."
    )
    if r:
        try:
            return json.loads(r.strip().strip("`").replace("json\n", "").strip())
        except:
            pass
    return None

def add_reminder(text):
    parsed = parse_reminder(text)
    if parsed:
        r = {"task": parsed.get("task", text), "datetime": parsed.get("datetime", ""), "done": False}
        data.setdefault("reminders", []).append(r)
        save_data()
        send(f"Reminder set: {r['task']}\nWhen: {r['datetime']}")
    else:
        data.setdefault("todos", []).append({"task": text, "created": datetime.datetime.now().isoformat(), "done": False})
        save_data()
        send(f"Added to to-do: {text}")

def show_todos():
    todos = [t for t in data.get("todos", []) if not t.get("done")]
    rems = [r for r in data.get("reminders", []) if not r.get("done")]
    msg = "Tasks\n\n"
    if rems:
        msg += "Reminders:\n" + "\n".join(f"- {r['task']} at {r['datetime']}" for r in rems) + "\n"
    buttons = []
    if todos:
        msg += "\nTo-Do:\n"
        for i, t in enumerate(todos):
            msg += f"[ ] {t['task']}\n"
            buttons.append({"text": f"Done {t['task'][:18]}", "data": f"todo_done:{i}"})
    elif not rems:
        msg = "Nothing pending. Nice."
    send(msg)
    if buttons:
        send_buttons("Complete:", buttons[:6])

def check_reminders():
    now = datetime.datetime.now().strftime("%Y-%m-%d %H:%M")
    for r in data.get("reminders", []):
        if r.get("done"):
            continue
        if r.get("datetime", "") and r["datetime"] <= now:
            send(f"REMINDER: {r['task']}")
            # Also send voice
            send_voice(r['task'])
            r["done"] = True
            save_data()

def start_sleep_log():
    conv_state["mode"] = "log_sleep"
    conv_state["step"] = 0
    conv_state["temp"] = {}
    send("When did you go to bed? (e.g. 4am)")

def finish_sleep_log():
    s = conv_state["temp"]
    entry = {"date": datetime.date.today().isoformat(), "bedtime": s.get("bedtime", "?"),
             "waketime": s.get("waketime", "?"), "hours": s.get("hours", 0), "quality": s.get("quality", 5)}
    data.setdefault("sleep_log", []).append(entry)
    save_data()
    conv_state["mode"] = None
    wk = data["sleep_log"][-7:]
    avg_h = sum(l.get("hours", 0) for l in wk) / max(len(wk), 1)
    msg = f"Sleep logged: {entry['hours']:.1f}h (quality {entry['quality']}/10)\n7-day avg: {avg_h:.1f}h"
    if avg_h < 6:
        msg += f"\n\n{NAME}, you're averaging under 6 hours. That's not it."
    elif avg_h < 7:
        msg += "\nTry to get that avg up to 7+."
    send(msg)

def show_sleep_stats():
    logs = data.get("sleep_log", [])[-14:]
    if not logs:
        send("No sleep data. Type 'sleep' to start.")
        return
    avg_h = sum(l.get("hours", 0) for l in logs) / len(logs)
    avg_q = sum(l.get("quality", 5) for l in logs) / len(logs)
    msg = f"Sleep ({len(logs)} nights)\n\nAvg: {avg_h:.1f}h, quality {avg_q:.1f}/10\n\nRecent:\n"
    for l in logs[-7:]:
        msg += f"{l.get('date', '?')[-5:]}: {l.get('hours', 0):.1f}h q:{l.get('quality', '?')}\n"
    send(msg)

def show_goals():
    goals = data.get("goals", [])
    if not goals:
        send("No goals. Use /goal to add one.")
        return
    msg = "Goals\n\n"
    buttons = []
    for i, g in enumerate(goals):
        pct = min(100, int(g.get("progress", 0) / max(g.get("target", 1), 1) * 100))
        bar = "#" * (pct // 10) + "-" * (10 - pct // 10)
        msg += f"{g['name']}\n[{bar}] {pct}% ({g.get('progress',0)}/{g.get('target',1)}) by {g.get('deadline','?')}\n\n"
        if g.get("type") != "growth":
            buttons.append({"text": f"Update {g['name'][:15]}", "data": f"goal_progress:{i}"})
    send(msg)
    if buttons:
        send_buttons("Update:", buttons[:4])

def start_add_goal():
    conv_state["mode"] = "add_goal"
    conv_state["step"] = 0
    conv_state["temp"] = {}
    send("What's the goal?")

def discord_stats():
    members, online = get_discord_count()
    hist = data.get("discord_history", [])
    msg = f"Discord Server\n\nMembers: {members}\nOnline: {online}\n"
    if len(hist) >= 2:
        week_ago = [h for h in hist if h.get("date", "") <= (datetime.date.today() - datetime.timedelta(days=7)).isoformat()]
        if week_ago:
            growth = members - week_ago[-1].get("members", members)
            msg += f"7-day growth: {'+' if growth >= 0 else ''}{growth}\n"
    msg += f"\nGoal: 20,000 members\nProgress: {members}/20000 ({members/200:.1f}%)\n"
    msg += f"\nInvite: {DISCORD_INVITE}"
    send(msg)

def show_news_with_buttons():
    global last_news_articles
    text, articles = get_news()
    last_news_articles = articles
    if not articles:
        send("No news.")
        return
    buttons = [{"text": f"{i+1}. {a.get('title','').split(' - ')[0][:28]}", "data": f"news_deep:{i}"} for i, a in enumerate(articles[:5])]
    send(f"News\n\n{text}")
    send_buttons("Deep dive:", buttons)

def deep_dive_news(idx):
    global last_news_articles
    if idx >= len(last_news_articles):
        return
    a = last_news_articles[idx]
    raw = f"Title: {a.get('title','')}\nSource: {a.get('source',{}).get('name','')}\nDesc: {a.get('description','')}\nContent: {a.get('content','')}"
    summary = ai_call(f"Summarize in 3-4 sentences then 'Why it matters:' one-liner.\n\n{raw}", "Summarize news. Be direct.")
    msg = f"{a.get('title','')}\n{a.get('source',{}).get('name','')}\n\n{summary or a.get('description','')}"
    url = a.get("url", "")
    if url:
        msg += f"\n\nFull: {url}"
    send(msg)
    send_buttons("Next?", [{"text": "More news", "data": "deep_news"}, {"text": "Related", "data": f"news_related:{idx}"}])

def search_related(idx):
    if idx >= len(last_news_articles):
        return
    topic = ai_call(f"Extract 2-3 word topic from: \"{last_news_articles[idx].get('title', '')}\"", "Return only topic words.")
    if topic:
        try:
            r = requests.get(f"https://newsapi.org/v2/everything?q={topic}&pageSize=3&sortBy=relevancy&apiKey={NEWS_API_KEY}", timeout=15)
            arts = r.json().get("articles", [])
            if arts:
                send(f"Related: {topic}\n\n" + "\n".join(f"- {a.get('title', '').split(' - ')[0]}" for a in arts))
        except:
            pass

def show_nutrition():
    today = datetime.date.today().isoformat()
    meals = [m for m in data.get("meals", []) if m.get("date") == today]
    if not meals:
        send("No meals today. Type 'ate [food]' to log.")
        return
    tc = sum(m.get("calories", 0) for m in meals)
    tp = sum(m.get("protein", 0) for m in meals)
    msg = "Nutrition\n\n" + "\n".join(f"- {m.get('time','')} {m.get('food','?')} ({m.get('calories',0)}kcal)" for m in meals)
    msg += f"\n\nTotal: {tc}kcal, {tp}g protein"
    send(msg)

def show_dashboard():
    """Full life dashboard."""
    today = datetime.date.today().isoformat()
    month = today[:7]

    # Streaks
    habit_streaks = {h: calculate_streak(d) for h, d in data.get("habits", {}).items()}
    longest = max(habit_streaks.values()) if habit_streaks else 0

    # This week workouts
    ws = (datetime.date.today() - datetime.timedelta(days=datetime.date.today().weekday())).isoformat()
    week_workouts = len([w for w in data.get("workouts", []) if w.get("date", "") >= ws])

    # Budget
    month_spent = sum(t["amount"] for t in data["budget"]["transactions"] if t.get("date", "")[:7] == month)

    # Sleep avg
    sleep_logs = data.get("sleep_log", [])[-7:]
    avg_sleep = sum(l.get("hours", 0) for l in sleep_logs) / max(len(sleep_logs), 1) if sleep_logs else 0

    # Discord
    dc_members, _ = get_discord_count()

    # Goals
    goals_msg = ""
    for g in data.get("goals", []):
        pct = min(100, int(g.get("progress", 0) / max(g.get("target", 1), 1) * 100))
        goals_msg += f"- {g['name']}: {pct}%\n"

    msg = (
        f"DASHBOARD\n\n"
        f"Workouts this week: {week_workouts}\n"
        f"Longest habit streak: {longest} days\n"
        f"Sleep avg: {avg_sleep:.1f}h\n"
        f"Budget used: ${month_spent:.0f}/${DISPOSABLE:.0f}\n"
        f"Discord: {dc_members} members\n\n"
        f"Goals:\n{goals_msg}"
    )
    send(msg)

# ═══════════════════════════════════════════════════════════════════════════════
#  CHECK-IN
# ═══════════════════════════════════════════════════════════════════════════════

CHECKIN_Q = ["Work out?", "Eat healthy?", "Sleep enough?", "Goal progress?", "Learn something?", "Avoid doomscrolling?", "Rate week 1-10:"]
CHECKIN_L = ["Workout", "Nutrition", "Sleep", "Goals", "Learning", "Focus", "Rating"]

def start_checkin():
    conv_state["mode"] = "checkin"
    conv_state["step"] = 0
    conv_state["temp"] = {"answers": []}
    send(f"Weekly check-in {NAME}. Let's go.")
    time.sleep(1)
    send_buttons(CHECKIN_Q[0], [{"text": "Yes", "data": "checkin:yes"}, {"text": "No", "data": "checkin:no"}])

def process_checkin(a):
    conv_state["temp"]["answers"].append(a)
    step = conv_state["step"] + 1
    conv_state["step"] = step
    if step < len(CHECKIN_Q):
        if step == 6:
            send(CHECKIN_Q[step])
        else:
            send_buttons(CHECKIN_Q[step], [{"text": "Yes", "data": "checkin:yes"}, {"text": "No", "data": "checkin:no"}])
    else:
        finish_checkin()

def finish_checkin():
    ans = conv_state["temp"]["answers"]
    conv_state["mode"] = None
    yc = sum(1 for a in ans[:6] if a.lower() == "yes")
    msg = "Weekly Results\n\n"
    for i, l in enumerate(CHECKIN_L):
        if i < len(ans):
            if i == 6:
                msg += f"Rating: {ans[i]}/10\n"
            else:
                msg += f"{'[x]' if ans[i].lower() == 'yes' else '[ ]'} {l}\n"
    msg += f"\nScore: {yc}/6"
    if yc == 6:
        msg += "\n\nPerfect week. That's rare. Keep it."
    elif yc >= 4:
        msg += "\n\nSolid. Build on this."
    elif yc >= 2:
        msg += "\n\nMid week. You know what to fix."
    else:
        msg += "\n\nRough one. Reset tomorrow."
    data.setdefault("checkin_history", []).append({"date": datetime.date.today().isoformat(), "score": yc, "answers": ans})
    save_data()
    send(msg)

# ═══════════════════════════════════════════════════════════════════════════════
#  JOB APPLICATION TRACKER
# ═══════════════════════════════════════════════════════════════════════════════

def start_job_log():
    conv_state["mode"] = "log_job"
    conv_state["step"] = 0
    conv_state["temp"] = {}
    send("Company name?")

def show_job_apps():
    apps = data.get("job_apps", [])
    if not apps:
        send("No applications tracked. Use 'applied' to log one.")
        return
    msg = "Job Applications\n\n"
    for j in apps[-10:]:
        status_icon = {"applied": "[ ]", "interview": "[!]", "rejected": "[x]", "offer": "[!!!]"}.get(j.get("status", ""), "?")
        msg += f"{status_icon} {j.get('company','?')} - {j.get('role','?')} ({j.get('status','?')})\n"
    msg += f"\nTotal: {len(apps)} apps"
    send(msg)

# ═══════════════════════════════════════════════════════════════════════════════
#  CALLBACK + TEXT HANDLERS
# ═══════════════════════════════════════════════════════════════════════════════

def handle_callback(cb):
    d = cb.get("data", "")
    answer_cb(cb.get("id", ""))

    if d.startswith("wtype:"):
        conv_state["temp"]["type"] = d.split(":")[1]
        conv_state["step"] = 1
        send("How many minutes?")
    elif d == "log_workout_quick":
        conv_state["temp"] = {"type": "bodyweight", "duration": 25, "notes": "suggested workout"}
        finish_workout_log()
    elif d == "suggest_workout":
        suggest_workout()
    elif d == "skip_workout":
        send("Alright. Tomorrow then.")
    elif d.startswith("habit_done:"):
        mark_habit_done(d.split(":", 1)[1])
    elif d.startswith("addhabit:"):
        h = d.split(":", 1)[1]
        data.setdefault("habits", {})[h] = []
        save_data()
        send(f"Added: {h}")
    elif d.startswith("scat:"):
        conv_state["temp"]["category"] = d.split(":")[1]
        conv_state["step"] = 2
        send("Note? (or skip)")
    elif d.startswith("checkin:"):
        process_checkin(d.split(":")[1])
    elif d.startswith("mood:"):
        log_mood(int(d.split(":")[1]))
    elif d.startswith("water:"):
        log_water(int(d.split(":")[1]))
    elif d.startswith("news_deep:"):
        deep_dive_news(int(d.split(":")[1]))
    elif d.startswith("news_related:"):
        search_related(int(d.split(":")[1]))
    elif d.startswith("goal_progress:"):
        idx = int(d.split(":")[1])
        goals = data.get("goals", [])
        if idx < len(goals):
            conv_state["mode"] = "goal_update"
            conv_state["temp"] = {"index": idx}
            send(f"Add progress (current: {goals[idx].get('progress', 0)}/{goals[idx].get('target', 1)})")
    elif d.startswith("todo_done:"):
        idx = int(d.split(":")[1])
        undone = [t for t in data.get("todos", []) if not t.get("done")]
        if idx < len(undone):
            undone[idx]["done"] = True
            save_data()
            send(f"Done: {undone[idx]['task']}")
    elif d.startswith("sq:"):
        conv_state["temp"]["quality"] = int(d.split(":")[1])
        finish_sleep_log()
    elif d == "crypto":
        send(f"Crypto\n\n{get_crypto()}")
    elif d == "crypto_analysis":
        send(f"Market Analysis\n\n{get_crypto_analysis()}")
    elif d == "emails":
        send(f"Email\n\n{get_gmail()}")
    elif d == "log_workout":
        start_workout_log()
    elif d == "log_spend":
        start_spend_log()
    elif d == "habits":
        show_habits()
    elif d == "log_mood":
        send_buttons("How you feeling?", [
            {"text": "Great", "data": "mood:5"}, {"text": "Good", "data": "mood:4"},
            {"text": "OK", "data": "mood:3"}, {"text": "Low", "data": "mood:2"}, {"text": "Bad", "data": "mood:1"}])
    elif d == "log_meal_prompt":
        send("What did you eat?")
        conv_state["mode"] = "log_meal"
    elif d == "deep_news":
        show_news_with_buttons()
    elif d == "more_news":
        show_news_with_buttons()
    elif d == "discord_stats":
        discord_stats()
    elif d == "full_brief":
        morning_briefing()

def handle_text(text):
    t = text.strip()
    tl = t.lower()

    # Conversation flows
    if conv_state["mode"] == "log_workout":
        if conv_state["step"] == 1:
            try:
                conv_state["temp"]["duration"] = int(tl)
                conv_state["step"] = 2
                send("Notes? (or skip)")
            except:
                send("Number please.")
        elif conv_state["step"] == 2:
            conv_state["temp"]["notes"] = "" if tl == "skip" else t
            finish_workout_log()
        return

    if conv_state["mode"] == "log_spend":
        if conv_state["step"] == 0:
            try:
                conv_state["temp"]["amount"] = float(tl.replace("$", ""))
                conv_state["step"] = 1
                send_buttons("Category?", [
                    {"text": "Food", "data": "scat:food"}, {"text": "Weed", "data": "scat:weed"},
                    {"text": "Transport", "data": "scat:transport"}, {"text": "Fun", "data": "scat:fun"},
                    {"text": "Shopping", "data": "scat:shopping"}, {"text": "Bills", "data": "scat:bills"},
                    {"text": "Other", "data": "scat:other"}])
            except:
                send("Number please.")
        elif conv_state["step"] == 2:
            conv_state["temp"]["note"] = "" if tl == "skip" else t
            finish_spend_log()
        return

    if conv_state["mode"] == "checkin":
        process_checkin(t)
        return

    if conv_state["mode"] == "log_sleep":
        if conv_state["step"] == 0:
            conv_state["temp"]["bedtime"] = t
            conv_state["step"] = 1
            send("Wake time?")
        elif conv_state["step"] == 1:
            conv_state["temp"]["waketime"] = t
            hrs = ai_call(f"Bed: {conv_state['temp']['bedtime']}, Wake: {t}. Hours? Return ONLY a number.", "Return only a decimal number.")
            try:
                conv_state["temp"]["hours"] = float(hrs.strip())
            except:
                conv_state["temp"]["hours"] = 7.0
            send_buttons("Quality?", [
                {"text": "Great(9)", "data": "sq:9"}, {"text": "Good(7)", "data": "sq:7"},
                {"text": "OK(5)", "data": "sq:5"}, {"text": "Bad(3)", "data": "sq:3"}])
        return

    if conv_state["mode"] == "log_meal":
        log_meal(t)
        conv_state["mode"] = None
        return

    if conv_state["mode"] == "add_goal":
        if conv_state["step"] == 0:
            conv_state["temp"]["name"] = t
            conv_state["step"] = 1
            send("Target number?")
        elif conv_state["step"] == 1:
            try:
                conv_state["temp"]["target"] = float(tl)
            except:
                conv_state["temp"]["target"] = 100
            conv_state["step"] = 2
            send("Deadline? (YYYY-MM-DD)")
        elif conv_state["step"] == 2:
            g = {"name": conv_state["temp"]["name"], "target": conv_state["temp"]["target"],
                 "progress": 0, "deadline": t, "created": datetime.date.today().isoformat()}
            data.setdefault("goals", []).append(g)
            save_data()
            conv_state["mode"] = None
            send(f"Goal added: {g['name']} - {g['target']} by {g['deadline']}")
        return

    if conv_state["mode"] == "goal_update":
        try:
            amt = float(tl)
            idx = conv_state["temp"]["index"]
            data["goals"][idx]["progress"] += amt
            save_data()
            conv_state["mode"] = None
            g = data["goals"][idx]
            pct = min(100, int(g["progress"] / max(g["target"], 1) * 100))
            send(f"{g['name']}: {pct}% ({g['progress']}/{g['target']})")
        except:
            send("Number please.")
        return

    if conv_state["mode"] == "log_job":
        if conv_state["step"] == 0:
            conv_state["temp"]["company"] = t
            conv_state["step"] = 1
            send("What role?")
        elif conv_state["step"] == 1:
            conv_state["temp"]["role"] = t
            entry = {"company": conv_state["temp"]["company"], "role": t,
                     "date": datetime.date.today().isoformat(), "status": "applied"}
            data.setdefault("job_apps", []).append(entry)
            save_data()
            conv_state["mode"] = None
            total = len(data["job_apps"])
            send(f"Logged: {entry['company']} - {entry['role']}\nTotal applications: {total}")
        return

    # Commands
    if tl in ["brief", "/brief", "/start", "morning"]:
        morning_briefing()
    elif tl in ["evening", "/evening", "recap"]:
        evening_recap()
    elif tl in ["crypto", "/crypto", "prices"]:
        send(f"Crypto\n\n{get_crypto()}")
    elif tl in ["analysis", "/analysis", "signals"]:
        send(f"Market Analysis\n\n{get_crypto_analysis()}")
    elif tl in ["weather", "/weather"]:
        w = get_weather()
        alerts = get_weather_forecast()
        msg = f"Weather\n\n{w}"
        if alerts:
            msg += "\n\nComing up:\n" + "\n".join(alerts)
        send(msg)
    elif tl in ["news", "/news"]:
        show_news_with_buttons()
    elif tl in ["events", "/events", "calendar"]:
        send(f"Calendar\n\n{get_calendar()}")
    elif tl in ["emails", "/emails", "mail"]:
        send(f"Email\n\n{get_gmail()}")
    elif tl in ["quote", "/quote"]:
        send(get_quote())
    elif tl in ["workout", "/workout", "gym"]:
        start_workout_log()
    elif tl in ["suggest", "/suggest"]:
        suggest_workout()
    elif tl in ["spend", "/spend", "spent"]:
        start_spend_log()
    elif tl in ["habits", "/habits"]:
        show_habits()
    elif tl in ["budget", "/budget"]:
        get_budget_summary()
    elif tl in ["checkin", "/checkin"]:
        start_checkin()
    elif tl in ["sleep", "/sleep"]:
        start_sleep_log()
    elif tl in ["sleepstats", "/sleepstats"]:
        show_sleep_stats()
    elif tl in ["mood", "/mood"]:
        send_buttons("How you feeling?", [
            {"text": "Great", "data": "mood:5"}, {"text": "Good", "data": "mood:4"},
            {"text": "OK", "data": "mood:3"}, {"text": "Low", "data": "mood:2"}, {"text": "Bad", "data": "mood:1"}])
    elif tl in ["goals", "/goals"]:
        show_goals()
    elif tl in ["goal", "/goal", "/addgoal"]:
        start_add_goal()
    elif tl in ["water", "/water"]:
        log_water(1)
    elif tl in ["nutrition", "/nutrition", "meals", "/meals"]:
        show_nutrition()
    elif tl in ["todos", "/todos", "tasks", "/tasks"]:
        show_todos()
    elif tl in ["discord", "/discord"]:
        discord_stats()
    elif tl in ["dashboard", "/dashboard", "dash"]:
        show_dashboard()
    elif tl in ["applied", "/applied"]:
        start_job_log()
    elif tl in ["jobs", "/jobs"]:
        show_job_apps()
    elif tl.startswith(("remind me", "/remind")):
        add_reminder(t)
    elif tl.startswith(("ate ", "i ate ", "i had ")):
        log_meal(re.sub(r'^(ate |i ate |i had )', '', tl))
    elif tl.startswith("/setbudget"):
        try:
            amt = float(tl.split()[-1])
            data["budget"]["monthly_limit"] = amt
            save_data()
            send(f"Budget: ${amt:.2f}/month")
        except:
            send("Usage: /setbudget 400")
    elif tl.startswith("/addhabit"):
        parts = t.split(maxsplit=1)
        if len(parts) > 1:
            data.setdefault("habits", {})[parts[1].strip()] = []
            save_data()
            send(f"Added: {parts[1].strip()}")
        else:
            send_buttons("Quick add:", [
                {"text": "Workout", "data": "addhabit:workout"}, {"text": "Reading", "data": "addhabit:reading"},
                {"text": "Meditate", "data": "addhabit:meditate"}, {"text": "Water", "data": "addhabit:water"},
                {"text": "No junk food", "data": "addhabit:no junk food"}, {"text": "Code", "data": "addhabit:code"}])
    elif tl.startswith("/addcrypto"):
        parts = t.split(maxsplit=1)
        if len(parts) > 1:
            coin = parts[1].strip().lower()
            if coin not in data["crypto_watchlist"]:
                data["crypto_watchlist"].append(coin)
                save_data()
                send(f"Added {coin}")
        else:
            send("Usage: /addcrypto dogecoin")
    elif tl.startswith(("/yt ", "/youtube ")):
        username = t.split(maxsplit=1)[1].strip()
        try:
            r = requests.get(f"https://mixerno.space/api/youtube-channel-counter/user/{username}", timeout=15)
            subs = r.json().get("counts", [{}])[0].get("count", "?")
            send(f"YouTube - {username}\nSubs: {subs:,}" if isinstance(subs, int) else f"Couldn't get stats for {username}")
        except:
            send(f"Couldn't fetch stats for {username}")
    elif tl in ["help", "/help", "menu"]:
        send(
            f"{NAME}'s Assistant v4\n\n"
            "DAILY: brief, evening, weather, news, crypto, analysis, events, emails, quote\n\n"
            "TRACKING: workout, suggest, 'ate [food]', water, spend, habits, mood, sleep\n\n"
            "PLANNING: 'remind me to...', todos, goals, budget, checkin\n\n"
            "GROWTH: discord, dashboard, applied, jobs\n\n"
            "STATS: sleepstats, nutrition, /yt [channel]\n\n"
            "SETTINGS: /setbudget, /addhabit, /addcrypto"
        )
    else:
        if GROQ_API_KEY:
            resp = ai_call(
                f"{NAME} said: {text}",
                f"You are {NAME}'s AI assistant. He's 22, night owl, gamer, tight budget. "
                "Be helpful, slightly sarcastic, honest. If he mentions food, estimate calories. "
                "If he mentions a task, offer to set a reminder. Keep it brief."
            )
            if resp:
                send(resp)
            else:
                send("Type 'help' for commands.")
        else:
            send("Type 'help' for commands.")

# ═══════════════════════════════════════════════════════════════════════════════
#  WEBHOOK + SCHEDULER
# ═══════════════════════════════════════════════════════════════════════════════

@app.route(f"/webhook/{TELEGRAM_TOKEN}", methods=["POST"])
def webhook():
    """Handle incoming Telegram updates via webhook."""
    try:
        update = request.get_json()
        if not update:
            return "ok", 200

        if "callback_query" in update:
            cb = update["callback_query"]
            if str(cb.get("message", {}).get("chat", {}).get("id", "")) == CHAT_ID:
                handle_callback(cb)
        elif "message" in update:
            msg = update["message"]
            txt = msg.get("text", "").strip()
            cid = str(msg.get("chat", {}).get("id", ""))
            if cid == CHAT_ID and txt:
                handle_text(txt)
            # Photo handling (receipt scanning)
            elif cid == CHAT_ID and msg.get("photo"):
                caption = msg.get("caption", "")
                if caption:
                    if any(w in caption.lower() for w in ["receipt", "spent", "bought", "paid"]):
                        send("Got the photo. What was the total amount?")
                        conv_state["mode"] = "log_spend"
                        conv_state["step"] = 0
    except Exception as e:
        log.error(f"Webhook error: {e}")
    return "ok", 200

def setup_webhook():
    """Set up Telegram webhook."""
    if not WEBHOOK_URL or not TELEGRAM_TOKEN:
        log.warning("No WEBHOOK_URL set, falling back to polling")
        return False
    url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/setWebhook"
    webhook_path = f"{WEBHOOK_URL}/webhook/{TELEGRAM_TOKEN}"
    r = requests.post(url, json={"url": webhook_path, "allowed_updates": ["message", "callback_query"]}, timeout=15)
    if r.status_code == 200 and r.json().get("ok"):
        log.info(f"Webhook set: {webhook_path}")
        return True
    log.error(f"Webhook setup failed: {r.text}")
    return False

def telegram_polling():
    """Fallback polling if webhook not configured."""
    last_id = None
    while True:
        try:
            r = requests.get(f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/getUpdates",
                params={"timeout": 15, "offset": last_id, "allowed_updates": ["message", "callback_query"]}, timeout=20)
            for u in r.json().get("result", []):
                last_id = u["update_id"] + 1
                if "callback_query" in u:
                    cb = u["callback_query"]
                    if str(cb.get("message", {}).get("chat", {}).get("id", "")) == CHAT_ID:
                        handle_callback(cb)
                elif "message" in u:
                    msg = u["message"]
                    txt = msg.get("text", "").strip()
                    cid = str(msg.get("chat", {}).get("id", ""))
                    if cid == CHAT_ID and txt:
                        handle_text(txt)
        except Exception as e:
            log.error(f"Poll error: {e}")
        time.sleep(2)

def check_upcoming_events():
    creds = get_google_creds()
    if not creds:
        return
    try:
        svc = build("calendar", "v3", credentials=creds)
        now = datetime.datetime.utcnow()
        soon = now + datetime.timedelta(minutes=31)
        evts = svc.events().list(calendarId="primary", timeMin=now.isoformat() + "Z",
            timeMax=soon.isoformat() + "Z", singleEvents=True, orderBy="startTime").execute().get("items", [])
        for e in evts:
            eid = e["id"]
            if eid not in alerted_events:
                name = e.get("summary", "?")
                start = e["start"].get("dateTime", e["start"].get("date"))
                try:
                    ts = datetime.datetime.fromisoformat(start.replace("Z", "+00:00")).astimezone().strftime("%I:%M %p")
                except:
                    ts = start
                send(f"HEADS UP: {name} at {ts} (~30min)")
                send_voice(f"Hey {NAME}, {name} starts in about 30 minutes")
                alerted_events.add(eid)
    except Exception as e:
        log.error(f"Event check error: {e}")

def scheduler():
    while True:
        try:
            now = datetime.datetime.now()
            h, m = now.hour, now.minute
            mh = data.get("settings", {}).get("morning_hour", 11)
            midh = data.get("settings", {}).get("midday_hour", 15)
            eh = data.get("settings", {}).get("evening_hour", 22)

            if h == mh and m == 0:
                morning_briefing()
                time.sleep(61)
            if h == midh and m == 0:
                midday_check()
                time.sleep(61)
            if h == eh and m == 0:
                evening_recap()
                time.sleep(61)

            # Water every 2h between 11am-10pm (Ashton's awake hours)
            if data.get("settings", {}).get("water_reminder") and mh <= h <= eh and h % 2 == 0 and m == 30:
                water_reminder()
                time.sleep(61)

            # Weather alerts every 3h
            if data.get("settings", {}).get("weather_alerts") and h % 3 == 0 and m == 15:
                alerts = get_weather_forecast()
                if alerts:
                    send("Weather heads up:\n" + "\n".join(alerts[:3]))
                time.sleep(61)

            # Sunday 2pm checkin (Ashton is awake by then)
            if now.weekday() == 6 and h == 14 and m == 0:
                start_checkin()
                time.sleep(61)

            # Follow up if no workout logged by 8pm
            if h == 20 and m == 0:
                today = datetime.date.today().isoformat()
                wk = [w for w in data.get("workouts", []) if w.get("date") == today]
                if not wk and "workout" in data.get("habits", {}):
                    send_buttons(f"No workout today {NAME}. Still time.", [
                        {"text": "Suggest one", "data": "suggest_workout"},
                        {"text": "Skip today", "data": "skip_workout"}])
                    time.sleep(61)

            check_reminders()
            check_upcoming_events()
        except Exception as e:
            log.error(f"Scheduler error: {e}")
        time.sleep(60)

# ═══════════════════════════════════════════════════════════════════════════════
#  FLASK ROUTES
# ═══════════════════════════════════════════════════════════════════════════════

from google_auth_oauthlib.flow import Flow

def get_flow(state=None, code_verifier=None):
    if not GOOGLE_CREDENTIALS:
        raise ValueError("GOOGLE_CREDENTIALS missing")
    flow = Flow.from_client_config(json.loads(GOOGLE_CREDENTIALS), scopes=SCOPES, redirect_uri=REDIRECT_URI, state=state)
    if code_verifier:
        flow.code_verifier = code_verifier
    return flow

@app.route("/")
def home():
    return f'<h2>{NAME}\'s AI Assistant v4</h2><p><a href="/login">Connect Google</a></p>'

@app.route("/login")
def login():
    cv = secrets.token_urlsafe(64)
    session["code_verifier"] = cv
    flow = get_flow(code_verifier=cv)
    auth_url, state = flow.authorization_url(access_type="offline", include_granted_scopes="true", code_challenge_method="S256", prompt="consent")
    session["state"] = state
    return redirect(auth_url)

@app.route("/oauth2callback")
def oauth2callback():
    if "state" not in session or "code_verifier" not in session:
        return "Session expired.", 400
    flow = get_flow(state=session["state"], code_verifier=session["code_verifier"])
    flow.fetch_token(authorization_response=request.url)
    creds = flow.credentials
    td = {"token": creds.token, "refresh_token": creds.refresh_token, "token_uri": creds.token_uri,
          "client_id": creds.client_id, "client_secret": creds.client_secret, "scopes": list(creds.scopes)}
    os.environ["GOOGLE_TOKEN"] = json.dumps(td)
    log.info("Google token saved")
    send("Google connected!")
    return "Connected! Check Telegram."

@app.route("/brief")
def manual_brief():
    morning_briefing()
    return "Sent!"

@app.route("/health")
def health():
    return "OK", 200

# ═══════════════════════════════════════════════════════════════════════════════
#  STARTUP
# ═══════════════════════════════════════════════════════════════════════════════

def start_threads():
    threading.Thread(target=scheduler, daemon=True).start()
    webhook_ok = setup_webhook()
    if not webhook_ok:
        threading.Thread(target=telegram_polling, daemon=True).start()
        log.info("Using polling mode")
    log.info(f"{NAME}'s Assistant v4 running")

if __name__ == "__main__":
    start_threads()
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 8080)), debug=False)
else:
    start_threads()
