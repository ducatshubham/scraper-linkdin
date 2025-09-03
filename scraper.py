import asyncio
import json
import csv
import os
import time
import sys
import subprocess
import random
import re
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
        print(f"📊 Opened Excel file.")
    except Exception as e:
        print(f"❌ Could not open Excel: {e}")

async def auto_scroll(page, step=600, max_rounds=30, wait_ms=1500):
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

def is_developer_profile(title: str) -> bool:
    """Check if profile title indicates a software development role."""
    if not title or title == "N/A":
        return False
    
    title_lower = title.lower()
    developer_keywords = [
        "software", "developer", "engineer", "programmer", "backend", "frontend", 
        "full stack", "fullstack", "python", "java", "react", "javascript", 
        "node", "angular", "vue", "devops", "sre", "tech lead", "technical",
        "architect", "senior", "lead", "principal", "staff", "api", "web",
        "mobile", "android", "ios", "flutter", "react native", "ml", "ai",
        "data scientist", "data engineer", "machine learning", "deep learning",
        "cloud", "aws", "azure", "gcp", "kubernetes", "docker", "microservices"
    ]
    
    return any(keyword in title_lower for keyword in developer_keywords)

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
# Scrape Education - NEW DEDICATED FUNCTION
# -----------------------
async def scrape_education(page, profile_url):
    try:
        # Clean URL & extract username
        base_url = clean_profile_url(profile_url)
        if "/in/" not in base_url:
            return ""
        username = base_url.split("/in/")[1].split("/")[0]
        education_url = f"https://www.linkedin.com/in/{username}/details/education/"

        print(f"🎓 Scraping education from: {education_url}")
        await page.goto(education_url, timeout=90000)
        await page.wait_for_timeout(4000)  # Extra wait
        await auto_scroll(page, step=700, max_rounds=15, wait_ms=1200)
        await page.wait_for_timeout(2500)

        # Extract education from education details page
        education = await page.evaluate(r"""() => {
            let collegeName = "";
            
            // Look for education items in the education page
            const eduItems = document.querySelectorAll('li.pvs-list__paged-list-item');
            
            for (const item of eduItems) {
                try {
                    // Look for school/college name - primary method
                    const schoolNameEl = item.querySelector('.hoverable-link-text.t-bold span[aria-hidden="true"]');
                    if (schoolNameEl) {
                        const schoolText = schoolNameEl.innerText.trim();
                        
                        // Filter to ensure it's actually an educational institution
                        if (schoolText && 
                            schoolText.length > 5 && 
                            (schoolText.toLowerCase().includes('university') || 
                             schoolText.toLowerCase().includes('college') || 
                             schoolText.toLowerCase().includes('institute') ||
                             schoolText.includes('IIT') ||
                             schoolText.includes('NIT') ||
                             schoolText.includes('IIIT') ||
                             schoolText.includes('BITS') ||
                             schoolText.toLowerCase().includes('school')) &&
                            !schoolText.toLowerCase().includes('gameskraft') &&
                            !schoolText.toLowerCase().includes('company') &&
                            !schoolText.toLowerCase().includes('pvt') &&
                            !schoolText.toLowerCase().includes('ltd') &&
                            !schoolText.toLowerCase().includes('technologies') &&
                            !schoolText.toLowerCase().includes('solutions')) {
                            
                            collegeName = schoolText;
                            break; // Take the first valid educational institution
                        }
                    }
                    
                    // Fallback: Look in the collapsed text areas within education items
                    const collapsedDiv = item.querySelector('div.inline-show-more-text--is-collapsed');
                    if (collapsedDiv && (!collegeName || collegeName === "")) {
                        const collapsedText = collapsedDiv.innerText.trim();
                        if (collapsedText && 
                            collapsedText.length > 5 &&
                            (collapsedText.toLowerCase().includes('university') || 
                             collapsedText.toLowerCase().includes('college') || 
                             collapsedText.toLowerCase().includes('institute') ||
                             collapsedText.includes('IIT') ||
                             collapsedText.includes('NIT') ||
                             collapsedText.includes('IIIT') ||
                             collapsedText.includes('BITS')) &&
                            !collapsedText.toLowerCase().includes('gameskraft') &&
                            !collapsedText.toLowerCase().includes('company')) {
                            collegeName = collapsedText;
                            break;
                        }
                    }
                    
                    // Another fallback: Look for education text in other spans
                    const allSpans = item.querySelectorAll('span[aria-hidden="true"]');
                    for (const span of allSpans) {
                        const spanText = span.innerText.trim();
                        if (spanText && 
                            spanText.length > 10 &&
                            (spanText.toLowerCase().includes('university') || 
                             spanText.toLowerCase().includes('college') || 
                             spanText.toLowerCase().includes('institute') ||
                             spanText.includes('IIT') ||
                             spanText.includes('NIT') ||
                             spanText.includes('IIIT') ||
                             spanText.includes('BITS')) &&
                            !spanText.toLowerCase().includes('gameskraft') &&
                            !spanText.toLowerCase().includes('company') &&
                            !spanText.toLowerCase().includes('·') &&
                            !spanText.match(/^\d+/)) {
                            collegeName = spanText;
                            break;
                        }
                    }
                    if (collegeName) break;
                    
                } catch (e) {
                    continue;
                }
            }
            
            return collegeName || "";
        }""")

        return education

    except Exception as e:
        print(f"❌ Failed to scrape education for {profile_url}: {e}")
        return ""

