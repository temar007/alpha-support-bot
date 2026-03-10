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
    # Пріоритет: Environment Variables (Koyeb) -> Config (локально)
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

# Ініціалізація клієнтів
supabase = create_client(SUPABASE_URL, SUPABASE_KEY)
bot = telebot.TeleBot(TOKEN)

if IS_WINDOWS:
    try:
        ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID(APP_NAME)
    except: pass

# --- SUPABASE API ENGINE ---
def sb_api(table, method="GET", data=None, params=None, is_storage=False):
    headers = {"apikey": SUPABASE_KEY, "Authorization": f"Bearer {SUPABASE_KEY}"}
    if is_storage:
        url = f"{SUPABASE_URL}/storage/v1/object/{STORAGE_BUCKET}/{table}"
    else:
        headers["Content-Type"] = "application/json"
        headers["Prefer"] = "return=representation"
        url = f"{SUPABASE_URL}/rest/v1/{table}"

    try:
        current_headers = headers.copy()
        if method == "UPLOAD": current_headers["Content-Type"] = "image/png"
        with httpx.Client(headers=current_headers, timeout=25.0) as client:
            if method == "GET": return client.get(url, params=params).json()
            if method == "POST": return client.post(url, json=data).json()
            if method == "PATCH": return client.patch(url, json=data, params=params).json()
            if method == "DELETE": return client.delete(url, params=params)
            if method == "UPLOAD": return client.post(url, content=data)
    except Exception as e:
        print(f"!!! API Error: {e}")
        return None

# --- СИСТЕМНІ УТИЛІТИ (WINDOWS ONLY) ---
def is_window_active():
    if not IS_WINDOWS: return False
    try:
        active_hwnd = win32gui.GetForegroundWindow()
        return APP_NAME in win32gui.GetWindowText(active_hwnd)
    except: return False

def flash_window():
    if not IS_WINDOWS: return
    try:
        hwnd = win32gui.FindWindow(None, APP_NAME)
        if hwnd: win32gui.FlashWindowEx(hwnd, win32con.FLASHW_TRAY | win32con.FLASHW_TIMERNOFG, 0, 0)
    except: pass

