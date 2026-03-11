import telebot
import httpx
import os
import threading
import time
from datetime import datetime

# --- КОНФІГУРАЦІЯ (Koyeb Environment Variables) ---
TOKEN = os.getenv("TOKEN")
SUPABASE_URL = os.getenv("SUPABASE_URL")
SUPABASE_KEY = os.getenv("SUPABASE_KEY")
STORAGE_BUCKET = os.getenv("STORAGE_BUCKET", "support_media")
OPERATOR_NAME = os.getenv("OPERATOR_NAME", "Admin")

bot = telebot.TeleBot(TOKEN)

# --- УНІВЕРСАЛЬНИЙ API МІСТ ---
def sb_api(table, method="GET", data=None, params=None, is_storage=False):
    headers = {
        "apikey": SUPABASE_KEY, 
        "Authorization": f"Bearer {SUPABASE_KEY}"
    }
    
    if is_storage:
        # Шлях до сховища: storage/v1/object/bucket_name/file_name
        url = f"{SUPABASE_URL}/storage/v1/object/{STORAGE_BUCKET}/{table}"
        if method == "UPLOAD":
            headers["Content-Type"] = "application/octet-stream"
            headers["x-upsert"] = "true"
    else:
        # Шлях до бази даних: rest/v1/table_name
        url = f"{SUPABASE_URL}/rest/v1/{table}"
        headers["Content-Type"] = "application/json"
        headers["Prefer"] = "return=representation"

    try:
        with httpx.Client(timeout=30.0) as client:
            if method == "UPLOAD":
                return client.post(url, headers=headers, content=data)
            
            if method == "GET": 
                return client.get(url, headers=headers, params=params).json()
            
            if method == "POST": 
                return client.post(url, headers=headers, json=data).json()
            
            if method == "DELETE": 
                return client.delete(url, headers=headers, params=params)
                
    except Exception as e:
        print(f"!!! API Error ({method} {table}): {e}")
        return None

# --- ОБРОБНИК ПОВІДОМЛЕНЬ TELEGRAM ---
@bot.message_handler(content_types=['text', 'photo', 'document'])
def handle_tg(message):
    print(f"📥 Нове повідомлення від {message.from_user.first_name} (ID: {message.chat.id})")
    
    file_name = None
    text_content = message.text or message.caption or ""

    # Якщо прийшло фото
    if message.content_type == 'photo':
        try:
            # Беремо найкращу якість
            file_id = message.photo[-1].file_id
            file_info = bot.get_file(file_id)
            downloaded_file = bot.download_file(file_info.file_path)
            
            # Генеруємо ім'я файлу
            file_name = f"tg_{int(time.time())}.jpg"
            
            print(f"🚀 Завантаження фото в бакет '{STORAGE_BUCKET}': {file_name}...")
            
            # Відправка в Supabase Storage
            res = sb_api(file_name, method="UPLOAD", data=downloaded_file, is_storage=True)
            
            if res and res.status_code in [200, 201]:
                print(f"✅ Успішно завантажено в Storage: {res.status_code}")
            else:
                status = res.status_code if res else "None"
                error_text = res.text if res else "No response"
                print(f"❌ Помилка Storage API: {status} - {error_text}")
            
            if not text_content: text_content = "[Фото]"
        except Exception as e:
            print(f"!!! Помилка при обробці медіа: {e}")

    # Записуємо в таблицю messages (завжди, і текст, і медіа)
    new_row = {
        "user_id": message.chat.id,
        "sender": message.from_user.first_name,
        "text": text_content,
        "file_path": file_name,
        "timestamp": datetime.now().strftime('%H:%M'),
        "tg_msg_id": message.message_id,
        "is_read": False
    }
    
    db_res = sb_api("messages", method="POST", data=new_row)
    if db_res:
        print(f"💾 Запис в базу додано")
    else:
        print(f"❌ Помилка запису в базу")

# --- СТАРТ СЕРВЕРУ ТА БОТА ---
if __name__ == "__main__":
    print("🤖 Alpha Bridge запускається...")
    
    # Скидаємо вебхуки, щоб не було конфлікту 409
    bot.remove_webhook(drop_pending_updates=True)
    time.sleep(1)
    
    # Запуск бота в нескінченному циклі
    print("📡 Поллінг Telegram активовано")
    bot.infinity_polling(timeout=20, long_polling_timeout=10)
