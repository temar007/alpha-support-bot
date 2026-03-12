import telebot
import httpx
import os
import time
import threading
from http.server import HTTPServer, BaseHTTPRequestHandler
from datetime import datetime

# --- КОНФІГУРАЦІЯ ---
TOKEN = os.getenv("TOKEN")
SUPABASE_URL = os.getenv("SUPABASE_URL")
SUPABASE_KEY = os.getenv("SUPABASE_KEY")
STORAGE_BUCKET = os.getenv("STORAGE_BUCKET", "support_media")
PORT = int(os.getenv("PORT", 8000))

bot = telebot.TeleBot(TOKEN)

# --- API МІСТ ---
def sb_api(table, method="GET", data=None, is_storage=False):
    headers = {"apikey": SUPABASE_KEY, "Authorization": f"Bearer {SUPABASE_KEY}"}
    if is_storage:
        url = f"{SUPABASE_URL}/storage/v1/object/{STORAGE_BUCKET}/{table}"
        if method == "UPLOAD":
            headers["Content-Type"] = "application/octet-stream"
            headers["x-upsert"] = "true"
    else:
        url = f"{SUPABASE_URL}/rest/v1/{table}"
        headers["Content-Type"] = "application/json"
        headers["Prefer"] = "return=representation"
    try:
        with httpx.Client(timeout=30.0) as client:
            if method == "UPLOAD": return client.post(url, headers=headers, content=data)
            if method == "POST": return client.post(url, headers=headers, json=data).json()
    except: return None

# --- ОБРОБНИК TG ---
# --- ТЕЛЕГРАМ POLLING ---
@bot.message_handler(func=lambda m: True)
def handle_tg(message):
    uid = message.chat.id
    name = message.from_user.first_name or "Unknown"
    
    print(f"📥 New message from {name} (ID: {uid})")
    
    # 1. ПЕРЕВІРЯЄМО/СТВОРЮЄМО КЛІЄНТА
    # Спробуємо оновити дату активності, якщо клієнт є. 
    # Якщо його немає (результат порожній), створюємо нового.
    check_user = sb_api("clients", params={"select": "id", "id": f"eq.{uid}"})
    
    if not check_user:
        print(f"🆕 Creating new client: {name}")
        sb_api("clients", method="POST", data={
            "id": uid, 
            "name": name, 
            "last_activity": datetime.now().isoformat()
        })
    else:
        # Оновлюємо час останньої активності, щоб він піднявся вгору списку
        sb_api("clients", method="PATCH", 
               data={"last_activity": datetime.now().isoformat()}, 
               params={"id": f"eq.{uid}"})

    # 2. ЗАПИСУЄМО ПОВІДОМЛЕННЯ
    new_row = {
        "user_id": uid,
        "sender": name,
        "text": message.text or "[Медіа]",
        "timestamp": datetime.now().strftime('%H:%M'),
        "tg_msg_id": message.message_id
    }
    sb_api("messages", method="POST", data=new_row)
    
    # 3. ОНОВЛЮЄМО UI
    page.pubsub.send_all({
        "type": "update", 
        "user_id": uid, 
        "name": name, 
        "text": message.text or "Файл"
    })

# --- СЕРВЕР-ЗАГЛУШКА ---
class HealthCheckHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200)
        self.send_header("Content-type", "text/plain")
        self.end_headers()
        self.wfile.write(b"ALIVE")
    def log_message(self, format, *args): return # Тиша в логах

def run_health_server():
    server = HTTPServer(('0.0.0.0', PORT), HealthCheckHandler)
    server.serve_forever()

# --- ЗАПУСК ---
if __name__ == "__main__":
    # 1. ЗАПУСКАЄМО ПОРТ НЕГАЙНО
    threading.Thread(target=run_health_server, daemon=True).start()
    print(f"✅ Health check server started on port {PORT}")
    
    # 2. ЧЕКАЄМО ТРОХИ, ЩОБ KOYEB ПОБАЧИВ ПОРТ
    time.sleep(2)
    
    # 3. СТАРТУЄМО БОТА
    print("🤖 Bridge is starting...")
    try: bot.remove_webhook()
    except: pass
    bot.infinity_polling(timeout=20)