# --- ГОЛОВНИЙ ДОДАТОК ---
def main(page: ft.Page):
    page.title = APP_NAME
    page.theme_mode = ft.ThemeMode.DARK
    if IS_WINDOWS:
        page.window_width = 1250
        page.window_height = 850

    state = {"selected_id": None, "unread": set(), "reply_tg_id": None, "is_loading": False}

    # UI Elements
    msg_area = ft.Column(scroll=ft.ScrollMode.AUTO, expand=True, spacing=10)
    msg_container = ft.Container(expand=True, padding=10, border=ft.border.all(1, ft.colors.GREY_800), border_radius=10)
    contact_list = ft.Column(scroll=ft.ScrollMode.AUTO, spacing=5)
    
    id_input = ft.TextField(label="ID", read_only=True)
    company_input = ft.TextField(label="Компанія")
    notes_input = ft.TextField(label="Нотатки", multiline=True, min_lines=3)
    answer_input = ft.TextField(hint_text="Відповідь...", expand=True, on_submit=lambda e: send_reply(None))

    reply_preview = ft.Container(visible=False, bgcolor=ft.colors.BLACK12, padding=10, border_radius=5)
    reply_text = ft.Text(size=11, italic=True, color=ft.colors.BLUE_200)

    # --- REALTIME BRIDGE ---
    async def listen_supabase():
        try:
            async_supabase: AsyncClient = await create_async_client(SUPABASE_URL, SUPABASE_KEY)
            def on_handle_async(payload):
                new_row = payload['record']
                page.pubsub.send_all({"type": "update", "user_id": new_row['user_id'], "name": new_row['sender'], "text": new_row.get('text', '')})
            
            channel = async_supabase.channel('db-changes')
            await channel.on_postgres_changes(event="INSERT", table="messages", schema="public", callback=on_handle_async).subscribe()
            while True: await asyncio.sleep(1)
        except Exception as e:
            print(f"Realtime connection lost: {e}")
            await asyncio.sleep(5)

    threading.Thread(target=lambda: asyncio.run(listen_supabase()), daemon=True).start()

    # --- ТЕЛЕГРАМ POLLING ---
    @bot.message_handler(func=lambda m: True)
    def handle_tg(message):
        print(f"📥 New message from {message.from_user.first_name}")
        new_row = {
            "user_id": message.chat.id,
            "sender": message.from_user.first_name,
            "text": message.text or "[Медіа]",
            "timestamp": datetime.now().strftime('%H:%M'),
            "tg_msg_id": message.message_id
        }
        sb_api("messages", method="POST", data=new_row)
        page.pubsub.send_all({"type": "update", "user_id": message.chat.id, "name": message.from_user.first_name, "text": message.text})

    print("🚀 Bot starting polling...")
    threading.Thread(target=bot.infinity_polling, kwargs={"timeout": 20}, daemon=True).start()

    # --- ЛОГІКА УПРАВЛІННЯ ---
    def load_chat(user_id, reset_unread=True):
        if not user_id or state["is_loading"]: return
        state["is_loading"] = True
        try:
            msg_area.controls.clear()
            page.update()
            state["selected_id"] = int(user_id)
            if reset_unread and state["selected_id"] in state["unread"]: 
                state["unread"].remove(state["selected_id"])
            
            cl_res = sb_api("clients", params={"select": "*", "id": f"eq.{user_id}"})
            current_lock = cl_res[0].get("locked_by") if cl_res else None

            if current_lock == OPERATOR_NAME:
                answer_input.disabled, answer_input.hint_text = False, "Ваша відповідь..."
                take_work_btn.visible, finish_btn.visible = False, True
            elif current_lock:
                answer_input.disabled, answer_input.hint_text = True, f"🔒 У роботі у: {current_lock}"
                take_work_btn.visible, finish_btn.visible = False, True
            else:
                answer_input.disabled, answer_input.hint_text = True, "Натисніть 'Взяти в роботу'"
                take_work_btn.visible, finish_btn.visible = True, False

            msgs = sb_api("messages", params={"select": "*", "user_id": f"eq.{user_id}", "order": "id.asc"})
            for m in (msgs or []):
                is_admin = "Я (" in (m.get("sender") or "")
                elements = []
                if m.get("file_path"):
                    img_url = f"{SUPABASE_URL}/storage/v1/object/public/{STORAGE_BUCKET}/{m['file_path']}"
                    elements.append(ft.Image(src=img_url, width=280, border_radius=10))
                if m.get("text") and m.get("text") != "[Скріншот]":
                    elements.append(ft.Text(m.get("text"), color=ft.colors.BLACK if is_admin else ft.colors.WHITE, selectable=True))
                
                action_row = ft.Row([
                    ft.IconButton(ft.icons.REPLY_ROUNDED, icon_size=14, on_click=lambda e, tid=m.get('tg_msg_id'), txt=m.get('text'): set_reply(tid, txt or "Медіа")),
                    ft.Text(m.get("timestamp", ""), size=9),
                    ft.IconButton(ft.icons.DELETE_OUTLINE, icon_size=14, on_click=lambda e, mid=m['id']: delete_message(mid, user_id))
                ], alignment=ft.MainAxisAlignment.END, spacing=0)
                elements.append(action_row)

                msg_area.controls.append(ft.Row([
                    ft.Container(content=ft.Column(elements, spacing=5, tight=True), bgcolor=ft.colors.BLUE_200 if is_admin else ft.colors.GREY_800, padding=12, border_radius=15)
                ], alignment=ft.MainAxisAlignment.END if is_admin else ft.MainAxisAlignment.START))
            
            if cl_res:
                id_input.value, company_input.value, notes_input.value = str(user_id), cl_res[0].get("company") or "", cl_res[0].get("notes") or ""
            
            msg_container.content = msg_area
            refresh_contacts(search_field.value)
            page.update()
            msg_area.scroll_to(offset=-1, duration=300)
        finally: state["is_loading"] = False

    def refresh_contacts(search=""):
        contact_list.controls.clear()
        clients = sb_api("clients", params={"select": "*", "order": "last_activity.desc"})
        for c in (clients or []):
            cid = c.get('id')
            if not search or search.lower() in c['name'].lower() or (c.get('company') and search.lower() in c['company'].lower()):
                is_unread, is_sel, lock_owner = cid in state["unread"], state["selected_id"] == cid, c.get("locked_by")
                lock_icon = ft.Icon(ft.icons.LOCK_PERSON_ROUNDED, size=16, color=ft.colors.GREEN_400 if lock_owner == OPERATOR_NAME else ft.colors.RED_400) if lock_owner else ft.Container()
                contact_list.controls.append(ft.Container(content=ft.ListTile(title=ft.Row([ft.Text(c['name'], weight="bold" if is_unread else "normal", color=ft.colors.AMBER if is_unread else (ft.colors.BLUE_200 if is_sel else None), expand=True), lock_icon]), subtitle=ft.Text(c.get('company') or "Клієнт", size=11), on_click=lambda e, i=cid: load_chat(i)), bgcolor=ft.colors.WHITE10 if is_sel else None, border_radius=10))
        page.update()

    def send_reply(e):
        if not state["selected_id"] or not answer_input.value: return
        uid, txt = state["selected_id"], answer_input.value
        try:
            res = bot.send_message(uid, txt, reply_to_message_id=state["reply_tg_id"])
            sb_api("messages", method="POST", data={"user_id": uid, "sender": f"Я ({OPERATOR_NAME})", "text": txt, "timestamp": datetime.now().strftime('%H:%M'), "tg_msg_id": res.message_id})
            answer_input.value, state["reply_tg_id"], reply_preview.visible = "", None, False
            load_chat(uid)
        except Exception as ex:
            print(f"Send error: {ex}")

    def delete_message(mid, uid):
        res = sb_api("messages", params={"select": "tg_msg_id", "id": f"eq.{mid}"})
        if res and res[0].get("tg_msg_id"):
            try: bot.delete_message(uid, res[0]["tg_msg_id"])
            except: pass
        sb_api("messages", method="DELETE", params={"id": f"eq.{mid}"})
        load_chat(uid)

    def set_reply(tg_id, text_snippet):
        state["reply_tg_id"] = tg_id
        reply_text.value, reply_preview.visible = f"Відповідь на: {text_snippet[:40]}...", True
        page.update()

    def lock_chat_to_me(e=None):
        if state["selected_id"]:
            sb_api("clients", method="PATCH", data={"locked_by": OPERATOR_NAME}, params={"id": f"eq.{state['selected_id']}"})
            load_chat(state["selected_id"])

    def unlock_chat(e=None):
        if state["selected_id"]:
            sb_api("clients", method="PATCH", data={"locked_by": None}, params={"id": f"eq.{state['selected_id']}"})
            load_chat(state["selected_id"])

    def handle_paste(e):
        if not IS_WINDOWS: return
        img = ImageGrab.grabclipboard()
        if isinstance(img, Image.Image):
            f_name = f"scr_{int(time.time())}.png"
            img.save(f_name, "PNG")
            with open(f_name, "rb") as f:
                res = sb_api(f_name, method="UPLOAD", data=f.read(), is_storage=True)
            if res and res.status_code in [200, 201]:
                tg_res = bot.send_photo(state["selected_id"], open(f_name, "rb"))
                sb_api("messages", method="POST", data={"user_id": state["selected_id"], "sender": f"Я ({OPERATOR_NAME})", "text": "[Скріншот]", "file_path": f_name, "tg_msg_id": tg_res.message_id})
                load_chat(state["selected_id"])
            if os.path.exists(f_name): os.remove(f_name)

    # --- PUB/SUB ---
    def on_broadcast(data):
        if data["type"] == "update":
            uid = int(data.get("user_id"))
            if state["selected_id"] == uid: load_chat(uid)
            else:
                state["unread"].add(uid)
                if IS_WINDOWS and not is_window_active():
                    flash_window()
                    notification.notify(title=f"📩 {data.get('name')}", message=data.get('text', 'Файл'), app_name=APP_NAME)
            refresh_contacts(search_field.value)
    
    page.pubsub.subscribe(on_broadcast)

    # --- LAYOUT ---
    search_field = ft.TextField(hint_text="Пошук...", on_change=lambda e: refresh_contacts(e.control.value))
    take_work_btn = ft.ElevatedButton("Взяти в роботу", icon=ft.icons.PLAY_ARROW, bgcolor=ft.colors.BLUE_700, on_click=lock_chat_to_me, visible=False)
    finish_btn = ft.OutlinedButton("Завершити", icon=ft.icons.DONE_ALL, on_click=unlock_chat, visible=False)
    reply_preview.content = ft.Row([ft.Icon(ft.icons.REPLY, size=15), reply_text, ft.IconButton(ft.icons.CLOSE, icon_size=15, on_click=lambda _: (setattr(reply_preview, 'visible', False), page.update()))])

    page.add(ft.Row([
        ft.Container(content=ft.Column([ft.Text("Діалоги", size=20, weight="bold"), search_field, ft.Divider(), contact_list], expand=True), width=300, padding=15, border=ft.border.all(1, ft.colors.GREY_800), border_radius=10),
        ft.Column([msg_container, reply_preview, ft.Row([ft.IconButton(ft.icons.SYNC, on_click=lambda _: load_chat(state["selected_id"])), answer_input, ft.IconButton(ft.icons.PASTE_ROUNDED, on_click=handle_paste, visible=IS_WINDOWS), ft.IconButton(ft.icons.SEND, on_click=send_reply)])], expand=True),
        ft.Container(content=ft.Column([ft.Text("Клієнт", size=18, weight="bold"), id_input, company_input, notes_input, take_work_btn, finish_btn, ft.ElevatedButton("Зберегти", icon=ft.icons.SAVE, on_click=lambda _: (sb_api("clients", method="PATCH", data={"company": company_input.value, "notes": notes_input.value}, params={"id": f"eq.{state['selected_id']}"}), refresh_contacts()))], spacing=15), width=260, padding=15, border=ft.border.all(1, ft.colors.GREY_800), border_radius=10)
    ], expand=True, spacing=15))
    
    refresh_contacts()

# --- RUN ---
if __name__ == "__main__":
    if not IS_WINDOWS:
        # Для Koyeb: тільки режим сервера без спроб відкрити браузер
        port = int(os.getenv("PORT", 8080))
        print(f"🌐 Starting server on port {port}...")
        ft.app(
            target=main, 
            view=None,  # Важливо! На сервері не потрібен view
            host="0.0.0.0", 
            port=port
        )
    else:
        ft.app(target=main)

