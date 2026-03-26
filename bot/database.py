"""
SQLite database layer for the Flipkart App Stock Bot.

Tables
------
products      — watched products (name, url, target_price, enabled)
scrape_results — append-only log, one row per product × address check
product_state  — latest status + alert timestamp per product × address (replaces state.json)
"""

import sqlite3
import json
import os
import time
from contextlib import contextmanager
from utils.logger import logger

DB_DIR  = "data"
DB_FILE = os.path.join(DB_DIR, "bot.db")
PRODUCTS_JSON = os.path.join("config", "products.json")


@contextmanager
def _conn():
    """Thread-safe connection with WAL mode for concurrent reads."""
    os.makedirs(DB_DIR, exist_ok=True)
    con = sqlite3.connect(DB_FILE, check_same_thread=False)
    con.row_factory = sqlite3.Row
    con.execute("PRAGMA journal_mode=WAL")
    con.execute("PRAGMA foreign_keys=ON")
    try:
        yield con
        con.commit()
    except Exception:
        con.rollback()
        raise
    finally:
        con.close()


# ---------------------------------------------------------------------------
# Schema
# ---------------------------------------------------------------------------

def init_db():
    """Create tables and migrate products.json / state.json on first run."""
    with _conn() as con:
        con.executescript("""
            CREATE TABLE IF NOT EXISTS products (
                id           INTEGER PRIMARY KEY AUTOINCREMENT,
                name         TEXT    NOT NULL,
                url          TEXT    NOT NULL UNIQUE,
                target_price INTEGER NOT NULL DEFAULT 999999999,
                enabled      INTEGER NOT NULL DEFAULT 1,
                added_at     INTEGER NOT NULL
            );

            CREATE TABLE IF NOT EXISTS scrape_results (
                id         INTEGER PRIMARY KEY AUTOINCREMENT,
                product_id INTEGER NOT NULL REFERENCES products(id),
                address    TEXT    NOT NULL,
                status     TEXT    NOT NULL,
                price      INTEGER,
                checked_at INTEGER NOT NULL
            );

            CREATE INDEX IF NOT EXISTS idx_results_product
                ON scrape_results(product_id, checked_at DESC);

            -- Upsert-style state table: one row per product × address
            CREATE TABLE IF NOT EXISTS product_state (
                id          INTEGER PRIMARY KEY AUTOINCREMENT,
                url         TEXT    NOT NULL,
                address     TEXT    NOT NULL,
                last_status TEXT,
                last_price  INTEGER,
                last_alert  INTEGER NOT NULL DEFAULT 0,
                UNIQUE(url, address)
            );
        """)

    _migrate_json()
    _migrate_state_json()


def _migrate_json():
    """Import products.json into the DB if they don't already exist."""
    if not os.path.exists(PRODUCTS_JSON):
        return
    try:
        with open(PRODUCTS_JSON, "r", encoding="utf-8") as f:
            items = json.load(f)
    except Exception as e:
        logger.warning(f"Could not read products.json for migration: {e}")
        return

    imported = 0
    for item in items:
        try:
            add_product(
                name=item["name"],
                url=item["url"],
                target_price=int(item.get("target_price", 999999999)),
            )
            imported += 1
        except Exception:
            pass  # already in DB — skip silently

    if imported:
        logger.info(f"🗄 Migrated {imported} product(s) from products.json → DB")


# ---------------------------------------------------------------------------
# Products CRUD
# ---------------------------------------------------------------------------

def get_products() -> list[dict]:
    """Return all enabled products as plain dicts."""
    with _conn() as con:
        rows = con.execute(
            "SELECT id, name, url, target_price FROM products WHERE enabled=1 ORDER BY id"
        ).fetchall()
    return [dict(r) for r in rows]


def add_product(name: str, url: str, target_price: int = 999999999) -> bool:
    """
    Insert a new product or re-enable + update an existing one (matched by URL).
    Returns True if newly added, False if updated in place.
    """
    url = url.split("?")[0].rstrip("/")   # normalise — drop query params
    with _conn() as con:
        existing = con.execute(
            "SELECT id FROM products WHERE url=?", (url,)
        ).fetchone()

        if existing:
            con.execute(
                "UPDATE products SET name=?, target_price=?, enabled=1 WHERE url=?",
                (name, target_price, url)
            )
            return False
        else:
            con.execute(
                "INSERT INTO products (name, url, target_price, enabled, added_at) VALUES (?,?,?,1,?)",
                (name, url, target_price, int(time.time()))
            )
            return True


