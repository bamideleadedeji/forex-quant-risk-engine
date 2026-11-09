import os
import requests

def send_telegram_alert(message: str):
    """
    Sends instant push alerts to your Telegram chat when orders are filled or events occur.
    """
    token = os.environ.get("TELEGRAM_BOT_TOKEN", "").strip()
    chat_id = os.environ.get("TELEGRAM_CHAT_ID", "").strip()
    
    if not token or not chat_id:
        return  # Bypasses silently if credentials aren't set yet

    url = f"https://api.telegram.org/bot{token}/sendMessage"
    payload = {
        "chat_id": chat_id, 
        "text": f"🤖 *Exness Quant Engine Alert*\n\n{message}", 
        "parse_mode": "Markdown"
    }
    
    try:
        response = requests.post(url, json=payload, timeout=5)
        if response.status_code != 200:
            print(f"  [WARN] Telegram alert failed: {response.text}")
    except Exception as e:
        print(f"  [WARN] Telegram connection error: {e}")
