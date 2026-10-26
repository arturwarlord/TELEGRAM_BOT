from __future__ import annotations

import json
import unittest
from pathlib import Path

from deal_hunter.sources import parse_feed


FIXTURE = Path(__file__).parent / "fixtures" / "sample_feed.xml"


class FeedParserTests(unittest.TestCase):
    def test_parses_xml_affiliate_feed_with_photo_and_prices(self):
        source = {
            "name": "test-affiliate-feed",
            "type": "xml",
            "enabled": True,
            "links_are_affiliate": False,
        }
        payload = FIXTURE.read_bytes()
        deals = parse_feed(payload, source)
        self.assertEqual(len(deals), 1)
        deal = deals[0]
        self.assertIn("Наушники Example", deal.title)
        self.assertEqual(deal.price, 1000.0)
        self.assertEqual(deal.old_price, 2000.0)
        self.assertEqual(deal.store, "Тестовый магазин")
        self.assertEqual(deal.affiliate_url, "https://affiliate.example.test/click?id=abc")
        self.assertEqual(deal.image_url, "https://cdn.example.test/item-123.jpg")
        self.assertEqual(deal.discount_percent, 50.0)

    def test_reads_json_feed_with_custom_fields(self):
        payload = json.dumps({
            "offers": [{
                "name": "Чайник",
                "product_link": "https://shop.example.test/kettle",
                "tracking_link": "https://affiliate.example.test/kettle",
                "picture": "https://cdn.example.test/kettle.jpg",
                "sale_price": "1 000 RUB",
                "regular_price": "2 000 RUB",
                "merchant": "Магазин",
            }]
        }, ensure_ascii=False).encode()
        source = {
            "name": "json-test",
            "type": "json",
            "items_path": "offers",
            "fields": {
                "title": "name",
                "product_url": "product_link",
                "affiliate_url": "tracking_link",
                "image_url": "picture",
                "price": "sale_price",
                "old_price": "regular_price",
                "store": "merchant",
            },
        }
        deals = parse_feed(payload, source)
        self.assertEqual(len(deals), 1)
        self.assertEqual(deals[0].price, 1000.0)
        self.assertEqual(deals[0].old_price, 2000.0)
        self.assertEqual(deals[0].affiliate_url, "https://affiliate.example.test/kettle")

    def test_uses_product_url_only_when_feed_is_declared_affiliate(self):
        payload = b'{"items":[{"title":"T","url":"https://shop.example.test/1","price":100,"old_price":200}]}'
        source = {"name": "declared-affiliate", "type": "json", "links_are_affiliate": True}
        deals = parse_feed(payload, source)
        self.assertEqual(deals[0].affiliate_url, deals[0].product_url)

    def test_rejects_records_without_product_url(self):
        payload = b'{"items":[{"title":"No URL","price":100,"old_price":200}]}'
        deals = parse_feed(payload, {"name": "json-test", "type": "json"})
        self.assertEqual(deals, [])


if __name__ == "__main__":
    unittest.main()
