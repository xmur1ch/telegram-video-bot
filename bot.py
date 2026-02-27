# bot.py
# pip install "python-telegram-bot[job-queue]==21.7" yt-dlp tzdata openai
# ENV (лучше так):
#   setx BOT_TOKEN "123:AA..."
#   setx OPENROUTER_API_KEY "sk-or-..."
#   setx OPENROUTER_MODEL "x-ai/grok-4-fast"

import os
import re
import asyncio
import logging
import base64
import yt_dlp

from datetime import datetime, timedelta, time
from zoneinfo import ZoneInfo

from telegram import Update, MessageEntity
from telegram.constants import ChatType
from telegram.ext import (
    ApplicationBuilder,
    MessageHandler,
    ContextTypes,
    filters,
    CommandHandler,
)

from openai import AsyncOpenAI

# =======================
# TOKENS (лучше через ENV)
# =======================
TOKEN = "8348752030:AAEK38inXyBghSGOAnxBCG6GxRYei-AJA_4"  # <-- поставь реальный токен или ENV
OPENROUTER_API_KEY = "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"  # <-- ключ OpenRouter
OPENROUTER_MODEL = os.getenv("OPENROUTER_MODEL", "x-ai/grok-4-fast")

# OpenRouter client (OpenAI-compatible)
openrouter_client = (
    AsyncOpenAI(
        base_url="https://openrouter.ai/api/v1",
        api_key=OPENROUTER_API_KEY,
        default_headers={
            "HTTP-Referer": "https://localhost",
            "X-Title": "telegram-bot",
        },
    )
    if OPENROUTER_API_KEY
    else None
)

DOWNLOAD_DIR = "downloads"
os.makedirs(DOWNLOAD_DIR, exist_ok=True)

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# ====== DMB TIMER SETTINGS ======
DMB_CHAT_ID = -1002016790881
DMB_TZ = ZoneInfo("Europe/Moscow")

DMB_PEOPLE = [
    {
        "name": "ратм",
        "start": datetime(2025, 10, 31, 0, 0, tzinfo=DMB_TZ),
        "end":   datetime(2026, 10, 31, 0, 0, tzinfo=DMB_TZ),
    },
    {
        "name": "марик",
        "start": datetime(2025, 10, 18, 0, 0, tzinfo=DMB_TZ),
        "end":   datetime(2026, 10, 18, 0, 0, tzinfo=DMB_TZ),
    },
]


# ---------- URL helpers ----------
def is_tiktok_url(text: str) -> bool:
    return bool(re.search(r"(?:https?://)?(?:www\.)?(?:vt\.tiktok\.com|tiktok\.com)/", text or "", re.I))


def is_instagram_url(text: str) -> bool:
    return bool(re.search(r"(?:https?://)?(?:www\.)?(?:instagram\.com|instagr\.am)/", text or "", re.I))


def is_supported_url(text: str) -> bool:
    return is_tiktok_url(text) or is_instagram_url(text)


