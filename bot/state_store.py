import json
import os
import time
import tempfile

STATE_FILE = "state.json"


def load_state():
    if not os.path.exists(STATE_FILE):
        return {}
    try:
        with open(STATE_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {}


def save_state(state):
    """Atomic write: write to temp file first, then rename over the target."""
    tmp_fd, tmp_path = tempfile.mkstemp(dir=".", suffix=".tmp")
    try:
        with os.fdopen(tmp_fd, "w", encoding="utf-8") as f:
            json.dump(state, f, indent=2)
        os.replace(tmp_path, STATE_FILE)
    except Exception:
        if os.path.exists(tmp_path):
            os.unlink(tmp_path)
        raise


def update_product_state(state: dict, url: str, status: str, price: int | None):
    """Update product state in-memory. Caller is responsible for load/save."""
    if url not in state:
        state[url] = {
            "last_status": None,
            "last_price": None,
            "last_alert": 0
        }

    prev = state[url].copy()

    state[url]["last_status"] = status
    state[url]["last_price"] = price

    return prev, state[url]


def mark_alerted(state: dict, url: str):
    """Mark a product as alerted in-memory. Caller is responsible for load/save."""
    if url not in state:
        state[url] = {}
    state[url]["last_alert"] = int(time.time())
