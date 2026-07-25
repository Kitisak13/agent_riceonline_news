# utils.py - Shared Utility Functions for Rice News Aggregator (Free AI V1)
# =========================================================================

import json
import logging
import logging.handlers
import os
import time
import requests
from datetime import datetime, timedelta
from urllib.parse import urlparse

from config import (
    BLOCKED_DOMAINS,
    BLOCKED_EXTENSIONS,
    DATE_FORMATS,
    FAILED_DOMAINS_CACHE_FILE,
    FAILED_DOMAINS_THRESHOLD,
    OLD_NEWS_DAYS,
    SELENIUM_REQUIRED_DOMAINS,
)

# --- LOGGING SETUP (Console + File) ---
LOG_DIR = "logs"
os.makedirs(LOG_DIR, exist_ok=True)

logger = logging.getLogger("rice_news_v1")
logger.setLevel(logging.DEBUG)

# Console handler (INFO level)
if not logger.handlers:
    console_handler = logging.StreamHandler()
    console_handler.setLevel(logging.INFO)
    console_handler.setFormatter(logging.Formatter(
        '%(asctime)s | %(levelname)-7s | %(message)s', datefmt='%H:%M:%S'
    ))

    # File handler (DEBUG level, rotating 5MB x 3 files)
    file_handler = logging.handlers.RotatingFileHandler(
        os.path.join(LOG_DIR, "rice_news_v1.log"),
        maxBytes=5 * 1024 * 1024,  # 5 MB
        backupCount=3,
        encoding='utf-8'
    )
    file_handler.setLevel(logging.DEBUG)
    file_handler.setFormatter(logging.Formatter(
        '%(asctime)s | %(levelname)-7s | %(message)s', datefmt='%Y-%m-%d %H:%M:%S'
    ))

    logger.addHandler(console_handler)
    logger.addHandler(file_handler)


class RateLimiter:
    """
    Enforces a delay between API calls to stay within RPM limits.
    """
    def __init__(self, requests_per_minute=12):
        self.delay = 60.0 / requests_per_minute
        self.last_call = 0.0
        
    def wait(self):
        elapsed = time.time() - self.last_call
        if elapsed < self.delay:
            time.sleep(self.delay - elapsed)
        self.last_call = time.time()


# Global rate limiter instance for Gemini API (12 requests/minute, safe for 15 RPM)
gemini_limiter = RateLimiter(requests_per_minute=12)


def parse_date_flexible(date_str):
    """
    Parse date from various string formats.
    """
    if not date_str:
        return None
    
    date_str = str(date_str).strip()
    
    for fmt in DATE_FORMATS:
        try:
            return datetime.strptime(date_str, fmt)
        except ValueError:
            continue
    
    try:
        return datetime.fromisoformat(date_str.replace('Z', '+00:00'))
    except ValueError:
        pass
    
    return None


def is_old_news(date_str, days=None):
    """
    Check if a date is older than X days.
    """
    if days is None:
        days = OLD_NEWS_DAYS
        
    dt = parse_date_flexible(date_str)
    if not dt:
        return False
    
    limit = datetime.now() - timedelta(days=days)
    return dt < limit


def is_valid_url(url):
    """
    Validate if URL is suitable for news scraping.
    """
    if not url:
        return False
    
    url_lower = url.lower()
    
    # 1. Check blocked domains
    for domain in BLOCKED_DOMAINS:
        if domain in url_lower:
            return False
    
    # 2. Check blocked file extensions
    if url_lower.endswith(BLOCKED_EXTENSIONS):
        return False
    
    # 3. Check for homepage
    try:
        parsed = urlparse(url)
        path = parsed.path.rstrip('/')
        
        if not path or path == '':
            return False
        
        path_parts = [p for p in path.split('/') if p]
        if len(path_parts) < 1:
            return False
            
        if len(path_parts) == 1 and len(path_parts[0]) < 3:
            return False
            
    except Exception:
        return False
    
    return True


def is_blocked_domain(url):
    if not url:
        return True
    url_lower = url.lower()
    return any(domain in url_lower for domain in BLOCKED_DOMAINS)


