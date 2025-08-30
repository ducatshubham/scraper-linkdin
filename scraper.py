import asyncio
import json
import csv
import os
import time
import sys
import subprocess
import random
from pathlib import Path
from urllib.parse import urlparse, urlunparse, urljoin, urlencode, parse_qs
from playwright.async_api import async_playwright

# -----------------------
# File paths
# -----------------------
cookies_path = Path("cookies.json")
output_csv = Path("linkedin_results.csv")

# -----------------------
# Helpers
# -----------------------
def ask_question(prompt_text: str) -> str:
    return input(prompt_text)

async def delay(ms: int):
    await asyncio.sleep(ms / 1000)

def save_to_csv(rows):
    headers = [
    "Name", "Title", "Location", "Education", "Profile URL",
    "Total Experience", "Experience Details", "Skills"
    ]
    with open(output_csv, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=headers)
        writer.writeheader()
        for r in rows:
            writer.writerow({
                "Name": r.get("name", "N/A"),
                "Title": r.get("title", "N/A"),
                "Location": r.get("location", "N/A"),
                "Education": r.get("education", "N/A"),
                "Profile URL": r.get("url", ""),
                "Total Experience": r.get("total_experience", "N/A"),
                "Experience Details": r.get("experience_details", "N/A"),
                "Skills": r.get("skills", "N/A")
            })
    print(f"✅ Data saved to {output_csv}")

def open_excel(file_path):
    try:
        if sys.platform.startswith("win"):
            os.startfile(file_path)
        elif sys.platform == "darwin":
            subprocess.run(["open", file_path])
        else:
            subprocess.run(["xdg-open", file_path])
        print("📂 Opened Excel file.")
    except Exception as e:
        print(f"❌ Could not open Excel: {e}")

async def auto_scroll(page, step=600, max_rounds=30, wait_ms=700):
    """Slow incremental scroll to trigger lazy-load."""
    try:
        last_height = await page.evaluate("() => document.body.scrollHeight")
        rounds = 0
        while rounds < max_rounds:
            rounds += 1
            await page.evaluate(f"window.scrollBy(0, {step});")
            await page.wait_for_timeout(wait_ms)
            new_height = await page.evaluate("() => document.body.scrollHeight")
            if new_height == last_height:
                # one extra tiny scroll to trigger observers
                await page.evaluate("window.scrollBy(0, 50);")
                await page.wait_for_timeout(wait_ms)
                new_height = await page.evaluate("() => document.body.scrollHeight")
                if new_height == last_height:
                    break
            last_height = new_height
        print("ℹ Scrolled page to load dynamic content.")
    except Exception as e:
        print(f"❌ Failed to scroll: {e}")

def clean_profile_url(u: str) -> str:
    """Remove tracking query params, force https, keep only /in/... path."""
    try:
        parsed = urlparse(u)
        # If relative link, make absolute
        if not parsed.netloc:
            u = urljoin("https://www.linkedin.com", u)
            parsed = urlparse(u)
        # strip query & fragment
        path = parsed.path
        if "/in/" in path:
            # normalize to end with a trailing slash (helps experience URL)
            if not path.endswith("/"):
                path = path + "/"
            clean = urlunparse(("https", "www.linkedin.com", path, "", "", ""))
            return clean
        return u
    except Exception:
        return u

