# raw_headline.py - Simple Headline Scraper (No Google Search) - Free AI V1
# ========================================================================

import json
import os
import time
from datetime import datetime, timedelta
import requests
from bs4 import BeautifulSoup
from dotenv import load_dotenv

from config import (
    RICEONLINE_URL,
    USER_AGENT,
)
from utils import (
    logger,
    parse_date_flexible,
    calculate_lookback_days,
    format_duration,
)

load_dotenv()
OUTPUT_FILE = 'raw_headline.json'


def scrape_headlines():
    start_time = time.time()
    
    logger.info("=" * 60)
    logger.info("🌾 RAW HEADLINE SCRAPER V1 - Starting")
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
        logger.error(f"Failed to fetch page: {e}")
        return []

    soup = BeautifulSoup(html, 'html.parser')
    date_divs = soup.find_all('div', class_='accordionButton')
    
    if not date_divs:
        logger.error("No date headers found on page")
        return []

    headlines_list = []
    
    for date_div in date_divs:
        date_str = date_div.get_text(strip=True)
        date_obj = parse_date_flexible(date_str)
        
        if not date_obj:
            logger.warning(f"Could not parse date: {date_str}")
            continue
            
        if date_obj.date() < cutoff_date:
            logger.info(f"🛑 Stopping at: {date_str} (before cutoff)")
            break
            
        logger.info(f"📅 Processing date: {date_str}")
        
        content_div = date_div.find_next_sibling('div', class_='accordionContent')
        if not content_div:
            continue
            
        links = content_div.find_all('a', class_='free_article_subscribers')
        
        for link in links:
            raw_text = link.get_text(" ", strip=True)
            
            if '"' not in raw_text:
                continue
                
            parts = raw_text.split(':', 1)
            if len(parts) < 2:
                continue
                
            source = parts[0].strip()
            headline = parts[1].strip().strip('"').strip()
            
            if headline:
                headlines_list.append({
                    "source": source,
                    "headline": headline,
                    "date": date_obj.strftime('%Y-%m-%d')
                })

    if headlines_list:
        with open(OUTPUT_FILE, 'w', encoding='utf-8') as f:
            json.dump(headlines_list, f, indent=2, ensure_ascii=False)
        
        duration = format_duration(time.time() - start_time)
        logger.info("\n" + "=" * 60)
        logger.info(f"✅ Saved {len(headlines_list)} headlines to {OUTPUT_FILE}")
        logger.info(f"⏱️  Duration: {duration}")
        logger.info("=" * 60)
    else:
        logger.warning("No headlines found")
    
    return headlines_list


if __name__ == "__main__":
    scrape_headlines()
