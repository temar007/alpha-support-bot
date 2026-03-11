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

def sb_api(table, method="GET", data=None, params=None, is_storage=False):
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
    except Exception as e:
        print(f"!!! API Error: {e}")
        return None

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
            res = sb_api(file_name, method="UPLOAD", data=downloaded_file, is_storage=True)
            if not text_content: text_content = "[Фото]"
        except: pass

    new_row = {
        "user_id": message.chat.id, "sender": message.from_user.first_name,
        "text": text_content, "file_path": file_name,
        "timestamp": datetime.now().strftime('%H:%M'),
        "tg_msg_id": message.message_id, "is_read": False
    }
    sb_api("messages", method="POST", data=new_row)
    print(f"💾 Message from {message.from_user.first_name} saved.")

if __name__ == "__main__":
    print("🤖 Bridge started (No Port Mode)")
    try:
        bot.remove_webhook()
    except: pass
    bot.infinity_polling(timeout=20)
