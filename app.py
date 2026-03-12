import telebot
import httpx
import os
import time
import threading
from http.server import HTTPServer, BaseHTTPRequestHandler
from datetime import datetime, timedelta

# --- КОНФІГУРАЦІЯ ---
TOKEN = os.getenv("TOKEN")
SUPABASE_URL = os.getenv("SUPABASE_URL")
SUPABASE_KEY = os.getenv("SUPABASE_KEY")
STORAGE_BUCKET = os.getenv("STORAGE_BUCKET", "support_media")
PORT = int(os.getenv("PORT", 8000))

bot = telebot.TeleBot(TOKEN)

# --- API МІСТ ---
def sb_api(table, method="GET", data=None, params=None, is_storage=False):
    headers = {"apikey": SUPABASE_KEY, "Authorization": f"Bearer {SUPABASE_KEY}"}
    if is_storage:
        url = f"{SUPABASE_URL}/storage/v1/object/{STORAGE_BUCKET}/{table}"
    else:
        url = f"{SUPABASE_URL}/rest/v1/{table}"
        headers["Content-Type"] = "application/json"
        headers["Prefer"] = "return=representation"

    try:
        with httpx.Client(timeout=30.0) as client:
            if method == "GET": return client.get(url, headers=headers, params=params).json()
            if method == "POST": return client.post(url, headers=headers, json=data).json()
            if method == "PATCH": return client.patch(url, headers=headers, json=data, params=params).json()
    except Exception as e:
        print(f"!!! API Error: {e}")
        return None

# --- ОБРОБНИК TG ---
@bot.message_handler(func=lambda m: True)
def handle_tg(message):
    uid = message.chat.id
    name = message.from_user.first_name or "Unknown"
    
    # Київський час (+2)
    kyiv_time = datetime.now() + timedelta(hours=2)
    timestamp_str = kyiv_time.strftime('%H:%M')
    iso_time = kyiv_time.isoformat()

    print(f"📥 New message from {name} at {timestamp_str}")

    # 1. Перевірка/Створення клієнта (Передаємо params!)
    check_user = sb_api("clients", method="GET", params={"select": "id", "id": f"eq.{uid}"})
    
    if not check_user:
        print(f"🆕 Creating new client: {name}")
        sb_api("clients", method="POST", data={
            "id": uid, 
            "name": name, 
            "last_activity": iso_time
        })
    else:
        sb_api("clients", method="PATCH", 
               data={"last_activity": iso_time}, 
               params={"id": f"eq.{uid}"})

    # 2. Запис повідомлення
    new_row = {
        "user_id": uid,
        "sender": name,
        "text": message.text or "[Медіа]",
        "timestamp": timestamp_str,
        "tg_msg_id": message.message_id
    }
    sb_api("messages", method="POST", data=new_row)
    # Тут pubsub не потрібен, десктоп сам побачить зміни через Realtime Supabase

# --- СЕРВЕР-ЗАГЛУШКА ---
class HealthCheckHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200)
        self.send_header("Content-type", "text/plain")
        self.end_headers()
        self.wfile.write(b"ALIVE")
    def log_message(self, format, *args): return

def run_health_server():
    server = HTTPServer(('0.0.0.0', PORT), HealthCheckHandler)
    server.serve_forever()

# --- ЗАПУСК ---
if __name__ == "__main__":
    threading.Thread(target=run_health_server, daemon=True).start()
    print(f"✅ Health check server started on port {PORT}")
    
    time.sleep(2)
    
    print("🤖 Bridge is starting...")
    try: bot.remove_webhook()
    except: pass
    bot.infinity_polling(timeout=20)
