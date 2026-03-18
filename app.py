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
def sb_api(table, method="GET", data=None, params=None, is_storage=False, file_content=None, content_type=None):
    headers = {"apikey": SUPABASE_KEY, "Authorization": f"Bearer {SUPABASE_KEY}"}
    
    if is_storage:
        # Для Storage URL має інший формат: /storage/v1/object/bucket/filename
        url = f"{SUPABASE_URL}/storage/v1/object/{STORAGE_BUCKET}/{table}"
        if content_type:
            headers["Content-Type"] = content_type
    else:
        url = f"{SUPABASE_URL}/rest/v1/{table}"
        headers["Content-Type"] = "application/json"
        headers["Prefer"] = "return=representation"

    try:
        with httpx.Client(timeout=60.0) as client:
            if method == "GET": return client.get(url, headers=headers, params=params).json()
            if method == "POST": return client.post(url, headers=headers, json=data).json()
            if method == "PATCH": return client.patch(url, headers=headers, json=data, params=params).json()
            # Додаємо PUT для завантаження файлів у Storage
            if method == "PUT": 
                return client.put(url, headers=headers, content=file_content).json()
    except Exception as e:
        print(f"!!! API Error: {e}")
        return None

# --- ОБРОБНИК TG (Універсальний для тексту та медіа) ---
@bot.message_handler(content_types=['text', 'photo', 'document', 'video', 'voice'])
def handle_tg(message):
    uid = message.chat.id
    name = f"{message.from_user.first_name or ''} {message.from_user.last_name or ''}".strip() or "Unknown"
    
    # Київський час (+2)
    kyiv_time = datetime.now() + timedelta(hours=2)
    timestamp_str = kyiv_time.strftime('%H:%M')
    iso_time = kyiv_time.isoformat()

    file_path_to_save = None
    caption_text = message.text or message.caption or ""

    # --- ЛОГІКА МЕДІА ---
    if message.content_type in ['photo', 'document', 'video', 'voice']:
        try:
            # Отримуємо ID файлу
            if message.content_type == 'photo':
                raw_file = message.photo[-1] # Найкраща якість
                orig_name = f"img_{int(time.time())}.jpg"
            else:
                raw_file = getattr(message, message.content_type)
                orig_name = getattr(raw_file, 'file_name', f"file_{int(time.time())}")

            # Завантажуємо файл з серверів Telegram
            file_info = bot.get_file(raw_file.file_id)
            tg_file_url = f"https://api.telegram.org/file/bot{TOKEN}/{file_info.file_path}"
            
            with httpx.Client() as client:
                resp = client.get(tg_file_url)
                if resp.status_code == 200:
                    # Робимо safe_name (як у нашому Sender/Chat)
                    file_ext = os.path.splitext(orig_name)[1]
                    safe_name = f"in_{int(time.time())}_{uid}{file_ext}"
                    
                    # Завантажуємо в Supabase Storage
                    upload_res = sb_api(safe_name, method="PUT", is_storage=True, file_content=resp.content)
                    if upload_res:
                        file_path_to_save = safe_name
                        if not caption_text: caption_text = f"📄 {orig_name}"
                        print(f"📎 Media saved to storage: {safe_name}")
        except Exception as e:
            print(f"⚠️ Error processing media: {e}")

    # --- 1. Перевірка/Створення клієнта ---
    check_user = sb_api("clients", method="GET", params={"select": "status", "id": f"eq.{uid}"})
    
    if not check_user:
        sb_api("clients", method="POST", data={
            "id": uid, "name": name, "status": "active", "last_activity": iso_time
        })
    else:
        sb_api("clients", method="PATCH", data={"last_activity": iso_time, "status": "active"}, params={"id": f"eq.{uid}"})

    # --- 2. Запис повідомлення ---
    new_row = {
        "user_id": uid,
        "sender": name,
        "text": caption_text,
        "file_path": file_path_to_save, # Сюди тепер записується ім'я файлу
        "timestamp": timestamp_str,
        "tg_msg_id": message.message_id
    }
    sb_api("messages", method="POST", data=new_row)
    print(f"📥 Message from {name} processed.")

# --- СЕРВЕР-ЗАГЛУШКА (без змін) ---
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

if __name__ == "__main__":
    threading.Thread(target=run_health_server, daemon=True).start()
    time.sleep(2)
    try: bot.remove_webhook()
    except: pass
    bot.infinity_polling(timeout=20)