def get_opts():
    opts = {
        "format": "mp4/best",
        "outtmpl": f"{DOWNLOAD_DIR}/%(extractor)s_%(id)s.%(ext)s",
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
    if os.path.exists("cookies.txt"):
        opts["cookiefile"] = "cookies.txt"
    return opts


def ytdlp_download(url: str) -> str:
    with yt_dlp.YoutubeDL(get_opts()) as ydl:
        info = ydl.extract_info(url, download=True)
        return ydl.prepare_filename(info)


# ---------- mention helpers ----------
def is_bot_mentioned(update: Update, bot_username: str) -> bool:
    msg = update.message
    if not msg or not msg.entities:
        return False

    text = msg.text or ""
    for ent in msg.entities:
        if ent.type == MessageEntity.MENTION:
            mention_text = text[ent.offset: ent.offset + ent.length]
            if mention_text.lower() == f"@{bot_username.lower()}":
                return True
    return False


def strip_bot_mention(text: str, bot_username: str) -> str:
    return re.sub(rf"@{re.escape(bot_username)}\b", "", text, flags=re.IGNORECASE).strip()


# ---------- misc helpers ----------
def _sender_pretty(msg) -> str:
    u = msg.from_user
    if not u:
        return "неизвестный пользователь"
    if u.username:
        return f"@{u.username}"
    return u.full_name or "пользователь без имени"


async def _delete_message_safe(context: ContextTypes.DEFAULT_TYPE, chat_id: int, message_id: int):
    try:
        await context.bot.delete_message(chat_id=chat_id, message_id=message_id)
    except Exception:
        pass


# ---------- OpenRouter LLM ----------
async def ask_llm_openrouter(user_text: str, image_bytes: bytes | None = None) -> str:
    if not openrouter_client:
        return "ИИ отключён (не задан OPENROUTER_API_KEY)."

    content = [{"type": "text", "text": user_text}]

    if image_bytes:
        b64 = base64.b64encode(image_bytes).decode("utf-8")
        # Telegram фото почти всегда JPEG, но пусть будет универсально
        data_url = f"data:image/jpeg;base64,{b64}"
        content.append({"type": "image_url", "image_url": {"url": data_url}})

    resp = await openrouter_client.chat.completions.create(
        model=OPENROUTER_MODEL,
        messages=[
            {"role": "system", "content": "Ты дерзкий русский ассистент. По-русски, коротко и по делу. Много материшься. И так-же добавляешь много смайликов в своей речи"},
            {"role": "user", "content": content},
        ],
        temperature=0.8,
    )

    return (resp.choices[0].message.content or "").strip()


# ---------- DMB helpers ----------
def _fmt_left(td: timedelta) -> str:
    total = int(td.total_seconds())
    if total <= 0:
        return "УЖЕ ДМБ ✅"
    days = total // 86400
    total %= 86400
    hours = total // 3600
    total %= 3600
    mins = total // 60
    return f"{days}д {hours}ч {mins}м"


def _clamp(x: float, a: float, b: float) -> float:
    return max(a, min(b, x))


def _progress_bar(pct: float, length: int = 14) -> str:
    pct = _clamp(pct, 0.0, 100.0)
    filled = int(round((pct / 100) * length))
    return "█" * filled + "░" * (length - filled)


def _service_pct(start: datetime, end: datetime, now: datetime) -> tuple[float, timedelta, timedelta]:
    total = end - start
    served = now - start
    left = end - now

    if total.total_seconds() <= 0:
        return 100.0, timedelta(0), timedelta(0)

    served_sec = _clamp(served.total_seconds(), 0, total.total_seconds())
    pct = (served_sec / total.total_seconds()) * 100.0

    served_td = timedelta(seconds=int(served_sec))
    left_td = timedelta(seconds=max(0, int(left.total_seconds())))
    return pct, served_td, left_td


def build_dmb_text() -> str:
    try:
        now = datetime.now(DMB_TZ)
    except Exception:
        now = datetime.now()

    lines = ["🪖 *ДМБ таймер*"]

    for p in DMB_PEOPLE:
        name = p["name"]
        start = p["start"]
        end = p["end"]

        pct, _, left_td = _service_pct(start, end, now)
        bar = _progress_bar(pct)
        left_str = _fmt_left(left_td)

        total_days = max(0, (end.date() - start.date()).days)
        left_days = max(0, (end.date() - now.date()).days)

        if left_td.total_seconds() <= 0:
            lines.append(
                f"\n👤 *{name}*\n"
                f"✅ *ДМБ!* (до {end.date()})\n"
                f"📊 {bar} *100%*"
            )
        else:
            lines.append(
                f"\n👤 *{name}*\n"
                f"⏳ Осталось: *{left_str}*  _(≈ {left_days} дн.)_\n"
                f"📅 Дембель: *{end.date()}*\n"
                f"📈 Отслужил: *{pct:.1f}%*  ({bar})\n"
                f"🧾 Всего: *{total_days}* дн."
            )

    return "\n".join(lines)


def is_allowed_chat(update: Update) -> bool:
    msg = update.message
    return bool(msg and msg.chat and msg.chat.id == DMB_CHAT_ID)


# ---------- Commands ----------
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "🚀 Отправь ссылку TikTok/Instagram — скачаю видео.\n"
        "💬 ИИ: `gpt: ...` или упоминание/реплай.\n"
        "🖼️ Можно с фото (vision): подпись `gpt: ...` + фото.\n"
        "🪖 ДМБ: /dmb (только в нужном чате)"
    )


