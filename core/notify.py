import requests
from core.config import TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID

def send(msg: str):
    print("NOTIFY:", msg[:200])
    if not TELEGRAM_BOT_TOKEN or not TELEGRAM_CHAT_ID:
        return
    try:
        requests.post(f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage",
                      data={"chat_id": TELEGRAM_CHAT_ID, "text": msg[:3500]}, timeout=20)
    except Exception as e:
        print("Telegram error:", e)
