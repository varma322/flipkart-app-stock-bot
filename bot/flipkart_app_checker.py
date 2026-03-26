import time
import re
from selenium.webdriver.common.by import By
from utils.logger import logger
from bot.constants import FLIPKART_PKG
import os
import datetime


class SessionDeadError(Exception):
    """Raised when the UiAutomator2 instrumentation process has crashed."""


def is_session_alive(driver) -> bool:
    """
    Quick probe: try to fetch the page source.
    Returns False if the instrumentation is not running (socket hang-up / proxy
    error), which means the Appium session must be restarted.
    """
    try:
        _ = driver.page_source
        return True
    except Exception as e:
        msg = str(e).lower()
        dead_signals = [
            "socket hang up",
            "instrumentation process is not running",
            "cannot be proxied",
            "session not created",
            "no such session",
        ]
        if any(s in msg for s in dead_signals):
            return False
        # Other transient errors (timeouts, etc.) — assume still alive
        return True


def wait_until_any(driver, signals, timeout=15, poll=0.5):
    """Poll page_source until any signal string appears (case-insensitive)."""
    start = time.time()
    while time.time() - start < timeout:
        try:
            source = driver.page_source.upper()
            for sig in signals:
                if sig.upper() in source:
                    return sig
        except Exception:
            pass
        time.sleep(poll)
    return None

def save_screenshot(driver, reason="unknown"):
    """Save a screenshot. Returns the path, or a placeholder string if the session is dead."""
    try:
        os.makedirs("screenshots", exist_ok=True)
        ts = datetime.datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
        path = f"screenshots/{reason}_{ts}.png"
        driver.save_screenshot(path)
        return path
    except Exception as e:
        logger.warning(f"Screenshot failed (session may be dead): {e}")
        return f"screenshots/{reason}_[screenshot_failed].png"



def close_popups(driver, timeout=3):
    """Dismiss common Flipkart popups. Polls until each popup disappears."""
    candidates = [
        "//*[@content-desc='Close']",
        "//*[@text='\u2715']",
        "//*[@text='X']",
        "//*[@text='Close']",
        "//*[@text='Not now']",
        "//*[@text='Skip']"
    ]

    for xp in candidates:
        els = driver.find_elements(By.XPATH, xp)
        if els:
            try:
                els[0].click()
                # Poll until the element is gone (max timeout seconds)
                deadline = time.time() + timeout
                while time.time() < deadline:
                    if not driver.find_elements(By.XPATH, xp):
                        break
                    time.sleep(0.2)
            except Exception:
                pass


def get_price(driver):
    """
    Extract real product price.
    Heuristic: Main product price is usually the largest text element starting with '₹'.
    """
    # 1. Start with exact matches for Buy buttons (often reliable)
    try:
        buy_at_els = driver.find_elements(By.XPATH, "//*[contains(@text, 'Buy at')]")
        for el in buy_at_els:
            text = el.text
            match = re.search(r"₹\s*([0-9,]+)", text)
            if match:
                price = int(match.group(1).replace(",", ""))
                logger.info(f"Found price from Buy button: {price}")
                return price
    except Exception:
        pass

    # 2. Scan all text elements with '₹'
    try:
        candidates = []
        price_elements = driver.find_elements(By.XPATH, "//*[contains(@text, '₹')]")

        for el in price_elements:
            try:
                text = el.text.strip()
                if not text or "₹" not in text:
                    continue

                # Check parent for AD marker
                try:
                    parent = el.find_element(By.XPATH, "./ancestor::*[1]")
                    parent_desc = parent.get_attribute("content-desc") or ""
                    if "AD" in parent_desc:
                        continue
                except:
                    pass

                # Parse price value
                # Extract number after ₹
                match = re.search(r"₹\s*([0-9,]+)", text)
                if not match:
                    continue
                
                value = int(match.group(1).replace(",", ""))

                # Get element size (height is good proxy for font size/prominence)
                bounds = el.get_attribute("bounds")
                if bounds:
                    # Parse bounds "[481,2056][651,2140]"
                    coords = re.findall(r"\[(\d+),(\d+)\]", bounds)
                    if len(coords) == 2:
                        x1, y1 = map(int, coords[0])
                        x2, y2 = map(int, coords[1])
                        height = y2 - y1
                        candidates.append({"value": value, "height": height})
            except Exception:
                continue

        if not candidates:
            logger.warning("No price candidates found.")
            return None

        # Sort by height descending
        candidates.sort(key=lambda x: x["height"], reverse=True)
        
        # Log candidates for debug
        # logger.info(f"Price candidates: {candidates}")

        # Pick largest height. If tie, pick max value? 
        # Usually main price is unique max height.
        best_price = candidates[0]["value"]
        logger.info(f"Selected price: {best_price} (height: {candidates[0]['height']})")
        return best_price

    except Exception as e:
        logger.error(f"Error extracting price: {e}")
        return None


