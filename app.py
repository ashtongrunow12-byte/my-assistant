"""
=============================================================================
  ASHTON'S AI ASSISTANT v3
  Telegram bot on Railway - full lifestyle assistant
=============================================================================
"""

from flask import Flask, redirect, request, session
import threading, time, os, json, datetime, secrets, requests, re
from google_auth_oauthlib.flow import Flow
from googleapiclient.discovery import build
from google.oauth2.credentials import Credentials

app = Flask(__name__)
app.secret_key = os.environ.get("FLASK_SECRET_KEY", "dev-secret-change-this")

TELEGRAM_TOKEN = os.environ.get("TELEGRAM_TOKEN")
CHAT_ID = os.environ.get("CHAT_ID")
GOOGLE_CREDENTIALS = os.environ.get("GOOGLE_CREDENTIALS")
NEWS_API_KEY = os.environ.get("NEWS_API_KEY")
GROQ_API_KEY = os.environ.get("GROQ_API_KEY", "")
SCOPES = ["https://www.googleapis.com/auth/calendar.readonly","https://www.googleapis.com/auth/gmail.readonly"]
REDIRECT_URI = "https://my-assistant-production-2fe1.up.railway.app/oauth2callback"
DATA_FILE = "assistant_data.json"
DEFAULT_DATA = {"alerted_events":[],"habits":{},"workouts":[],"budget":{"monthly_limit":2000,"transactions":[]},"crypto_watchlist":["bitcoin","ethereum","solana"],"checkin_history":[],"reminders":[],"todos":[],"sleep_log":[],"goals":[],"meals":[],"water":{"daily_goal":8,"log":{}},"mood_log":[],"settings":{"morning_hour":9,"evening_hour":21,"water_reminder":True,"weather_alerts":True},"last_weather":None}

def load_data():
    if os.path.exists(DATA_FILE):
        try:
            with open(DATA_FILE,"r") as f: saved=json.load(f)
            for k,v in DEFAULT_DATA.items():
                if k not in saved: saved[k]=v
            return saved
        except: pass
    return dict(DEFAULT_DATA)

def save_data():
    try:
        with open(DATA_FILE,"w") as f: json.dump(data,f,indent=2,default=str)
    except Exception as e: print(f"[SAVE ERR] {e}")

data = load_data()
alerted_events = set(data.get("alerted_events",[]))
conv_state = {"mode":None,"step":0,"temp":{}}
last_news_articles = []

def send(message,reply_markup=None,parse_mode="Markdown"):
    if not TELEGRAM_TOKEN or not CHAT_ID: return
    url=f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
    payload={"chat_id":CHAT_ID,"text":message,"parse_mode":parse_mode}
    if reply_markup: payload["reply_markup"]=json.dumps(reply_markup)
    try:
        r=requests.post(url,json=payload,timeout=15)
        if r.status_code!=200:
            payload.pop("parse_mode",None)
            requests.post(url,json=payload,timeout=15)
    except Exception as e: print(f"[SEND ERR] {e}")

def send_buttons(message,buttons):
    kb={"inline_keyboard":[]}; row=[]
    for i,btn in enumerate(buttons):
        row.append({"text":btn["text"],"callback_data":btn["data"]})
        if len(row)>=2 or i==len(buttons)-1: kb["inline_keyboard"].append(row); row=[]
    send(message,reply_markup=kb)

