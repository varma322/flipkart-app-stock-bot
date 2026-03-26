import os
from dotenv import load_dotenv
from appium import webdriver
from appium.options.android import UiAutomator2Options

load_dotenv()


def setup_termux_env():
    """Ensure Android SDK environment is set properly for Termux."""
    # Only run in Termux environment
    if os.getenv("TERMUX_VERSION") or "/data/data/com.termux" in os.getenv("PATH", ""):
        # Appium in Termux often defaults to looking in /usr
        sdk_path = "/data/data/com.termux/files/usr"
        
        # Set variables if not already set
        if "ANDROID_HOME" not in os.environ:
            os.environ["ANDROID_HOME"] = sdk_path
        if "ANDROID_SDK_ROOT" not in os.environ:
            os.environ["ANDROID_SDK_ROOT"] = sdk_path
        
        # Ensure platform-tools is in PATH
        pt = f"{sdk_path}/platform-tools"
        if pt not in os.environ.get("PATH", ""):
            os.environ["PATH"] = f"{pt}:{os.environ.get('PATH', '')}"


def create_driver():
    setup_termux_env()
    is_termux = os.getenv("TERMUX_VERSION") is not None
    
    appium_server = os.getenv("APPIUM_SERVER", "http://127.0.0.1:4723")
    device_name = os.getenv("APPIUM_DEVICE_NAME", "localhost" if is_termux else "Mi 11X")

    opts = UiAutomator2Options()
    opts.platform_name = "Android"
    opts.device_name = device_name
    opts.automation_name = "UiAutomator2"

    # Skip operations that fail without WRITE_SECURE_SETTINGS
    opts.set_capability("ignoreHiddenApiPolicyError", True)
    
    # Fix 4 — Use skipDeviceInitialization to Reduce Footprint
    opts.set_capability("skipDeviceInitialization", True)
    opts.set_capability("skipServerInstallation", False)
    opts.set_capability("skipUnlock", True)
    opts.set_capability("autoGrantPermissions", True)
    
    # Stability for Xiaomi/Android 14+ / A16 (Baklava)
    opts.set_capability("disableWindowAnimation", True)
    opts.set_capability("ensureWebviewsHavePages", True)
    opts.set_capability("nativeWebScreenshot", True)
    
    # Extreme timeouts and stability for A16/Baklava
    opts.set_capability("uiautomator2ServerLaunchTimeout", 60000) 
    opts.set_capability("uiautomator2ServerInstallTimeout", 60000)
    opts.set_capability("adbExecTimeout", 60000)
    opts.set_capability("androidInstallTimeout", 90000)
    opts.set_capability("useResourcesForServerDelay", True)
    
    # NEW: Mitigate crashes on Android 14+ / A16
    opts.set_capability("disableSuppressAccessibilityService", True)
    opts.set_capability("forceAppLaunch", True)
    opts.set_capability("shouldTerminateApp", True)
    opts.set_capability("waitForIdleTimeout", 500) 
    opts.set_capability("appWaitDuration", 60000)

    # Fix 3 — Bypass instrumentation detection
    opts.set_capability("hideKeyboard", True)
    opts.set_capability("optionalIntentArguments", "--ez INSTRUMENTATION_NO 1")
    opts.set_capability("systemPort", 8201)

    # IMPORTANT: noReset=True keeps your Flipkart login session
    opts.no_reset = True 
    opts.full_reset = False

    # Some ROMs need this to permit background orchestration
    opts.set_capability("ignoreHiddenApiPolicyError", True)
    opts.set_capability("noSign", True) # Don't try to re-sign apps

    opts.new_command_timeout = 300

    driver = webdriver.Remote(appium_server, options=opts)
    return driver
