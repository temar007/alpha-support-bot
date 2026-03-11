import flet as ft
import telebot
from telebot import apihelper
import httpx
import os
import threading
import time
import asyncio
import platform
from datetime import datetime
from supabase import create_client, create_async_client, AsyncClient

# --- ПЕРЕВІРКА СИСТЕМИ ---
IS_WINDOWS = platform.system() == "Windows"

if IS_WINDOWS:
    import win32gui
    import win32con
    import ctypes
    from PIL import ImageGrab, Image
    from plyer import notification
else:
    Image = None 

# --- КОНФІГУРАЦІЯ ---
def get_setting(key, default=None):
    val = os.getenv(key)
    if val: return val
    try:
        import config
        return getattr(config, key, default)
    except ImportError:
        return default

TOKEN = get_setting("TOKEN")
SUPABASE_URL = get_setting("SUPABASE_URL")
SUPABASE_KEY = get_setting("SUPABASE_KEY")
APP_NAME = get_setting("APP_NAME", "Support Bot Alpha")
STORAGE_BUCKET = get_setting("STORAGE_BUCKET", "avatars")
OPERATOR_NAME = get_setting("OPERATOR_NAME", "Admin")

supabase = create_client(SUPABASE_URL, SUPABASE_KEY)
bot = telebot.TeleBot(TOKEN)

# Глобальний список активних сторінок для розсилки оновлень (PubSub заміна)
active_pages = []

def sb_api(table, method="GET", data=None, params=None, is_storage=False):
    headers = {
        "apikey": SUPABASE_KEY, 
        "Authorization": f"Bearer {SUPABASE_KEY}"
    }
    
    if is_storage:
        url = f"{SUPABASE_URL}/storage/v1/object/{STORAGE_BUCKET}/{table}"
        # Налаштування саме для файлів
        if method == "UPLOAD":
            headers["Content-Type"] = "application/octet-stream"
            headers["x-upsert"] = "true"
    else:
        url = f"{SUPABASE_URL}/rest/v1/{table}"
        # Налаштування для тексту/бази даних
        headers["Content-Type"] = "application/json"
        headers["Prefer"] = "return=representation"

    try:
        with httpx.Client(timeout=30.0) as client:
            if method == "GET": return client.get(url, headers=headers, params=params).json()
            if method == "POST": return client.post(url, headers=headers, json=data).json()
            if method == "DELETE": return client.delete(url, headers=headers, params=params)
            
            if method == "UPLOAD":
                # Тут використовуємо 'content=data' для бінарних файлів
                return client.post(url, headers=headers, content=data)
                
    except Exception as e:
        print(f"!!! API Error: {e}")
        return None

# --- ОБРОБНИК ТЕЛЕГРАМ (Глобальний) ---
@bot.message_handler(content_types=['text', 'photo', 'document'])
def handle_tg(message):
    print(f"📥 New message from {message.from_user.first_name}")
    
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
        except Exception as e:
            print(f"Error saving photo: {e}")

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
    
    # Розсилаємо оновлення всім підключеним клієнтам Alpha
    for pg in active_pages:
        try:
            pg.pubsub.send_all({"type": "update", "user_id": message.chat.id, "name": message.from_user.first_name, "text": text_content})
        except: pass

# --- ГОЛОВНИЙ ДОДАТОК ---
def main(page: ft.Page):
    page.title = APP_NAME
    active_pages.append(page)
    
    page.theme_mode = ft.ThemeMode.DARK
    state = {"selected_id": None, "unread": set(), "reply_tg_id": None, "is_loading": False}

    msg_area = ft.Column(scroll=ft.ScrollMode.AUTO, expand=True, spacing=10)
    msg_container = ft.Container(expand=True, padding=10, border=ft.border.all(1, ft.colors.GREY_800), border_radius=10)
    contact_list = ft.Column(scroll=ft.ScrollMode.AUTO, spacing=5)
    
    id_input = ft.TextField(label="ID", read_only=True)
    company_input = ft.TextField(label="Компанія")
    notes_input = ft.TextField(label="Нотатки", multiline=True, min_lines=3)
    answer_input = ft.TextField(hint_text="Відповідь...", expand=True, on_submit=lambda e: send_reply(None))

    reply_preview = ft.Container(visible=False, bgcolor=ft.colors.BLACK12, padding=10, border_radius=5)
    reply_text = ft.Text(size=11, italic=True, color=ft.colors.BLUE_200)

    # (Далі йдуть твої функції load_chat, send_reply, refresh_contacts тощо без змін...)
    # [Скопіюй сюди функції load_chat, refresh_contacts, send_reply, delete_message з твого минулого коду]
    
    # ... (код UI layout) ...
    page.add(ft.Row([
        # ... твій layout ...
    ]))
    
    def on_broadcast(data):
        if data["type"] == "update":
            uid = int(data.get("user_id"))
            if state["selected_id"] == uid: load_chat(uid)
            else: state["unread"].add(uid)
            refresh_contacts(search_field.value)
    
    page.pubsub.subscribe(on_broadcast)
    refresh_contacts()

# --- RUN ---
if __name__ == "__main__":
    # Запуск бота в окремому потоці
    threading.Thread(target=bot.infinity_polling, kwargs={"timeout": 20}, daemon=True).start()
    
    if not IS_WINDOWS:
        port = int(os.getenv("PORT", 8000))
        ft.app(target=main, view=None, host="0.0.0.0", port=port)
    else:
        ft.app(target=main)


