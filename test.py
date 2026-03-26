from appium import webdriver
from appium.options.android import UiAutomator2Options
import time

opts = UiAutomator2Options()
opts.device_name = "192.168.29.183:41755" 
opts.platform_name = "Android"
opts.automation_name = "UiAutomator2"

# Use the full explicit component name
opts.app_package = "com.android.settings"
opts.app_activity = "com.android.settings.Settings" # Fully qualified name

# --- The "Force Open" Flags ---
opts.set_capability("appium:noReset", True)
opts.set_capability("appium:intentAction", "android.settings.SETTINGS")
opts.set_capability("appium:appWaitActivity", "com.android.settings.*")
opts.set_capability("appium:appWaitDuration", 20000)

# Stability & Bypass for crDroid / A16 / Xiaomi
opts.set_capability("appium:disableWindowAnimation", True)
opts.set_capability("appium:ignoreHiddenApiPolicyError", True)
opts.set_capability("appium:noSign", True)
opts.set_capability("appium:optionalIntentArguments", "--ez INSTRUMENTATION_NO 1")
opts.set_capability("appium:uiautomator2ServerLaunchTimeout", 180000) # 3 mins
opts.set_capability("appium:uiautomator2ServerInstallTimeout", 90000)
opts.set_capability("appium:adbExecTimeout", 60000)

try:
    print("🚀 Forcing Settings app to open...")
    driver = webdriver.Remote("http://127.0.0.1:4723", options=opts)
    
    # Force it again just in case the initial launch failed
    driver.activate_app("com.android.settings")
    
    time.sleep(2) # Wait for animation
    
    current = driver.current_activity
    print(f"✅ Current Activity: {current}")
    
    if "settings" in current.lower():
        print("📱 Settings app is now visible!")
    else:
        print("⚠️ Still in Termux. Trying manual start via ADB...")
        driver.execute_script('mobile: shell', {
            'command': 'am start',
            'args': ['-n', 'com.android.settings/.Settings']
        })

    time.sleep(5)
    driver.quit()

except Exception as e:
    print(f"❌ Error: {e}")