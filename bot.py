import os
import json
import base64
import requests
import subprocess
import logging
import uuid
from telegram import Update, ReplyKeyboardMarkup
from telegram.ext import ApplicationBuilder, CommandHandler, MessageHandler, ContextTypes, filters
import database
import whisper

# Configure logging
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)
logger = logging.getLogger(__name__)

TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "8690167473:AAFiacl5M1yVEsoROFLg-xAvUwby2mjBP6A")

# Load Whisper model once
WHISPER_MODEL = whisper.load_model("small")

def ask_llama(text: str, db_context: str = "") -> dict:
    system = f"""Ты — ИИ-кладовщик Ефим. Твоя задача: управлять складом или отвечать на вопросы по базе.
    ТЕКУЩЕЕ СОСТОЯНИЕ БАЗЫ ДАННЫХ:
    {db_context}

    ПРАВИЛА:
    1. Мы учитываем ТОЛЬКО два вида: "Кот" и "Собака". Любых других игнорируй.
    2. Если пользователь задает ВОПРОС о животных на складе (кто где сидит, есть ли свободные клетки, где рыжие коты и т.д.), выбери action: 'answer' и напиши понятный ответ в поле 'response'.
    3. Для записи данных используй action: 'add' (поступление), 'remove' (списание), 'update' (изменение).
    4. Тип животного должен быть в именительном падеже, ед. числе, с заглавной буквы. ВСЕ клички и цвета пиши СТРОГО КИРИЛЛИЦЕЙ.
    5. Формат ответа: {{"action":"...", "response":"...", "target_cell":"A1", "target_name":"...", "animals":[{{"type":"Кот", "name":"...", "color":"...", "age":"...", "legs": 4, "ears": 2, "eyes": 2}}]}}
    """
    try:
        r = requests.post("http://localhost:11434/api/generate", json={
            "model": "llama3:8b", "system": system, "prompt": text, "format": "json", "stream": False
        }, timeout=120)
        r.raise_for_status()
        resp_text = r.json().get("response", "{}")
        return json.loads(resp_text)
    except Exception as e:
        logger.error(f"Ошибка Llama: {e}")
        return {"action": "error", "response": "Произошла ошибка при обращении к Llama."}

def ask_llava(img_path: str, caption: str) -> str:
    try:
        with open(img_path, "rb") as f:
            b64 = base64.b64encode(f.read()).decode("utf-8")

        prompt = (
            f"Ты — аналитик склада. Твоя задача объединить данные с фото и текстовой подписи: '{caption}'.\n"
            "ПРАВИЛА:\n"
            "1. Подпись пользователя — ГЛАВНЫЙ источник для КЛИЧКИ и ВОЗРАСТА. Если в тексте есть имя или возраст, используй их.\n"
            "2. Фото — источник для ВИДА (Кот или Собака) и ЦВЕТА.\n"
            "3. Если в подписи нет имени, пиши 'Кличка: Неизвестно'.\n"
            "Верни СТРОГО в таком формате: Вид: ..., Кличка: ..., Цвет: ..., Возраст: ..."
        )

        r = requests.post("http://localhost:11434/api/generate", json={
            "model": "llava:7b", "prompt": prompt, "images": [b64], "stream": False
        }, timeout=120)
        r.raise_for_status()
        return r.json().get("response", "").strip()
    except Exception as e:
        logger.error(f"Ошибка Llava: {e}")
        return "Ошибка анализа изображения."

