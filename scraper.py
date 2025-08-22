# Author: WEB SCRAPING MAROC (Enhanced by ChatGPT + Custom Enhancements)
import time
import csv
import random
import os
import webbrowser
from parsel import Selector
from selenium import webdriver
from selenium.webdriver.common.by import By
from selenium.webdriver.common.keys import Keys
from selenium.webdriver.chrome.service import Service
from webdriver_manager.chrome import ChromeDriverManager

# ---------------- CSV setup ----------------
csv_file = 'LinkedInProfiles.csv'
writer = csv.writer(open(csv_file, 'w', encoding='utf-8', newline=''))
writer.writerow(['name', 'job_title', 'schools', 'location', 'ln_url'])

# ---------------- LinkedIn Bot ----------------
class LinkedinBot:
    def __init__(self, li_at_cookie):
        service = Service(ChromeDriverManager().install())
        options = webdriver.ChromeOptions()
        options.add_argument("--disable-logging")
        options.add_argument("--log-level=3")
        self.driver = webdriver.Chrome(service=service, options=options)
        self.driver.maximize_window()

        self.base_url = 'https://www.linkedin.com'
        self.google_url = 'https://www.google.com'
        self.li_at = li_at_cookie

    def _nav(self, url):
        self.driver.get(url)
        time.sleep(random.uniform(2, 5))

    def login_with_cookie(self):
        """Login using li_at cookie"""
        self._nav(self.base_url)
        self.driver.add_cookie({'name': 'li_at', 'value': self.li_at, 'domain': '.linkedin.com'})
        self._nav(self.base_url)
        time.sleep(random.uniform(3, 5))

    def _human_typing(self, element, text):
        """Type like a human"""
        for char in text:
            element.send_keys(char)
            time.sleep(random.uniform(0.05, 0.2))

    def search(self, text, max_profiles=20):
        self._nav(self.google_url)
        search_input = self.driver.find_element(By.NAME, 'q')
        self._human_typing(search_input, f"site:linkedin.com/in/ {text}")
        search_input.send_keys(Keys.RETURN)
        time.sleep(random.uniform(3, 6))

        scraped_profiles = set()
        profile_count = 0
        page_number = 1

        while profile_count < max_profiles:
            # === CAPTCHA detection ===
            if "sorry" in self.driver.page_source.lower() or "unusual traffic" in self.driver.page_source.lower():
                input("CAPTCHA detected! Solve it manually in Chrome and then press Enter to continue...")

            # Grab all profile links (Google search results)
            profile_elements = self.driver.find_elements(By.XPATH, '//a[h3]')
            profiles = [elem.get_attribute('href') for elem in profile_elements if "linkedin.com/in" in elem.get_attribute('href')]

            # Remove duplicates
            profiles = [p for p in profiles if p not in scraped_profiles]

            if not profiles:
                print("⚠️ No more profiles found on this page!")
                break

            print(f"\n🌍 Page {page_number}: Found {len(profiles)} new profiles")

            for profile in profiles:
                if profile_count >= max_profiles:
                    break

                self.driver.get(profile)
                time.sleep(random.uniform(3, 6))

                # Human-like scroll
                self.driver.execute_script("window.scrollTo(0, document.body.scrollHeight/2);")
                time.sleep(random.uniform(1, 2))
                self.driver.execute_script("window.scrollTo(0, document.body.scrollHeight);")
                time.sleep(random.uniform(1, 2))

                try:
                    sel = Selector(text=self.driver.page_source)
                    name = sel.xpath('//title/text()').get(default='').split(' | ')[0]
                    job_title = sel.xpath('//h2/text()').get(default='').strip()
                    schools = ', '.join(sel.xpath('//*[contains(@class,"pv-entity__school-name")]/text()').getall())
                    location = sel.xpath('//*[@class="t-16 t-black t-normal inline-block"]/text()').get(default='').strip()
                    ln_url = self.driver.current_url
                except:
                    continue

                profile_count += 1
                scraped_profiles.add(profile)
                print(f"[{profile_count}] {name} | {job_title} | {schools} | {location} | {ln_url}")
                writer.writerow([name, job_title, schools, location, ln_url])

            # Next Google page
            try:
                next_button = self.driver.find_element(By.ID, 'pnnext')
                next_button.click()
                page_number += 1
                time.sleep(random.uniform(3, 6))
            except:
                print("⚠️ No more Google pages available.")
                break

        print(f"\n✅ Total profiles scraped: {profile_count}")
        if profile_count < max_profiles:
            print(f"⚠️ Alert: Sirf {profile_count} profiles hi mil paye, aapne {max_profiles} maange the.")

        self.driver.quit()

        # Automatically open Excel / CSV file
        try:
            abs_path = os.path.abspath(csv_file)
            webbrowser.open(abs_path)
        except:
            print(f"CSV saved at {abs_path}. Open manually.")

# ---------------- Main ----------------
if __name__ == '__main__':
    li_at = "AQEDAV3jfIMFHkmHAAABmMv90YcAAAGY8ApVh00AJw16rwPXmbYJP9J2RodYqU3UwyfgC_4SPOjEc44NUP_g7H7DTaxniMZZpbRst8lDxcD2eTOfmJeHi0xKs_smiFT4HTmg14DJeRkgXYaX0FN848fj"
    search_text = input("Search Text: ")
    max_profiles = int(input("Kitne profiles scrape karne hain? "))

    bot = LinkedinBot(li_at)
    bot.login_with_cookie()
    bot.search(search_text, max_profiles=max_profiles)
