import os
import requests
from dotenv import load_dotenv
from utils.logger import logger

load_dotenv()

BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
CHAT_ID = os.getenv("TELEGRAM_CHAT_ID")


def send_telegram(msg: str):
    if not BOT_TOKEN or not CHAT_ID:
        raise RuntimeError("Missing TELEGRAM_BOT_TOKEN or TELEGRAM_CHAT_ID in .env")

    url = f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage"
    payload = {
        "chat_id": CHAT_ID,
        "text": msg,
        "disable_web_page_preview": True
    }

    try:
        r = requests.post(url, json=payload, timeout=20)
        r.raise_for_status()
    except Exception:
        # Don't crash bot if telegram fails
        pass


def send_telegram_photo(photo_path: str, caption: str = ""):
    """
    Send a photo to Telegram with an optional caption.

    Falls back to a plain text message if the screenshot file is missing
    or the upload fails, so the bot never silently loses the alert.
    """
    if not BOT_TOKEN or not CHAT_ID:
        logger.warning("send_telegram_photo: missing BOT_TOKEN or CHAT_ID — skipping photo send")
        return

    # --- Attempt to send the actual image ---
    if photo_path and not photo_path.endswith("[screenshot_failed].png") and os.path.isfile(photo_path):
        url = f"https://api.telegram.org/bot{BOT_TOKEN}/sendPhoto"
        try:
            with open(photo_path, "rb") as f:
                r = requests.post(
                    url,
                    data={"chat_id": CHAT_ID, "caption": caption[:1024]},  # Telegram caption limit
                    files={"photo": f},
                    timeout=30,
                )
            if r.ok:
                logger.info(f"Screenshot sent to Telegram: {photo_path}")
                return
            else:
                logger.warning(f"sendPhoto failed ({r.status_code}): {r.text[:200]}")
        except Exception as e:
            logger.warning(f"send_telegram_photo upload error: {e}")

    # --- Fallback: plain text so the alert is never lost ---
    logger.warning("Falling back to text-only Telegram alert (no screenshot)")
    fallback_msg = caption if caption else "⚠️ Ambiguous state detected (screenshot unavailable)"
    send_telegram(fallback_msg)