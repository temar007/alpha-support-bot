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
@bot.message_handler(content_types=['text', 'photo', 'document'])
def handle_tg(message):
    file_name = None
    text_content = message.text or message.caption or ""
    if message.content_type == 'photo':
        try:
            file_id = message.photo[-1].file_id
            file_info = bot.get_file(file_id)
            downloaded_file = bot.download_file(file_info.file_path)
            file_name = f"tg_{int(time.time())}.jpg"
            sb_api(file_name, method="UPLOAD", data=downloaded_file, is_storage=True)
            if not text_content: text_content = "[Фото]"
        except: pass

    new_row = {
        "user_id": message.chat.id, "sender": message.from_user.first_name,
        "text": text_content, "file_path": file_name,
        "timestamp": datetime.now().strftime('%H:%M'),
        "tg_msg_id": message.message_id, "is_read": False
    }
    sb_api("messages", method="POST", data=new_row)

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