async def chatid(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(f"chat_id: {update.message.chat.id}")


async def dmb(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_allowed_chat(update):
        return
    await update.message.reply_text(build_dmb_text(), parse_mode="Markdown")

# ---------- Main router ----------
async def router(update: Update, context: ContextTypes.DEFAULT_TYPE):
    msg = update.message
    if not msg:
        return

    # текст может быть в msg.text или в подписи к фото msg.caption
    text = (msg.text or msg.caption or "").strip()

    # 1) Скачивание видео по ссылкам + ⏳, который удаляется
    if text and is_supported_url(text):
        loop = asyncio.get_running_loop()
        file_path = None
        status_msg = None

        link_chat_id = msg.chat_id
        link_message_id = msg.message_id
        sender = _sender_pretty(msg)

        try:
            status_msg = await context.bot.send_message(chat_id=link_chat_id, text="⏳")
            file_path = await loop.run_in_executor(None, lambda: ytdlp_download(text))

            caption = f"✅ *Видео готово*\n👤 От: *{sender}*\n"

            with open(file_path, "rb") as video_file:
                await context.bot.send_video(
                    chat_id=link_chat_id,
                    video=video_file,
                    caption=caption,
                    parse_mode="Markdown",
                )

            if msg.chat.type in (ChatType.GROUP, ChatType.SUPERGROUP):
                await _delete_message_safe(context, link_chat_id, link_message_id)

        except Exception as e:
            logger.exception("Download error")
            await context.bot.send_message(
                chat_id=link_chat_id,
                text=f"❌ *Ошибка загрузки*\n👤 От: *{sender}*\n`{e}`",
                parse_mode="Markdown",
            )

        finally:
            if status_msg:
                try:
                    await status_msg.delete()
                except Exception:
                    pass

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

    triggered = False

    # gpt: работает и для текста, и для подписи к фото
    if text.lower().startswith("gpt:"):
        triggered = True

    if not triggered and bot_username and msg.text:
        triggered = is_bot_mentioned(update, bot_username)

    if not triggered and is_reply_to_bot:
        triggered = True

    if not triggered:
        return

    prompt = text
    if prompt.lower().startswith("gpt:"):
        prompt = prompt[4:].strip()
    if bot_username and prompt:
        prompt = strip_bot_mention(prompt, bot_username)

    if not prompt:
        await msg.reply_text("Напиши вопрос после `gpt:` или после упоминания 🙂")
        return

    # если есть фото — заберём bytes
    image_bytes = None
    if msg.photo:
        try:
            tg_file = await msg.photo[-1].get_file()
            image_bytes = bytes(await tg_file.download_as_bytearray())
        except Exception:
            image_bytes = None

    try:
        await msg.reply_chat_action("typing")
        answer = await ask_llm_openrouter(prompt, image_bytes=image_bytes)

        if not answer:
            answer = "Не смог сформировать ответ. Попробуй переформулировать вопрос."
        if len(answer) > 3900:
            answer = answer[:3900] + "…"

        await msg.reply_text(answer)

    except Exception as e:
        logger.exception("LLM error")
        await msg.reply_text(f"❌ Ошибка ИИ\n{e}")


def main():
    app = ApplicationBuilder().token(TOKEN).build()

    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("chatid", chatid))
    app.add_handler(CommandHandler("dmb", dmb))

    # Ловим: текст, фото (и подписи к фото тоже попадут в update.message)
    app.add_handler(MessageHandler((filters.TEXT | filters.PHOTO) & ~filters.COMMAND, router))

    print("BOT STARTED 🚀")
    app.run_polling()


if __name__ == "__main__":
    main()




