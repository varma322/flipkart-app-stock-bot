import os
import random
import time
from selenium.webdriver.common.by import By
from bot.flipkart_app_checker import close_popups, save_screenshot, wait_until_any, is_session_alive, SessionDeadError
from bot.constants import FLIPKART_PKG
from utils.logger import logger


# Signals that indicate Flipkart home screen has loaded
HOME_SIGNALS = ["Explore", "Search", "Categories", "HOME", "WORK"]


def wait_for_text(driver, text, timeout=8):
    """Poll until an element containing `text` appears on screen."""
    start = time.time()
    while time.time() - start < timeout:
        if driver.find_elements(By.XPATH, f"//*[contains(@text,'{text}')]"):
            return True
        time.sleep(0.4)
    return False


# XPath selectors — find text node then walk up to the clickable container.
# This is the ONLY strategy that has been proven to work; trying unknown
# resource-IDs first only wastes 5+ seconds per loop iteration.
ADDRESS_BAR_XPATH_SELECTORS = [
    # Clickable ancestor of the address label (WORK / HOME / address text / street identifiers)
    "//*[contains(@text,'WORK') or contains(@text,'HOME') "
    "or contains(@text,'Select delivery location') "
    "or contains(@text,'Select address') or contains(@text,'Deliver to') "
    "or contains(@text,'-111') or contains(@text,'Km-')"
    "]/ancestor::*[@clickable='true'][1]",
    # Fallback: clickable element whose content-desc mentions the location
    "//*[contains(@content-desc,'WORK') or contains(@content-desc,'HOME') "
    "or contains(@content-desc,'delivery location') or contains(@content-desc,'Deliver to')]"
    "[@clickable='true']",
]

# UiAutomator text selectors — last resort
ADDRESS_BAR_TEXT_SELECTORS = [
    'new UiSelector().textContains("WORK")',
    'new UiSelector().textContains("HOME")',
    'new UiSelector().textContains("Select delivery location")',
    'new UiSelector().textContains("HIG-111")',
    'new UiSelector().textContains("Km-111")',
]


def tap_address_bar(driver, timeout=12):
    """Tap the delivery-location bar on the Flipkart home screen."""
    start = time.time()
    while time.time() - start < timeout:

        # Strategy 1 (last resort): Randomized coordinate tap
        # This bypasses structural UI changes and mimics human behavior.
        # Uses ranges from .env, e.g., ADDRESS_BAR_X_RANGE="120,870", ADDRESS_BAR_Y_RANGE="280,340"
        try:
            x_range = [int(v) for v in os.getenv("ADDRESS_BAR_X_RANGE", "120,870").split(",")]
            y_range = [int(v) for v in os.getenv("ADDRESS_BAR_Y_RANGE", "280,340").split(",")]
            
            x = random.randint(min(x_range), max(x_range))
            y = random.randint(min(y_range), max(y_range))
            
            driver.tap([(x, y)])
            logger.info(f"Address bar tapped via randomized coordinates: ({x}, {y})")
            time.sleep(1.0)
            return True
        except Exception as e:
            logger.warning(f"Coordinate tap failed: {e}")

        # Strategy 2 (fast): XPath ancestor — proven to work
        for xp in ADDRESS_BAR_XPATH_SELECTORS:
            try:
                els = driver.find_elements(By.XPATH, xp)
                if els:
                    els[0].click()
                    logger.info("Address bar tapped via XPath ancestor")
                    time.sleep(0.8)
                    return True
            except Exception:
                pass

        # Strategy 3 (fallback): UiAutomator text
        for sel in ADDRESS_BAR_TEXT_SELECTORS:
            try:
                el = driver.find_element(By.ANDROID_UIAUTOMATOR, sel)
                el.click()
                logger.info(f"Address bar tapped via text selector: {sel}")
                time.sleep(0.8)
                return True
            except Exception:
                pass



        time.sleep(0.4)
    return False


def _is_on_flipkart_home(driver):
    """
    Return True ONLY if Flipkart is the foreground app AND the home screen
    signals are visible in its page source.
    Checking page_source alone is unreliable — the Android launcher also
    contains words like 'Home' and 'Search', causing false positives.
    """
    try:
        if driver.current_package != FLIPKART_PKG:
            return False
        src = driver.page_source.upper()
        return any(sig.upper() in src for sig in HOME_SIGNALS)
    except Exception:
        return False


def _wait_for_flipkart_home(driver, timeout=15):
    """
    Wait until Flipkart is the foreground package AND a home signal is visible.
    Unlike wait_until_any, this checks current_package first to avoid false
    positives from the Android launcher's own page source.
    """
    start = time.time()
    while time.time() - start < timeout:
        try:
            if driver.current_package == FLIPKART_PKG:
                src = driver.page_source.upper()
                if any(sig.upper() in src for sig in HOME_SIGNALS):
                    return True
        except Exception:
            pass
        time.sleep(0.5)
    return False


