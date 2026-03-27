"""
Interactive Telegram command bot.

Runs in a daemon background thread. Uses long-polling (getUpdates) — no
webhook or public IP required.

Supported commands (only accepted from TELEGRAM_CHAT_ID):
  /add   Name | URL | target_price   — add product to watchlist
  /remove name                        — remove by partial name; lists choices if ambiguous
  /list                               — show all watched products
  /status                             — last scrape result per product × address
"""

import os
import time
import threading
import requests
from dotenv import load_dotenv
from utils.logger import logger
import bot.database as db

load_dotenv()

BOT_TOKEN  = os.getenv("TELEGRAM_BOT_TOKEN", "")
CHAT_ID    = str(os.getenv("TELEGRAM_CHAT_ID", "")).strip()
API_BASE   = f"https://api.telegram.org/bot{BOT_TOKEN}"

_POLL_TIMEOUT = 30          # long-poll seconds per getUpdates call
_RETRY_DELAY  = 5           # seconds to wait after a network error


# ---------------------------------------------------------------------------
# Low-level API helpers
# ---------------------------------------------------------------------------

def _send(text: str, chat_id: str = CHAT_ID):
    """Send a plain-text message (silently swallows errors)."""
    try:
        requests.post(
            f"{API_BASE}/sendMessage",
            json={"chat_id": chat_id, "text": text,
                  "disable_web_page_preview": True},
            timeout=20,
        )
    except Exception as e:
        logger.warning(f"Telegram send error: {e}")


def _get_updates(offset: int) -> list[dict]:
    """Long-poll for new updates. Returns list of update dicts."""
    try:
        r = requests.get(
            f"{API_BASE}/getUpdates",
            params={"offset": offset, "timeout": _POLL_TIMEOUT,
                    "allowed_updates": ["message"]},
            timeout=_POLL_TIMEOUT + 10,
        )
        r.raise_for_status()
        return r.json().get("result", [])
    except Exception as e:
        logger.warning(f"getUpdates error: {e}")
        return []


# ---------------------------------------------------------------------------
# Command handlers
# ---------------------------------------------------------------------------

def _cmd_add(args: str) -> str:
    parts = [p.strip() for p in args.split("|")]
    if len(parts) < 2:
        return (
            "Usage: /add Name | URL | target_price\n"
            "Example: /add Instax Mini 10 | https://www.flipkart.com/... | 700"
        )
    name  = parts[0]
    url   = parts[1]
    price = int(parts[2]) if len(parts) >= 3 and parts[2].isdigit() else 999999999

    if not url.startswith("http"):
        return "❌ Invalid URL. Must start with http/https."

    is_new = db.add_product(name, url, price)
    if is_new:
        return f"✅ Added: {name}\nTarget: ₹{price}"
    else:
        return f"🔄 Updated: {name}\nTarget: ₹{price}"


def _cmd_remove(args: str) -> str:
    parts = args.strip().split(None, 1)

    # --- Case 1: /remove #<id>  (user picked from a disambiguation list) ---
    if len(parts) == 1 and parts[0].lstrip("#").isdigit():
        product_id = int(parts[0].lstrip("#"))
        matched = db.remove_product_by_id(product_id)
        if matched:
            return f"✅ Removed: {matched}"
        return f"❌ No enabled product with ID {product_id}."

    # --- Case 2: /remove <name query> ---
    if not args.strip():
        return "Usage: /remove <partial name>  or  /remove #<id>\nExample: /remove Instax"

    query = args.strip()
    matches = db.find_products_by_name(query)

    if not matches:
        return f"❌ No enabled product matching \"{query}\" found."

    if len(matches) == 1:
        db.remove_product_by_id(matches[0]["id"])
        return f"✅ Removed: {matches[0]['name']}"

    # Multiple matches — ask user to confirm with ID
    lines = [f"⚠️ {len(matches)} products match \"{query}\". Reply with /remove #<id>:\n"]
    for m in matches:
        lines.append(f"  /remove #{m['id']} — {m['name']}")
    return "\n".join(lines)