# Signals that indicate the product page has loaded
PRODUCT_PAGE_SIGNALS = ["₹", "Buy Now", "Buy at", "Add to cart", "Notify Me", "Sold Out"]


def open_product(driver, url):
    """
    Open a product page and wait until it finishes loading.
    Uses mobile:deepLink (Appium native) — safer than `am start` shell because
    it is handled by the UiAutomator2 driver, not an external process that can
    race with the running app and crash the instrumentation.
    """
    try:
        driver.execute_script("mobile: deepLink", {
            "url": url,
            "package": FLIPKART_PKG
        })
    except Exception:
        # Fallback to am start if deepLink is not supported on this Appium version
        driver.execute_script("mobile: shell", {
            "command": "am",
            "args": ["start", "-a", "android.intent.action.VIEW", "-d", url, FLIPKART_PKG]
        })

    result = wait_until_any(driver, PRODUCT_PAGE_SIGNALS, timeout=20)
    if result is None:
        logger.warning(f"Product page may not have loaded fully for: {url}")



def detect_state(driver):
    # Wait for page to stabilize if needed, but open_product already waits for signals
    page_source = driver.page_source.lower()

    # 1️⃣ Hard OOS indicators
    oos_keywords = [
        "notify me",
        "out of stock",
        "currently unavailable",
        "sold out"
    ]

    for word in oos_keywords:
        if word in page_source:
            return "OUT_OF_STOCK"

    # 2️⃣ Delivery checks
    if "not deliverable" in page_source or "change address" in page_source:
        return "NOT_DELIVERABLE"

    # 3️⃣ Strong IN STOCK indicators
    try:
        # Use find_elements to catch if present
        # Note: XPath is case sensitive, but "Buy Now" usually has this exact casing or "BUY NOW"
        # Since page_source is lower(), logic check uses lowercase, but find_elements uses exact if not normalized.
        # Let's keep using 'contains' with text() for safety.
        # But wait, we want robust logic.
        
        # Check source for immediate string match (faster/simpler)
        if "buy now" in page_source or "buy at" in page_source:
            return "IN_STOCK"
            
        # Add to cart is tricky (race condition).
        # Only return IN_STOCK if NO negative signals are found after a wait.
        if "add to cart" in page_source:
            # Poll until a definitive signal appears (max 3s) instead of a flat 2s sleep.
            # Returns as soon as the page settles — much faster on good connections.
            deadline = time.time() + 3
            while time.time() < deadline:
                try:
                    src = driver.page_source.lower()
                    if ("not deliverable" in src or "change address" in src
                            or "notify me" in src or "sold out" in src
                            or "buy now" in src or "buy at" in src):
                        page_source = src
                        break
                except Exception:
                    pass
                time.sleep(0.3)
            else:
                # Timeout — re-read source for final verdict
                try:
                    page_source = driver.page_source.lower()
                except Exception:
                    pass

            if "not deliverable" in page_source or "change address" in page_source:
                return "NOT_DELIVERABLE"
            if "notify me" in page_source or "sold out" in page_source:
                return "OUT_OF_STOCK"
            return "IN_STOCK"

    except:
        pass

    # 4️⃣ Fallback — if price exists but no Notify Me
    price = get_price(driver)
    if price:
        # If we have a price and passed all negative checks, it's likely IN STOCK.
        # Returning IN_STOCK allows main loop to verify price drop etc.
        return "IN_STOCK"
    
    # If we are here, it's truly unknown/ambiguous
    # Dump source for debugging
    try:
        ts = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
        with open(f"logs/ambiguous_source_{ts}.txt", "w", encoding="utf-8") as f:
            f.write(driver.page_source) # Save original case source
        logger.warning(f"Ambiguous state. Source dumped to logs/ambiguous_source_{ts}.txt")
    except Exception as e:
        logger.error(f"Failed to dump source: {e}")

    return "AMBIGUOUS"
