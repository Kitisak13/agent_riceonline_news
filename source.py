# source.py - News Discovery Module (News Scout) - Free AI V1
# ==========================================================

import difflib
import json
import os
import time
from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional
from urllib.parse import urlparse

import requests
from bs4 import BeautifulSoup
from dotenv import load_dotenv
from google import genai
from google.genai import types

from config import (
    RICEONLINE_URL,
    OUTPUT_SOURCE_FILE,
    BLOCKED_DOMAINS,
    BLOCKED_EXTENSIONS,
    DATE_RESTRICT_DAYS,
    GOOGLE_API_DELAY,
    GOOGLE_API_RESULTS,
    GEMINI_PRIMARY_MODEL,
    GEMINI_FALLBACK_MODEL,
    USER_AGENT,
    DATA_DIR,
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
    save_json_atomic,
    clean_json_response,
    extract_domain,
)

# Load environment variables
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
        clean_key = GEMINI_API_KEY.strip().strip('"').strip("'")
        gemini_client = genai.Client(api_key=clean_key)
        logger.info(f"✨ Gemini AI configured successfully with model: {GEMINI_PRIMARY_MODEL}")
    except Exception as e:
        logger.error(f"Failed to configure Gemini: {e}")
        gemini_client = None

CURRENT_YEAR = datetime.now().year

# --- API QUOTA TRACKING ---
GOOGLE_API_DAILY_LIMIT = 100
API_CALL_COUNT = 0

# --- DATA FILES ---
HISTORY_FILE = os.path.join(DATA_DIR, 'processed_history.json')
LEARNED_DOMAINS_FILE = os.path.join(DATA_DIR, 'learned_source_domains.json')


# ==============================================================================
# AUTO-LEARNING SOURCE DOMAIN MANAGER
# ==============================================================================
class SourceDomainManager:
    """
    Manages mapping of News Source names to official web domains.
    Includes built-in mappings and auto-learns new domains dynamically.
    """
    _instance = None

    BUILTIN_DOMAINS = {
        "reuters": "reuters.com",
        "bloomberg": "bloomberg.com",
        "wsj": "wsj.com",
        "wall street journal": "wsj.com",
        "nikkei": "asia.nikkei.com",
        "financial times": "ft.com",
        "the hindu": "thehindu.com",
        "times of india": "timesofindia.indiatimes.com",
        "business standard": "business-standard.com",
        "bangkok post": "bangkokpost.com",
        "the nation": "nationthailand.com",
        "vietnam plus": "en.vietnamplus.vn",
        "vna": "en.vietnamplus.vn",
        "agweb": "agweb.com",
        "agriculture": "agriculture.com",
        "farm progress": "farmprogress.com",
        "usda": "fas.usda.gov",
        "oryza": "oryza.com",
        "livemint": "livemint.com",
        "cnbc": "cnbc.com",
        "bbc": "bbc.com",
    }

    def __new__(cls):
        if cls._instance is None:
            cls._instance = super().__new__(cls)
            cls._instance._learned = cls._instance._load_learned()
        return cls._instance

    def _load_learned(self) -> Dict[str, str]:
        if os.path.exists(LEARNED_DOMAINS_FILE):
            try:
                with open(LEARNED_DOMAINS_FILE, 'r', encoding='utf-8') as f:
                    return json.load(f)
            except (json.JSONDecodeError, IOError):
                pass
        return {}

    def get_domain(self, source_name: str) -> Optional[str]:
        if not source_name:
            return None
        src_key = source_name.strip().lower()

        # 1. Check auto-learned cache first
        if src_key in self._learned:
            return self._learned[src_key]

        # 2. Check built-in mappings
        for key, domain in self.BUILTIN_DOMAINS.items():
            if key in src_key or src_key in key:
                return domain

        return None

    def learn_domain(self, source_name: str, url: str) -> None:
        if not source_name or not url:
            return
        domain = extract_domain(url)
        if not domain or is_blocked_domain(url):
            return

        src_key = source_name.strip().lower()
        if self._learned.get(src_key) != domain:
            self._learned[src_key] = domain
            save_json_atomic(LEARNED_DOMAINS_FILE, self._learned)
            logger.info(f"🧠 Auto-learned Source Domain: '{source_name}' ➔ '{domain}'")


def calculate_title_similarity(target_headline: str, candidate_title: str) -> float:
    """
    Calculate text similarity ratio between target headline and candidate title.
    """
    if not target_headline or not candidate_title:
        return 0.0
    t1 = target_headline.lower().strip()
    t2 = candidate_title.lower().strip()
    return difflib.SequenceMatcher(None, t1, t2).ratio()