def remove_product(name_query: str) -> str | None:
    """
    Soft-delete the first product whose name matches (case-insensitive substring).
    Returns the matched product name, or None if not found.
    """
    with _conn() as con:
        row = con.execute(
            "SELECT id, name FROM products WHERE enabled=1 AND LOWER(name) LIKE LOWER(?)",
            (f"%{name_query}%",)
        ).fetchone()
        if not row:
            return None
        con.execute("UPDATE products SET enabled=0 WHERE id=?", (row["id"],))
        return row["name"]


# ---------------------------------------------------------------------------
# Scrape Results
# ---------------------------------------------------------------------------

def save_scrape_result(product_id: int, address: str, status: str, price: int | None):
    """Persist a single product-check result."""
    with _conn() as con:
        con.execute(
            "INSERT INTO scrape_results (product_id, address, status, price, checked_at) VALUES (?,?,?,?,?)",
            (product_id, address, status, price, int(time.time()))
        )


def get_last_run_status() -> list[dict]:
    """
    Return the most recent scrape result per product × address combination.
    """
    with _conn() as con:
        rows = con.execute("""
            SELECT
                p.name,
                r.address,
                r.status,
                r.price,
                r.checked_at
            FROM scrape_results r
            JOIN products p ON p.id = r.product_id
            WHERE r.id IN (
                SELECT MAX(id)
                FROM scrape_results
                GROUP BY product_id, address
            )
            ORDER BY p.name, r.address
        """).fetchall()
    return [dict(r) for r in rows]


def _migrate_state_json():
    """
    One-time import of state.json into product_state.
    state.json keys are 'url::address' with last_status, last_price, last_alert.
    """
    STATE_FILE = "state.json"
    if not os.path.exists(STATE_FILE):
        return
    try:
        with open(STATE_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
    except Exception as e:
        logger.warning(f"Could not read state.json for migration: {e}")
        return

    imported = 0
    for key, val in data.items():
        if "::" not in key:
            continue
        url, address = key.split("::", 1)
        try:
            _upsert_state(
                url=url,
                address=address,
                last_status=val.get("last_status"),
                last_price=val.get("last_price"),
                last_alert=int(val.get("last_alert", 0)),
            )
            imported += 1
        except Exception:
            pass

    if imported:
        logger.info(f"🗄 Migrated {imported} state entries from state.json → DB")


# ---------------------------------------------------------------------------
# State (replaces state_store.py / state.json)
# ---------------------------------------------------------------------------

def _upsert_state(url: str, address: str, last_status, last_price, last_alert: int):
    """Internal upsert for product_state."""
    with _conn() as con:
        con.execute("""
            INSERT INTO product_state (url, address, last_status, last_price, last_alert)
            VALUES (?, ?, ?, ?, ?)
            ON CONFLICT(url, address) DO UPDATE SET
                last_status = excluded.last_status,
                last_price  = excluded.last_price,
                last_alert  = excluded.last_alert
        """, (url, address, last_status, last_price, last_alert))


def get_state(url: str, address: str) -> dict:
    """Return the current state dict for a product × address. Empty dict if unseen."""
    key = f"{url}::{address}"
    with _conn() as con:
        row = con.execute(
            "SELECT last_status, last_price, last_alert FROM product_state WHERE url=? AND address=?",
            (url, address)
        ).fetchone()
    if row:
        return dict(row)
    return {"last_status": None, "last_price": None, "last_alert": 0}


def update_state(url: str, address: str, status: str, price: int | None) -> tuple[dict, dict]:
    """
    Read the previous state, write the new status+price, return (prev, current).
    Does NOT touch last_alert (use mark_alerted_db for that).
    """
    prev = get_state(url, address)
    last_alert = prev.get("last_alert", 0)
    _upsert_state(url, address, status, price, last_alert)
    current = {"last_status": status, "last_price": price, "last_alert": last_alert}
    return prev, current


def mark_alerted_db(url: str, address: str):
    """Stamp the current time as last_alert for a product × address."""
    with _conn() as con:
        con.execute("""
            INSERT INTO product_state (url, address, last_alert)
            VALUES (?, ?, ?)
            ON CONFLICT(url, address) DO UPDATE SET last_alert = excluded.last_alert
        """, (url, address, int(time.time())))
