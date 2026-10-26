from __future__ import annotations

import unittest
from unittest.mock import patch

from deal_hunter.scoring import evaluate_deal
from deal_hunter.takprodam import parse_promotion_items, resolve_source_id


class TakprodamTests(unittest.TestCase):
    def sample_item(self):
        return {
            "id": "deal-1",
            "product_id": "sku-123",
            "title": "Умные часы Example",
            "image_url": "https://cdn.example.test/watch.jpg",
            "price": 12000,
            "price_discount": 6000,
            "discount_percent": 50,
            "commission": 120.0,
            "marketplace_title": "Ozon",
            "store_title": "Продавец Example",
            "external_link": "https://www.ozon.ru/product/example-123/",
            "tracking_link": "https://track.example.test/abc123",
            "legal_text": "Реклама. Данные рекламодателя из партнёрского API.",
        }

    def test_maps_promotion_product_into_deal(self):
        deals = parse_promotion_items([self.sample_item()], "Ozon")
        self.assertEqual(len(deals), 1)
        deal = deals[0]
        self.assertEqual(deal.title, "Умные часы Example")
        self.assertEqual(deal.price, 6000)
        self.assertEqual(deal.old_price, 12000)
        self.assertEqual(deal.discount_percent, 50)
        self.assertEqual(deal.affiliate_url, "https://track.example.test/abc123")
        self.assertEqual(deal.product_url, "https://www.ozon.ru/product/example-123/")
        self.assertEqual(deal.image_url, "https://cdn.example.test/watch.jpg")
        self.assertEqual(deal.commission, 120.0)
        self.assertIn("Реклама", deal.legal_text)

    def test_rejects_unavailable_or_invalid_price_pair(self):
        invalid = self.sample_item()
        invalid["price_discount"] = 13000
        deals = parse_promotion_items([invalid], "Ozon")
        self.assertEqual(deals, [])

    def test_filters_marketplace(self):
        deals = parse_promotion_items([self.sample_item()], "Wildberries")
        self.assertEqual(deals, [])

    def test_mapped_deal_is_ready_for_normal_quality_filter(self):
        deals = parse_promotion_items([self.sample_item()], "Ozon")
        decision = evaluate_deal(deals[0], require_affiliate=True, require_image=True)
        self.assertTrue(decision.eligible)
        self.assertAlmostEqual(decision.discount_percent, 50)

    @patch("deal_hunter.takprodam._request_json")
    def test_resolves_single_approved_source(self, mock_request):
        mock_request.return_value = {
            "items": [
                {"id": 7, "title": "Telegram deals", "status": "approved"},
                {"id": 8, "title": "Pending website", "status": "verification"},
            ]
        }
        self.assertEqual(resolve_source_id("dummy-token"), "7")
        mock_request.assert_called_once()

    @patch("deal_hunter.takprodam._request_json")
    def test_configured_source_must_be_approved(self, mock_request):
        mock_request.return_value = {
            "items": [
                {"id": 7, "title": "Telegram deals", "status": "approved"},
                {"id": 8, "title": "Pending website", "status": "verification"},
            ]
        }
        with self.assertRaisesRegex(RuntimeError, "not in the approved sources"):
            resolve_source_id("dummy-token", "8")


    def test_same_product_dedupes_across_promotion_links(self):
        first = self.sample_item()
        second = dict(first)
        first.pop("external_link", None)
        second.pop("external_link", None)
        second["tracking_link"] = "https://track.example.test/other-promotion"
        deals = parse_promotion_items([first, second], "Ozon")
        self.assertEqual(len(deals), 2)
        self.assertEqual(deals[0].key, deals[1].key)

if __name__ == "__main__":
    unittest.main()
