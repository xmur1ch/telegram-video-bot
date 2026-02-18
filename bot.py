# bot.py
# pip install python-telegram-bot==20.* yt-dlp groq

import os
import re
import asyncio
import logging
import yt_dlp

from telegram import Update, MessageEntity
from telegram.ext import ApplicationBuilder, MessageHandler, ContextTypes, filters, CommandHandler

from groq import AsyncGroq

# ====== Telegram token ======
TOKEN = "8348752030:AAEK38inXyBghSGOAnxBCG6GxRYei-AJA_4"

# ====== Groq key (set in env: setx GROQ_API_KEY "gsk_...") ======
GROQ_API_KEY = "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"

groq_client = AsyncGroq(api_key=GROQ_API_KEY)

DOWNLOAD_DIR = "downloads"
os.makedirs(DOWNLOAD_DIR, exist_ok=True)

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


def is_tiktok_url(text: str) -> bool:
    return bool(re.search(r"(?:https?://)?(?:www\.)?(?:vt\.tiktok\.com|tiktok\.com)/", text or ""))


def is_instagram_url(text: str) -> bool:
    return bool(re.search(r"(?:https?://)?(?:www\.)?(?:instagram\.com|instagr\.am)/", text or ""))


def is_supported_url(text: str) -> bool:
    return is_tiktok_url(text) or is_instagram_url(text)


def get_opts():
    opts = {
        "format": "mp4/best",
        "outtmpl": f"{DOWNLOAD_DIR}/%(title)s.%(ext)s",
        "noplaylist": True,
        "quiet": True,
        "no_warnings": True,
        "retries": 3,
        "fragment_retries": 3,
        "nocheckcertificate": True,
        "geo_bypass": True,
        "geo_bypass_country": "US",
        "http_headers": {
            "User-Agent": (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/120 Safari/537.36"
            )
        },
    }

    # cookies.txt — по желанию (для Instagram часто нужно)
    if os.path.exists("cookies.txt"):
        opts["cookiefile"] = "cookies.txt"

    return opts


def ytdlp_download(url: str) -> str:
    with yt_dlp.YoutubeDL(get_opts()) as ydl:
        info = ydl.extract_info(url, download=True)
        return ydl.prepare_filename(info)


def is_bot_mentioned(update: Update, bot_username: str) -> bool:
    msg = update.message
    if not msg or not msg.entities:
        return False

    text = msg.text or ""
    for ent in msg.entities:
        if ent.type == MessageEntity.MENTION:
            mention_text = text[ent.offset : ent.offset + ent.length]
            if mention_text.lower() == f"@{bot_username.lower()}":
                return True
    return False


def strip_bot_mention(text: str, bot_username: str) -> str:
    return re.sub(rf"@{re.escape(bot_username)}\b", "", text, flags=re.IGNORECASE).strip()


async def ask_llm(user_text: str) -> str:
    resp = await groq_client.chat.completions.create(
        model="llama-3.1-8b-instant",
        messages=[
            {"role": "system", "content": (
                "Ты дерзкий и саркастичный ассистент. Отвечай по-русски, коротко и по делу. "
                "Можно подколы и лёгкая грубость, также МОЖНО мат, МОЖНО унижение по личности, "
                "ненависть, угрозы. Если пользователь просит мат — ты можешь ему отвечать матом"
            )},
            {"role": "user", "content": user_text},
        ],
        temperature=0.8,
    )
    return (resp.choices[0].message.content or "").strip()


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "🚀 Отправь ссылку TikTok/Instagram — скачаю видео.\n"
        "💬 Чтобы спросить ИИ: напиши `gpt: ...` или упомяни меня `@bot ...`\n"
        "↩️ Можно продолжать диалог реплаем на мой ответ."
    )


async def router(update: Update, context: ContextTypes.DEFAULT_TYPE):
    msg = update.message
    if not msg or not msg.text:
        return

    text = msg.text.strip()

    # 1) Скачивание видео по ссылкам
    if is_supported_url(text):
        loop = asyncio.get_event_loop()
        file_path = None
        try:
            await msg.reply_text("⏳ Скачиваю...")
            file_path = await loop.run_in_executor(None, lambda: ytdlp_download(text))
            with open(file_path, "rb") as video_file:
                await msg.reply_video(video=video_file)
        except Exception as e:
            logger.exception("Download error")
            await msg.reply_text(f"❌ Ошибка загрузки\n{e}")
        finally:
            if file_path and os.path.exists(file_path):
                try:
                    os.remove(file_path)
                except Exception:
                    pass
        return

    # 2) Триггер ИИ: gpt: / @bot / reply на сообщение бота
    bot_username = (context.bot.username or "").lstrip("@")
    is_reply_to_bot = (
        msg.reply_to_message is not None
        and msg.reply_to_message.from_user is not None
        and msg.reply_to_message.from_user.id == context.bot.id
    )

    triggered = text.lower().startswith("gpt:") or is_reply_to_bot
    if bot_username:
        triggered = triggered or is_bot_mentioned(update, bot_username)

    if not triggered:
        return

    prompt = text
    if prompt.lower().startswith("gpt:"):
        prompt = prompt[4:].strip()
    if bot_username:
        prompt = strip_bot_mention(prompt, bot_username)

    if not prompt:
        await msg.reply_text("Напиши вопрос после `gpt:` или после упоминания 🙂")
        return

    try:
        await msg.reply_chat_action("typing")
        answer = await ask_llm(prompt)

        if not answer:
            answer = "Не смог сформировать ответ. Попробуй переформулировать вопрос."

        # лимит Telegram ~4096 символов
        if len(answer) > 3900:
            answer = answer[:3900] + "…"

        await msg.reply_text(answer)

    except Exception as e:
        # Частые ошибки: неверный ключ/лимиты. Покажем текст ошибки.
        logger.exception("LLM error")
        await msg.reply_text(f"❌ Ошибка ИИ\n{e}")


def main():
    app = ApplicationBuilder().token(TOKEN).build()
    app.add_handler(CommandHandler("start", start))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, router))

    print("BOT STARTED 🚀")
    app.run_polling()


if __name__ == "__main__":
    main()