def _cmd_list() -> str:
    products = db.get_products()
    if not products:
        return "📭 No products in watchlist.\nUse /add to add one."
    lines = ["📋 *Watchlist*\n"]
    for i, p in enumerate(products, 1):
        price_str = f"₹{p['target_price']}" if p['target_price'] < 999999999 else "any"
        lines.append(f"{i}. {p['name']}\n   Target: {price_str}")
    return "\n".join(lines)


def _cmd_status() -> str:
    results = db.get_last_run_status()
    if not results:
        return "⏳ No scrape results yet. Wait for the first cycle to complete."

    STATUS_EMOJI = {
        "IN_STOCK":       "✅",
        "OUT_OF_STOCK":   "❌",
        "NOT_DELIVERABLE":"🚫",
        "AMBIGUOUS":      "⚠️",
    }

    lines = ["📊 *Last Scrape Status*\n"]
    current_name = None
    for r in results:
        if r["name"] != current_name:
            current_name = r["name"]
            lines.append(f"\n🏷 {r['name']}")

        emoji  = STATUS_EMOJI.get(r["status"], "❓")
        price  = f"₹{r['price']}" if r["price"] else "—"
        ts     = time.strftime("%d %b %H:%M", time.localtime(r["checked_at"]))
        lines.append(f"  {emoji} {r['address']}: {r['status']} | {price} | {ts}")

    return "\n".join(lines)


def _cmd_help() -> str:
    return (
        "🤖 *Flipkart Stock Bot Commands*\n\n"
        "/list              — Show watched products\n"
        "/add Name | URL | price  — Add a product\n"
        "/remove Name       — Remove a product (use #id if ambiguous)\n"
        "/status            — Last scrape results"
    )


# ---------------------------------------------------------------------------
# Main poll loop
# ---------------------------------------------------------------------------

def _handle(update: dict):
    """Process a single Telegram update."""
    msg = update.get("message") or update.get("edited_message")
    if not msg:
        return

    # Security — only accept from authorised chat
    from_id = str(msg.get("chat", {}).get("id", ""))
    if from_id != CHAT_ID:
        logger.warning(f"Ignoring message from unauthorised chat_id={from_id}")
        return

    text = (msg.get("text") or "").strip()
    if not text.startswith("/"):
        return

    # Split /command args
    parts   = text.split(None, 1)
    command = parts[0].split("@")[0].lower()   # strip @BotName suffix
    args    = parts[1].strip() if len(parts) > 1 else ""

    logger.info(f"Telegram command: {command!r} args={args!r}")

    if command == "/add":
        reply = _cmd_add(args)
    elif command == "/remove":
        reply = _cmd_remove(args)
    elif command == "/list":
        reply = _cmd_list()
    elif command == "/status":
        reply = _cmd_status()
    elif command in ("/help", "/start"):
        reply = _cmd_help()
    else:
        reply = f"Unknown command: {command}\nType /help for available commands."

    _send(reply)


def _poll_loop():
    """Blocking poll loop — run in a daemon thread."""
    logger.info("🤖 Telegram command bot started (long-polling)")
    offset = 0
    while True:
        updates = _get_updates(offset)
        for upd in updates:
            offset = upd["update_id"] + 1
            try:
                _handle(upd)
            except Exception as e:
                logger.error(f"Error handling update {upd.get('update_id')}: {e}")
        if not updates:
            time.sleep(0.1)   # brief pause only on empty polls


class TelegramCommandBot:
    """Start/stop the long-polling bot in a daemon thread."""

    def __init__(self):
        self._thread = threading.Thread(
            target=_poll_loop, name="telegram-cmd-bot", daemon=True
        )

    def start(self):
        if not BOT_TOKEN or not CHAT_ID:
            logger.warning("Telegram command bot disabled: BOT_TOKEN or CHAT_ID missing")
            return
        self._thread.start()
        logger.info("🤖 Telegram command bot thread started")