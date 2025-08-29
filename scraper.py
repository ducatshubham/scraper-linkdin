import asyncio
import json
import csv
import os
import time
import sys
import subprocess
from pathlib import Path
from playwright.async_api import async_playwright

# File paths
cookies_path = Path("cookies.json")
output_csv = Path("linkedin_results.csv")


# Helper: Ask question in console
def ask_question(prompt_text: str) -> str:
    return input(prompt_text)


# Helper: Delay
async def delay(ms: int):
    await asyncio.sleep(ms / 1000)


# Save data to CSV
def save_to_csv(rows):
    headers = ["Name", "Title", "Location", "Education", "Profile URL"]
    with open(output_csv, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=headers)
        writer.writeheader()
        for r in rows:
            writer.writerow({
                "Name": r.get("name", "N/A"),
                "Title": r.get("title", "N/A"),
                "Location": r.get("location", "N/A"),
                "Education": r.get("education", "N/A"),
                "Profile URL": r.get("url", "")
            })
    print(f"✅ Data saved to {output_csv}")


# Open file in Excel automatically
def open_excel(file_path):
    try:
        if sys.platform.startswith("win"):
            os.startfile(file_path)  # ✅ Windows
        elif sys.platform == "darwin":
            subprocess.run(["open", file_path])  # ✅ macOS
        else:
            subprocess.run(["xdg-open", file_path])  # ✅ Linux
        print("📂 Opened Excel file.")
    except Exception as e:
        print(f"❌ Could not open Excel: {e}")


# Scroll page to load dynamic content
async def auto_scroll(page):
    try:
        await page.evaluate("""async () => {
            await new Promise((resolve) => {
                let totalHeight = 0;
                const distance = 100;
                const timer = setInterval(() => {
                    window.scrollBy(0, distance);
                    totalHeight += distance;
                    if (totalHeight >= document.body.scrollHeight) {
                        clearInterval(timer);
                        resolve();
                    }
                }, 100);
            });
        }""")
        print("ℹ Scrolled page to load dynamic content.")
    except Exception as e:
        print(f"❌ Failed to scroll: {e}")


# Setup Browser with Correct User-Agent
async def setup_browser(playwright):
    browser = await playwright.chromium.launch(
        headless=False,
        args=["--no-sandbox", "--disable-setuid-sandbox", "--window-size=1920,1080", "--disable-dev-shm-usage"]
    )

    context = await browser.new_context(
        user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/117.0.0.0 Safari/537.36"
    )

    page = await context.new_page()

    if cookies_path.exists():
        try:
            cookies = json.loads(cookies_path.read_text(encoding="utf-8"))
            await context.add_cookies(cookies)
            print("✅ Loaded cookies from file.")
        except Exception as e:
            print(f"❌ Failed to load cookies: {e}")

    try:
        await page.goto("https://www.linkedin.com/feed/", timeout=90000)
        print("✅ LinkedIn feed loaded successfully.")
    except Exception:
        print("❌ Failed to load LinkedIn feed.")

    if "/login" in page.url:
        print("👉 Please log in manually in the opened browser window...")
        ask_question("🔑 Press Enter after login...")
        cookies = await context.cookies()
        cookies_path.write_text(json.dumps(cookies, indent=2), encoding="utf-8")
        print("💾 Login session saved!")

    return browser, context, page


# Scrape Profile Details
async def scrape_profile(page, profile_url):
    try:
        await page.goto(profile_url, timeout=90000)
        await page.wait_for_selector("h1", timeout=15000)
        await page.evaluate("window.scrollBy(0, document.body.scrollHeight)")
        await page.wait_for_timeout(2000)

        data = await page.evaluate("""() => {
            const getText = (selectors) => {
                for (const sel of selectors) {
                    const el = document.querySelector(sel);
                    if (el && el.innerText.trim()) return el.innerText.trim();
                }
                return "N/A";
            };

            const name = getText([
                "h1.inline.t-24.v-align-middle.break-words",
                "h1.text-heading-xlarge",
                "h1"
            ]);

            const title = getText([
                "div.text-body-medium.break-words",
                "div.text-body-medium",
                ".mt1.t-18.t-black.t-normal.break-words"
            ]);

            const location = getText([
                "span.text-body-small.inline.t-black--light.break-words",
                "span.text-body-small"
            ]);

            // 🔹 Education scraping
            let education = "N/A";
            const eduSection = document.querySelector("section[id='education'], section[data-section='education'], div[data-view-name='education']");
            if (eduSection) {
                const items = eduSection.querySelectorAll("li span[aria-hidden='true']");
                if (items.length > 0) {
                    education = Array.from(items)
                        .map(el => el.innerText.trim())
                        .filter(text => text.length > 0)
                        .slice(0, 2)
                        .join(" | ");
                }
            }
            if (education === "N/A" && title.includes("|")) {
                const parts = title.split("|");
                const possibleEdu = parts[parts.length - 1].trim();
                if (possibleEdu.length > 3) {
                    education = possibleEdu;
                }
            }

            return { name, title, location, education };
        }""")

        print(f"✅ Scraped {profile_url}: {data}")
        return {**data, "url": profile_url}

    except Exception as e:
        print(f" Failed to scrape {profile_url}: {e}")
        return {
            "name": "N/A", "title": "N/A", "location": "N/A",
            "education": "N/A", "url": profile_url
        }


# Main Function
async def main():
    async with async_playwright() as p:
        browser, _, page = await setup_browser(p)

        limit = int(ask_question("🔢 How many profiles to scrape?: "))

        search_url = "https://www.linkedin.com/company/gameskraft/people/"
        await page.goto(search_url, timeout=90000)

        profile_urls = set()

        # 🔗 Collect profile URLs
        while len(profile_urls) < limit:
            await auto_scroll(page)
            await delay(2000)

            urls = await page.evaluate("""() => {
                const selectors = ["a.app-aware-link", "a[href*='/in/']"];
                let links = [];
                for (const selector of selectors) {
                    const found = [...document.querySelectorAll(selector)]
                        .map(a => a.href)
                        .filter(h => h.includes("/in/") && !h.includes("/mini-profile/"));
                    if (found.length > 0) {
                        links = found;
                        break;
                    }
                }
                return links;
            }""")

            for u in urls:
                profile_urls.add(u)
            print(f"ℹ Collected {len(profile_urls)} profile URLs...")

            if len(profile_urls) >= limit:
                break

            next_btn = await page.query_selector("button.artdeco-pagination__button--next:not([disabled])")
            if not next_btn:
                print("ℹ No more pages.")
                break
            await next_btn.click()
            await delay(3000)

        # 📝 Scrape Profiles
        results = []
        for url in list(profile_urls)[:limit]:
            print(f"🔍 Scraping {url}")
            info = await scrape_profile(page, url)
            results.append(info)
            await delay(2000)

        print("📋 All data scraped:")
        for r in results:
            print(r)

        save_to_csv(results)
        open_excel(str(output_csv))  # 📂 Auto open Excel

        await browser.close()
        print("🏁 Done!")


if __name__ == "__main__":
    asyncio.run(main())
