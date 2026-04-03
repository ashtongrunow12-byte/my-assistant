import os
import json
import datetime
import requests

TELEGRAM_TOKEN = os.environ.get("TELEGRAM_TOKEN")
CHAT_ID = os.environ.get("CHAT_ID")
GOOGLE_CREDENTIALS = os.environ.get("GOOGLE_CREDENTIALS")

def send_telegram(message):
    url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
    requests.post(url, json={"chat_id": CHAT_ID, "text": message}, timeout=15)

def run_briefing():
    now = datetime.datetime.now()
    greeting = f"🌅 Good morning Ashton! Here's your briefing for today:\n\n"
    send_telegram(greeting)
    print("Morning briefing sent!")

if __name__ == "__main__":
    run_briefing()