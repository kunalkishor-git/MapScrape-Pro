import re
import time
import random
import pandas as pd
import requests
from flask import Flask, render_template, request, send_file, jsonify

from selenium import webdriver
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from bs4 import BeautifulSoup
from urllib.parse import urlparse, urljoin

app = Flask(__name__)

# ─────────────────────────────────────────────
#  CONSTANTS
# ─────────────────────────────────────────────

SOCIAL_DOMAINS = {
    "facebook":  "facebook.com",
    "instagram": "instagram.com",
    "twitter":   "twitter.com",
    "x":         "x.com",
    "linkedin":  "linkedin.com",
    "youtube":   "youtube.com",
    "tiktok":    "tiktok.com",
    "pinterest": "pinterest.com",
}

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                  "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/121.0.0.0 Safari/537.36"
}


# ─────────────────────────────────────────────
#  SHARED HELPERS
# ─────────────────────────────────────────────

def build_driver(headless=True):
    opts = Options()
    if headless:
        opts.add_argument("--headless=new")
    opts.add_argument("--no-sandbox")
    opts.add_argument("--disable-dev-shm-usage")
    opts.add_argument("--window-size=1920,1080")
    opts.add_argument("--disable-blink-features=AutomationControlled")
    opts.add_experimental_option("excludeSwitches", ["enable-automation"])
    opts.add_argument(
        "user-agent=Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/121.0.0.0 Safari/537.36"
    )
    return webdriver.Chrome(options=opts)


def extract_emails(text):
    emails = set(re.findall(r'[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}', text))
    filtered = {e for e in emails if not any(x in e.lower() for x in
                ['@2x', '.png', '.jpg', '.gif', 'sentry', 'example', 'wixpress', 'schema'])}
    return ", ".join(filtered) if filtered else "N/A"


def extract_phone(soup):
    tel_link = soup.find("a", href=re.compile(r'^tel:'))
    if tel_link:
        return tel_link["href"].replace("tel:", "").strip()
    m = re.search(
        r'(\+?\d{1,3}[\s\-.]?)?\(?\d{3}\)?[\s\-.]?\d{3}[\s\-.]?\d{4}',
        soup.get_text(separator=' ')
    )
    return m.group().strip() if m else "N/A"


def get_emails_from_url(url):
    if not url or url == "N/A" or "google.com" in url:
        return "N/A"
    try:
        resp = requests.get(url, timeout=10, headers=HEADERS)
        return extract_emails(resp.text)
    except:
        return "N/A"


# ─────────────────────────────────────────────
#  SOCIAL MEDIA HELPER
# ─────────────────────────────────────────────

def find_social_links(soup, base_url):
    """
    Scan all <a href> tags on the page and return a dict of
    { 'facebook': 'https://...', 'instagram': 'https://...', ... }
    """
    found = {}
    for a in soup.find_all("a", href=True):
        href = a["href"].strip()
        # Resolve relative URLs
        if href.startswith("/"):
            href = urljoin(base_url, href)
        for platform, domain in SOCIAL_DOMAINS.items():
            if domain in href and platform not in found:
                # Filter out generic homepage links like facebook.com/sharer or just facebook.com
                if len(href.split(domain)[-1].strip("/")) > 2:
                    found[platform] = href
    return found


def scrape_social_contacts(social_links, driver):
    """
    Visit each social media link and try to extract phone/email from the public page.
    Returns dict: { 'facebook_url': ..., 'facebook_email': ..., 'facebook_phone': ..., ... }
    """
    result = {}
    for platform, url in social_links.items():
        # Save the URL
        result[f"{platform}_url"] = url

        # Only scrape platforms that expose public contact info without login
        # Facebook pages, LinkedIn company pages, and YouTube channels sometimes show it
        try:
            driver.get(url)
            time.sleep(2.5)
            soup = BeautifulSoup(driver.page_source, "html.parser")
            page_text = soup.get_text(separator=' ')

            email = extract_emails(page_text)
            phone = extract_phone(soup)

            if email != "N/A":
                result[f"{platform}_email"] = email
            if phone != "N/A":
                result[f"{platform}_phone"] = phone

        except Exception as e:
            print(f"    ✗ Could not scrape {platform} ({url}): {e}")

    return result


# ─────────────────────────────────────────────
#  FULL CONTACT EXTRACTION FROM ONE WEBSITE
# ─────────────────────────────────────────────

