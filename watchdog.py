import subprocess
import os
import signal
import time
import sys

from bot.telegram_notifier import send_telegram
from utils.logger import logger

CHECK_INTERVAL = 30        # seconds between checks
RESTART_DELAY = 5          # seconds before restarting
HANG_TIMEOUT = 20 * 60    # 20 minutes — if no heartbeat for this long, assume hang
HEARTBEAT_FILE = "heartbeat.txt"


def start_bot():
    return subprocess.Popen([sys.executable, "main.py"])


def read_heartbeat():
    """Read the last heartbeat timestamp from file. Returns 0 if missing."""
    try:
        with open(HEARTBEAT_FILE, "r") as f:
            return float(f.read().strip())
    except Exception:
        return 0.0


def kill_bot(bot):
    """Force-kill the bot process."""
    try:
        bot.kill()
        bot.wait(timeout=10)
    except Exception:
        pass


def restart(bot, reason):
    """Kill, notify, and restart the bot."""
    msg = f"⚠ Watchdog: {reason}, restarting now..."
    logger.warning(msg)
    try:
        send_telegram(msg)
    except Exception:
        pass

    kill_bot(bot)
    time.sleep(RESTART_DELAY)
    return start_bot()


def main():
    logger.info("🛡 Watchdog started")
    bot = start_bot()

    while True:
        time.sleep(CHECK_INTERVAL)

        # 1) Crash detection — process exited
        if bot.poll() is not None:
            bot = restart(bot, "bot crashed (exit code: {})".format(bot.returncode))
            continue

        # 2) Hang detection — process alive but heartbeat stale
        last_beat = read_heartbeat()
        if last_beat > 0:
            stale = time.time() - last_beat
            if stale > HANG_TIMEOUT:
                bot = restart(bot, f"bot appears hung (no heartbeat for {int(stale // 60)}m)")
                continue


if __name__ == "__main__":
    main()
