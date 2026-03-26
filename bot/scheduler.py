import random
import time


def sleep_random(min_minutes=8, max_minutes=15):
    seconds = random.randint(min_minutes * 60, max_minutes * 60)
    mins = seconds // 60
    secs = seconds % 60
    print(f"⏰ Sleeping {mins}m {secs}s...")
    time.sleep(seconds)