def extract_all_contacts(site_url, scrape_social=False, driver=None):
    """
    Visit a website, extract email/phone, find social links,
    optionally visit each social page and extract further contact info.
    Returns a flat dict of all contact data.
    """
    contacts = {
        "email":   "N/A",
        "phone":   "N/A",
    }
    social_data = {}

    try:
        time.sleep(random.uniform(0.8, 2.0))
        resp      = requests.get(site_url, timeout=10, headers=HEADERS)
        page_soup = BeautifulSoup(resp.text, "html.parser")

        contacts["email"] = extract_emails(resp.text)
        contacts["phone"] = extract_phone(page_soup)

        if scrape_social:
            social_links = find_social_links(page_soup, site_url)
            print(f"    Social links found: {list(social_links.keys())}")

            if social_links and driver:
                social_data = scrape_social_contacts(social_links, driver)
            elif social_links:
                # Just save URLs without visiting
                for platform, url in social_links.items():
                    social_data[f"{platform}_url"] = url

    except Exception as e:
        print(f"    ✗ Could not fetch {site_url}: {e}")

    return {**contacts, **social_data}


# ─────────────────────────────────────────────
#  MODE 1 — GOOGLE MAPS
# ─────────────────────────────────────────────

def scrape_google_maps(keyword, location, category, max_results=20, scrape_social=False):
    query  = f"{keyword} {category} {location}".strip()
    url    = f"https://www.google.com/maps/search/{query.replace(' ', '+')}"
    driver = build_driver(headless=True)
    results = []

    try:
        driver.get(url)
        WebDriverWait(driver, 20).until(
            EC.presence_of_element_located((By.XPATH, '//div[@role="feed"]'))
        )

        feed = driver.find_element(By.XPATH, '//div[@role="feed"]')
        seen, unique_hrefs = set(), []
        scroll_attempts = 0
        max_scrolls = max_results + 10

        print(f"[Maps] Scrolling to find {max_results} listings...")
        while len(unique_hrefs) < max_results and scroll_attempts < max_scrolls:
            driver.execute_script("arguments[0].scrollTop += 1500", feed)
            time.sleep(1.5)
            links = driver.find_elements(
                By.XPATH, '//div[@role="feed"]//a[contains(@href, "/maps/place/")]'
            )
            for link in links:
                href = link.get_attribute("href")
                if href and href not in seen:
                    seen.add(href)
                    unique_hrefs.append(href)
            scroll_attempts += 1

        print(f"[Maps] Found {len(unique_hrefs)} listings. Processing top {max_results}...")

        for href in unique_hrefs[:max_results]:
            try:
                driver.get(href)
                WebDriverWait(driver, 12).until(
                    EC.presence_of_element_located((By.TAG_NAME, "h1"))
                )
                time.sleep(1.5)
                soup = BeautifulSoup(driver.page_source, "html.parser")

                # Name
                h1   = soup.find("h1")
                name = h1.get_text(strip=True) if h1 else "N/A"

                # Phone from Maps detail panel
                phone     = "N/A"
                phone_btn = soup.find("button", attrs={"aria-label": re.compile(r"^Phone:", re.I)})
                if phone_btn:
                    phone = phone_btn["aria-label"].replace("Phone:", "").strip()
                else:
                    phone = extract_phone(soup)

                # Website from Maps detail panel
                website    = "N/A"
                web_anchor = soup.find("a", attrs={"aria-label": re.compile(r"website", re.I), "href": True})
                if web_anchor:
                    website = web_anchor["href"]
                else:
                    for a in soup.find_all("a", href=True):
                        h = a["href"]
                        if h.startswith("http") and "google.com" not in h and "goo.gl" not in h:
                            website = h
                            break

                row = {"Business Name": name, "Phone": phone, "Website": website, "Email": "N/A"}

                # Deep contact extraction from website + social
                if website != "N/A":
                    contacts = extract_all_contacts(website, scrape_social=scrape_social, driver=driver if scrape_social else None)
                    row["Email"] = contacts.get("email", "N/A")
                    if phone == "N/A":
                        row["Phone"] = contacts.get("phone", "N/A")
                    # Merge social columns
                    for k, v in contacts.items():
                        if k not in ("email", "phone"):
                            row[k] = v

                if name != "N/A":
                    results.append(row)
                    social_cols = {k: v for k, v in row.items() if "_url" in k}
                    print(f"  ✓ {name} | {row['Phone']} | {row['Email']} | socials: {list(social_cols.keys())}")

            except Exception as e:
                print(f"  ✗ Skipping listing: {e}")

        df = pd.DataFrame(results)
        return df.drop_duplicates(subset=["Business Name"]) if not df.empty else df

    finally:
        driver.quit()


# ─────────────────────────────────────────────
#  MODE 2 — GOOGLE SEARCH
# ─────────────────────────────────────────────

