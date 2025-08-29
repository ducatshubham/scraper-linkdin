from selenium import webdriver
from selenium.webdriver.chrome.service import Service
from selenium.webdriver.chrome.options import Options
from webdriver_manager.chrome import ChromeDriverManager
import json
import time

def save_cookies():
    options = Options()
    options.add_argument("--start-maximized")
    driver = webdriver.Chrome(service=Service(ChromeDriverManager().install()), options=options)

    print("🌐 Opening LinkedIn login page...")
    driver.get("https://www.linkedin.com/login")

    print("🔑 Please log in manually...")
    time.sleep(40)  # 40 sec rukega taaki tu manually login kar sake

    cookies = driver.get_cookies()
    with open("cookies.json", "w") as f:
        json.dump(cookies, f)

    print("✅ Cookies saved to cookies.json")
    driver.quit()

if __name__ == "__main__":
    save_cookies()
