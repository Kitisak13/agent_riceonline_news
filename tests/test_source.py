# tests/test_source.py - Unit Tests for Source Discovery & Domain Learning
# =========================================================================

import os
import tempfile
import unittest

from source import SourceDomainManager, calculate_title_similarity


class TestSourceDiscovery(unittest.TestCase):

    def test_calculate_title_similarity(self):
        h1 = "India eases rice export tax to boost global supplies"
        h2 = "India Eases Rice Export Tax To Boost Global Supplies - Reuters"
        h3 = "Wheat market prices jump in European trade"

        sim_high = calculate_title_similarity(h1, h2)
        sim_low = calculate_title_similarity(h1, h3)

        self.assertGreater(sim_high, 0.6)
        self.assertLess(sim_low, 0.4)

    def test_source_domain_manager_builtin(self):
        manager = SourceDomainManager()
        self.assertEqual(manager.get_domain("Reuters"), "reuters.com")
        self.assertEqual(manager.get_domain("Bangkok Post"), "bangkokpost.com")
        self.assertEqual(manager.get_domain("The Hindu News"), "thehindu.com")

    def test_source_domain_manager_learn(self):
        manager = SourceDomainManager()
        test_source = "Custom Rice Daily Journal"
        test_url = "https://customricedaily.org/articles/2026/07/25/export-update"

        manager.learn_domain(test_source, test_url)
        learned = manager.get_domain(test_source)

        self.assertEqual(learned, "customricedaily.org")


if __name__ == "__main__":
    unittest.main()