def scrape_google_search(query, tags=None, num_pages=2, scrape_social=False):
    tag_list   = [t.strip() for t in (tags or []) if t.strip()]
    tag_str    = " ".join(f'"{t}"' for t in tag_list) if tag_list else ""
    full_query = f"{query} {tag_str}".strip()

    print(f"[Search] Query: {full_query} | Pages: {num_pages} | Social: {scrape_social}")

    driver  = build_driver(headless=True)
    results = []
    visited = set()

    try:
        for page in range(num_pages):
            start      = page * 10
            search_url = f"https://www.google.com/search?q={full_query.replace(' ', '+')}&start={start}"
            driver.get(search_url)

            WebDriverWait(driver, 15).until(
                EC.presence_of_element_located((By.ID, "search"))
            )
            time.sleep(2)
            soup = BeautifulSoup(driver.page_source, "html.parser")

            result_blocks = []
            for a_tag in soup.find_all("a", href=True):
                h3_tag = a_tag.find("h3")
                if h3_tag:
                    href = a_tag["href"]
                    if href.startswith("http") and "google.com" not in href and "goo.gl" not in href:
                        result_blocks.append({"anchor": a_tag, "h3": h3_tag})

            print(f"[Search] Page {page+1}: {len(result_blocks)} valid links found.")

            for item in result_blocks:
                try:
                    title    = item["h3"].get_text(strip=True)
                    site_url = item["anchor"]["href"]

                    if site_url in visited:
                        continue
                    visited.add(site_url)

                    # Snippet
                    snippet      = "N/A"
                    parent_block = item["anchor"].find_parent("div", class_="g")
                    if not parent_block:
                        parent_block = item["anchor"].parent.parent.parent
                    if parent_block:
                        for sel in ["div[data-sncf]", "div.VwiC3b", "span.aCOpRe", "div.Uroaid"]:
                            el = parent_block.select_one(sel)
                            if el:
                                snippet = el.get_text(strip=True)
                                break

                    print(f"  → Visiting: {site_url}")

                    contacts = extract_all_contacts(
                        site_url,
                        scrape_social=scrape_social,
                        driver=driver if scrape_social else None
                    )

                    page_text    = ""
                    try:
                        resp      = requests.get(site_url, timeout=8, headers=HEADERS)
                        page_text = BeautifulSoup(resp.text, "html.parser").get_text(separator=' ').lower()
                    except:
                        pass

                    matched_tags = [t for t in tag_list if t.lower() in page_text] if page_text else []
                    tags_col     = ", ".join(matched_tags) if matched_tags else ("N/A" if tag_list else "")

                    row = {
                        "Title":   title,
                        "Website": site_url,
                        "Phone":   contacts.get("phone", "N/A"),
                        "Email":   contacts.get("email", "N/A"),
                        "Snippet": snippet,
                    }
                    if tag_list:
                        row["Tags Matched"] = tags_col

                    # Merge social columns
                    for k, v in contacts.items():
                        if k not in ("email", "phone"):
                            row[k] = v

                    results.append(row)
                    social_keys = [k for k in row if "_url" in k]
                    print(f"  ✓ {title} | {row['Phone']} | {row['Email']} | socials: {social_keys}")

                except Exception as e:
                    print(f"  ✗ Skipping block: {e}")

        df = pd.DataFrame(results)
        return df.drop_duplicates(subset=["Website"]) if not df.empty else df

    finally:
        driver.quit()


# ─────────────────────────────────────────────
#  FLASK ROUTES
# ─────────────────────────────────────────────

@app.route("/")
def index():
    return render_template("index.html")


@app.route("/scrape", methods=["POST"])
def run_scrape():
    data         = request.json
    mode         = data.get("mode", "maps")
    scrape_social = data.get("scrape_social", False)

    if mode == "maps":
        max_res = max(1, int(data.get("max_results", 20)))
        df = scrape_google_maps(
            data.get("keyword", ""),
            data.get("location", ""),
            data.get("category", ""),
            max_results=max_res,
            scrape_social=scrape_social,
        )
    else:
        query = data.get("search_query", "").strip()
        tags  = [t for t in data.get("tags", []) if t.strip()]
        pages = max(1, min(int(data.get("pages", 2)), 5))
        if not query:
            return jsonify({"status": "no_data"})
        df = scrape_google_search(
            query,
            tags=tags,
            num_pages=pages,
            scrape_social=scrape_social,
        )

    if df is None or df.empty:
        return jsonify({"status": "no_data"})

    df.to_csv("results.csv", index=False)
    return jsonify({"status": "success", "count": len(df)})


@app.route("/download")
def download():
    return send_file("results.csv", as_attachment=True)


if __name__ == "__main__":
    app.run(debug=True)
