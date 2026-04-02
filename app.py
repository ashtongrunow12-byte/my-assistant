from flask import Flask
import os
import requests

app = Flask(__name__)

TELEGRAM_TOKEN = "8127824873:AAHCEOLuDHvmh22Ospprnyn4zi-BYUzq6nE"
CHAT_ID = "8798214200"

def send_telegram(message):
    url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
    requests.post(url, json={"chat_id": CHAT_ID, "text": message})

@app.route('/')
def home():
    send_telegram("👋 Hey Ashton! Your assistant is alive and talking to you!")
    return 'Message sent to your phone!'

if __name__ == '__main__':
    port = int(os.environ.get('PORT', 8080))
    app.run(host='0.0.0.0', port=port, debug=False)