# source.py - News Discovery Module (News Scout) - Free AI V1
# ==========================================================

import json
import os
import time
from datetime import datetime, timedelta
import requests
from bs4 import BeautifulSoup
from dotenv import load_dotenv
from google import genai

from config import (
    RICEONLINE_URL,
    OUTPUT_SOURCE_FILE,
    BLOCKED_DOMAINS,
    BLOCKED_EXTENSIONS,
    DATE_RESTRICT_DAYS,
    GOOGLE_API_DELAY,
    GOOGLE_API_RESULTS,
    GEMINI_PRIMARY_MODEL,
    USER_AGENT,
)
from utils import (
    logger,
    parse_date_flexible,
    is_blocked_domain,
    calculate_lookback_days,
    format_duration,
    gemini_limiter,
    is_valid_url,
    resolve_google_redirect,
)

# Load environment variables
# Look for .env first in current folder, then parent
load_dotenv()
if not os.getenv("GEMINI_API_KEY"):
    load_dotenv("../.env")

# API Keys
API_KEY = os.getenv("GOOGLE_SEARCH_API_KEY")
SEARCH_ENGINE_ID = os.getenv("GOOGLE_SEARCH_CX")
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY")

# Configure Gemini client (new google.genai SDK)
gemini_client = None
if GEMINI_API_KEY:
    try:
        # Strip quotes if they were added in .env file
        clean_key = GEMINI_API_KEY.strip().strip('"').strip("'")
        gemini_client = genai.Client(api_key=clean_key)
        logger.info(f"✨ Gemini AI configured successfully with model: {GEMINI_PRIMARY_MODEL}")
    except Exception as e:
        logger.error(f"Failed to configure Gemini: {e}")
        gemini_client = None

# Current year for filtering old URLs
CURRENT_YEAR = datetime.now().year

# --- API QUOTA TRACKING ---
GOOGLE_API_DAILY_LIMIT = 100
API_CALL_COUNT = 0

# --- DUPLICATE DETECTION ---
HISTORY_FILE = 'processed_history.json'


def _load_history():
    """Load previously processed headlines to detect duplicates across runs."""
    if os.path.exists(HISTORY_FILE):
        try:
            with open(HISTORY_FILE, 'r', encoding='utf-8') as f:
                return json.load(f)
        except (json.JSONDecodeError, IOError):
            pass
    return {"processed_headlines": [], "processed_urls": []}


def _save_history(history):
    """Save processed headlines/URLs history to file."""
    # Keep only last 500 entries to prevent file from growing forever
    history["processed_headlines"] = history["processed_headlines"][-500:]
    history["processed_urls"] = history["processed_urls"][-500:]
    try:
        with open(HISTORY_FILE, 'w', encoding='utf-8') as f:
            json.dump(history, f, indent=2, ensure_ascii=False)
    except IOError as e:
        logger.warning(f"Failed to save history: {e}")


def _call_google_api_with_retry(params, max_retries=3):
    """Call Google Custom Search API with retry logic."""
    global API_CALL_COUNT
    api_url = "https://www.googleapis.com/customsearch/v1"
    
    for retry in range(max_retries):
        try:
            if API_CALL_COUNT >= GOOGLE_API_DAILY_LIMIT:
                logger.error(f"   🚫 Google API quota reached ({API_CALL_COUNT}/{GOOGLE_API_DAILY_LIMIT})! Stopping searches.")
                return None
            
            response = requests.get(api_url, params=params, timeout=10)
            API_CALL_COUNT += 1
            
            if response.status_code == 200:
                return response.json()
            
            if response.status_code in (429, 500, 503):
                wait_time = (2 ** retry) * 2
                logger.warning(f"   ⚠️ API Error {response.status_code} (retry {retry+1}/{max_retries}), waiting {wait_time}s...")
                time.sleep(wait_time)
                continue
            
            logger.error(f"   ❌ API Error: {response.status_code} - {response.text[:100]}")
            return None
            
        except requests.Timeout:
            wait_time = (2 ** retry) * 2
            logger.warning(f"   ⏱️ API Timeout (retry {retry+1}/{max_retries}), waiting {wait_time}s...")
            time.sleep(wait_time)
        except requests.RequestException as e:
            logger.error(f"   ❌ Request error: {e}")
            return None
            
    return None


