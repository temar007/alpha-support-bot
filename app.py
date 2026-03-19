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

def sb_api(table, method="GET", data=None, params=None, is_storage=False, file_content=None):
    headers = {"apikey": SUPABASE_KEY, "Authorization": f"Bearer {SUPABASE_KEY}"}
    if is_storage:
        url = f"{SUPABASE_URL}/storage/v1/object/{STORAGE_BUCKET}/{table}"
    else:
        url = f"{SUPABASE_URL}/rest/v1/{table}"
        headers["Content-Type"] = "application/json"
        headers["Prefer"] = "return=representation"

    try:
        with httpx.Client(timeout=60.0) as client:
            if method == "GET": return client.get(url, headers=headers, params=params).json()
            if method == "POST": return client.post(url, headers=headers, json=data).json()
            if method == "PATCH": return client.patch(url, headers=headers, json=data, params=params).json()
            if method == "PUT": return client.put(url, headers=headers, content=file_content).json()
    except Exception as e:
        print(f"!!! API Error: {e}")
        return None

# --- ФОНОВЕ ЗАВАНТАЖЕННЯ МЕДІА ---
def upload_media_bg(msg_db_id, file_id, uid, orig_name, content_type):
    try:
        print(f"🛠 BG: Fetching file {file_id} from Telegram...")
        file_info = bot.get_file(file_id)
        tg_url = f"https://api.telegram.org/file/bot{TOKEN}/{file_info.file_path}"
        
        with httpx.Client(timeout=30.0) as client:
            resp = client.get(tg_url)
            if resp.status_code == 200:
                file_ext = os.path.splitext(orig_name)[1]
                safe_name = f"in_{int(time.time())}_{uid}{file_ext}"
                
                print(f"☁️ BG: Uploading to Supabase as {safe_name}...")
                up_res = sb_api(safe_name, method="PUT", is_storage=True, file_content=resp.content)
                
                # Оновлюємо базу
                patch_res = sb_api("messages", method="PATCH", 
                                   data={"file_path": safe_name, "text": f"📄 {orig_name}"},
                                   params={"id": f"eq.{msg_db_id}"})
                print(f"✅ BG: Finished! Database updated.")
            else:
                print(f"❌ BG: Failed to download from TG. Status: {resp.status_code}")
    except Exception as e:
        print(f"❌ BG CRITICAL ERROR: {e}")

# --- ОСНОВНИЙ ОБРОБНИК ---
@bot.message_handler(content_types=['text', 'photo', 'document', 'video', 'voice'])
def handle_tg(message):
    uid = message.chat.id
    first = message.from_user.first_name or ""
    last = message.from_user.last_name or ""
    name = f"{first} {last}".strip() or "Unknown"
    kyiv_time = datetime.now() + timedelta(hours=2)
    timestamp_str = kyiv_time.strftime('%d.%m.%Y %H:%M')
    iso_time = kyiv_time.isoformat()

    # 1. Робота з клієнтом (створення/активація)
    if not check_user:
        sb_api("clients", method="POST", data={
            "id": uid, 
            "name": name, 
            "status": "active", 
            "last_activity": iso_time
        })
    else:
        # Оновлюємо статус на active, якщо клієнт написав сам
        sb_api("clients", method="PATCH", 
               data={"last_activity": iso_time, "status": "active", "name": name}, # Оновимо ім'я теж, про всяк випадок
               params={"id": f"eq.{uid}"})

    # --- 2. Швидкий запис у базу ---
    is_media = message.content_type in ['photo', 'document', 'video', 'voice']
    initial_text = message.text or message.caption or ""
    if is_media and not initial_text:
        initial_text = "[Отримуємо медіа...]"
    
    # Створюємо запис і ЯВНО просимо повернути дані (return=representation вже є в sb_api)
    new_msg_res = sb_api("messages", method="POST", data={
        "user_id": uid,
        "sender": name,
        "text": initial_text,
        "timestamp": timestamp_str,
        "tg_msg_id": message.message_id
    })

    # Перевіряємо, чи отримали ми ID від бази
    if is_media and new_msg_res and isinstance(new_msg_res, list) and len(new_msg_res) > 0:
        msg_db_id = new_msg_res[0].get('id')
        
        if msg_db_id:
            # Визначаємо файл перед запуском потоку
            if message.content_type == 'photo':
                raw_file = message.photo[-1]
                orig_name = f"img_{int(time.time())}.jpg"
            else:
                raw_file = getattr(message, message.content_type)
                orig_name = getattr(raw_file, 'file_name', f"file_{int(time.time())}")

            # ЗАПУСК ФОНУ
            t = threading.Thread(
                target=upload_media_bg, 
                args=(msg_db_id, raw_file.file_id, uid, orig_name, message.content_type)
            )
            t.daemon = True
            t.start()
            print(f"🚀 Started background upload for msg_id: {msg_db_id}")
    else:
        print(f"📥 Plain text message from {name} saved.")

# --- HEALTH CHECK SERVER ---
class HealthCheckHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200)
        self.end_headers()
        self.wfile.write(b"ALIVE")
    def log_message(self, format, *args): return

def run_health_server():
    HTTPServer(('0.0.0.0', PORT), HealthCheckHandler).serve_forever()

if __name__ == "__main__":
    threading.Thread(target=run_health_server, daemon=True).start()
    bot.remove_webhook()
    bot.infinity_polling(timeout=20)