def answer_callback(cb_id,text=""):
    try: requests.post(f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/answerCallbackQuery",json={"callback_query_id":cb_id,"text":text},timeout=10)
    except: pass

def ai_call(prompt,system_msg=None,max_tokens=500):
    if not GROQ_API_KEY: return None
    try:
        msgs=[]
        if system_msg: msgs.append({"role":"system","content":system_msg})
        msgs.append({"role":"user","content":prompt})
        r=requests.post("https://api.groq.com/openai/v1/chat/completions",headers={"Authorization":f"Bearer {GROQ_API_KEY}","Content-Type":"application/json"},json={"model":"llama-3.3-70b-versatile","messages":msgs,"max_tokens":max_tokens,"temperature":0.4},timeout=30)
        if r.status_code==200: return r.json()["choices"][0]["message"]["content"].strip()
        return None
    except: return None

def ai_summarize(raw,context=""):
    r=ai_call(f"Context: {context}\n\nData:\n{raw}","You are Ashton's personal AI assistant. Summarize concisely for Telegram. Use emojis sparingly. Be direct. Under 300 words.")
    return r or raw

def get_weather():
    try:
        r=requests.get("https://wttr.in/Abbotsford,BC?format=j1",timeout=20); d=r.json(); c=d["current_condition"][0]
        return f"{c['weatherDesc'][0]['value']}, {c['temp_C']}C (feels {c['FeelsLikeC']}C), humidity {c['humidity']}%"
    except: return "Weather unavailable"

def get_weather_forecast():
    try:
        r=requests.get("https://wttr.in/Abbotsford,BC?format=j1",timeout=20); d=r.json()
        current=d["current_condition"][0]; cur_desc=current["weatherDesc"][0]["value"].lower(); cur_temp=int(current["temp_C"])
        hourly=d.get("weather",[{}])[0].get("hourly",[]); now_hour=datetime.datetime.now().hour; alerts=[]
        for h in hourly:
            ht=int(h.get("time","0").replace("00","").strip() or "0")
            if ht<=now_hour or ht>now_hour+4: continue
            rain=int(h.get("chanceofrain","0")); temp=int(h.get("tempC",cur_temp)); desc=h.get("weatherDesc",[{}])[0].get("value","").lower()
            if rain>50 and "rain" not in cur_desc: alerts.append(f"Rain likely ~{ht}:00 ({rain}%)")
            if rain>70: alerts.append(f"Heavy rain ~{ht}:00 - grab umbrella!")
            if abs(temp-cur_temp)>=5: alerts.append(f"Temp shift to {temp}C by {ht}:00")
            if "snow" in desc and "snow" not in cur_desc: alerts.append(f"Snow expected ~{ht}:00!")
            if "thunder" in desc: alerts.append(f"Thunderstorm possible ~{ht}:00")
        return alerts
    except: return []

def get_news():
    try:
        r=requests.get(f"https://newsapi.org/v2/top-headlines?language=en&pageSize=5&apiKey={NEWS_API_KEY}",timeout=15)
        articles=r.json().get("articles",[])
        lines=[f"{i+1}. {a.get('title','').split(' - ')[0]}" for i,a in enumerate(articles)]
        return "\n".join(lines),articles
    except: return "News unavailable",[]

def get_quote():
    try: r=requests.get("https://zenquotes.io/api/random",timeout=10); d=r.json()[0]; return f'"{d["q"]}"\n- {d["a"]}'
    except: return "Keep pushing!"

def get_calendar():
    try:
        cj=os.environ.get("GOOGLE_TOKEN")
        if not cj: return "No calendar connected."
        creds=Credentials(**json.loads(cj)); svc=build("calendar","v3",credentials=creds)
        now=datetime.datetime.utcnow().replace(hour=0,minute=0,second=0).isoformat()+"Z"
        evts=svc.events().list(calendarId="primary",timeMin=now,maxResults=5,singleEvents=True,orderBy="startTime").execute().get("items",[])
        if not evts: return "No events today."
        return "\n".join(f"- {e.get('summary','?')} at {datetime.datetime.fromisoformat(e['start'].get('dateTime',e['start'].get('date')).replace('Z','')).strftime('%I:%M %p')}" for e in evts)
    except Exception as e: return f"Calendar error: {e}"

def get_gmail():
    try:
        cj=os.environ.get("GOOGLE_TOKEN")
        if not cj: return "No Gmail connected."
        creds=Credentials(**json.loads(cj)); g=build("gmail","v1",credentials=creds)
        msgs=g.users().messages().list(userId="me",labelIds=["UNREAD","INBOX"],maxResults=5).execute().get("messages",[])
        if not msgs: return "Inbox clear."
        lines=[]
        for m in msgs:
            msg=g.users().messages().get(userId="me",id=m["id"],format="metadata",metadataHeaders=["From","Subject"]).execute()
            hdrs=msg.get("payload",{}).get("headers",[])
            subj=next((h["value"] for h in hdrs if h["name"]=="Subject"),"?")
            sender=next((h["value"] for h in hdrs if h["name"]=="From"),"?").split("<")[0].strip()[:25]
            lines.append(f"- {sender}: {subj}")
        return "\n".join(lines)
    except Exception as e: return f"Gmail error: {e}"

def get_crypto():
    try:
        coins=data.get("crypto_watchlist",["bitcoin","ethereum","solana"]); ids=",".join(coins)
        r=requests.get(f"https://api.coingecko.com/api/v3/simple/price?ids={ids}&vs_currencies=usd&include_24hr_change=true",timeout=15)
        prices=r.json(); syms={"bitcoin":"BTC","ethereum":"ETH","solana":"SOL","dogecoin":"DOGE","cardano":"ADA","ripple":"XRP","litecoin":"LTC","polkadot":"DOT"}
        lines=[]
        for c in coins:
            if c in prices:
                p=prices[c]["usd"]; ch=prices[c].get("usd_24h_change",0); s=syms.get(c,c.upper()[:4])
                ps=f"${p:,.2f}" if p>=1 else f"${p:.6f}"
                lines.append(f"{'G' if ch>=0 else 'R'} {s}: {ps} ({ch:+.1f}%)")
        return "\n".join(lines) if lines else "Crypto unavailable."
    except: return "Crypto unavailable."

def calculate_streak(dates):
    if not dates: return 0
    sd=sorted(set(dates),reverse=True); today=datetime.date.today(); streak=0
    for i,d in enumerate(sd):
        if d==(today-datetime.timedelta(days=i)).isoformat(): streak+=1
        else: break
    return streak

def parse_reminder(text):
    r=ai_call(f'Parse reminder: "{text}"\nNow: {datetime.datetime.now().strftime("%Y-%m-%d %H:%M")}\nReturn ONLY JSON: {{"task":"what","datetime":"YYYY-MM-DD HH:MM"}}', "Parse reminders to JSON. Return only JSON.")
    if r:
        try: return json.loads(r.strip().strip("`").replace("json\n","").strip())
        except: pass
    return None

def estimate_calories(food):
    r=ai_call(f'Estimate calories for: "{food}"\nReturn ONLY JSON: {{"food":"name","calories":num,"protein_g":num,"carbs_g":num,"fat_g":num}}', "Estimate calories. Return only JSON.")
    if r:
        try: return json.loads(r.strip().strip("`").replace("json\n","").strip())
        except: pass
    return {"food":food,"calories":0,"protein_g":0,"carbs_g":0,"fat_g":0}

def morning_briefing():
    weather=get_weather(); calendar=get_calendar(); gmail=get_gmail(); news_text,_=get_news(); quote=get_quote(); crypto=get_crypto()
    alerts=get_weather_forecast(); alert_txt="\n".join(alerts) if alerts else ""
    today=datetime.date.today().isoformat()
    habit_lines=[f"{h}: {calculate_streak(d)} day streak" for h,d in data.get("habits",{}).items()]
    reminders=[r for r in data.get("reminders",[]) if not r.get("done") and r.get("datetime","").startswith(today)]
    rem_txt="\n".join(f"- {r['task']} at {r['datetime'][-5:]}" for r in reminders)
    raw=f"WEATHER: {weather}\n{'ALERTS: '+alert_txt if alert_txt else ''}\nCALENDAR:\n{calendar}\nEMAIL:\n{gmail}\nNEWS:\n{news_text}\nCRYPTO:\n{crypto}\nHABITS:\n"+("\n".join(habit_lines) or "None")+f"\n{'REMINDERS:\n'+rem_txt if rem_txt else ''}\nQUOTE: {quote}"
    summary=ai_summarize(raw,"morning briefing - mention weather alerts prominently if any")
    send(f"Good morning Ashton\n\n{summary}",parse_mode=None)
    time.sleep(1)
    send_buttons("Quick actions:",[{"text":"News","data":"deep_news"},{"text":"Crypto","data":"crypto"},{"text":"Workout","data":"log_workout"},{"text":"Meal","data":"log_meal_prompt"},{"text":"Habits","data":"habits"},{"text":"Mood","data":"log_mood"}])

def evening_recap():
    today=datetime.date.today().isoformat()
    wk=[w for w in data.get("workouts",[]) if w.get("date")==today]; wk_txt="\n".join(f"- {w['type']} {w['duration']}min" for w in wk) or "No workout"
    txns=[t for t in data["budget"]["transactions"] if t.get("date")==today]; spent=sum(t["amount"] for t in txns)
    month_spent=sum(t["amount"] for t in data["budget"]["transactions"] if t.get("date","")[:7]==today[:7])
    meals=[m for m in data.get("meals",[]) if m.get("date")==today]; cal=sum(m.get("calories",0) for m in meals)
    water=data.get("water",{}).get("log",{}).get(today,0); wgoal=data.get("water",{}).get("daily_goal",8)
    done=[h for h,d in data.get("habits",{}).items() if today in d]; missed=[h for h,d in data.get("habits",{}).items() if today not in d]
    moods=[m for m in data.get("mood_log",[]) if m.get("date")==today]
    raw=f"WORKOUT: {wk_txt}\nNUTRITION: {cal}kcal, {len(meals)} meals\nWATER: {water}/{wgoal}\nSPENDING: ${spent:.2f} today, ${month_spent:.2f} month\nHABITS DONE: {', '.join(done) or 'None'}\nMISSED: {', '.join(missed) or 'All done!'}\nMOOD: {moods[-1].get('label','?') if moods else 'Not logged'}\nCRYPTO:\n{get_crypto()}"
    summary=ai_summarize(raw,"evening recap - honest but encouraging")
    send(f"Evening Recap\n\n{summary}",parse_mode=None)
    if missed: send_buttons("Still time:",[{"text":f"Done {h}","data":f"habit_done:{h}"} for h in missed[:4]])

def midday_check():
    today=datetime.date.today().isoformat()
    missed=[h for h,d in data.get("habits",{}).items() if today not in d]
    wk=[w for w in data.get("workouts",[]) if w.get("date")==today]
    water=data.get("water",{}).get("log",{}).get(today,0)
    msg=f"Midday Check\n\n"
    if not wk: msg+="No workout yet\n"
    msg+=f"Water: {water}/{data.get('water',{}).get('daily_goal',8)}\n"
    if missed: msg+=f"Habits to do: {', '.join(missed)}\n"
    alerts=get_weather_forecast()
    if alerts: msg+="\nWeather Alert:\n"+"\n".join(alerts[:3])
    msg+=f"\n\nCrypto\n{get_crypto()}"
    send(msg,parse_mode=None)

def show_news_with_buttons():
    global last_news_articles
    text,articles=get_news(); last_news_articles=articles
    if not articles: send("No news."); return
    buttons=[{"text":f"{i+1}. {a.get('title','').split(' - ')[0][:28]}","data":f"news_deep:{i}"} for i,a in enumerate(articles[:5])]
    send(f"Top News\n\n{text}",parse_mode=None)
    send_buttons("Tap for deep dive:",buttons)

def deep_dive_news(idx):
    global last_news_articles
    if idx>=len(last_news_articles): send("Not found."); return
    a=last_news_articles[idx]
    raw=f"Title: {a.get('title','')}\nSource: {a.get('source',{}).get('name','')}\nDesc: {a.get('description','')}\nContent: {a.get('content','')}"
    summary=ai_call(f"Summarize this article in 3-4 sentences, then add 'Why it matters:' one-liner.\n\n{raw}","Summarize news clearly.")
    msg=f"{a.get('title','')}\n{a.get('source',{}).get('name','')}\n\n{summary or a.get('description','')}"
    url=a.get("url","")
    if url: msg+=f"\n\nFull article: {url}"
    send(msg,parse_mode=None)
    send_buttons("Next?",[{"text":"More news","data":"deep_news"},{"text":"Related","data":f"news_related:{idx}"}])

def search_related(idx):
    if idx>=len(last_news_articles): return
    topic=ai_call(f"Extract 2-3 word topic from: \"{last_news_articles[idx].get('title','')}\"","Return only topic words.")
    if topic:
        try:
            r=requests.get(f"https://newsapi.org/v2/everything?q={topic}&pageSize=3&sortBy=relevancy&apiKey={NEWS_API_KEY}",timeout=15)
            arts=r.json().get("articles",[])
            if arts: send(f"Related: {topic}\n\n"+"\n".join(f"- {a.get('title','').split(' - ')[0]}" for a in arts),parse_mode=None)
            else: send("No related articles found.")
        except: send("Search failed.")

def log_meal(food):
    est=estimate_calories(food)
    entry={"date":datetime.date.today().isoformat(),"time":datetime.datetime.now().strftime("%H:%M"),"food":est.get("food",food),"calories":est.get("calories",0),"protein":est.get("protein_g",0),"carbs":est.get("carbs_g",0),"fat":est.get("fat_g",0)}
    data.setdefault("meals",[]).append(entry); save_data()
    today=datetime.date.today().isoformat(); tm=[m for m in data["meals"] if m.get("date")==today]
    tc=sum(m.get("calories",0) for m in tm); tp=sum(m.get("protein",0) for m in tm)
    send(f"Meal Logged\n\n{entry['food']}\n~{entry['calories']}kcal | P:{entry['protein']}g C:{entry['carbs']}g F:{entry['fat']}g\n\nToday: {tc}kcal, {tp}g protein ({len(tm)} meals)",parse_mode=None)

def show_nutrition():
    today=datetime.date.today().isoformat(); meals=[m for m in data.get("meals",[]) if m.get("date")==today]
    if not meals: send("No meals today. Type 'ate [food]' to log."); return
    tc=sum(m.get("calories",0) for m in meals); tp=sum(m.get("protein",0) for m in meals)
    msg="Today's Nutrition\n\n"+"\n".join(f"- {m.get('time','')} {m.get('food','?')} ({m.get('calories',0)}kcal)" for m in meals)
    msg+=f"\n\nTotal: {tc}kcal, {tp}g protein"
    send(msg,parse_mode=None)

def log_water(n=1):
    today=datetime.date.today().isoformat(); wl=data.setdefault("water",{"daily_goal":8,"log":{}})
    cur=wl["log"].get(today,0)+n; wl["log"][today]=cur; save_data()
    goal=wl["daily_goal"]; msg=f"Water: {cur}/{goal} glasses"
    if cur>=goal: msg+="\nDaily goal reached!"
    send(msg,parse_mode=None)

def water_reminder():
    today=datetime.date.today().isoformat(); cur=data.get("water",{}).get("log",{}).get(today,0)
    goal=data.get("water",{}).get("daily_goal",8); hour=datetime.datetime.now().hour
    expected=int(goal*(hour-8)/14) if hour>8 else 0
    if cur<expected and cur<goal:
        send_buttons(f"Water check: {cur}/{goal} glasses. Drink up!",[{"text":"Log 1","data":"water:1"},{"text":"Log 2","data":"water:2"}])

def log_mood(score):
    labels={5:"Great",4:"Good",3:"Okay",2:"Low",1:"Bad"}
    entry={"date":datetime.date.today().isoformat(),"time":datetime.datetime.now().strftime("%H:%M"),"score":score,"label":labels.get(score,"?")}
    data.setdefault("mood_log",[]).append(entry); save_data()
    week=data["mood_log"][-7:]; avg_m=sum(m.get("score",3) for m in week)/max(len(week),1)
    emojis={5:"=)",4:"=)",3:"=|",2:"=(",1:"=("}
    msg=f"Logged: {labels.get(score,'?')}\n\nThis week: "+" ".join(emojis.get(m.get("score",3),"?") for m in week)
    msg+=f"\nAvg: {avg_m:.1f}/5"
    send(msg,parse_mode=None)

def show_habits():
    today=datetime.date.today().isoformat(); habits=data.get("habits",{})
    if not habits:
        send("No habits tracked. Use /addhabit to start.")
        send_buttons("Quick add:",[{"text":"Workout","data":"addhabit:workout"},{"text":"Reading","data":"addhabit:reading"},{"text":"Meditate","data":"addhabit:meditate"},{"text":"Water","data":"addhabit:water"}])
        return
    msg="Today's Habits\n\n"; buttons=[]
    for h,dates in habits.items():
        done=today in dates; streak=calculate_streak(dates)
        icon="[x]" if done else "[ ]"; fire=" FIRE" if streak>=7 else (" *" if streak>=3 else "")
        msg+=f"{icon} {h} - {streak} day streak{fire}\n"
        if not done: buttons.append({"text":f"Done {h}","data":f"habit_done:{h}"})
    send(msg,parse_mode=None)
    if buttons: send_buttons("Mark complete:",buttons)

def mark_habit_done(h):
    today=datetime.date.today().isoformat()
    data.setdefault("habits",{}).setdefault(h,[])
    if today not in data["habits"][h]: data["habits"][h].append(today); save_data()
    streak=calculate_streak(data["habits"][h])
    send(f"Done: {h}! Streak: {streak} days",parse_mode=None)

def start_workout_log():
    conv_state["mode"]="log_workout"; conv_state["step"]=0; conv_state["temp"]={}
    send_buttons("Type?",[{"text":"Weights","data":"wtype:weights"},{"text":"Cardio","data":"wtype:cardio"},{"text":"Yoga","data":"wtype:yoga"},{"text":"Combat","data":"wtype:combat"}])

def finish_workout_log():
    w=conv_state["temp"]; entry={"date":datetime.date.today().isoformat(),"type":w.get("type","general"),"duration":w.get("duration",0),"notes":w.get("notes","")}
    data.setdefault("workouts",[]).append(entry); save_data(); conv_state["mode"]=None
    ws=(datetime.date.today()-datetime.timedelta(days=datetime.date.today().weekday())).isoformat()
    wc=len([x for x in data["workouts"] if x.get("date","")>=ws])
    send(f"Workout Logged!\n{entry['type']} - {entry['duration']}min\nThis week: {wc} workouts",parse_mode=None)

def start_spend_log():
    conv_state["mode"]="log_spend"; conv_state["step"]=0; conv_state["temp"]={}
    send("How much? (number only)")

def finish_spend_log():
    t=conv_state["temp"]; entry={"date":datetime.date.today().isoformat(),"amount":t.get("amount",0),"category":t.get("category","other"),"note":t.get("note","")}
    data["budget"]["transactions"].append(entry); save_data(); conv_state["mode"]=None
    month=datetime.date.today().isoformat()[:7]; total=sum(tx["amount"] for tx in data["budget"]["transactions"] if tx.get("date","")[:7]==month)
    limit=data["budget"]["monthly_limit"]; pct=min(100,int(total/limit*100)) if limit>0 else 0
    send(f"Logged: ${entry['amount']:.2f} ({entry['category']})\n\n${total:.2f} / ${limit:.2f} ({pct}%)",parse_mode=None)

def get_budget_summary():
    month=datetime.date.today().isoformat()[:7]; txns=[t for t in data["budget"]["transactions"] if t.get("date","")[:7]==month]
    total=sum(t["amount"] for t in txns); limit=data["budget"]["monthly_limit"]
    cats={}
    for t in txns: c=t.get("category","other"); cats[c]=cats.get(c,0)+t["amount"]
    msg=f"Budget - {datetime.date.today().strftime('%B %Y')}\n\n${total:.2f} / ${limit:.2f}\n"
    if cats: msg+="\nBy category:\n"+"\n".join(f"- {c}: ${a:.2f}" for c,a in sorted(cats.items(),key=lambda x:-x[1]))
    msg+=f"\n\nRemaining: ${max(0,limit-total):.2f}"
    send(msg,parse_mode=None)

def add_reminder(text):
    parsed=parse_reminder(text)
    if parsed:
        r={"task":parsed.get("task",text),"datetime":parsed.get("datetime",""),"created":datetime.datetime.now().isoformat(),"done":False}
        data.setdefault("reminders",[]).append(r); save_data()
        send(f"Reminder set!\n{r['task']}\nWhen: {r['datetime']}",parse_mode=None)
    else:
        data.setdefault("todos",[]).append({"task":text,"created":datetime.datetime.now().isoformat(),"done":False}); save_data()
        send(f"Added to to-do: {text}",parse_mode=None)

def show_todos():
    todos=[t for t in data.get("todos",[]) if not t.get("done")]
    reminders=[r for r in data.get("reminders",[]) if not r.get("done")]
    msg="Tasks\n\n"
    if reminders: msg+="Reminders:\n"+"\n".join(f"- {r['task']} at {r['datetime']}" for r in reminders)+"\n"
    buttons=[]
    if todos:
        msg+="\nTo-Do:\n"
        for i,t in enumerate(todos): msg+=f"[ ] {t['task']}\n"; buttons.append({"text":f"Done {t['task'][:18]}","data":f"todo_done:{i}"})
    elif not reminders: msg="Nothing on your list!"
    send(msg,parse_mode=None)
    if buttons: send_buttons("Complete:",buttons[:6])

def check_reminders():
    now=datetime.datetime.now().strftime("%Y-%m-%d %H:%M")
    for r in data.get("reminders",[]):
        if r.get("done"): continue
        if r.get("datetime","") and r["datetime"]<=now:
            send(f"REMINDER\n\n{r['task']}",parse_mode=None); r["done"]=True; save_data()

def start_sleep_log():
    conv_state["mode"]="log_sleep"; conv_state["step"]=0; conv_state["temp"]={}
    send("What time did you go to bed? (e.g. 11pm)")

def finish_sleep_log():
    s=conv_state["temp"]
    entry={"date":datetime.date.today().isoformat(),"bedtime":s.get("bedtime","?"),"waketime":s.get("waketime","?"),"hours":s.get("hours",0),"quality":s.get("quality",5)}
    data.setdefault("sleep_log",[]).append(entry); save_data(); conv_state["mode"]=None
    wk=data["sleep_log"][-7:]; avg_h=sum(l.get("hours",0) for l in wk)/max(len(wk),1)
    msg=f"Sleep Logged\n\nBed: {entry['bedtime']}\nWake: {entry['waketime']}\nHours: {entry['hours']:.1f}h\nQuality: {entry['quality']}/10\n\n7-day avg: {avg_h:.1f}h"
    if avg_h<7: msg+="\nYou're averaging under 7h - try for more rest."
    send(msg,parse_mode=None)

def show_sleep_stats():
    logs=data.get("sleep_log",[])[-14:]
    if not logs: send("No sleep data. Use 'sleep' to start."); return
    avg_h=sum(l.get("hours",0) for l in logs)/len(logs); avg_q=sum(l.get("quality",5) for l in logs)/len(logs)
    msg=f"Sleep Stats ({len(logs)} nights)\n\nAvg: {avg_h:.1f}h, quality {avg_q:.1f}/10\n\nRecent:\n"
    for l in logs[-7:]: msg+=f"{l.get('date','?')[-5:]}: {l.get('hours',0):.1f}h (q:{l.get('quality','?')})\n"
    send(msg,parse_mode=None)

def start_add_goal():
    conv_state["mode"]="add_goal"; conv_state["step"]=0; conv_state["temp"]={}
    send("What's the goal? (e.g. 'Read 12 books')")

def show_goals():
    goals=data.get("goals",[])
    if not goals: send("No goals. Use /goal to add one."); return
    msg="Your Goals\n\n"; buttons=[]
    for i,g in enumerate(goals):
        pct=min(100,int(g.get("progress",0)/max(g.get("target",100),1)*100))
        msg+=f"{g['name']}\n{pct}% - {g.get('progress',0)}/{g.get('target',100)} by {g.get('deadline','?')}\n\n"
        buttons.append({"text":f"Update {g['name'][:15]}","data":f"goal_progress:{i}"})
    send(msg,parse_mode=None)
    if buttons: send_buttons("Update:",buttons[:4])

def get_social_stats(platform,username):
    if platform=="youtube":
        try:
            r=requests.get(f"https://mixerno.space/api/youtube-channel-counter/user/{username}",timeout=15)
            subs=r.json().get("counts",[{}])[0].get("count","?")
            return f"YouTube - {username}\nSubs: {subs:,}" if isinstance(subs,int) else f"YouTube stats unavailable for {username}"
        except: return f"Couldn't fetch YouTube stats for {username}"
    return f"Try: /yt [channel]"

CHECKIN_QUESTIONS=["Work out?","Eat healthy?","Sleep enough?","Goal progress?","Learn something?","Avoid doomscrolling?","Rate week 1-10:"]
CHECKIN_LABELS=["Workout","Nutrition","Sleep","Goals","Learning","Focus","Rating"]

def start_checkin():
    conv_state["mode"]="checkin"; conv_state["step"]=0; conv_state["temp"]={"answers":[]}
    send("Weekly Check-in! Let's review.",parse_mode=None); time.sleep(1)
    send_buttons(CHECKIN_QUESTIONS[0],[{"text":"Yes","data":"checkin:yes"},{"text":"No","data":"checkin:no"}])

def process_checkin_answer(a):
    conv_state["temp"]["answers"].append(a); step=conv_state["step"]+1; conv_state["step"]=step
    if step<len(CHECKIN_QUESTIONS):
        if step==6: send(CHECKIN_QUESTIONS[step])
        else: send_buttons(CHECKIN_QUESTIONS[step],[{"text":"Yes","data":"checkin:yes"},{"text":"No","data":"checkin:no"}])
    else: finish_checkin()

def finish_checkin():
    ans=conv_state["temp"]["answers"]; conv_state["mode"]=None
    yc=sum(1 for a in ans[:6] if a.lower()=="yes")
    msg="Weekly Results\n\n"
    for i,l in enumerate(CHECKIN_LABELS):
        if i<len(ans):
            if i==6: msg+=f"Rating: {ans[i]}/10\n"
            else: msg+=f"{'[x]' if ans[i].lower()=='yes' else '[ ]'} {l}\n"
    msg+=f"\nScore: {yc}/6"
    data.setdefault("checkin_history",[]).append({"date":datetime.date.today().isoformat(),"score":yc,"answers":ans}); save_data()
    send(msg,parse_mode=None)

def handle_callback(cb):
    d=cb.get("data",""); answer_callback(cb.get("id",""))
    if d.startswith("wtype:"): conv_state["temp"]["type"]=d.split(":")[1]; conv_state["step"]=1; send("How many minutes?")
    elif d.startswith("habit_done:"): mark_habit_done(d.split(":",1)[1])
    elif d.startswith("addhabit:"): h=d.split(":",1)[1]; data.setdefault("habits",{})[h]=[]; save_data(); send(f"Added: {h}")
    elif d.startswith("scat:"): conv_state["temp"]["category"]=d.split(":")[1]; conv_state["step"]=2; send("Note? (or 'skip')")
    elif d.startswith("checkin:"): process_checkin_answer(d.split(":")[1])
    elif d.startswith("mood:"): log_mood(int(d.split(":")[1]))
    elif d.startswith("water:"): log_water(int(d.split(":")[1]))
    elif d.startswith("news_deep:"): deep_dive_news(int(d.split(":")[1]))
    elif d.startswith("news_related:"): search_related(int(d.split(":")[1]))
    elif d.startswith("goal_progress:"):
        idx=int(d.split(":")[1]); goals=data.get("goals",[])
        if idx<len(goals): conv_state["mode"]="goal_update"; conv_state["temp"]={"index":idx}; send(f"Add progress (current: {goals[idx].get('progress',0)}/{goals[idx].get('target',100)})")
    elif d.startswith("todo_done:"):
        idx=int(d.split(":")[1]); undone=[t for t in data.get("todos",[]) if not t.get("done")]
        if idx<len(undone): undone[idx]["done"]=True; save_data(); send(f"Done: {undone[idx]['task']}")
    elif d.startswith("sq:"): conv_state["temp"]["quality"]=int(d.split(":")[1]); finish_sleep_log()
    elif d=="crypto": send(f"Crypto\n\n{get_crypto()}",parse_mode=None)
    elif d=="emails": send(f"Email\n\n{get_gmail()}",parse_mode=None)
    elif d=="log_workout": start_workout_log()
    elif d=="log_spend": start_spend_log()
    elif d=="habits": show_habits()
    elif d=="log_mood":
        send_buttons("How are you feeling?",[{"text":"Great","data":"mood:5"},{"text":"Good","data":"mood:4"},{"text":"Okay","data":"mood:3"},{"text":"Low","data":"mood:2"},{"text":"Bad","data":"mood:1"}])
    elif d=="log_meal_prompt": send("What did you eat?"); conv_state["mode"]="log_meal"
    elif d=="full_brief":
        w=get_weather(); alerts=get_weather_forecast(); nt,_=get_news()
        msg=f"Full Brief\n\nWeather: {w}\n"
        if alerts: msg+="Alerts: "+" | ".join(alerts[:3])+"\n"
        msg+=f"\nNews:\n{nt}\n\nCrypto:\n{get_crypto()}\n\nCalendar:\n{get_calendar()}\n\nEmail:\n{get_gmail()}\n\n{get_quote()}"
        send(msg,parse_mode=None)
    elif d=="deep_news": show_news_with_buttons()
    elif d=="more_news": show_news_with_buttons()

def handle_text(text):
    t=text.strip(); tl=t.lower()
    if conv_state["mode"]=="log_workout":
        if conv_state["step"]==1:
            try: conv_state["temp"]["duration"]=int(tl); conv_state["step"]=2; send("Notes? (or skip)")
            except: send("Send a number.")
        elif conv_state["step"]==2: conv_state["temp"]["notes"]="" if tl=="skip" else t; finish_workout_log()
        return
    if conv_state["mode"]=="log_spend":
        if conv_state["step"]==0:
            try:
                conv_state["temp"]["amount"]=float(tl.replace("$",""));conv_state["step"]=1
                send_buttons("Category?",[{"text":"Food","data":"scat:food"},{"text":"Transport","data":"scat:transport"},{"text":"Shopping","data":"scat:shopping"},{"text":"Fun","data":"scat:entertainment"},{"text":"Bills","data":"scat:bills"},{"text":"Other","data":"scat:other"}])
            except: send("Send a number.")
        elif conv_state["step"]==2: conv_state["temp"]["note"]="" if tl=="skip" else t; finish_spend_log()
        return
    if conv_state["mode"]=="checkin": process_checkin_answer(t); return
    if conv_state["mode"]=="log_sleep":
        if conv_state["step"]==0: conv_state["temp"]["bedtime"]=t; conv_state["step"]=1; send("Wake time?")
        elif conv_state["step"]==1:
            conv_state["temp"]["waketime"]=t
            hrs=ai_call(f"Bed: {conv_state['temp']['bedtime']}, Wake: {t}. Hours of sleep? Return ONLY a number.","Return only a number.")
            try: conv_state["temp"]["hours"]=float(hrs.strip())
            except: conv_state["temp"]["hours"]=7.0
            send_buttons("Sleep quality?",[{"text":"Great(9)","data":"sq:9"},{"text":"Good(7)","data":"sq:7"},{"text":"OK(5)","data":"sq:5"},{"text":"Bad(3)","data":"sq:3"}])
        return
    if conv_state["mode"]=="log_meal": log_meal(t); conv_state["mode"]=None; return
    if conv_state["mode"]=="add_goal":
        if conv_state["step"]==0: conv_state["temp"]["name"]=t; conv_state["step"]=1; send("Target number? (e.g. 12)")
        elif conv_state["step"]==1:
            try: conv_state["temp"]["target"]=float(tl)
            except: conv_state["temp"]["target"]=100
            conv_state["step"]=2; send("Deadline? (e.g. 2026-12-31)")
        elif conv_state["step"]==2:
            g={"name":conv_state["temp"]["name"],"target":conv_state["temp"]["target"],"progress":0,"deadline":t,"created":datetime.date.today().isoformat()}
            data.setdefault("goals",[]).append(g); save_data(); conv_state["mode"]=None
            send(f"Goal added: {g['name']} - target {g['target']} by {g['deadline']}",parse_mode=None)
        return
    if conv_state["mode"]=="goal_update":
        try:
            amt=float(tl); idx=conv_state["temp"]["index"]; data["goals"][idx]["progress"]+=amt; save_data(); conv_state["mode"]=None
            g=data["goals"][idx]; pct=min(100,int(g["progress"]/max(g["target"],1)*100))
            send(f"{g['name']}: {pct}% ({g['progress']}/{g['target']})",parse_mode=None)
        except: send("Send a number.")
        return

    if tl in ["brief","/brief","/start","morning"]: morning_briefing()
    elif tl in ["evening","/evening","recap"]: evening_recap()
    elif tl in ["crypto","/crypto","prices"]: send(f"Crypto\n\n{get_crypto()}",parse_mode=None)
    elif tl in ["weather","/weather"]:
        w=get_weather(); alerts=get_weather_forecast()
        msg=f"Weather\n\n{w}"
        if alerts: msg+="\n\nComing up:\n"+"\n".join(alerts)
        send(msg,parse_mode=None)
    elif tl in ["news","/news"]: show_news_with_buttons()
    elif tl in ["events","/events","calendar"]: send(f"Calendar\n\n{get_calendar()}",parse_mode=None)
    elif tl in ["emails","/emails","mail"]: send(f"Email\n\n{get_gmail()}",parse_mode=None)
    elif tl in ["quote","/quote"]: send(get_quote(),parse_mode=None)
    elif tl in ["workout","/workout","gym"]: start_workout_log()
    elif tl in ["spend","/spend","spent"]: start_spend_log()
    elif tl in ["habits","/habits"]: show_habits()
    elif tl in ["budget","/budget"]: get_budget_summary()
    elif tl in ["checkin","/checkin"]: start_checkin()
    elif tl in ["sleep","/sleep"]: start_sleep_log()
    elif tl in ["sleepstats","/sleepstats"]: show_sleep_stats()
    elif tl in ["mood","/mood"]:
        send_buttons("How are you feeling?",[{"text":"Great","data":"mood:5"},{"text":"Good","data":"mood:4"},{"text":"OK","data":"mood:3"},{"text":"Low","data":"mood:2"},{"text":"Bad","data":"mood:1"}])
    elif tl in ["goals","/goals"]: show_goals()
    elif tl in ["goal","/goal","/addgoal"]: start_add_goal()
    elif tl in ["water","/water"]: log_water(1)
    elif tl in ["nutrition","/nutrition","meals","/meals"]: show_nutrition()
    elif tl in ["todos","/todos","tasks","/tasks"]: show_todos()
    elif tl.startswith(("remind me","/remind")): add_reminder(t)
    elif tl.startswith(("ate ","i ate ","i had ")): log_meal(re.sub(r'^(ate |i ate |i had )','',tl))
    elif tl.startswith("/setbudget"):
        try: amt=float(tl.split()[-1]); data["budget"]["monthly_limit"]=amt; save_data(); send(f"Budget: ${amt:.2f}/month")
        except: send("Usage: /setbudget 2000")
    elif tl.startswith("/addhabit"):
        parts=t.split(maxsplit=1)
        if len(parts)>1: data.setdefault("habits",{})[parts[1].strip()]=[]; save_data(); send(f"Added: {parts[1].strip()}")
        else: send_buttons("Quick add:",[{"text":"Workout","data":"addhabit:workout"},{"text":"Reading","data":"addhabit:reading"},{"text":"Meditate","data":"addhabit:meditate"},{"text":"Water","data":"addhabit:water"}])
    elif tl.startswith("/addcrypto"):
        parts=t.split(maxsplit=1)
        if len(parts)>1:
            coin=parts[1].strip().lower()
            if coin not in data["crypto_watchlist"]: data["crypto_watchlist"].append(coin); save_data(); send(f"Added {coin}")
        else: send("Usage: /addcrypto dogecoin")
    elif tl.startswith(("/yt ","/youtube ")): send(get_social_stats("youtube",t.split(maxsplit=1)[1].strip()),parse_mode=None)
    elif tl in ["help","/help","menu"]:
        send("Ashton's AI Assistant v3\n\nDAILY: brief, evening, weather, news, crypto, events, emails, quote\n\nTRACKING: workout, 'ate [food]', water, spend, habits, mood, sleep\n\nPLANNING: 'remind me to...', todos, goals, budget, checkin\n\nSTATS: sleepstats, nutrition, /yt [channel]\n\nSETTINGS: /setbudget, /addhabit, /addcrypto",parse_mode=None)
    else:
        if GROQ_API_KEY:
            resp=ai_call(f"User said: {text}","You are Ashton's AI assistant. Respond helpfully and briefly. If they mention food, estimate calories. If they mention a task, offer to set a reminder.")
            if resp: send(resp,parse_mode=None)
            else: send("Type 'help' for commands.")
        else: send("Type 'help' for commands.")

def check_upcoming_events():
    try:
        cj=os.environ.get("GOOGLE_TOKEN")
        if not cj: return
        creds=Credentials(**json.loads(cj)); svc=build("calendar","v3",credentials=creds)
        now=datetime.datetime.utcnow(); soon=now+datetime.timedelta(minutes=31)
        evts=svc.events().list(calendarId="primary",timeMin=now.isoformat()+"Z",timeMax=soon.isoformat()+"Z",singleEvents=True,orderBy="startTime").execute().get("items",[])
        for e in evts:
            eid=e["id"]
            if eid not in alerted_events:
                name=e.get("summary","?"); start=e["start"].get("dateTime",e["start"].get("date"))
                try: ts=datetime.datetime.fromisoformat(start.replace("Z","+00:00")).astimezone().strftime("%I:%M %p")
                except: ts=start
                send(f"REMINDER: {name} at {ts} (~30min!)",parse_mode=None); alerted_events.add(eid)
    except Exception as e: print(f"[EVT ERR] {e}")

def scheduler():
    while True:
        now=datetime.datetime.now(); h,m=now.hour,now.minute
        mh=data.get("settings",{}).get("morning_hour",9); eh=data.get("settings",{}).get("evening_hour",21)
        if h==mh and m==0: morning_briefing(); time.sleep(61)
        if h==13 and m==0: midday_check(); time.sleep(61)
        if h==eh and m==0: evening_recap(); time.sleep(61)
        if data.get("settings",{}).get("water_reminder") and 9<=h<=21 and h%2==0 and m==0: water_reminder(); time.sleep(61)
        if data.get("settings",{}).get("weather_alerts") and h%3==0 and m==30:
            alerts=get_weather_forecast()
            if alerts: send("Weather Update\n\n"+"\n".join(alerts[:3]),parse_mode=None)
            time.sleep(61)
        if now.weekday()==6 and h==10 and m==0: start_checkin(); time.sleep(61)
        check_reminders(); check_upcoming_events(); time.sleep(60)

def telegram_listener():
    last_id=None
    while True:
        try:
            r=requests.get(f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/getUpdates",params={"timeout":15,"offset":last_id,"allowed_updates":["message","callback_query"]},timeout=20)
            for u in r.json().get("result",[]):
                last_id=u["update_id"]+1
                if "callback_query" in u:
                    cb=u["callback_query"]
                    if str(cb.get("message",{}).get("chat",{}).get("id",""))==CHAT_ID: handle_callback(cb)
                elif "message" in u:
                    msg=u["message"]; txt=msg.get("text","").strip(); cid=str(msg.get("chat",{}).get("id",""))
                    if cid==CHAT_ID and txt: handle_text(txt)
        except Exception as e: print(f"[LISTEN ERR] {e}")
        time.sleep(2)

def get_flow(state=None,code_verifier=None):
    if not GOOGLE_CREDENTIALS: raise ValueError("GOOGLE_CREDENTIALS missing.")
    flow=Flow.from_client_config(json.loads(GOOGLE_CREDENTIALS),scopes=SCOPES,redirect_uri=REDIRECT_URI,state=state)
    if code_verifier: flow.code_verifier=code_verifier
    return flow

@app.route("/")
def home(): return '<h2>AI Assistant v3</h2><p><a href="/login">Connect Google</a></p>'

@app.route("/login")
def login():
    cv=secrets.token_urlsafe(64); session["code_verifier"]=cv
    flow=get_flow(code_verifier=cv)
    auth_url,state=flow.authorization_url(access_type="offline",include_granted_scopes="true",code_challenge_method="S256",prompt="consent")
    session["state"]=state; return redirect(auth_url)

@app.route("/oauth2callback")
def oauth2callback():
    if "state" not in session or "code_verifier" not in session: return "Session expired.",400
    flow=get_flow(state=session["state"],code_verifier=session["code_verifier"])
    flow.fetch_token(authorization_response=request.url); creds=flow.credentials
    td={"token":creds.token,"refresh_token":creds.refresh_token,"token_uri":creds.token_uri,"client_id":creds.client_id,"client_secret":creds.client_secret,"scopes":list(creds.scopes)}
    print("GOOGLE_TOKEN:",json.dumps(td)); send("Google connected!",parse_mode=None); return "Connected! Check Telegram."

@app.route("/brief")
def manual_brief(): morning_briefing(); return "Sent!"

def start_threads():
    threading.Thread(target=scheduler,daemon=True).start()
    threading.Thread(target=telegram_listener,daemon=True).start()
    print("[OK] Assistant v3 running.")

if __name__=="__main__":
    start_threads(); app.run(host="0.0.0.0",port=int(os.environ.get("PORT",8080)),debug=False)
else:
    start_threads()