def force_home(driver, retries=3):
    """
    Kill Flipkart via `am force-stop` (shell) and relaunch via Appium's activate_app.

    Why this split:
    - `am force-stop` kills only the Flipkart process cleanly, leaving the
      UiAutomator2 instrumentation socket untouched.
    - `driver.terminate_app()` is an Appium-level call that can destabilise
      the instrumentation connection, causing 'socket hang up' on the next
      find_elements call.
    - `activate_app` is the correct Appium way to launch — it resolves the
      real launcher activity automatically, no hardcoded class name needed.
    """
    for attempt in range(1, retries + 1):
        try:
            try:
                # Kill app to ensure clean state launch on EVERY attempt
                driver.execute_script("mobile: shell", {
                    "command": "am",
                    "args": ["force-stop", FLIPKART_PKG]
                })
                time.sleep(1.5)
            except Exception as e:
                # Fallback if adb_shell is restricted/disabled (e.g. server flag missing/typo)
                if "adb_shell" in str(e) or "insecure" in str(e):
                    logger.warning(f"adb_shell not enabled. Attempting terminate_app fallback.")
                    try:
                        driver.terminate_app(FLIPKART_PKG)
                        time.sleep(1.5)
                    except Exception:
                        pass
                else:
                    logger.warning(f"am force-stop failed: {e}")

            driver.activate_app(FLIPKART_PKG)
        except Exception as e:
            logger.warning(f"activate_app failed (attempt {attempt}): {e}")

        # Give it a base load time
        time.sleep(4) 
        
        wait = 12 + (attempt - 1) * 3
        if _wait_for_flipkart_home(driver, timeout=wait):
            logger.info(f"Flipkart home loaded (attempt {attempt})")
            return

        logger.warning(f"Home screen not loaded (attempt {attempt}). App may have restored state. Attempting back-button unwind...")
        
        # Unwind backstack (e.g., if stuck on product page)
        for _ in range(3):
            try:
                if _is_on_flipkart_home(driver):
                    logger.info("Reached home screen via back-button unwind")
                    return
                if driver.current_package != FLIPKART_PKG:
                    break
                driver.back()
                time.sleep(1.8)
            except Exception:
                pass

        if _is_on_flipkart_home(driver):
            logger.info("Reached home screen via back-button unwind")
            return

        logger.warning(f"Flipkart did not load (attempt {attempt}/{retries}), retrying...")
        time.sleep(1)

    logger.error("Flipkart home screen could not be confirmed after all retries")
    # If the instrumentation is dead, escalate immediately so the main loop
    # can tear the session down and create a fresh driver.
    if not is_session_alive(driver):
        raise SessionDeadError("UiAutomator2 instrumentation is not running after force_home retries")

    path = save_screenshot(driver, "home_screen_failure")
    raise Exception(f"Failed to load Flipkart home screen after retries. Screenshot: {path}")


def ensure_home(driver):
    """
    Get to the Flipkart home screen as fast as possible.
    - If Flipkart is already in the foreground on home: skip relaunch.
    - Otherwise: full force_home (kill + relaunch with verification).
    Raises SessionDeadError immediately if the Appium/UiAutomator2 session is dead.
    """
    # Fast dead-session probe — avoids spending 1+ minute on hopeless retries.
    if not is_session_alive(driver):
        raise SessionDeadError("Appium session is dead (instrumentation not running)")

    if _is_on_flipkart_home(driver):
        logger.info("Already on Flipkart home screen, skipping relaunch")
        close_popups(driver)
        return

    force_home(driver)
    close_popups(driver)


def select_saved_address(driver, address_name: str):
    ensure_home(driver)

    # Open the address-selection sheet
    # NEW: Retry loop because the sheet sometimes fails to open on the first tap
    max_taps = 4
    sheet_found = False
    sheet_signals = ["Select delivery address", "Deliver to", "Select address", "Choose delivery location"]

    for tap_at in range(1, max_taps + 1):
        if not tap_address_bar(driver):
            logger.warning(f"Address bar not found (tap attempt {tap_at}/{max_taps})")
        else:
            # Wait for any of the sheet signals
            start_wait = time.time()
            while time.time() - start_wait < 10:
                inner_src = driver.page_source.upper()
                if any(sig.upper() in inner_src for sig in sheet_signals):
                    sheet_found = True
                    break
                time.sleep(1)
            
            if sheet_found:
                break
        
        if tap_at < max_taps:
            logger.warning(f"Address sheet not opening (attempt {tap_at}/{max_taps}). Retrying tap...")
            # Try to back out just in case we hit something that blocks the sheet
            driver.back() if tap_at > 1 else None
            time.sleep(1.5)

    if not sheet_found:
        path = save_screenshot(driver, "address_sheet_not_opening")
        raise Exception(f"Address sheet not opening after {max_taps} retries. Screenshot: {path}")

    # If already selected, dismiss and return immediately
    try:
        already = driver.find_elements(
            By.XPATH,
            f"//*[contains(@text,'{address_name}')]"
            f"/following-sibling::*[contains(@text,'Currently selected')]"
            f" | //*[contains(@text,'{address_name}')]"
            f"/../*[contains(@text,'Currently selected')]"
        )
        if already:
            logger.info(f"'{address_name}' already selected, dismissing sheet")
            driver.back()
            time.sleep(0.4)
            return True
    except Exception:
        pass

    # Click the address row via clickable ancestor (compound view)
    clicked = False
    try:
        rows = driver.find_elements(
            By.XPATH,
            f"//*[contains(@text,'{address_name}')]/ancestor::*[@clickable='true'][1]"
        )
        if rows:
            rows[0].click()
            logger.info(f"'{address_name}' tapped via XPath ancestor")
            clicked = True
    except Exception:
        pass

    # Fallback: UiScrollable (handles many saved addresses)
    if not clicked:
        try:
            addr = driver.find_element(
                By.ANDROID_UIAUTOMATOR,
                f'new UiScrollable(new UiSelector().scrollable(true)).scrollIntoView('
                f'new UiSelector().textContains("{address_name}"))'
            )
            addr.click()
            logger.info(f"'{address_name}' tapped via UiScrollable")
            clicked = True
        except Exception:
            pass

    if not clicked:
        path = save_screenshot(driver, f"address_not_found_{address_name}")
        raise Exception(f"Address '{address_name}' not found. Screenshot: {path}")

    # Wait until sheet closes
    wait_for_text(driver, "Explore", timeout=6)

    try:
        driver.back()
    except Exception:
        pass
    time.sleep(0.4)

    return True