def calculate_lookback_days():
    """
    Calculate lookback days based on current weekday.
    """
    today = datetime.now().date()
    weekday = today.weekday()
    
    if weekday == 0:  # Monday
        return 3, "Monday: Looking back to Friday (skipping weekend)"
    elif weekday == 6:  # Sunday
        return 2, "Sunday: Looking back to Friday"
    else:
        return 1, "Normal day: Looking at yesterday"


def mask_sensitive_value(value, visible_chars=4):
    if not value:
        return "NOT SET"
    if len(value) <= visible_chars * 2:
        return "*" * len(value)
    return f"{value[:visible_chars]}...{value[-visible_chars:]}"


def format_duration(seconds):
    if seconds < 60:
        return f"{seconds:.1f}s"
    elif seconds < 3600:
        minutes = seconds / 60
        return f"{minutes:.1f}m"
    else:
        hours = seconds / 3600
        return f"{hours:.1f}h"


def extract_domain(url):
    if not url:
        return None
    try:
        parsed = urlparse(url)
        domain = parsed.netloc.lower()
        if domain.startswith('www.'):
            domain = domain[4:]
        return domain
    except Exception:
        return None


def requires_selenium(url):
    domain = extract_domain(url)
    if not domain:
        return False
    
    for selenium_domain in SELENIUM_REQUIRED_DOMAINS:
        if selenium_domain in domain or domain in selenium_domain:
            return True
    
    cache = DomainFailureCache()
    if cache.should_use_selenium(domain):
        return True
    
    return False


def resolve_google_redirect(url):
    """
    If the URL is a Google Search Grounding redirect URL, resolve it to the final destination.
    """
    if not url:
        return url
        
    if "grounding-api-redirect" in url or "googleusercontent.com" in url:
        try:
            logger.info(f"   🔗 Resolving Google redirect URL: {url[:50]}...")
            headers = {
                'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36'
            }
            # Use HEAD request for speed, fall back to GET if it fails or returns error status
            r = requests.head(url, headers=headers, allow_redirects=True, timeout=5)
            if r.status_code >= 400:
                r = requests.get(url, headers=headers, allow_redirects=True, timeout=5)
            logger.info(f"   ✅ Resolved to: {r.url[:50]}...")
            return r.url
        except Exception as e:
            logger.warning(f"   ⚠️ Failed to resolve redirect URL: {e}")
    return url


class DomainFailureCache:
    _instance = None
    _cache = None
    
    def __new__(cls):
        if cls._instance is None:
            cls._instance = super().__new__(cls)
            cls._cache = cls._instance._load_cache()
        return cls._instance
    
    def _load_cache(self):
        if os.path.exists(FAILED_DOMAINS_CACHE_FILE):
            try:
                with open(FAILED_DOMAINS_CACHE_FILE, 'r') as f:
                    return json.load(f)
            except (json.JSONDecodeError, IOError):
                pass
        return {"failures": {}, "selenium_required": []}
    
    def _save_cache(self):
        try:
            with open(FAILED_DOMAINS_CACHE_FILE, 'w') as f:
                json.dump(self._cache, f, indent=2)
        except IOError as e:
            logger.warning(f"Failed to save domain cache: {e}")
    
    def record_failure(self, url):
        domain = extract_domain(url)
        if not domain:
            return
        
        current = self._cache["failures"].get(domain, 0)
        self._cache["failures"][domain] = current + 1
        
        if self._cache["failures"][domain] >= FAILED_DOMAINS_THRESHOLD:
            if domain not in self._cache["selenium_required"]:
                self._cache["selenium_required"].append(domain)
                logger.info(f"Auto-learned: {domain} now requires Selenium")
        
        self._save_cache()
    
    def record_success(self, url):
        domain = extract_domain(url)
        if not domain:
            return
        
        if domain in self._cache["failures"]:
            del self._cache["failures"][domain]
            self._save_cache()
    
    def should_use_selenium(self, domain):
        return domain in self._cache.get("selenium_required", [])
    
    def get_stats(self):
        return {
            "domains_with_failures": len(self._cache.get("failures", {})),
            "selenium_required_count": len(self._cache.get("selenium_required", [])),
            "selenium_required_domains": self._cache.get("selenium_required", [])
        }