def _load_history() -> Dict[str, List[str]]:
    """Load previously processed headlines to detect duplicates across runs."""
    if os.path.exists(HISTORY_FILE):
        try:
            with open(HISTORY_FILE, 'r', encoding='utf-8') as f:
                return json.load(f)
        except (json.JSONDecodeError, IOError):
            pass
    return {"processed_headlines": [], "processed_urls": []}


def _save_history(history: Dict[str, List[str]]) -> None:
    """Save processed headlines/URLs history atomically to file."""
    history["processed_headlines"] = history["processed_headlines"][-500:]
    history["processed_urls"] = history["processed_urls"][-500:]
    save_json_atomic(HISTORY_FILE, history)


def _call_google_api_with_retry(params: Dict[str, Any], max_retries: int = 3) -> Optional[Dict[str, Any]]:
    """Call Google Custom Search API with retry logic."""
    global API_CALL_COUNT
    api_url = "https://www.googleapis.com/customsearch/v1"
    
    if API_CALL_COUNT >= GOOGLE_API_DAILY_LIMIT:
        logger.error(f"   🚫 Google API quota limit reached ({API_CALL_COUNT}/{GOOGLE_API_DAILY_LIMIT})!")
        return None
    
    for retry in range(max_retries):
        try:
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


def smart_select_url(headline: str, source: str, search_items: List[Dict[str, Any]]) -> Optional[str]:
    """
    Use Gemini to select the best URL from search candidates.
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


def gemini_direct_grounding_search(headline: str, source: str) -> Optional[str]:
    """
    Tier 4 Fallback: Use Gemini 2.5 Flash Search Grounding to find original news article URL.
    """
    if not gemini_client:
        return None

    logger.info(f"   🧠 [Tier 4 Gemini Search Grounding] Searching for: {headline[:50]}...")
    prompt = f"""
    Find the direct original news article URL for this headline and source.
    
    Headline: "{headline}"
    Source: "{source}"
    
    Respond with ONLY the exact direct article URL. Do not summarize or add markdown. If not found, respond "None".
    """

    try:
        gemini_limiter.wait()
        config = types.GenerateContentConfig(
            tools=[types.Tool(google_search=types.GoogleSearch())]
        )
        response = gemini_client.models.generate_content(
            model=GEMINI_FALLBACK_MODEL,
            contents=prompt,
            config=config
        )
        url = response.text.strip().replace("```", "").strip()
        if url.startswith("http") and "None" not in url and is_valid_url(url):
            resolved = resolve_google_redirect(url)
            if is_valid_url(resolved):
                logger.info(f"   ✅ [Tier 4 Hit!] {resolved[:60]}...")
                return resolved
    except Exception as e:
        logger.warning(f"   ⚠️ Tier 4 Gemini Search Grounding failed: {e}")

    return None


def find_real_url(headline: str, source: str) -> Optional[str]:
    """
    4-Tier Search Strategy with Auto-Learning Domain Resolution:
    - Tier 1: Target Domain Search (site:domain.com) if domain known
    - Tier 2: Source Name + Headline Phrase Search
    - Tier 3: Exact Headline Search + Fuzzy Title Match Verification
    - Tier 4: Gemini 2.5 Flash Direct Search Grounding Fallback
    """
    domain_manager = SourceDomainManager()
    known_domain = domain_manager.get_domain(source)

    clean_headline = headline.replace('"', '').replace("'", "").strip()
    clean_headline_short = clean_headline[:80]

    # --- TIER 1: Target Domain Search (site:domain.com) ---
    if known_domain and API_KEY and SEARCH_ENGINE_ID and API_CALL_COUNT < GOOGLE_API_DAILY_LIMIT:
        query_t1 = f"site:{known_domain} {clean_headline_short}"
        logger.info(f"   🎯 [Tier 1 Domain Match: {known_domain}] {query_t1[:55]}...")
        params_t1 = {
            'key': API_KEY,
            'cx': SEARCH_ENGINE_ID,
            'q': query_t1,
            'num': 5,
            'gl': 'us',
            'lr': 'lang_en',
            'dateRestrict': f'd{DATE_RESTRICT_DAYS}',
        }
        data_t1 = _call_google_api_with_retry(params_t1)
        if data_t1 and data_t1.get('items'):
            for item in data_t1['items']:
                link = item.get('link', '')
                title = item.get('title', '')
                if known_domain in link.lower() and is_valid_url(link):
                    sim = calculate_title_similarity(headline, title)
                    if sim >= 0.25:
                        resolved = resolve_google_redirect(link)
                        if is_valid_url(resolved):
                            logger.info(f"   ✅ [Tier 1 Hit!] (sim={sim:.2f}) {resolved[:60]}...")
                            return resolved

    # --- TIER 2: Source Name + Headline Search ---
    if API_KEY and SEARCH_ENGINE_ID and API_CALL_COUNT < GOOGLE_API_DAILY_LIMIT:
        query_t2 = f'"{source}" "{clean_headline_short}"'
        logger.info(f"   🔎 [Tier 2 Source+Phrase Search] {query_t2[:55]}...")
        params_t2 = {
            'key': API_KEY,
            'cx': SEARCH_ENGINE_ID,
            'q': query_t2,
            'num': 5,
            'gl': 'us',
            'lr': 'lang_en',
        }
        data_t2 = _call_google_api_with_retry(params_t2)
        if data_t2 and data_t2.get('items'):
            for item in data_t2['items']:
                link = item.get('link', '')
                title = item.get('title', '')
                if is_valid_url(link):
                    sim = calculate_title_similarity(headline, title)
                    if sim >= 0.30:
                        resolved = resolve_google_redirect(link)
                        if is_valid_url(resolved):
                            logger.info(f"   ✅ [Tier 2 Hit!] (sim={sim:.2f}) {resolved[:60]}...")
                            domain_manager.learn_domain(source, resolved)
                            return resolved

    # --- TIER 3: Exact Headline Search + AI / Fuzzy Select ---
    if API_KEY and SEARCH_ENGINE_ID and API_CALL_COUNT < GOOGLE_API_DAILY_LIMIT:
        query_t3 = f'"{clean_headline_short}"'
        logger.info(f"   🔎 [Tier 3 Headline Search] {query_t3[:55]}...")
        params_t3 = {
            'key': API_KEY,
            'cx': SEARCH_ENGINE_ID,
            'q': query_t3,
            'num': GOOGLE_API_RESULTS,
            'gl': 'us',
            'lr': 'lang_en',
        }
        data_t3 = _call_google_api_with_retry(params_t3)
        if data_t3 and data_t3.get('items'):
            items = data_t3['items']
            selected_url = smart_select_url(headline, source, items)
            if selected_url:
                resolved = resolve_google_redirect(selected_url)
                if is_valid_url(resolved):
                    logger.info(f"   ✅ [Tier 3 Hit! AI Selected] {resolved[:60]}...")
                    domain_manager.learn_domain(source, resolved)
                    return resolved

            # Fallback scan for Tier 3
            for item in items:
                link = item.get('link', '')
                title = item.get('title', '')
                if is_valid_url(link):
                    sim = calculate_title_similarity(headline, title)
                    if sim >= 0.35:
                        resolved = resolve_google_redirect(link)
                        if is_valid_url(resolved):
                            logger.info(f"   ✅ [Tier 3 Fallback Hit!] (sim={sim:.2f}) {resolved[:60]}...")
                            domain_manager.learn_domain(source, resolved)
                            return resolved

    # --- TIER 4: Gemini Direct Search Grounding Fallback ---
    tier4_url = gemini_direct_grounding_search(headline, source)
    if tier4_url:
        domain_manager.learn_domain(source, tier4_url)
        return tier4_url

    logger.warning("   ❌ All 4 Search Tiers failed to find suitable URL")
    return None


def scrape_riceonline() -> List[Dict[str, str]]:
    """Main scraping flow for RiceOnline."""
    start_time = time.time()
    
    logger.info("=" * 60)
    logger.info("🌾 RICE NEWS SCOUT V1 - Starting (4-Tier Auto-Learning Search)")
    logger.info("=" * 60)
    
    lookback_days, lookback_msg = calculate_lookback_days()
    cutoff_date = datetime.now().date() - timedelta(days=lookback_days)
    logger.info(f"🗓️  {lookback_msg}")
    logger.info(f"🗓️  Cutoff date: {cutoff_date.strftime('%Y-%m-%d')}")
    
    logger.info(f"🌍 Fetching {RICEONLINE_URL}...")
    headers = {'User-Agent': USER_AGENT}
    try:
        response = requests.get(RICEONLINE_URL, headers=headers, timeout=15)
        response.raise_for_status()
        html = response.text
        logger.info(f"✅ Page fetched: {len(html):,} bytes")
    except requests.RequestException as e:
        logger.error(f"Failed to fetch homepage: {e}")
        return []

    soup = BeautifulSoup(html, 'html.parser')
    date_divs = soup.find_all('div', class_='accordionButton')
    
    if not date_divs:
        logger.error("No date headers found on page")
        return []

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
        return []

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
        
        headline_key = headline.lower().strip()
        if headline_key in known_headlines:
            duplicate_count += 1
            logger.info(f"   🔁 Duplicate skipped: {headline[:45]}...")
            continue
        
        logger.info(f"\n[{i}/{len(candidate_links)}] Source: '{source}' | {headline[:45]}...")
        
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
            logger.info("   ⏭️ Skipped (no recent URL found)")
        
        time.sleep(GOOGLE_API_DELAY)

    if news_list:
        save_json_atomic(OUTPUT_SOURCE_FILE, news_list)
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

    return news_list


if __name__ == "__main__":
    scrape_riceonline()
