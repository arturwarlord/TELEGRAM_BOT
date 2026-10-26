from __future__ import annotations

import unittest
from unittest.mock import patch

from deal_hunter.scoring import evaluate_deal
from deal_hunter.takprodam import (
    fetch_commissions,
    fetch_promotion_deals,
    parse_promotion_items,
    resolve_source_id,
    summarize_commissions,
)


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
        with self.assertRaisesRegex(RuntimeError, "not approved yet"):
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

    @patch("deal_hunter.takprodam._request_json")
    def test_explains_when_channel_source_is_pending_approval(self, mock_request):
        mock_request.return_value = {
            "items": [
                {"id": 21, "title": "Telegram Channel", "status": "verification"}
            ]
        }
        with self.assertRaisesRegex(RuntimeError, "Telegram Channel=verification"):
            resolve_source_id("dummy-token")


    @patch("deal_hunter.takprodam._request_json")
    def test_promotion_fetch_passes_subid_for_attribution(self, mock_request):
        mock_request.return_value = {"items": []}
        fetch_promotion_deals(
            "dummy-token",
            "7",
            ["Ozon"],
            limit=20,
            max_pages=1,
            subid="telegram_deal_hunter",
        )
        self.assertEqual(
            mock_request.call_args.kwargs["params"],
            {
                "source_id": "7",
                "marketplace": "Ozon",
                "page": 1,
                "limit": 20,
                "subid": "telegram_deal_hunter",
            },
        )

    @patch("deal_hunter.takprodam._request_json")
    def test_fetch_commissions_filters_by_subid(self, mock_request):
        mock_request.return_value = {
            "total_count": 1,
            "items": [{
                "id": "commission-1",
                "status": "approved",
                "commission_amount": 125.5,
                "cart_amount": 2500,
                "subid": "telegram_deal_hunter",
            }],
        }
        items = fetch_commissions(
            "dummy-token",
            subid="telegram_deal_hunter",
            limit=50,
            max_pages=3,
        )
        self.assertEqual(len(items), 1)
        self.assertEqual(mock_request.call_args.kwargs["params"]["subid"], "telegram_deal_hunter")
        self.assertEqual(mock_request.call_args.kwargs["params"]["page"], 1)

    def test_summarizes_commissions_by_status_without_order_details(self):
        summary = summarize_commissions([
            {"status": "approved", "commission_amount": 10.25, "cart_amount": 100},
            {"status": "approved", "commission_amount": 1.75, "cart_amount": 50},
            {"status": "pending", "commission_amount": 5, "cart_amount": 25},
            {"status": "declined", "commission_amount": 3, "cart_amount": 30},
        ])
        self.assertEqual(summary["approved"], {
            "count": 2,
            "commission_amount": 12.0,
            "cart_amount": 150.0,
        })
        self.assertEqual(summary["pending"]["count"], 1)
        self.assertEqual(summary["declined"]["count"], 1)

    def test_rejects_unsupported_commission_status_filter(self):
        with self.assertRaisesRegex(ValueError, "Commission status must be"):
            fetch_commissions("dummy-token", status="confirmed")

if __name__ == "__main__":
    unittest.main()