# -----------------------
# Browser setup
# -----------------------
async def setup_browser(playwright):
    browser = await playwright.chromium.launch(
        headless=False,
        args=[
            "--no-sandbox",
            "--disable-setuid-sandbox",
            "--window-size=1920,1080",
            "--disable-dev-shm-usage"
        ]
    )
    context = await browser.new_context(
        user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/117.0.0.0 Safari/537.36",
        viewport={"width": 1366, "height": 768}
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
        await page.wait_for_load_state("domcontentloaded")
        print("✅ LinkedIn feed loaded successfully.")
    except Exception:
        print("❌ Failed to load LinkedIn feed.")

    if "/login" in page.url or "challenge" in page.url:
        print("👉 Please log in manually in the opened browser window...")
        ask_question("🔑 Press Enter after login...")
        cookies = await context.cookies()
        cookies_path.write_text(json.dumps(cookies, indent=2), encoding="utf-8")
        print("💾 Login session saved!")

    return browser, context, page

# -----------------------
# Scrape Skills
# -----------------------
async def scrape_skills(page, profile_url):
    try:
        # Clean URL & extract username
        base_url = clean_profile_url(profile_url)
        if "/in/" not in base_url:
            return []
        username = base_url.split("/in/")[1].split("/")[0]
        skills_url = f"https://www.linkedin.com/in/{username}/details/skills/"

        print(f"🔍 Scraping skills from: {skills_url}")
        await page.goto(skills_url, timeout=90000)
        await page.wait_for_timeout(2000)
        await auto_scroll(page, step=700, max_rounds=20, wait_ms=400)
        await page.wait_for_timeout(1200)

        # Extract skills
        skills = await page.evaluate(r"""() => {
            const skillsList = [];
            
            // Look for skills in the skills section
            const skillItems = document.querySelectorAll('li.pvs-list__paged-list-item span[aria-hidden="true"]');
            
            skillItems.forEach((item) => {
                const skillText = item.innerText.trim();
                // Filter out empty text, numbers, and common non-skill text
                if (skillText && 
                    skillText.length > 1 && 
                    !skillText.match(/^\d+$/) && 
                    !skillText.includes('endorsement') &&
                    !skillText.includes('connection') &&
                    skillText !== '·') {
                    skillsList.push(skillText);
                }
            });

            // Remove duplicates and return unique skills
            return [...new Set(skillsList)];
        }""")

        return skills

    except Exception as e:
        print(f"❌ Failed to scrape skills for {profile_url}: {e}")
        return []

# -----------------------
# Scrape Experience
# -----------------------
async def scrape_experience(page, profile_url):
    try:
        # Clean URL & extract username
        base_url = clean_profile_url(profile_url)
        if "/in/" not in base_url:
            return {
                "experiences": [],
                "currentCompany": "N/A",
                "currentTitle": "N/A",
                "totalExperience": "N/A"
            }
        username = base_url.split("/in/")[1].split("/")[0]
        experience_url = f"https://www.linkedin.com/in/{username}/details/experience/"

        print(f"🔍 Scraping experience from: {experience_url}")
        await page.goto(experience_url, timeout=90000)
        await page.wait_for_timeout(2000)
        await auto_scroll(page, step=700, max_rounds=20, wait_ms=400)
        await page.wait_for_timeout(1200)

        # Evaluate JS in page context (NOTE: raw string to preserve backslashes)
        experience_data = await page.evaluate(r"""() => {
            const experiences = [];
            let currentCompany = "N/A";
            let currentTitle = "N/A";
            let totalExperience = "N/A";

            const allItems = document.querySelectorAll('li.pvs-list__paged-list-item');

            allItems.forEach((item) => {
                try {
                    const sub = item.querySelector('.pvs-entity__sub-components');
                    if (sub) {
                        const companyNameEl = item.querySelector('.hoverable-link-text.t-bold span[aria-hidden="true"]');
                        const companyName = companyNameEl ? companyNameEl.innerText.trim() : "N/A";
                        const pos = sub.querySelectorAll('li.pvs-list__paged-list-item');
                        pos.forEach((pi) => {
                            const titleEl = pi.querySelector('.hoverable-link-text.t-bold span[aria-hidden="true"]');
                            const title = titleEl ? titleEl.innerText.trim() : "N/A";
                            const durationEl = pi.querySelector('span.pvs-entity__caption-wrapper');
                            const duration = durationEl ? durationEl.innerText.trim() : "N/A";
                            const typeEls = pi.querySelectorAll('span.t-14.t-normal span[aria-hidden="true"]');
                            let employmentType = "";
                            for (const el of typeEls) {
                                const t = el.innerText.trim();
                                if (/Full-time|Part-time|Contract|Internship|Freelance|Self-employed|Temporary/i.test(t)) {
                                    employmentType = t;
                                    break;
                                }
                            }
                            experiences.push({ company: companyName, title, duration, employmentType });
                        });
                    } else {
                        const titleEl = item.querySelector('.hoverable-link-text.t-bold span[aria-hidden="true"]');
                        const title = titleEl ? titleEl.innerText.trim() : "N/A";
                        let company = "N/A";
                        const normalSpans = item.querySelectorAll('span.t-14.t-normal span[aria-hidden="true"]');
                        for (const el of normalSpans) {
                            const tx = el.innerText.trim();
                            if (tx && !/(Full-time|Part-time|Contract|Internship|Freelance|Self-employed|Temporary)/i.test(tx) && !/[·]|\d+\s*(yrs?|mos?)/i.test(tx)) {
                                company = tx;
                                break;
                            }
                        }
                        const durationEl = item.querySelector('span.pvs-entity__caption-wrapper');
                        const duration = durationEl ? durationEl.innerText.trim() : "N/A";
                        experiences.push({ company, title, duration, employmentType: "" });
                    }
                } catch (e) { /* skip item */ }
            });

            // Current role detection
            for (const exp of experiences) {
                if (/Present/i.test(exp.duration)) {
                    currentCompany = exp.company;
                    currentTitle = exp.title;
                    break;
                }
            }
            if (currentCompany === "N/A" && experiences.length) {
                currentCompany = experiences[0].company;
                currentTitle = experiences[0].title;
            }

            // Total experience
            let totalYears = 0;
            let totalMonths = 0;
            experiences.forEach(exp => {
                const y = exp.duration.match(/(\d+)\s*yrs?/i);
                const m = exp.duration.match(/(\d+)\s*mos?/i);
                if (y) totalYears += parseInt(y[1]);
                if (m) totalMonths += parseInt(m[1]);
            });
            totalYears += Math.floor(totalMonths / 12);
            totalMonths = totalMonths % 12;
            if (totalYears > 0 || totalMonths > 0) {
                totalExperience = `${totalYears} yrs ${totalMonths} mos`;
            }

            return { experiences, currentCompany, currentTitle, totalExperience };
        }""")

        return experience_data

    except Exception as e:
        print(f"❌ Failed to scrape experience for {profile_url}: {e}")
        return {
            "experiences": [],
            "currentCompany": "N/A",
            "currentTitle": "N/A",
            "totalExperience": "N/A"
        }

# -----------------------
# Scrape Profile basics + experience
# -----------------------
async def scrape_profile(page, profile_url):
    try:
        url = clean_profile_url(profile_url)
        await page.goto(url, timeout=90000)
        await page.wait_for_load_state("domcontentloaded")
        # Try to ensure header visible
        await page.wait_for_selector("h1", timeout=15000)
        await page.evaluate("window.scrollBy(0, document.body.scrollHeight)")
        await page.wait_for_timeout(1500)

        basic_data = await page.evaluate(r"""() => {
            const getText = (selectors) => {
                for (const sel of selectors) {
                    const el = document.querySelector(sel);
                    if (el && el.innerText && el.innerText.trim()) return el.innerText.trim();
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

            // Education (best effort)
            let education = "N/A";

            // Education section selectors (LinkedIn structure alag-alag hota hai)
            const eduSection = document.querySelector("section[id='education'], section[data-section='education'], div[data-view-name='education']");

            if (eduSection) {
                const items = eduSection.querySelectorAll("li span[aria-hidden='true']");
                if (items.length > 0) {
                    // Sirf college/university ka naam filter karke
                    education = Array.from(items)
                        .map(el => el.innerText.trim())
                        .filter(text => text.length > 3 
                            && !text.toLowerCase().includes("intern") 
                            && !text.toLowerCase().includes("engineer") 
                            && !text.toLowerCase().includes("developer")
                            && !text.toLowerCase().includes("kraft") // company names avoid
                        )
                        .slice(0, 1) // 👈 bas pehla college name chahiye
                        .join("");
                }
            }

            return {
                name,
                title,
                location,
                education
            };
        }""")

        # Experience details
        experience_data = await scrape_experience(page, url)

        # Skills details
        skills_data = await scrape_skills(page, url)

        # Format for CSV
        experience_details = []
        for exp in (experience_data.get("experiences") or []):
            detail = f"{exp.get('company','N/A')} | {exp.get('title','N/A')} | {exp.get('duration','N/A')}"
            et = exp.get('employmentType')
            if et:
                detail += f" | {et}"
            experience_details.append(detail)
        experience_details_str = " || ".join(experience_details[:5])  # limit to 5

        # Format skills for CSV
        skills_str = " | ".join(skills_data[:10]) if skills_data else "N/A"  # limit to 10 skills

        # Extract college name from title if present
        title_raw = basic_data.get("title", "N/A")
        education_raw = basic_data.get("education", "N/A")
        # Try to extract college from title
        import re
        college_pattern = r"\b(NIT [A-Za-z]+|DTU \(DCE\) \d{4}|IIT [A-Za-z]+|IIIT [A-Za-z]+|BITS [A-Za-z]+|[A-Za-z ]+ University|[A-Za-z ]+ College)\b"
        college_match = re.search(college_pattern, title_raw)
        college_name = college_match.group(0) if college_match else None
        # Remove college from title
        title_clean = title_raw
        if college_name:
            title_clean = title_clean.replace(college_name, "").replace("|", "").strip()
        # If currently at Gameskraft, append to title
        current_company = experience_data.get("currentCompany", "N/A")
        if current_company and "gameskraft" in current_company.lower():
            if "at gameskraft" not in title_clean.lower():
                title_clean = f"{title_clean} at Gameskraft".strip()
        # Set education field
        education = college_name if college_name else (education_raw if education_raw != "N/A" else "")
        # Clean up N/A values
        def clean_na(val):
            return "" if val == "N/A" else val
        result = {
            "name": clean_na(basic_data.get("name", "N/A")),
            "title": title_clean,
            "location": clean_na(basic_data.get("location", "N/A")),
            "education": education,
            "url": url,
            "total_experience": clean_na(experience_data.get("totalExperience", "N/A")),
            "experience_details": clean_na(experience_details_str),
            "skills": clean_na(skills_str)
        }
        print(f"✅ Scraped {url}: {result['name']}")
        return result

    except Exception as e:
        print(f"❌ Failed to scrape {profile_url}: {e}")
        return {
            "name": "N/A", "title": "N/A", "location": "N/A",
            "education": "N/A", "url": clean_profile_url(profile_url),
            "total_experience": "N/A", "experience_details": "N/A",
            "skills": "N/A"
        }

# -----------------------
# IMPROVED: Collect profile URLs from a Company People page with better pagination and show more handling
# -----------------------
async def collect_profile_urls(page, people_url, limit):
    profile_urls = set()
    print(f"🔍 Starting to collect {limit} profile URLs from: {people_url}")

    # Go to people page
    await page.goto(people_url, timeout=90000)
    await page.wait_for_load_state("domcontentloaded")
    await page.wait_for_timeout(3000)  # Increased wait time

    # More aggressive collection strategy
    max_attempts = 50  # Increased from 10
    attempt = 0
    no_new_profiles_count = 0
    
    while attempt < max_attempts and len(profile_urls) < limit:
        attempt += 1
        previous_count = len(profile_urls)
        
        print(f"🔄 Collection attempt {attempt}/{max_attempts} - Current profiles: {len(profile_urls)}")
        
        # Extended scroll with more patience
        await auto_scroll(page, step=1000, max_rounds=15, wait_ms=800)
        await page.wait_for_timeout(2000)

        # Try to click "Show more results" button if present
        try:
            show_more_selectors = [
                "button[aria-label*='Show more']",
                "button[aria-label*='show more']", 
                "button:has-text('Show more')",
                "button:has-text('show more')",
                "button.artdeco-button--secondary:has-text('Show')",
                ".artdeco-button.artdeco-button--muted.artdeco-button--2.artdeco-button--secondary",
                "button[data-control-name='show_more_results']"
            ]
            
            for selector in show_more_selectors:
                try:
                    show_more_btn = await page.query_selector(selector)
                    if show_more_btn:
                        is_visible = await show_more_btn.is_visible()
                        is_enabled = await show_more_btn.is_enabled()
                        if is_visible and is_enabled:
                            print("🔲 Found and clicking 'Show more results' button...")
                            await show_more_btn.click()
                            await page.wait_for_timeout(3000)  # Wait for new content to load
                            break
                except Exception as e:
                    continue
        except Exception as e:
            pass

        # Try pagination next button with multiple selectors
        try:
            next_button_selectors = [
                "button[aria-label='Next']",
                "button[aria-label='next']",
                "button.artdeco-pagination__button--next:not([disabled])",
                "button:has-text('Next')",
                ".artdeco-pagination__button.artdeco-pagination__button--next",
                "li.artdeco-pagination__indicator--number + li button"
            ]
            
            for selector in next_button_selectors:
                try:
                    next_btn = await page.query_selector(selector)
                    if next_btn:
                        is_disabled = await next_btn.get_attribute('disabled')
                        is_visible = await next_btn.is_visible()
                        if not is_disabled and is_visible:
                            print("➡️ Found and clicking Next button...")
                            await next_btn.click()
                            await page.wait_for_timeout(4000)  # Wait for page to load
                            break
                except Exception:
                    continue
        except Exception:
            pass

        # Collect URLs with more comprehensive selectors
        urls = await page.evaluate(r"""() => {
            // Multiple strategies to find profile links
            const selectors = [
                "a[href*='/in/']",
                "a[data-control-name='people_profile_card_name_link']",
                "a[data-control-name='member_name']", 
                ".org-people-profile-card__profile-title a",
                ".entity-result__title-text a",
                ".reusable-search__result-container a[href*='/in/']"
            ];
            
            let allLinks = [];
            selectors.forEach(selector => {
                const links = Array.from(document.querySelectorAll(selector));
                allLinks = allLinks.concat(links);
            });
            
            const hrefs = allLinks
                .map(a => a.href || a.getAttribute("href") || "")
                .filter(h => h && h.includes("/in/") && 
                        !h.includes("/miniProfile/") && 
                        !h.includes("/company/") &&
                        !h.includes("/school/") &&
                        !h.includes("/feed/"))
                .map(h => h.split('?')[0]); // Remove query params
            
            return [...new Set(hrefs)]; // Remove duplicates
        }""")

        for u in urls:
            if u:
                profile_urls.add(u)

        new_count = len(profile_urls)
        new_profiles_found = new_count - previous_count
        
        print(f"📊 Found {new_profiles_found} new profiles. Total: {new_count}/{limit}")

        # If no new profiles found, increment counter
        if new_profiles_found == 0:
            no_new_profiles_count += 1
        else:
            no_new_profiles_count = 0

        # If we haven't found new profiles in 5 consecutive attempts, try different approach
        if no_new_profiles_count >= 5:
            print("🔄 No new profiles found in recent attempts. Trying different scroll pattern...")
            # Try scrolling to top and back down
            await page.evaluate("window.scrollTo(0, 0)")
            await page.wait_for_timeout(2000)
            await page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
            await page.wait_for_timeout(3000)
            no_new_profiles_count = 0

        # Break if we have enough profiles
        if len(profile_urls) >= limit:
            print(f"✅ Collected enough profiles: {len(profile_urls)}")
            break

        # Random delay to avoid being detected
        await delay(1000 + random.randint(500, 2000))

    # Clean and trim URLs
    cleaned = []
    for u in profile_urls:
        cleaned_url = clean_profile_url(u)
        if cleaned_url and cleaned_url not in cleaned:
            cleaned.append(cleaned_url)

    final_list = cleaned[:limit]
    print(f"🎯 Final collection: {len(final_list)} unique profile URLs")
    
    return final_list

# -----------------------
# Main
# -----------------------
async def main():
    async with async_playwright() as p:
        browser, _, page = await setup_browser(p)

        try:
            limit = int(ask_question("🔢 How many profiles to scrape?: ").strip())
        except Exception:
            limit = 5

        # Fixed Gameskraft people URL
        people_url = "https://www.linkedin.com/company/gameskraft/people/"

        # Collect profile URLs with improved method
        urls = await collect_profile_urls(page, people_url, limit)
        
        if not urls:
            print("⚠️ No profile URLs found. This might be due to:")
            print("   - LinkedIn requiring login")
            print("   - Company page not accessible") 
            print("   - Rate limiting")
            print("ℹ Try scrolling manually in the opened page, then press Enter here.")
            ask_question("➡️ Press Enter to attempt re-collect...")
            urls = await collect_profile_urls(page, people_url, limit)

        if len(urls) < limit:
            print(f"⚠️ Only found {len(urls)} profiles instead of requested {limit}")
            print("   This is normal for smaller companies or restricted access")

        # Scrape profiles
        results = []
        for i, url in enumerate(urls, 1):
            print(f"🔍 Scraping profile {i}/{len(urls)}: {url}")
            try:
                info = await scrape_profile(page, url)
                results.append(info)
                print(f"✅ Successfully scraped: {info['name']}")
            except Exception as e:
                print(f"❌ Failed to scrape {url}: {e}")
                # Add placeholder data for failed scrapes
                results.append({
                    "name": "Failed to scrape", "title": "N/A", "location": "N/A",
                    "education": "N/A", "url": url, "total_experience": "N/A", 
                    "experience_details": "N/A", "skills": "N/A"
                })
            
            # Increased delay between profile scrapes
            await delay(3000 + random.randint(1000, 3000))

        print(f"\n📋 Successfully scraped {len([r for r in results if r['name'] != 'Failed to scrape'])} profiles:")
        for r in results:
            if r['name'] != 'Failed to scrape':
                print(f"   • {r['name']} - {r['title']}")

        save_to_csv(results)
        open_excel(str(output_csv))

        await browser.close()
        print("🏁 Scraping completed!")

if __name__ == "__main__":
    asyncio.run(main())