def smart_select_url(headline, source, search_items):
    """
    Use gemini-3.1-flash-lite to select the best URL from search candidates.
    Prioritizes the correct source name.
    """
    if not gemini_client:
        logger.warning("   ⚠️ Gemini client not configured, falling back to first match")
        return None

    try:
        candidates = []
        for item in search_items:
            link = item.get("link", "")
            if not is_blocked_domain(link) and not link.lower().endswith(BLOCKED_EXTENSIONS):
                candidates.append({
                    "title": item.get("title", ""),
                    "snippet": item.get("snippet", ""),
                    "link": link
                })

        if not candidates:
            return None

        prompt = f"""
        Identify the single best URL for the news headline below.
        
        Target Headline: "{headline}"
        Preferred News Source: "{source}"
        
        Task:
        1. Select the URL that points to the actual news article content.
        2. Priority: Look for a link from the preferred news source ("{source}"). Match the domain if possible.
        3. If no matching article from the preferred source exists, choose the most credible/direct news source.
        4. Strict Rules:
           - Exclude homepages, tag pages, search index pages, PDF files, or sign-up/subscription screens.
           - Ignore news published in old years.
        
        Candidates:
        {json.dumps(candidates, indent=2)}
        
        Output:
        Return ONLY the raw URL string. If no suitable URL is found, return "None".
        """
        
        # Enforce rate limiter
        gemini_limiter.wait()
        
        response = gemini_client.models.generate_content(
            model=GEMINI_PRIMARY_MODEL,
            contents=prompt
        )
        
        selected_url = response.text.strip().replace("```", "").strip()
        if "None" in selected_url or not selected_url.startswith("http"):
            return None
            
        logger.info(f"   🧠 AI Selected ({GEMINI_PRIMARY_MODEL}): {selected_url[:55]}...")
        return selected_url

    except Exception as e:
        logger.error(f"   ⚠️ Gemini URL Selection Error: {e}")
        return None


def find_real_url(headline, source):
    """
    Search Google Custom Search using an optimized query (clean headline only),
    and ask Gemini to choose the best URL.
    """
    if not API_KEY or not SEARCH_ENGINE_ID:
        logger.error("Missing GOOGLE_SEARCH_API_KEY or GOOGLE_SEARCH_CX")
        return None

    clean_headline = headline.replace('"', '').replace("'", "").strip()
    if len(clean_headline) > 100:
        clean_headline = clean_headline[:100]

    # Query formulation: Optimize by searching for the headline directly
    # This yields much better candidates than forcing strict quote + source queries
    query = clean_headline

    params = {
        'key': API_KEY,
        'cx': SEARCH_ENGINE_ID,
        'q': query,
        'num': GOOGLE_API_RESULTS,
        'gl': 'us',
        'lr': 'lang_en',
    }

    # First attempt: Try restricting to date
    params['dateRestrict'] = f'd{DATE_RESTRICT_DAYS}'
    params['sort'] = 'date'
    
    logger.info(f"   🔎 [date-restricted search] {query[:55]}...")
    data = _call_google_api_with_retry(params)
    
    # If no results, try relaxed search without date restrict
    if not data or not data.get('items'):
        logger.info("   ⚠️ No recent results, trying relaxed search...")
        if 'dateRestrict' in params: del params['dateRestrict']
        if 'sort' in params: del params['sort']
        data = _call_google_api_with_retry(params)

    if not data or not data.get('items'):
        logger.warning("   ❌ No search results returned from Google")
        return None

    items = data.get('items', [])
    
    # Let Gemini select the best URL
    selected_url = smart_select_url(headline, source, items)
    
    if selected_url:
        # If Gemini chose a Google redirect URL, resolve it to get the final target URL
        resolved_url = resolve_google_redirect(selected_url)
        if is_valid_url(resolved_url):
            return resolved_url
            
    # Fallback: scan candidate list linearly if Gemini failed or made an invalid choice
    logger.info("   ⚠️ Falling back to linear scan of search results...")
    for item in items:
        url = item.get('link')
        if not url: continue
        resolved_url = resolve_google_redirect(url)
        if is_valid_url(resolved_url):
            logger.info(f"   ✅ Found (Fallback): {resolved_url[:60]}...")
            return resolved_url

    logger.warning("   ❌ No suitable URL found")
    return None


