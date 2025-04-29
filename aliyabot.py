import os
import logging
import sqlite3
import asyncio
from aiohttp import web
from telegram import Update, ReplyKeyboardMarkup, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    ApplicationBuilder, CommandHandler, MessageHandler, filters,
    ContextTypes, ConversationHandler, CallbackQueryHandler
)

BOT_TOKEN = os.getenv("BOT_TOKEN")
WEBHOOK_PATH = "/webhook"
PORT = int(os.getenv("PORT", 8080))
RAILWAY_URL = os.getenv("RAILWAY_URL")

logging.basicConfig(level=logging.INFO)

CHOOSING, TYPING_DESC, ASK_PHOTO, SENDING_PHOTO = range(4)

conn = sqlite3.connect("lostfound.db", check_same_thread=False)
cursor = conn.cursor()

cursor.execute('''
    CREATE TABLE IF NOT EXISTS items (
        id INTEGER PRIMARY KEY,
        user_id INTEGER,
        username TEXT,
        type TEXT,
        description TEXT,
        photo_file_id TEXT
    )
''')

cursor.execute('''
    CREATE TABLE IF NOT EXISTS users (
        user_id INTEGER PRIMARY KEY,
        username TEXT
    )
''')

cursor.execute('''
    CREATE TABLE IF NOT EXISTS button_stats (
        button_name TEXT PRIMARY KEY,
        clicks INTEGER DEFAULT 0
    )
''')
conn.commit()

def update_button_stat(button_name):
    cursor.execute('''
        INSERT INTO button_stats (button_name, clicks)
        VALUES (?, 1)
        ON CONFLICT(button_name)
        DO UPDATE SET clicks = clicks + 1
    ''', (button_name,))
    conn.commit()

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.message.from_user.id
    username = update.message.from_user.username or "anon"

    cursor.execute("INSERT OR IGNORE INTO users (user_id, username) VALUES (?, ?)", (user_id, username))
    conn.commit()

    cursor.execute("SELECT COUNT(*) FROM users")
    count = cursor.fetchone()[0]

    keyboard = [["🟢 Нашёл", "🔴 Потерял"],
                ["🟢 Найдено", "🔴 Потеряно"],
                ["🗂 Мои посты"]]

    await update.message.reply_text(
        f"Привет! Ты {count}-й пользователь по счёту.\nЯ бот Lost&Found AlmaU. Выберите действие:",
        reply_markup=ReplyKeyboardMarkup(keyboard, resize_keyboard=True)
    )
    return CHOOSING

async def send_template(update: Update, context: ContextTypes.DEFAULT_TYPE, mode: str = ""):
    if mode == "found":
        template = (
            "❗️*Шаблон для сообщения, если вы нашли вещь:*\n\n"
            "*1. Что вы нашли:*\n"
            "_Кратко опишите предмет — например: студенческий, браслет, бутылка и т.п._\n\n"
            "*2. Где нашли:*\n"
            "_Укажите место: аудитория, корпус, коридор, столовая и т.п._\n\n"
            "*3. Когда нашли:*\n"
            "_Дата и время, если знаете_\n\n"
            "*4. Контакт или где забрать:*"
        )
    elif mode == "lost":
        template = (
            "❗️*Шаблон для сообщения, если вы ищите вещь:*\n\n"
            "*1. Что вы потеряли:*\n"
            "_Кратко и понятно опишите предмет — например: зонт, ключи, студенческий билет и т.д._\n\n"
            "*2. Где примерно потеряли:*\n"
            "_Укажите корпус, аудиторию, зону (библиотека, столовая и т.д.)_\n\n"
            "*3. Когда потеряли:*\n"
            "_Дата и примерное время_\n\n"
            "*4. Фото (если есть)*"
        )
    else:
        template = (
            "❗️*Общий шаблон:*\n\n"
            "1. Что потеряно/найдено\n"
            "2. Где\n"
            "3. Когда\n"
            "4. Фото (если есть)\n"
            "5. Контакт: @username\n"
            "#поиск #находка"
        )
    await update.message.reply_text(template, parse_mode="Markdown")
    return CHOOSING

