import os
import re
import asyncio
import logging
import yt_dlp

from telegram import Update
from telegram.ext import ApplicationBuilder, MessageHandler, ContextTypes, filters, CommandHandler

# ====== ВСТАВЬ СЮДА СВОЙ ТОКЕН ======
TOKEN = "8348752030:AAEK38inXyBghSGOAnxBCG6GxRYei-AJA_4"

DOWNLOAD_DIR = "downloads"
os.makedirs(DOWNLOAD_DIR, exist_ok=True)

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


def is_tiktok_url(text: str) -> bool:
    return bool(re.search(r"(?:https?://)?(?:www\.)?(?:vt\.tiktok\.com|tiktok\.com)/", text or ""))


def is_instagram_url(text: str) -> bool:
    return bool(re.search(r"(?:https?://)?(?:www\.)?(?:www\.)?(?:instagram\.com|instagr\.am)/", text or ""))


def is_supported_url(text: str) -> bool:
    return is_tiktok_url(text) or is_instagram_url(text)


def get_opts():
    opts = {
        # ВАЖНО: один файл mp4, без ffmpeg
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
        }
    }

    # cookies.txt — по желанию (для Instagram часто нужно)
    if os.path.exists("cookies.txt"):
        opts["cookiefile"] = "cookies.txt"

    return opts


def ytdlp_download(url: str) -> str:
    with yt_dlp.YoutubeDL(get_opts()) as ydl:
        info = ydl.extract_info(url, download=True)
        return ydl.prepare_filename(info)


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("🚀 Отправь ссылку TikTok или Instagram (скачаю видео без ffmpeg)")


async def download(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = (update.message.text or "").strip()

    # реагируем только на TikTok или Instagram ссылки
    if not is_supported_url(text):
        return

    loop = asyncio.get_event_loop()
    file_path = None

    try:
        await update.message.reply_text("⏳ Скачиваю...")

        file_path = await loop.run_in_executor(None, lambda: ytdlp_download(text))

        with open(file_path, "rb") as video_file:
            await update.message.reply_video(video=video_file)

    except Exception as e:
        logger.exception("Download error")
        await update.message.reply_text(f"❌ Ошибка загрузки\n{e}")

    finally:
        if file_path and os.path.exists(file_path):
            try:
                os.remove(file_path)
            except Exception:
                pass


def main():
    app = ApplicationBuilder().token(TOKEN).build()

    app.add_handler(CommandHandler("start", start))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, download))

    print("BOT STARTED 🚀")
    app.run_polling()


if __name__ == "__main__":
    main()