def scrape_riceonline():
    """Main scraping flow for RiceOnline."""
    start_time = time.time()
    
    logger.info("=" * 60)
    logger.info("🌾 RICE NEWS SCOUT V1 - Starting")
    logger.info("=" * 60)
    
    lookback_days, lookback_msg = calculate_lookback_days()
    cutoff_date = datetime.now().date() - timedelta(days=lookback_days)
    logger.info(f"🗓️  {lookback_msg}")
    logger.info(f"🗓️  Cutoff date: {cutoff_date.strftime('%Y-%m-%d')}")
    
    # Fetch RiceOnline homepage
    logger.info(f"🌍 Fetching {RICEONLINE_URL}...")
    headers = {'User-Agent': USER_AGENT}
    try:
        response = requests.get(RICEONLINE_URL, headers=headers, timeout=15)
        response.raise_for_status()
        html = response.text
        logger.info(f"✅ Page fetched: {len(html):,} bytes")
    except requests.RequestException as e:
        logger.error(f"Failed to fetch homepage: {e}")
        return

    # Parse headlines in accordions
    soup = BeautifulSoup(html, 'html.parser')
    date_divs = soup.find_all('div', class_='accordionButton')
    
    if not date_divs:
        logger.error("No date headers found on page")
        return

    candidate_links = []
    
    for date_div in date_divs:
        date_str = date_div.get_text(strip=True)
        date_obj = parse_date_flexible(date_str)
        
        if not date_obj:
            logger.warning(f"Could not parse date: {date_str}")
            continue
            
        if date_obj.date() < cutoff_date:
            logger.info(f"🛑 Stopping at: {date_str} (before cutoff)")
            break
            
        logger.info(f"📅 Processing date section: {date_str}")
        
        content_div = date_div.find_next_sibling('div', class_='accordionContent')
        if content_div:
            links = content_div.find_all('a', class_='free_article_subscribers')
            candidate_links.extend(links)

    logger.info(f"🔍 Found {len(candidate_links)} candidate items")
    
    if not candidate_links:
        logger.warning("No candidate items found")
        return

    # Process candidates
    news_list = []
    skipped_count = 0
    duplicate_count = 0
    
    history = _load_history()
    known_headlines = set(history.get("processed_headlines", []))
    known_urls = set(history.get("processed_urls", []))
    
    logger.info(f"📚 Duplicate DB: {len(known_headlines)} headlines tracked")
    
    for i, link in enumerate(candidate_links, 1):
        raw_text = link.get_text(" ", strip=True)
        
        if '"' not in raw_text:
            continue
            
        parts = raw_text.split(':', 1)
        if len(parts) < 2:
            continue
            
        source = parts[0].strip()
        headline = parts[1].strip().strip('"').strip()
        
        # Check duplicate
        headline_key = headline.lower().strip()
        if headline_key in known_headlines:
            duplicate_count += 1
            logger.info(f"   🔁 Duplicate skipped: {headline[:45]}...")
            continue
        
        logger.info(f"\n[{i}/{len(candidate_links)}] {headline[:45]}...")
        
        real_url = find_real_url(headline, source)
        
        if real_url:
            if real_url in known_urls:
                duplicate_count += 1
                logger.info(f"   🔁 Duplicate URL skipped: {real_url[:50]}...")
                continue
            
            news_item = {
                "source": source,
                "headline": headline,
                "URL": real_url
            }
            news_list.append(news_item)
            
            known_headlines.add(headline_key)
            known_urls.add(real_url)
            history["processed_headlines"].append(headline_key)
            history["processed_urls"].append(real_url)
        else:
            skipped_count += 1
            logger.info(f"   ⏭️ Skipped (no recent URL found)")
        
        time.sleep(GOOGLE_API_DELAY)

    # Save to source.json
    if news_list:
        with open(OUTPUT_SOURCE_FILE, 'w', encoding='utf-8') as f:
            json.dump(news_list, f, indent=2, ensure_ascii=False)
        _save_history(history)
        
        duration = format_duration(time.time() - start_time)
        logger.info("\n" + "=" * 60)
        logger.info(f"✅ Saved {len(news_list)} items to {OUTPUT_SOURCE_FILE}")
        logger.info(f"⏭️  Skipped {skipped_count} items")
        logger.info(f"🔁 Duplicates skipped: {duplicate_count}")
        logger.info(f"📊 Google API calls: {API_CALL_COUNT}/{GOOGLE_API_DAILY_LIMIT}")
        logger.info(f"⏱️  Duration: {duration}")
        logger.info("=" * 60)
    else:
        logger.warning("No valid news items found to save.")


if __name__ == "__main__":
    scrape_riceonline()
