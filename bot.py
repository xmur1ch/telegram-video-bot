# bot.py
# pip install python-telegram-bot==20.* yt-dlp groq

import os
import re
import asyncio
import logging
import yt_dlp

from datetime import datetime, timedelta, time
from zoneinfo import ZoneInfo

from telegram import Update, MessageEntity
from telegram.ext import (
    ApplicationBuilder,
    MessageHandler,
    ContextTypes,
    filters,
    CommandHandler,
)

from groq import AsyncGroq

# =======================
# !!! НЕ ХРАНИ СЕКРЕТЫ В КОДЕ !!!
# setx BOT_TOKEN "123:AA..."
# setx GROQ_API_KEY "gsk_..."
# =======================
TOKEN = "8348752030:AAEK38inXyBghSGOAnxBCG6GxRYei-AJA_4"
GROQ_API_KEY = "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"


groq_client = AsyncGroq(api_key=GROQ_API_KEY) if GROQ_API_KEY else None

DOWNLOAD_DIR = "downloads"
os.makedirs(DOWNLOAD_DIR, exist_ok=True)

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# ====== DMB TIMER SETTINGS ======
DMB_CHAT_ID = -1002016790881  # <-- поставь ID нужного чата (группа/канал/чат)
DMB_TZ = ZoneInfo("Europe/Moscow")  # можешь заменить на "Europe/Moscow" если надо

# Две записи: имя + дата/время дембеля (локальное время в DMB_TZ)
from datetime import datetime

DMB_PEOPLE = [
    {
        "name": "ратм",
        "start": datetime(2025, 10, 31, 0, 0),  # дата начала службы
        "end":   datetime(2026, 10, 31, 0, 0),  # дата дембеля
    },
    {
        "name": "марик",
        "start": datetime(2025, 10, 18, 0, 0),
        "end":   datetime(2026, 10, 18, 0, 0),
    },
]



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


async def ask_llm(user_text: str) -> str:
    if not groq_client:
        return "ИИ отключён (не задан GROQ_API_KEY)."

    # ВАЖНО: я убрал из промпта разрешение на ненависть/угрозы/унижения.
    # Иначе рано или поздно словишь бан/репорт в чатах.
    resp = await groq_client.chat.completions.create(
        model="llama-3.1-8b-instant",
        messages=[
            {"role": "system", "content": "Ты дерзкий ассистент. По-русски, коротко и по делу. Без угроз и травли."},
            {"role": "user", "content": user_text},
        ],
        temperature=0.8,
    )
    return (resp.choices[0].message.content or "").strip()


# ---------- DMB helpers ----------
from datetime import datetime, timedelta

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
    # 0..100 -> бар из блоков
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
    now = datetime.now()  # без ZoneInfo, чтобы не падало на Windows
    lines = ["🪖 *ДМБ таймер*"]

    for p in DMB_PEOPLE:
        name = p["name"]
        start = p["start"]
        end = p["end"]

        pct, served_td, left_td = _service_pct(start, end, now)
        bar = _progress_bar(pct)
        left_str = _fmt_left(left_td)

        # Для красоты: сколько дней всего/осталось
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
        "🪖 ДМБ: /dmb (только в нужном чате)"
    )


async def chatid(update: Update, context: ContextTypes.DEFAULT_TYPE):
    # чтобы ты легко узнал chat_id
    await update.message.reply_text(f"chat_id: {update.message.chat.id}")


async def dmb(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_allowed_chat(update):
        return  # молчим, если не тот чат
    await update.message.reply_text(build_dmb_text(), parse_mode="Markdown")


async def router(update: Update, context: ContextTypes.DEFAULT_TYPE):
    msg = update.message
    if not msg or not msg.text:
        return

    text = msg.text.strip()

    # 1) Скачивание видео по ссылкам + ⏳, который удаляется
    # 1) Скачивание видео по ссылкам
    if is_supported_url(text):
        loop = asyncio.get_running_loop()
        file_path = None
        status_msg = None

        try:
            # отправляем только песочные часы
            status_msg = await msg.reply_text("⏳")

            # качаем в отдельном потоке
            file_path = await loop.run_in_executor(None, lambda: ytdlp_download(text))

            # отправляем видео
            with open(file_path, "rb") as video_file:
                await msg.reply_video(video=video_file)

        except Exception as e:
            logger.exception("Download error")
            await msg.reply_text(f"❌ Ошибка загрузки\n{e}")

        finally:
            # удаляем сообщение с ⏳ (best-effort)
            if status_msg:
                try:
                    await status_msg.delete()
                except Exception:
                    pass

            # удаляем файл
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
        if len(answer) > 3900:
            answer = answer[:3900] + "…"
        await msg.reply_text(answer)
    except Exception as e:
        logger.exception("LLM error")
        await msg.reply_text(f"❌ Ошибка ИИ\n{e}")

async def weekly_dmb_job(context: ContextTypes.DEFAULT_TYPE):
    await context.bot.send_message(
        chat_id=DMB_CHAT_ID,
        text=build_dmb_text(),
        parse_mode="Markdown",
    )

def main():
    app = ApplicationBuilder().token(TOKEN).build()

    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("chatid", chatid))
    app.add_handler(CommandHandler("dmb", dmb))

    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, router))
        # ===== Weekly DMB: every Friday 21:00 =====
    now = datetime.now()
    target_weekday = 4      # Friday=4 (Mon=0 ... Sun=6)
    target_time = time(21, 0)  # 21:00

    days_ahead = (target_weekday - now.weekday()) % 7
    first_run = datetime.combine((now + timedelta(days=days_ahead)).date(), target_time)

    # если сегодня уже позже 21:00 — переносим на следующую неделю
    if first_run <= now:
        first_run += timedelta(days=7)

    app.job_queue.run_repeating(
        weekly_dmb_job,
        interval=7 * 24 * 60 * 60,  # раз в 7 дней
        first=first_run,
        name="weekly_dmb",
    )

    print("BOT STARTED 🚀")
    app.run_polling()


if __name__ == "__main__":
    main()

