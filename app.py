import telebot
import httpx
import os
import time
from datetime import datetime

# --- КОНФІГУРАЦІЯ ---
TOKEN = os.getenv("TOKEN")
SUPABASE_URL = os.getenv("SUPABASE_URL")
SUPABASE_KEY = os.getenv("SUPABASE_KEY")
STORAGE_BUCKET = os.getenv("STORAGE_BUCKET", "support_media")

bot = telebot.TeleBot(TOKEN)

# --- API МІСТ ---
def sb_api(table, method="GET", data=None, params=None, is_storage=False):
    headers = {
        "apikey": SUPABASE_KEY, 
        "Authorization": f"Bearer {SUPABASE_KEY}"
    }
    
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
            if method == "UPLOAD":
                return client.post(url, headers=headers, content=data)
            if method == "POST": 
                return client.post(url, headers=headers, json=data).json()
            if method == "GET": 
                return client.get(url, headers=headers, params=params).json()
    except Exception as e:
        print(f"!!! API Error: {e}")
        return None

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
            
            print(f"🚀 Uploading {file_name}...")
            res = sb_api(file_name, method="UPLOAD", data=downloaded_file, is_storage=True)
            
            if res and res.status_code in [200, 201]:
                print(f"✅ Success: {res.status_code}")
            else:
                print(f"❌ Storage Error: {res.status_code if res else 'No response'} - {res.text if res else ''}")
            
            if not text_content: text_content = "[Фото]"
        except Exception as e:
            print(f"!!! Media Error: {e}")

    # Запис у базу
    new_row = {
        "user_id": message.chat.id,
        "sender": message.from_user.first_name,
        "text": text_content,
        "file_path": file_name,
        "timestamp": datetime.now().strftime('%H:%M'),
        "tg_msg_id": message.message_id,
        "is_read": False
    }
    sb_api("messages", method="POST", data=new_row)
    print(f"💾 Saved to DB: {text_content[:20]}...")

# --- ЗАПУСК ---
if __name__ == "__main__":
    print("🤖 Bridge is starting...")
    bot.remove_webhook(drop_pending_updates=True)
    # Жодного ft.app()! Тільки поллінг.
    bot.infinity_polling(timeout=20)
