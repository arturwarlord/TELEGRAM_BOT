from __future__ import annotations

import json
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


class DirectOnlyProjectPolicyTests(unittest.TestCase):
    def test_main_code_enforces_direct_links_and_disables_partner_api(self):
        source = (ROOT / "main.py").read_text(encoding="utf-8")
        self.assertIn("allow_direct_links = True", source)
        self.assertIn("partner_api_enabled = False", source)

    def test_workflow_does_not_inject_partner_credentials_or_publish_partner_posts(self):
        workflow = (ROOT / ".github" / "workflows" / "deal-hunter.yml").read_text(encoding="utf-8")
        self.assertIn('ALLOW_DIRECT_LINKS: "true"', workflow)
        self.assertNotIn("TAKPRODAM_API_TOKEN:", workflow)
        self.assertNotIn("ENABLE_TAKPRODAM_PARTNER_API:", workflow)
        self.assertNotIn("launch one-time partner posts:", workflow)

    def test_commission_reporting_workflow_is_removed(self):
        self.assertFalse((ROOT / ".github" / "workflows" / "takprodam-commission-report.yml").exists())

    def test_enabled_sources_are_not_coupon_or_promo_catalogues(self):
        config = json.loads((ROOT / "config" / "sources.json").read_text(encoding="utf-8"))
        enabled = [source for source in config.get("sources", []) if source.get("enabled") is True]
        self.assertTrue(enabled)
        self.assertFalse(any(source.get("type") == "html_promos" for source in enabled))
        self.assertFalse(any(source.get("category") == "coupons" for source in enabled))

    def test_marketplace_sources_require_an_authorized_machine_readable_feed(self):
        config = json.loads((ROOT / "config" / "sources.json").read_text(encoding="utf-8"))
        marketplace_markers = (
            "wildberries", "ozon", "яндекс маркет", "мегамаркет",
            "aliexpress", "ламода", "детский мир",
        )
        machine_feed_types = {"json", "xml", "rss", "atom"}
        enabled_marketplace_sources = [
            source
            for source in config.get("sources", [])
            if source.get("enabled") is True
            and any(
                marker in " ".join(
                    str(source.get(field) or "")
                    for field in ("name", "store", "url")
                ).casefold()
                for marker in marketplace_markers
            )
        ]
        for source in enabled_marketplace_sources:
            self.assertIn(
                source.get("type"),
                machine_feed_types,
                msg=f"Marketplace source must be a machine-readable feed: {source.get('name')}",
            )
            self.assertIs(
                source.get("authorized_marketplace_feed"),
                True,
                msg=f"Marketplace feed authorization must be explicit: {source.get('name')}",
            )


if __name__ == "__main__":
    unittest.main()
