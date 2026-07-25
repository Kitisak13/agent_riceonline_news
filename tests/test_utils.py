# tests/test_utils.py - Unit Tests for Rice News Aggregator Utilities
# ====================================================================

import json
import os
import tempfile
import unittest
from datetime import datetime, timedelta

from utils import (
    clean_json_response,
    format_duration,
    is_blocked_domain,
    is_old_news,
    is_valid_url,
    mask_sensitive_value,
    parse_date_flexible,
    save_json_atomic,
)


class TestUtils(unittest.TestCase):

    def test_parse_date_flexible(self):
        self.assertEqual(parse_date_flexible("2026-07-25"), datetime(2026, 7, 25))
        self.assertEqual(parse_date_flexible("July 25, 2026"), datetime(2026, 7, 25))
        self.assertEqual(parse_date_flexible("25/07/2026"), datetime(2026, 7, 25))
        self.assertIsNone(parse_date_flexible("invalid-date-string"))
        self.assertIsNone(parse_date_flexible(""))

    def test_is_old_news(self):
        old_date = (datetime.now() - timedelta(days=15)).strftime("%Y-%m-%d")
        recent_date = (datetime.now() - timedelta(days=2)).strftime("%Y-%m-%d")
        self.assertTrue(is_old_news(old_date, days=10))
        self.assertFalse(is_old_news(recent_date, days=10))

    def test_is_valid_url(self):
        self.assertTrue(is_valid_url("https://www.reuters.com/business/grain-market-2026-07-25/"))
        self.assertFalse(is_valid_url("https://facebook.com/somepost"))
        self.assertFalse(is_valid_url("https://example.com/document.pdf"))
        self.assertFalse(is_valid_url("https://example.com/"))
        self.assertFalse(is_valid_url(""))

    def test_is_blocked_domain(self):
        self.assertTrue(is_blocked_domain("https://twitter.com/user"))
        self.assertTrue(is_blocked_domain("https://youtube.com/watch"))
        self.assertFalse(is_blocked_domain("https://www.reuters.com/article"))

    def test_clean_json_response(self):
        raw_json_markdown = '```json\n{\n  "headline": "Test",\n  "content": "Valid content"\n}\n```'
        data = clean_json_response(raw_json_markdown)
        self.assertEqual(data["headline"], "Test")
        self.assertEqual(data["content"], "Valid content")

        raw_plain_json = '{"headline": "Test2", "content": "Valid"}'
        data2 = clean_json_response(raw_plain_json)
        self.assertEqual(data2["headline"], "Test2")

    def test_mask_sensitive_value(self):
        self.assertEqual(mask_sensitive_value("AIzaSy1234567890SecretKey"), "AIza...tKey")
        self.assertEqual(mask_sensitive_value(""), "NOT SET")
        self.assertEqual(mask_sensitive_value("short"), "*****")

    def test_format_duration(self):
        self.assertEqual(format_duration(15.5), "15.5s")
        self.assertEqual(format_duration(120), "2.0m")
        self.assertEqual(format_duration(3600), "1.0h")

    def test_save_json_atomic(self):
        with tempfile.TemporaryDirectory() as tmp_dir:
            target_path = os.path.join(tmp_dir, "test_output.json")
            test_data = {"status": "success", "count": 42}
            
            result = save_json_atomic(target_path, test_data)
            self.assertTrue(result)
            self.assertTrue(os.path.exists(target_path))
            
            with open(target_path, 'r', encoding='utf-8') as f:
                loaded = json.load(f)
            self.assertEqual(loaded, test_data)


if __name__ == "__main__":
    unittest.main()
