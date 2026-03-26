import os
import time
from dotenv import load_dotenv
from bot.address_switcher import select_saved_address

from bot.constants import FLIPKART_PKG
from bot.appium_driver import create_driver
from bot.flipkart_app_checker import open_product, detect_state, get_price, close_popups, save_screenshot, SessionDeadError
from bot.telegram_notifier import send_telegram
from bot.scheduler import sleep_random
from bot.telegram_bot import TelegramCommandBot
import bot.database as db
from utils.logger import logger
from utils.cleanup import cleanup_old_screenshots

HEARTBEAT_FILE = "heartbeat.txt"

load_dotenv()

REMINDER_SECONDS = int(os.getenv("REMINDER_SECONDS", "2700"))
ADDRESSES = [a.strip() for a in os.getenv("ADDRESSES", "Kishore,Varma Kamadi").split(",")]


def format_msg(kind, product_name, status, price, url, extra=""):
    return (
        f"{kind}\n\n"
        f"{product_name}\n"
        f"Status: {status}\n"
        f"Price: {price}\n"
        f"{extra}\n\n"
        f"{url}"
    )


def write_heartbeat():
    """Write current timestamp to heartbeat file for watchdog."""
    try:
        with open(HEARTBEAT_FILE, "w") as f:
            f.write(str(time.time()))
    except Exception:
        pass


def main():
    # Initialise database — creates tables, migrates products.json + state.json
    db.init_db()

    # Start interactive Telegram command bot in background thread
    TelegramCommandBot().start()

    print("🚀 Starting Flipkart App Stock Bot")

    while True:
        driver = None
        try:
            write_heartbeat()
            cleanup_old_screenshots()
            driver = create_driver()
            logger.info("🔄 Home screen")

            # Load products fresh from DB each cycle (picks up /add and /remove live)
            products = db.get_products()
            if not products:
                logger.warning("No products in watchlist. Use /add via Telegram to add one.")

            for addr in ADDRESSES:
                logger.info(f"📍 Switching address -> {addr}")
                select_saved_address(driver, addr)

                for p in products:
                    name       = p["name"]
                    url        = p["url"].split("?")[0]
                    target     = int(p.get("target_price", 10**9))
                    product_id = p["id"]

                    logger.info(f"🔎 Checking: {name} @ {addr}")

                    open_product(driver, url)
                    logger.info("🔄 Product opened")
                    close_popups(driver)
                    logger.info("🔄 Popups closed")

                    status = detect_state(driver)
                    price  = get_price(driver)
                    logger.info(f"🔄 Status: {status}, Price: {price}")
                    write_heartbeat()

                    # Persist scrape result + update state — both in DB
                    db.save_scrape_result(product_id, addr, status, price)
                    prev, cur = db.update_state(url, addr, status, price)

                    now        = time.time()
                    last_alert = cur.get("last_alert", 0)

                    # UNKNOWN / AMBIGUOUS
                    if status in ["UNKNOWN", "AMBIGUOUS"]:
                        path = save_screenshot(driver, f"{status.lower()}_{addr}")
                        send_telegram(
                            f"⚠ {status} UI detected\n"
                            f"{name}\n"
                            f"Address: {addr}\n"
                            f"Price: {price}\n"
                            f"Screenshot: {path}\n"
                            f"{url}"
                        )
                        continue

                    if status in ["OUT_OF_STOCK", "NOT_DELIVERABLE"]:
                        continue

                    if status == "IN_STOCK":

                        # PRICE DROP
                        if price is not None and price <= target:
                            send_telegram(
                                format_msg(
                                    "🔥 PRICE DROP ALERT",
                                    name, status, price, url,
                                    extra=f"Address: {addr}\nTarget was ₹{target}"
                                )
                            )
                            db.mark_alerted_db(url, addr)
                            logger.info("🔄 Price drop alert sent")
                            continue

                        # RESTOCK
                        if prev.get("last_status") != "IN_STOCK":
                            send_telegram(
                                format_msg(
                                    "🔥 RESTOCK ALERT",
                                    name, status, price, url,
                                    extra=f"Address: {addr}\nProduct is BUYABLE now"
                                )
                            )
                            db.mark_alerted_db(url, addr)
                            logger.info("🔄 Restock alert sent")
                            continue

                        # REMINDER
                        if now - last_alert > REMINDER_SECONDS:
                            send_telegram(
                                format_msg(
                                    "⏰ REMINDER",
                                    name, status, price, url,
                                    extra=f"Address: {addr}\nStill in stock"
                                )
                            )
                            db.mark_alerted_db(url, addr)
                            logger.info("🔄 Reminder alert sent")

        except SessionDeadError as e:
            logger.warning(f"💀 Dead session detected, restarting driver: {e}")

        except Exception as e:
            print("❌ Bot error:", e)

        finally:
            if driver:
                try:
                    driver.terminate_app(FLIPKART_PKG)
                    logger.info("🔄 Flipkart app closed")
                    driver.quit()
                except Exception:
                    pass

        # Random sleep between cycles
        sleep_random(4, 7)


if __name__ == "__main__":
    main()
