# config.py - Shared Configuration for Rice News Aggregator (Free AI V1)
# =====================================================================

import os

# --- PATHS & DIRECTORIES ---
DATA_DIR = "data"
LOG_DIR = "logs"
os.makedirs(DATA_DIR, exist_ok=True)
os.makedirs(LOG_DIR, exist_ok=True)

# --- API & SCRAPING CONFIG ---
RICEONLINE_URL = "https://riceonline.com/"
OUTPUT_SOURCE_FILE = os.path.join(DATA_DIR, "source.json")

# --- SELENIUM CONFIG ---
HEADLESS_BROWSER = True  # Run in headless mode (no UI)
SELENIUM_WAIT_TIMEOUT = 20  # seconds
PAGE_LOAD_TIMEOUT = 20  # seconds

# --- AI & PROCESSING CONFIG ---
# Primary model for text selection and cleaning (15 RPM, 500 RPD)
GEMINI_PRIMARY_MODEL = "gemini-3.1-flash-lite"
# Fallback model for search grounding / Cloudflare bypass (5 RPM, 20 RPD)
GEMINI_FALLBACK_MODEL = "gemini-2.5-flash"

GEMINI_API_DELAY = 5.0  # Delay between Gemini API calls to respect 15 RPM
MAX_RETRIES = 3
OLD_NEWS_DAYS = 10  # Filter out news older than X days

# --- GOOGLE CUSTOM SEARCH CONFIG ---
GOOGLE_API_DELAY = 0.3  # seconds between Google Search API calls
GOOGLE_API_RESULTS = 5  # Number of results to fetch
DATE_RESTRICT_DAYS = 8  # Only search for news from last X days

# --- URL VALIDATION ---
MIN_ARTICLE_URL_LENGTH = 20
MIN_PATH_SEGMENTS = 2

# --- BLOCKED DOMAINS ---
BLOCKED_DOMAINS = [
    # Social Media
    "facebook.com", "twitter.com", "x.com", "instagram.com", "linkedin.com", "tiktok.com", "pinterest.com",
    # Video Platforms
    "youtube.com",
    # Forums
    "reddit.com",
    # Source Site
    "riceonline.com",
    # Known Non-News Sites
    "who.int", "oryza.com", "livemint.com", "fas.usda.gov"
]

# --- BLOCKED FILE EXTENSIONS ---
BLOCKED_EXTENSIONS = ('.pdf', '.doc', '.docx', '.xls', '.xlsx', '.ppt', '.pptx')

# --- DATE FORMATS ---
DATE_FORMATS = [
    "%Y-%m-%d", "%Y/%m/%d",
    "%B %d, %Y", "%b %d, %Y",
    "%m-%d-%Y", "%m/%d/%Y",
    "%d-%m-%Y", "%d/%m/%Y",
    "%d %B %Y", "%d.%m.%Y",
    "%A, %B %d, %Y",
]

# --- REQUIRED ENV KEYS ---
REQUIRED_ENV_KEYS = [
    "GEMINI_API_KEY",
    "GOOGLE_SEARCH_API_KEY",
    "GOOGLE_SEARCH_CX",
    "GOOGLE_CREDENTIALS_JSON",
    "EMAIL_SENDER",
    "EMAIL_PASSWORD",
    "DRIVE_FOLDER_ID",
]

# --- USER AGENT ---
USER_AGENT = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"

# --- SELENIUM SESSION REUSE ---
SELENIUM_SESSION_MAX_URLS = 10

# --- DOMAINS REQUIRING SELENIUM ---
SELENIUM_REQUIRED_DOMAINS = [
    "reuters.com", "bloomberg.com", "wsj.com", "ft.com", "nikkei.com",
    "agweb.com", "agriculture.com", "farmprogress.com",
    "businessinsider.com", "insider.com",
]

# --- DOMAINS TO AUTO-LEARN ---
FAILED_DOMAINS_CACHE_FILE = os.path.join(DATA_DIR, "failed_domains_cache.json")
FAILED_DOMAINS_THRESHOLD = 2