async def choose_action(update: Update, context: ContextTypes.DEFAULT_TYPE):
    msg = update.message.text
    if msg == "🟢 Нашёл":
        update_button_stat("Нашёл")
        context.user_data["type"] = "found"
        await send_template(update, context, "found")
        await update.message.reply_text("✏️ Введите описание:")
        return TYPING_DESC
    elif msg == "🔴 Потерял":
        update_button_stat("Потерял")
        context.user_data["type"] = "lost"
        await send_template(update, context, "lost")
        await update.message.reply_text("✏️ Введите описание:")
        return TYPING_DESC
    elif msg == "🟢 Найдено":
        update_button_stat("Найдено")
        return await show_found_items(update, context)
    elif msg == "🔴 Потеряно":
        update_button_stat("Потеряно")
        return await show_lost_items(update, context)
    elif msg == "🗂 Мои посты":
        update_button_stat("Мои посты")
        return await show_my_posts(update, context)
    else:
        await update.message.reply_text("Выберите действие с клавиатуры.")
        return CHOOSING

async def get_description(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = update.message.text
    control_buttons = ["🟢 Нашёл", "🔴 Потерял", "🟢 Найдено", "🔴 Потеряно", "🗂 Мои посты"]

    if text in control_buttons:
        return await choose_action(update, context)

    context.user_data["description"] = text
    keyboard = [["✅ Да", "❌ Нет"]]
    await update.message.reply_text("У вас есть фото этой вещи?", reply_markup=ReplyKeyboardMarkup(keyboard, resize_keyboard=True))
    return ASK_PHOTO

async def ask_for_photo(update: Update, context: ContextTypes.DEFAULT_TYPE):
    answer = update.message.text
    if answer == "✅ Да":
        await update.message.reply_text("📸 Пожалуйста, отправьте фото:")
        return SENDING_PHOTO
    elif answer == "❌ Нет":
        return await save_item_without_photo(update, context)
    else:
        await update.message.reply_text("Пожалуйста, выберите ✅ Да или ❌ Нет.")
        return ASK_PHOTO

async def save_item_without_photo(update: Update, context: ContextTypes.DEFAULT_TYPE):
    data = context.user_data
    cursor.execute('''
        INSERT INTO items (user_id, username, type, description, photo_file_id)
        VALUES (?, ?, ?, ?, ?)
    ''', (
        update.message.from_user.id,
        update.message.from_user.username or "anon",
        data["type"],
        data["description"],
        None
    ))
    conn.commit()
    await update.message.reply_text("✅ Объявление без фото добавлено!")
    return await start(update, context)

async def get_photo(update: Update, context: ContextTypes.DEFAULT_TYPE):
    data = context.user_data
    photo_file_id = update.message.photo[-1].file_id
    cursor.execute('''
        INSERT INTO items (user_id, username, type, description, photo_file_id)
        VALUES (?, ?, ?, ?, ?)
    ''', (
        update.message.from_user.id,
        update.message.from_user.username or "anon",
        data["type"],
        data["description"],
        photo_file_id
    ))
    conn.commit()
    await update.message.reply_text("✅ Объявление с фото добавлено!")
    return await start(update, context)

async def show_found_items(update: Update, context: ContextTypes.DEFAULT_TYPE):
    cursor.execute("SELECT username, description, photo_file_id FROM items WHERE type = 'found' ORDER BY id DESC LIMIT 5")
    rows = cursor.fetchall()
    if not rows:
        await update.message.reply_text("❌ Найденных вещей пока нет.")
        return CHOOSING
    for username, desc, photo in rows:
        caption = f"🟢 *Найдено*\n\n*Описание:* {desc}\n*Контакт:* @{username}"
        if photo:
            await update.message.reply_photo(photo, caption=caption, parse_mode="Markdown")
        else:
            await update.message.reply_text(caption + "\n\n📌 *Фото не было отправлено*", parse_mode="Markdown")
    return CHOOSING

async def show_lost_items(update: Update, context: ContextTypes.DEFAULT_TYPE):
    cursor.execute("SELECT username, description, photo_file_id FROM items WHERE type = 'lost' ORDER BY id DESC LIMIT 5")
    rows = cursor.fetchall()
    if not rows:
        await update.message.reply_text("❌ Потерянных вещей пока нет.")
        return CHOOSING
    for username, desc, photo in rows:
        caption = f"🔴 *Потеряно*\n\n*Описание:* {desc}\n*Контакт:* @{username}"
        if photo:
            await update.message.reply_photo(photo, caption=caption, parse_mode="Markdown")
        else:
            await update.message.reply_text(caption + "\n\n📌 *Фото не было отправлено*", parse_mode="Markdown")
    return CHOOSING

async def handle_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    data = query.data
    if data.startswith("delete:"):
        item_id = int(data.split(":")[1])
        cursor.execute("DELETE FROM items WHERE id = ?", (item_id,))
        conn.commit()
        try:
            await query.message.delete()
        except Exception as e:
            print("Ошибка удаления сообщения:", e)
    elif data == "ignore":
        await query.answer("👌 Хорошо, пост сохранён.", show_alert=False)

async def show_my_posts(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.message.from_user.id
    cursor.execute("SELECT id, type, description, photo_file_id FROM items WHERE user_id = ? ORDER BY id DESC LIMIT 10", (user_id,))
    rows = cursor.fetchall()
    if not rows:
        await update.message.reply_text("❌ У вас пока нет добавленных постов.")
        return CHOOSING
    for item_id, item_type, desc, photo in rows:
        label = "🟢 Найдено" if item_type == "found" else "🔴 Потеряно"
        caption = f"{label}\n\n*Описание:* {desc}\n*Вы добавили этот пост.*"
        keyboard = InlineKeyboardMarkup([[
            InlineKeyboardButton("🗑 Удалить", callback_data=f"delete:{item_id}"),
            InlineKeyboardButton("✅ Оставить", callback_data="ignore")
        ]])
        if photo:
            await update.message.reply_photo(photo, caption=caption, parse_mode="Markdown", reply_markup=keyboard)
        else:
            await update.message.reply_text(caption + "\n\n📌 *Фото не было отправлено*", parse_mode="Markdown", reply_markup=keyboard)
    return CHOOSING

async def cancel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("❌ Действие отменено.")
    return ConversationHandler.END

async def show_user_count(update: Update, context: ContextTypes.DEFAULT_TYPE):
    cursor.execute("SELECT COUNT(*) FROM users")
    count = cursor.fetchone()[0]
    await update.message.reply_text(f"👥 Общее количество пользователей: {count}")

async def main():
    app = ApplicationBuilder().token(BOT_TOKEN).build()

    conv_handler = ConversationHandler(
        entry_points=[CommandHandler("start", start)],
        states={
            CHOOSING: [MessageHandler(filters.TEXT & ~filters.COMMAND, choose_action)],
            TYPING_DESC: [MessageHandler(filters.TEXT & ~filters.COMMAND, get_description)],
            ASK_PHOTO: [MessageHandler(filters.TEXT & ~filters.COMMAND, ask_for_photo)],
            SENDING_PHOTO: [MessageHandler(filters.PHOTO, get_photo)],
        },
        fallbacks=[CommandHandler("cancel", cancel)],
    )

    app.add_handler(conv_handler)
    app.add_handler(CallbackQueryHandler(handle_callback))
    app.add_handler(CommandHandler("users", show_user_count))

    await app.bot.delete_webhook()
    webhook_url = f"{RAILWAY_URL}{WEBHOOK_PATH}"
    await app.bot.set_webhook(webhook_url)

    async def handler(request):
        data = await request.json()
        update = Update.de_json(data, app.bot)
        await app.process_update(update)          
        return web.Response(text="OK")

    web_app = web.Application()
    web_app.router.add_post(WEBHOOK_PATH, handler)

    print(f"🚀 Webhook работает: {webhook_url}")
    await app.initialize()
    await app.start()
    await web._run_app(web_app, port=PORT)
    await app.stop()

if __name__ == "__main__":
    asyncio.run(main())