# -----------------------
# Scrape Skills - FIXED VERSION
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
        await page.wait_for_timeout(4000)  # Increased delay
        await auto_scroll(page, step=700, max_rounds=20, wait_ms=1200)  # Increased wait
        await page.wait_for_timeout(3000)  # Increased delay

        # FIXED: Extract only actual skills, filter out experience and other text
        skills = await page.evaluate(r"""() => {
            const skillsList = [];
            const seenSkills = new Set();
            
            // Strategy 1: Look for skills in the main skill items
            const skillItems = document.querySelectorAll('li.pvs-list__paged-list-item');
            
            skillItems.forEach((item) => {
                try {
                    // Look for the main skill name in the prominent link text
                    const skillNameEl = item.querySelector('.hoverable-link-text.t-bold span[aria-hidden="true"]');
                    if (skillNameEl) {
                        const skillText = skillNameEl.innerText.trim();
                        
                        // Filter to ensure it's actually a skill name (not experience/company)
                        if (skillText && 
                            skillText.length > 1 && 
                            skillText.length < 50 && // Skills are usually short
                            !skillText.match(/^\d+/) && // Not starting with numbers
                            !skillText.includes('experience') &&
                            !skillText.includes('company') &&
                            !skillText.includes('at ') &&
                            !skillText.includes(' at ') &&
                            !skillText.includes('|') &&
                            !skillText.includes('endorsement') &&
                            !skillText.includes('connection') &&
                            !skillText.toLowerCase().includes('baazi') &&
                            !skillText.toLowerCase().includes('makemytrip') &&
                            !skillText.toLowerCase().includes('gameskraft') &&
                            !skillText.toLowerCase().includes('engineer') &&
                            !skillText.toLowerCase().includes('developer') &&
                            !skillText.toLowerCase().includes('software') &&
                            !skillText.toLowerCase().includes('senior') &&
                            !skillText.toLowerCase().includes('passed') &&
                            !skillText.toLowerCase().includes('linkedin') &&
                            !skillText.toLowerCase().includes('skill assessment') &&
                            skillText !== '·') {
                            
                            if (!seenSkills.has(skillText.toLowerCase())) {
                                skillsList.push(skillText);
                                seenSkills.add(skillText.toLowerCase());
                            }
                        }
                    }
                } catch (e) {
                    // Continue if there's an error with this item
                }
            });

            // Strategy 2: Fallback - look for skills in other span elements if we don't have many
            if (skillsList.length < 5) {
                const allSpans = document.querySelectorAll('span[aria-hidden="true"]');
                allSpans.forEach((span) => {
                    const skillText = span.innerText.trim();
                    
                    // More strict filtering for fallback
                    if (skillText && 
                        skillText.length > 2 && 
                        skillText.length < 30 &&
                        !skillText.match(/^\d+/) &&
                        !skillText.includes('experience') &&
                        !skillText.includes('at ') &&
                        !skillText.includes('|') &&
                        !skillText.includes('endorsement') &&
                        !skillText.includes('connection') &&
                        !skillText.includes('·') &&
                        !skillText.toLowerCase().includes('baazi') &&
                        !skillText.toLowerCase().includes('makemytrip') &&
                        !skillText.toLowerCase().includes('company') &&
                        !skillText.toLowerCase().includes('passed') &&
                        !skillText.toLowerCase().includes('linkedin') &&
                        !skillText.toLowerCase().includes('assessment') &&
                        // Check if it looks like a technical skill
                        (skillText.toLowerCase().includes('.js') ||
                         skillText.toLowerCase().includes('script') ||
                         skillText.toLowerCase().includes('css') ||
                         skillText.toLowerCase().includes('html') ||
                         skillText.toLowerCase().includes('python') ||
                         skillText.toLowerCase().includes('java') ||
                         skillText.toLowerCase().includes('react') ||
                         skillText.toLowerCase().includes('node') ||
                         skillText.toLowerCase().includes('sql') ||
                         skillText.toLowerCase().includes('git') ||
                         skillText.toLowerCase().includes('data') ||
                         skillText.toLowerCase().includes('programming') ||
                         skillText.toLowerCase().includes('development') ||
                         skillText.toLowerCase().includes('bootstrap') ||
                         skillText.toLowerCase().includes('jquery') ||
                         skillText.toLowerCase().includes('matlab') ||
                         skillText.toLowerCase().includes('arduino') ||
                         skillText.toLowerCase().includes('fpga') ||
                         skillText.toLowerCase().includes('express') ||
                         skillText.toLowerCase().includes('redux') ||
                         skillText.toLowerCase().includes('typescript') ||
                         skillText.toLowerCase().includes('webpack') ||
                         skillText.toLowerCase().includes('mobx') ||
                         skillText.toLowerCase().includes('vite') ||
                         skillText.toLowerCase().includes('electron'))) {
                        
                        if (!seenSkills.has(skillText.toLowerCase()) && skillsList.length < 50) {
                            skillsList.push(skillText);
                            seenSkills.add(skillText.toLowerCase());
                        }
                    }
                });
            }

            return skillsList;
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
        await page.wait_for_timeout(4000)
        await auto_scroll(page, step=700, max_rounds=20, wait_ms=1200)
        await page.wait_for_timeout(3000)

        # Updated experience extraction based on actual LinkedIn HTML structure
        experience_data = await page.evaluate(r"""() => {
            const experiences = [];
            let currentCompany = "N/A";
            let currentTitle = "N/A";
            let totalExperience = "N/A";

            // Get all experience items - updated selector
            const experienceItems = document.querySelectorAll('li.pvs-list__paged-list-item');
            
            experienceItems.forEach((item) => {
                try {
                    let title = "N/A";
                    let company = "N/A";
                    let duration = "N/A";
                    let employmentType = "";
                    
                    // Strategy 1: Look for title in the main clickable area
                    const titleSelectors = [
                        'div.display-flex.align-items-center span[aria-hidden="true"]',
                        'div.hoverable-link-text.t-bold span[aria-hidden="true"]',
                        '.pvs-entity__summary-info .hoverable-link-text span[aria-hidden="true"]',
                        'a[data-field*="experience"] span[aria-hidden="true"]',
                        '.t-bold span[aria-hidden="true"]'
                    ];
                    
                    for (const selector of titleSelectors) {
                        const titleEl = item.querySelector(selector);
                        if (titleEl && titleEl.textContent && titleEl.textContent.trim()) {
                            const titleText = titleEl.textContent.trim();
                            // Skip if it looks like a company name or duration
                            if (!titleText.match(/\d+\s*(yr|mo|year|month)/i) && 
                                titleText.length < 100 && 
                                !titleText.includes('·')) {
                                title = titleText;
                                break;
                            }
                        }
                    }
                    
                    // Strategy 2: Look for company name
                    const companySelectors = [
                        '.pvs-entity__sub-components .hoverable-link-text span[aria-hidden="true"]',
                        '.t-14.t-normal span[aria-hidden="true"]',
                        '.pvs-entity__summary-info .t-14 span[aria-hidden="true"]'
                    ];
                    
                    for (const selector of companySelectors) {
                        const companyEl = item.querySelector(selector);
                        if (companyEl && companyEl.textContent && companyEl.textContent.trim()) {
                            const companyText = companyEl.textContent.trim();
                            // Skip employment types and durations
                            if (!companyText.match(/Full-time|Part-time|Contract|Internship|Freelance|Self-employed|Temporary|\d+\s*(yr|mo)/i) &&
                                !companyText.includes('·') &&
                                companyText.length > 2) {
                                company = companyText;
                                break;
                            }
                        }
                    }
                    
                    // Strategy 3: Look for duration
                    const durationSelectors = [
                        '.pvs-entity__caption-wrapper',
                        '.t-12.t-normal span[aria-hidden="true"]',
                        '.pvs-entity__sub-components .t-12 span[aria-hidden="true"]'
                    ];
                    
                    for (const selector of durationSelectors) {
                        const durationEl = item.querySelector(selector);
                        if (durationEl && durationEl.textContent && durationEl.textContent.trim()) {
                            const durationText = durationEl.textContent.trim();
                            if (durationText.match(/\d+\s*(yr|mo|year|month)|Present|Current/i)) {
                                duration = durationText;
                                break;
                            }
                        }
                    }
                    
                    // Strategy 4: Look for employment type
                    const employmentSelectors = [
                        '.t-14.t-normal span[aria-hidden="true"]',
                        '.pvs-entity__sub-components span[aria-hidden="true"]'
                    ];
                    
                    for (const selector of employmentSelectors) {
                        const elements = item.querySelectorAll(selector);
                        for (const el of elements) {
                            const text = el.textContent ? el.textContent.trim() : '';
                            if (text.match(/Full-time|Part-time|Contract|Internship|Freelance|Self-employed|Temporary/i)) {
                                employmentType = text;
                                break;
                            }
                        }
                        if (employmentType) break;
                    }
                    
                    // Alternative strategy: Check if this is a multi-position company
                    const subComponents = item.querySelector('.pvs-entity__sub-components');
                    if (subComponents) {
                        // This is a company with multiple positions
                        const companyNameEl = item.querySelector('.hoverable-link-text.t-bold span[aria-hidden="true"]');
                        const companyName = companyNameEl ? companyNameEl.textContent.trim() : "N/A";
                        
                        // Get all positions under this company
                        const positions = subComponents.querySelectorAll('li.pvs-list__paged-list-item');
                        positions.forEach(position => {
                            try {
                                const posTitle = position.querySelector('.hoverable-link-text.t-bold span[aria-hidden="true"]');
                                const posDuration = position.querySelector('.pvs-entity__caption-wrapper');
                                const posType = position.querySelector('.t-14.t-normal span[aria-hidden="true"]');
                                
                                experiences.push({
                                    company: companyName,
                                    title: posTitle ? posTitle.textContent.trim() : "N/A",
                                    duration: posDuration ? posDuration.textContent.trim() : "N/A",
                                    employmentType: posType ? posType.textContent.trim() : ""
                                });
                            } catch (e) {
                                console.log('Error parsing position:', e);
                            }
                        });
                    } else {
                        // Single position entry
                        if (title !== "N/A" || company !== "N/A") {
                            experiences.push({
                                company: company,
                                title: title,
                                duration: duration,
                                employmentType: employmentType
                            });
                        }
                    }
                    
                } catch (e) {
                    console.log('Error parsing experience item:', e);
                }
            });

            // Remove duplicates and clean up
            const uniqueExperiences = [];
            const seen = new Set();
            
            experiences.forEach(exp => {
                const key = `${exp.company}-${exp.title}-${exp.duration}`;
                if (!seen.has(key) && exp.title !== "N/A" && exp.company !== "N/A") {
                    seen.add(key);
                    uniqueExperiences.push(exp);
                }
            });

            // Current role detection - look for "Present" or "Current"
            for (const exp of uniqueExperiences) {
                if (exp.duration && /Present|Current/i.test(exp.duration)) {
                    currentCompany = exp.company;
                    currentTitle = exp.title;
                    break;
                }
            }
            
            // Fallback: use most recent (first) experience
            if (currentCompany === "N/A" && uniqueExperiences.length > 0) {
                currentCompany = uniqueExperiences[0].company;
                currentTitle = uniqueExperiences[0].title;
            }

            // Calculate total experience
            let totalYears = 0;
            let totalMonths = 0;
            
            uniqueExperiences.forEach(exp => {
                if (exp.duration) {
                    const yearMatch = exp.duration.match(/(\d+)\s*(yr|year)s?/i);
                    const monthMatch = exp.duration.match(/(\d+)\s*(mo|month)s?/i);
                    
                    if (yearMatch) {
                        totalYears += parseInt(yearMatch[1]);
                    }
                    if (monthMatch) {
                        totalMonths += parseInt(monthMatch[1]);
                    }
                }
            });
            
            // Convert months to years
            totalYears += Math.floor(totalMonths / 12);
            totalMonths = totalMonths % 12;
            
            if (totalYears > 0 || totalMonths > 0) {
                totalExperience = `${totalYears} yrs ${totalMonths} mos`;
            }

            return {
                experiences: uniqueExperiences,
                currentCompany: currentCompany,
                currentTitle: currentTitle,
                totalExperience: totalExperience
            };
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
        await page.wait_for_timeout(4000)  # Increased delay

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

            return {
                name,
                title,
                location
            };
        }""")

        # Education details - NEW: Get from dedicated education page
        education_data = await scrape_education(page, url)

        # Experience details
        experience_data = await scrape_experience(page, url)

        # Skills details - FIXED VERSION
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

        # FIXED: Format ALL skills for CSV without limit
        skills_str = " | ".join(skills_data) if skills_data else "N/A"  # NO LIMIT - show all skills

        # Extract college name from title if present (as fallback)
        title_raw = basic_data.get("title", "N/A")
        
        # Try to extract college from title as fallback
        college_pattern = r"\b(NIT [A-Za-z]+|DTU \(DCE\) \d{4}|IIT [A-Za-z]+|IIIT [A-Za-z]+|BITS [A-Za-z]+|[A-Za-z ]+ University|[A-Za-z ]+ College|[A-Za-z ]+ Institute of Technology)\b"
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
        
        # Set education field - use the dedicated education scraping result
        education = education_data if education_data else ""
        
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
        
        # Log if this is a developer profile (for priority, but collect all)
        if is_developer_profile(result['title']):
            print(f"👨‍💻 Developer found: {result['name']} - {result['title']}")
        else:
            print(f"✅ Scraped {url}: {result['name']} - {result['title']}")
            
        # Display all skills as headings
        if skills_data and len(skills_data) > 0:
            print(f"🔧 Skills found for {result['name']}:")
            for skill in skills_data:  # Show ALL skills as headings
                print(f"   • {skill}")
            
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
# MODIFIED: Collect ALL profile URLs with developer priority
# -----------------------
async def collect_profile_urls(page, people_url, limit):
    profile_urls = set()
    developer_profiles = set()  # Priority collection for developers (but collect all)
    print(f"🔍 Starting to collect {limit} profile URLs (prioritizing developers but collecting all) from: {people_url}")

    # Go to people page
    await page.goto(people_url, timeout=90000)
    await page.wait_for_load_state("domcontentloaded")
    await page.wait_for_timeout(5000)  # Increased wait time

    # More aggressive collection strategy
    max_attempts = 60  # Increased attempts
    attempt = 0
    no_new_profiles_count = 0
    
    while attempt < max_attempts and len(profile_urls) < limit:
        attempt += 1
        previous_count = len(profile_urls)
        previous_developer_count = len(developer_profiles)
        
        print(f"🔄 Collection attempt {attempt}/{max_attempts} - Developers: {len(developer_profiles)}, Total: {len(profile_urls)}")
        
        # Extended scroll with more patience
        await auto_scroll(page, step=1200, max_rounds=18, wait_ms=1500)  # Increased params
        await page.wait_for_timeout(4000)  # Increased delay

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
                            await page.wait_for_timeout(5000)  # Increased wait for new content
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
                            await page.wait_for_timeout(6000)  # Increased wait for page to load
                            break
                except Exception:
                    continue
        except Exception:
            pass

        # Collect ALL URLs (no filtering) but identify developers for priority
        url_data = await page.evaluate(r"""() => {
            // Multiple strategies to find profile links with titles
            const profileData = [];
            
            // Strategy 1: Profile cards with titles
            const profileCards = document.querySelectorAll('.org-people-profile-card, .entity-result, .reusable-search__result-container');
            profileCards.forEach(card => {
                const link = card.querySelector("a[href*='/in/']");
                if (link) {
                    const href = link.href || link.getAttribute("href") || "";
                    if (href && href.includes("/in/") && 
                        !href.includes("/miniProfile/") && 
                        !href.includes("/company/") &&
                        !href.includes("/school/") &&
                        !href.includes("/feed/")) {
                        
                        // Extract title from card
                        let title = "";
                        const titleSelectors = [
                            '.org-people-profile-card__profile-info h3',
                            '.entity-result__primary-subtitle',
                            '.subline-level-1',
                            '.t-14.t-normal',
                            '[data-anonymize="title"]',
                            '.org-people-profile-card__profile-info .subline-level-1'
                        ];
                        
                        for (const selector of titleSelectors) {
                            const titleEl = card.querySelector(selector);
                            if (titleEl && titleEl.innerText.trim()) {
                                title = titleEl.innerText.trim();
                                break;
                            }
                        }
                        
                        profileData.push({
                            url: href.split('?')[0],
                            title: title
                        });
                    }
                }
            });
            
            // Strategy 2: Direct links (fallback)
            if (profileData.length === 0) {
                const directLinks = document.querySelectorAll("a[href*='/in/']");
                directLinks.forEach(link => {
                    const href = link.href || link.getAttribute("href") || "";
                    if (href && href.includes("/in/") && 
                        !href.includes("/miniProfile/") && 
                        !href.includes("/company/") &&
                        !href.includes("/school/") &&
                        !href.includes("/feed/")) {
                        
                        profileData.push({
                            url: href.split('?')[0],
                            title: ""
                        });
                    }
                });
            }
            
            return profileData;
        }""")

        # Add ALL profiles, but identify developers for priority
        for data in url_data:
            url = data['url']
            title = data['title']
            
            if url:
                profile_urls.add(url)
                
                # Check if this looks like a developer profile (for priority, but collect all)
                if is_developer_profile(title):
                    developer_profiles.add(url)
                    print(f"👨‍💻 Found developer: {title}")

        new_count = len(profile_urls)
        new_developer_count = len(developer_profiles)
        new_profiles_found = new_count - previous_count
        new_developers_found = new_developer_count - previous_developer_count
        
        print(f"📊 Found {new_profiles_found} new profiles ({new_developers_found} developers). Developers: {new_developer_count}, Total: {new_count}")

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
            await page.wait_for_timeout(4000)  # Increased delay
            await page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
            await page.wait_for_timeout(5000)  # Increased delay
            no_new_profiles_count = 0

        # Continue until we have enough total profiles
        if len(profile_urls) >= limit:
            print(f"✅ Collected enough profiles: {len(profile_urls)}")
            break

        # Random delay to avoid being detected - increased range
        await delay(4000 + random.randint(3000, 6000))

    # Prioritize developer profiles, fill remaining with others
    final_list = list(developer_profiles)[:limit]
    
    # If we need more profiles, add non-developer ones
    if len(final_list) < limit:
        remaining_needed = limit - len(final_list)
        other_profiles = [url for url in profile_urls if url not in developer_profiles]
        final_list.extend(other_profiles[:remaining_needed])

    print(f"🎯 Final collection: {len(final_list)} profiles ({len([url for url in final_list if url in developer_profiles])} developers)")
    
    return final_list

# -----------------------
# Main execution function
# -----------------------
async def main():
    async with async_playwright() as p:
        browser, context, page = await setup_browser(p)

        try:
            limit = int(ask_question("🔢 How many profiles to scrape?: ").strip())
        except Exception:
            limit = 5

        # Fixed Gameskraft people URL
        people_url = "https://www.linkedin.com/company/mobile-premier-league/people/"

        # Collect profile URLs with improved method (prioritizing developers but collecting all)
        urls = await collect_profile_urls(page, people_url, limit)
        
        if not urls:
            print("❌ No profile URLs found. Exiting.")
            await browser.close()
            return

        print(f"🎯 Starting to scrape {len(urls)} profiles...")
        results = []
        
        for i, url in enumerate(urls, 1):
            print(f"\n🔍 [{i}/{len(urls)}] Scraping: {url}")
            try:
                profile_data = await scrape_profile(page, url)
                results.append(profile_data)
                
                # Random delay between profiles to avoid detection
                if i < len(urls):  # Don't delay after the last profile
                    delay_time = 5000 + random.randint(2000, 8000)
                    print(f"⏳ Waiting {delay_time/1000:.1f}s before next profile...")
                    await delay(delay_time)
                    
            except Exception as e:
                print(f"❌ Failed to scrape profile {url}: {e}")
                # Add a placeholder entry
                results.append({
                    "name": "Failed to scrape", 
                    "title": "N/A", 
                    "location": "N/A",
                    "education": "N/A", 
                    "url": url,
                    "total_experience": "N/A", 
                    "experience_details": "N/A",
                    "skills": "N/A"
                })

        # Save results to CSV
        if results:
            save_to_csv(results)
            open_excel(output_csv)
            
            # Summary statistics
            developer_count = sum(1 for r in results if is_developer_profile(r.get('title', '')))
            print(f"\n🎉 Scraping completed!")
            print(f"📊 Total profiles: {len(results)}")
            print(f"👨‍💻 Developers found: {developer_count}")
            print(f"📁 Results saved to: {output_csv}")
        else:
            print("❌ No data to save.")

        await browser.close()

# -----------------------
# Entry point
# -----------------------
if __name__ == "__main__":
    print("🚀 LinkedIn Profile Scraper for Gameskraft")
    print("=" * 50)
    
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print("\n⏹️ Scraping interrupted by user.")
    except Exception as e:
        print(f"❌ Unexpected error: {e}")
    
    print("\n👋 Thanks for using the LinkedIn scraper!")