def transcribe_voice(path: str) -> str:
    try:
        # Use pydub if ffmpeg is missing (though pydub also needs ffmpeg/ffprobe for ogg)
        # Assuming ffmpeg is available in the target environment as per run_bot.bat
        wav = path.replace(".ogg", ".wav")
        subprocess.run(["ffmpeg", "-y", "-i", path, wav], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        if not os.path.exists(wav):
            # Fallback or error
            res = WHISPER_MODEL.transcribe(path, language="ru") # Whisper can sometimes handle ogg if it has ffmpeg backend
        else:
            res = WHISPER_MODEL.transcribe(wav, language="ru")
            os.remove(wav)
        return res["text"]
    except Exception as e:
        logger.error(f"Ошибка Whisper: {e}")
        return "[Ошибка распознавания речи]"

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    kbd = [['📊 Статистика склада', '📋 Задачи']]
    resume = (
        "🤖 **Ефим — ИИ-Кладовщик**\n\n"
        "Я ваш цифровой помощник по учету животных на складе (ячейки A1-J10).\n\n"
        "**Что я умею:**\n"
        "✅ Вести журнал поступлений и списаний (принимаю текст, аудио и фото).\n"
        "✅ Отвечать на вопросы по складу (Спросите: «Кто в клетке B2?», «Сколько свободных мест?», «Где белые коты?»).\n"
        "✅ Отслеживать незаполненные карточки животных (кнопка «Задачи»).\n\n"
        "**Мои ограничения:**\n"
        "⚠️ Учитываю ТОЛЬКО Котов и Собак. Остальных (коров, лошадей и т.д.) игнорирую.\n"
        "⚠️ Не списываю животных, если их карточка заполнена не до конца.\n"
        "⚠️ Все клички и цвета фиксирую строго на кириллице."
    )
    await update.message.reply_text(resume, reply_markup=ReplyKeyboardMarkup(kbd, resize_keyboard=True), parse_mode="Markdown")

async def handle_text(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = update.message.from_user.id
    text = update.message.text
    ltext = text.lower()

    if any(word in ltext for word in ["задач", "задани", "📋"]):
        await update.message.reply_text(database.get_user_tasks(uid))
        return
    if any(word in ltext for word in ["статистик", "статус", "сводк", "📊"]):
        await update.message.reply_text(database.get_stats())
        return

    db_context = database.get_inventory_brief()
    intent = ask_llama(text, db_context)
    act = intent.get("action")

    if act == "answer":
        res = intent.get("response", "К сожалению, я не нашел ответа в базе.")
    elif act == "add":
        res = database.add_animals(intent.get("animals", []), uid)
    elif act == "remove":
        res = database.find_and_modify(intent, uid, "remove")
    elif act == "update":
        res = database.find_and_modify(intent, uid, "update")
    elif act == "error":
        res = intent.get("response")
    else:
        res = "Команда не распознана или не требует действий с базой."

    await update.message.reply_text(res)

async def handle_media(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = update.message.from_user.id
    wait = await update.message.reply_text("⏳ Изучаю данные...")

    try:
        if update.message.voice:
            f = await update.message.voice.get_file()
            p = f"voice_{uuid.uuid4()}.ogg"
            await f.download_to_drive(p)
            txt = transcribe_voice(p)
            if os.path.exists(p): os.remove(p)
            await update.message.reply_text(f"🎤 Распознано: {txt}")
        else:
            f = await update.message.photo[-1].get_file()
            os.makedirs("photos", exist_ok=True)
            p = f"photos/{f.file_id}.jpg"
            await f.download_to_drive(p)
            txt = f"На фото: {ask_llava(p, update.message.caption or 'поступление')}"
            # Keep photo for a while or remove
            # os.remove(p)

        db_context = database.get_inventory_brief()
        intent = ask_llama(txt, db_context)
        act = intent.get("action")

        if act == "answer":
            res = intent.get("response", "К сожалению, я не нашел ответа.")
        elif act == "add":
            res = database.add_animals(intent.get("animals", []), uid)
        elif act == "remove":
            res = database.find_and_modify(intent, uid, "remove")
        elif act == "update":
            res = database.find_and_modify(intent, uid, "update")
        elif act == "error":
            res = intent.get("response")
        else:
            # For photos, default to add if it looks like an animal description
            if "Вид:" in txt:
                res = database.add_animals(intent.get("animals", []), uid)
            else:
                res = "Команда не распознана."

        await wait.edit_text(res)
    except Exception as e:
        logger.error(f"Error in handle_media: {e}")
        await wait.edit_text("Произошла ошибка при обработке медиа.")

def main():
    app = ApplicationBuilder().token(TOKEN).build()
    app.add_handler(CommandHandler("start", start))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_text))
    app.add_handler(MessageHandler(filters.VOICE | filters.PHOTO, handle_media))
    logger.info("Bot started...")
    app.run_polling()

if __name__ == "__main__":
    main()
